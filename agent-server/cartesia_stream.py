"""Live meeting audio -> Cartesia ink-2 STT, gated by a wake phrase.

Teams / Meet / Zoom do not give a simple "tap the call" API. On a Mac the
hackathon path is a virtual loopback device (BlackHole):

  1. Install BlackHole 2ch: https://existential.audio/blackhole/
  2. Open Audio MIDI Setup -> create a Multi-Output Device of
     (your speakers/headphones + BlackHole 2ch) so you can still hear the call.
  3. Set macOS output to that Multi-Output Device.
  4. In Teams (or any meeting app) use the system output, or pick BlackHole
     as the speaker if you do not want a multi-output device.
  5. Run this script with --device pointing at BlackHole 2ch.

Until BlackHole is set up, --device default captures your microphone so you
can test "Hey Argo, ..." locally.

Cartesia ink-2 decides when a thought is finished (turn.end). That is how we
know the user has stopped talking to Argo.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import io
import json
import os
import random
import re
import sys
import wave
from urllib.parse import urlencode

import httpx
import sounddevice as sd
import websockets
from dotenv import load_dotenv

SAMPLE_RATE = 16000
CHUNK_MS = 100
FRAMES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS // 1000
LEVEL_EVERY_CHUNKS = 20  # ~2s at 100ms chunks
STT_URL = "wss://api.cartesia.ai/stt/turns/websocket"
CARTESIA_VERSION = "2026-08-14"
DEFAULT_WAKE = "hey argo"
ACKNOWLEDGEMENTS = (
    "Okay, I'm on it.",
    "Got it. Let me take care of that.",
    "Sure, I'll check that now.",
    "Understood. I'm working on it.",
    "All right, give me a moment.",
)

# Patient-ish: meeting speech has pauses. Cartesia still emits turn.end when
# the thought is actually done.
TURN_END_TIMEOUT_MS = 8000
TURN_END_THRESHOLD = 0.1
TURN_EAGER_END_THRESHOLD = 0.3
TURN_START_THRESHOLD = 0.8


def list_devices() -> None:
    print(sd.query_devices())
    print(f"\nDefault input: {sd.default.device[0]}")
    print("Pick the BlackHole 2ch input index for meeting loopback.")


def resolve_device(spec: str | None) -> int | None:
    if spec is None or spec == "default":
        return None
    try:
        return int(spec)
    except ValueError:
        pass
    needle = spec.lower()
    matches = [
        (idx, dev)
        for idx, dev in enumerate(sd.query_devices())
        if needle in str(dev["name"]).lower() and int(dev["max_input_channels"]) > 0
    ]
    if not matches:
        raise SystemExit(
            f"No input device matching {spec!r}. Run with --list-devices."
        )
    if len(matches) > 1:
        names = ", ".join(f"{i}: {d['name']}" for i, d in matches)
        print(f"Multiple matches ({names}); using {matches[0][0]}", file=sys.stderr)
    return matches[0][0]


def input_channel_count(device: int | None) -> int:
    info = sd.query_devices(device, "input")
    channels = int(info["max_input_channels"])
    if channels < 1:
        raise SystemExit(f"Device {info['name']!r} has no inputs.")
    return channels


def downmix_to_mono_s16(indata, channels: int) -> bytes:
    raw = bytes(indata)
    if channels == 1:
        return raw
    samples = memoryview(raw).cast("h")
    frames = len(samples) // channels
    out = array.array("h")
    for i in range(frames):
        acc = 0
        base = i * channels
        for c in range(channels):
            acc += samples[base + c]
        out.append(int(acc / channels))
    return out.tobytes()


def peak_level(mono: bytes) -> float:
    samples = memoryview(mono).cast("h")
    if not samples:
        return 0.0
    return max(abs(s) for s in samples) / 32768.0


def speak_with_cartesia(text: str) -> None:
    """Synthesize a short reply with Cartesia and play it on the default output."""
    if not text.strip():
        return
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("Set CARTESIA_API_KEY to enable Cartesia speech output.")

    response = httpx.post(
        "https://api.cartesia.ai/tts/bytes",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Cartesia-Version": os.getenv("CARTESIA_TTS_VERSION", CARTESIA_VERSION),
            "Content-Type": "application/json",
        },
        json={
            "model_id": os.getenv("CARTESIA_TTS_MODEL", "sonic-3.6"),
            "transcript": text,
            "voice": os.getenv(
                "CARTESIA_TTS_VOICE_ID", "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
            ),
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": 44100,
            },
        },
        timeout=30.0,
    )
    if not response.is_success:
        raise RuntimeError(f"Cartesia TTS {response.status_code}: {response.text[:500]}")

    with wave.open(io.BytesIO(response.content), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise RuntimeError("Cartesia TTS returned unexpected WAV audio format.")
        frames = audio.readframes(audio.getnframes())
        with sd.RawOutputStream(
            samplerate=audio.getframerate(), channels=1, dtype="int16"
        ) as output:
            output.write(frames)


def wake_pattern(phrase: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in phrase.strip().split() if p]
    if not parts:
        raise SystemExit("Wake phrase cannot be empty.")
    inner = r"[\s,]+".join(parts)
    return re.compile(rf"(?:^|[^\w])({inner})\b[,\s]*", re.IGNORECASE)


def command_after_wake(transcript: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(transcript)
    if not match:
        return None
    return transcript[match.end() :].strip(" \t,.-")


def stt_url(wake_phrase: str) -> str:
    params = [
        ("model", "ink-2"),
        ("encoding", "pcm_s16le"),
        ("sample_rate", str(SAMPLE_RATE)),
        ("cartesia_version", CARTESIA_VERSION),
        ("turn_start_threshold", str(TURN_START_THRESHOLD)),
        ("turn_eager_end_threshold", str(TURN_EAGER_END_THRESHOLD)),
        ("turn_end_threshold", str(TURN_END_THRESHOLD)),
        ("turn_end_timeout_ms", str(TURN_END_TIMEOUT_MS)),
        ("keyterm", wake_phrase),
        ("keyterm", "Argo"),
    ]
    return f"{STT_URL}?{urlencode(params)}"


class ArgoListener:
    def __init__(self, wake_phrase: str, verbose: bool) -> None:
        self.wake_re = wake_pattern(wake_phrase)
        self.wake_phrase = wake_phrase
        self.verbose = verbose
        self.awaiting_command = False
        self.armed_command = ""

    def on_event(self, event: dict) -> str | None:
        kind = event.get("type")
        transcript = (event.get("transcript") or "").strip()

        if kind == "error":
            print(f"[cartesia error] {event.get('message')}", file=sys.stderr)
            return None
        if self.verbose and kind:
            extra = f" {transcript!r}" if transcript else ""
            print(f"  ({kind}{extra})")

        if kind == "turn.start":
            return None

        if kind in {"turn.update", "turn.eager_end"}:
            self._maybe_arm(transcript)
            if self.awaiting_command and transcript:
                shown = command_after_wake(transcript, self.wake_re)
                if shown is None and self.awaiting_command:
                    shown = transcript
                if shown:
                    print(f"[partial] {shown}", flush=True)
            return None

        if kind == "turn.end":
            self._maybe_arm(transcript)
            if not self.awaiting_command:
                return
            command = command_after_wake(transcript, self.wake_re)
            if command is None:
                command = transcript
            command = (command or self.armed_command).strip()
            print("-" * 60)
            print("[argo command]")
            print(command if command else "(wake word only, no command)")
            print("-" * 60, flush=True)
            self.awaiting_command = False
            self.armed_command = ""
            print(f"[idle] say '{self.wake_phrase}, ...' to address Argo", flush=True)
            return command or ""
        return None

    def _maybe_arm(self, transcript: str) -> None:
        if self.awaiting_command or not transcript:
            return
        command = command_after_wake(transcript, self.wake_re)
        if command is None:
            return
        self.awaiting_command = True
        self.armed_command = command
        print(f"[wake] heard '{self.wake_phrase}' — waiting until the thought ends", flush=True)


async def stream_meeting(device, wake_phrase: str, verbose: bool) -> None:
    load_dotenv()
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise SystemExit("Set CARTESIA_API_KEY in the environment or a .env file.")

    audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)
    loop = asyncio.get_running_loop()
    listener = ArgoListener(wake_phrase, verbose)
    in_channels = input_channel_count(device)
    device_info = sd.query_devices(device, "input")
    chunk_index = 0
    peak_window = 0.0

    def enqueue_audio(chunk: bytes) -> None:
        if audio_q.full():
            try:
                audio_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            audio_q.put_nowait(chunk)
        except asyncio.QueueFull:
            pass

    def on_audio(indata, frames, time_info, status) -> None:  # noqa: ARG001
        nonlocal chunk_index, peak_window
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        mono = downmix_to_mono_s16(indata, in_channels)
        peak_window = max(peak_window, peak_level(mono))
        chunk_index += 1
        if chunk_index % LEVEL_EVERY_CHUNKS == 0:
            bars = min(20, int(peak_window * 20))
            meter = "#" * bars + "." * (20 - bars)
            hint = "getting audio" if peak_window > 0.01 else "SILENT — nothing is playing into this device"
            print(f"[level {meter}] {hint}", flush=True)
            peak_window = 0.0
        loop.call_soon_threadsafe(enqueue_audio, mono)

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=in_channels,
        dtype="int16",
        blocksize=FRAMES_PER_CHUNK,
        device=device,
        callback=on_audio,
    )

    url = stt_url(wake_phrase)
    headers = {
        "X-API-Key": api_key,
        "Cartesia-Version": CARTESIA_VERSION,
    }

    print(f"[stt] connecting ink-2 @ {SAMPLE_RATE} Hz pcm_s16le")
    print(
        f"[audio] {device_info['name']} (index={device if device is not None else 'default'}, "
        f"{in_channels} ch)"
    )
    if "blackhole" in str(device_info["name"]).lower():
        print(
            "[audio] BlackHole cannot hear your mouth. Play system audio "
            "(YouTube / Teams speaker) through a Multi-Output that includes BlackHole 2ch."
        )
    print(f"[idle] say '{wake_phrase}, ...' then finish the thought", flush=True)

    async with websockets.connect(
        url,
        additional_headers=headers,
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        stream.start()
        try:

            async def send_audio() -> None:
                while True:
                    chunk = await audio_q.get()
                    if chunk is None:
                        await ws.send(json.dumps({"type": "close"}))
                        return
                    await ws.send(chunk)

            async def recv_events() -> None:
                previous_acknowledgement = None
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"[stt] non-json: {raw!r}", file=sys.stderr)
                        continue
                    command = listener.on_event(event)
                    if command is not None:
                        if not command:
                            print("[claude] skipped: wake phrase heard without a command", flush=True)
                            continue
                        print("[claude] planning Jira operation...", flush=True)
                        try:
                            choices = [
                                phrase
                                for phrase in ACKNOWLEDGEMENTS
                                if phrase != previous_acknowledgement
                            ]
                            acknowledgement = random.choice(choices)
                            previous_acknowledgement = acknowledgement
                            print(f"[spoken acknowledgement] {acknowledgement}", flush=True)
                            from claude_jira import plan_from_transcript

                            context = {
                                "default_project_key": os.getenv("JIRA_PROJECT_KEY", "SCRUM"),
                                "default_issue_type": "Task",
                                "current_issue_key": os.getenv("JIRA_CURRENT_ISSUE"),
                                "known_issue_keys": [os.getenv("JIRA_CURRENT_ISSUE")] if os.getenv("JIRA_CURRENT_ISSUE") else [],
                            }
                            planning = asyncio.create_task(
                                asyncio.to_thread(plan_from_transcript, command, context=context)
                            )
                            try:
                                await asyncio.to_thread(speak_with_cartesia, acknowledgement)
                            except Exception as exc:
                                print(f"[tts error] {exc}", file=sys.stderr, flush=True)
                            plan = await planning
                            print("[claude plan]")
                            print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
                            if plan.get("needs_clarification") or plan.get("validation_errors"):
                                print("[jira] skipped: Claude needs clarification or returned an invalid plan", flush=True)
                                reply = plan.get("spoken_reply") or plan.get("clarification_question") or "I need a clearer Jira request."
                                print(f"[spoken result] {reply}", flush=True)
                                try:
                                    await asyncio.to_thread(speak_with_cartesia, reply)
                                except Exception as exc:
                                    print(f"[tts error] {exc}", file=sys.stderr, flush=True)
                                continue
                            if not plan.get("operations"):
                                print("[jira] no operations to execute", flush=True)
                                reply = plan.get("spoken_reply") or "I don't have a Jira action to run."
                                print(f"[spoken result] {reply}", flush=True)
                                try:
                                    await asyncio.to_thread(speak_with_cartesia, reply)
                                except Exception as exc:
                                    print(f"[tts error] {exc}", file=sys.stderr, flush=True)
                                continue

                            from jira_executor import execute_plan_sync

                            # Voice commands run as dry runs by default. Set
                            # JIRA_DRY_RUN=0 only when ready to make Jira changes.
                            dry_run = os.getenv("JIRA_DRY_RUN", "1").strip().lower() not in {
                                "0", "false", "no", "off"
                            }
                            execution = await asyncio.to_thread(
                                execute_plan_sync, plan, dry_run=dry_run
                            )
                            print("[jira execution]")
                            print(
                                json.dumps(execution.to_dict(), indent=2, ensure_ascii=False),
                                flush=True,
                            )
                            reply = execution.spoken_result or plan.get("spoken_reply") or "I couldn't complete that Jira request."
                            if execution.status == "dry_run":
                                reply = (
                                    "Dry run only. No Jira changes were made. "
                                    + (plan.get("spoken_reply") or reply)
                                )
                            print(f"[spoken result] {reply}", flush=True)
                            try:
                                await asyncio.to_thread(speak_with_cartesia, reply)
                            except Exception as exc:
                                print(f"[tts error] {exc}", file=sys.stderr, flush=True)
                        except Exception as exc:
                            print(f"[agent error] {exc}", file=sys.stderr, flush=True)

            sender = asyncio.create_task(send_audio())
            receiver = asyncio.create_task(recv_events())
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            stream.stop()
            stream.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream mic/loopback audio to Cartesia and print Hey Argo commands."
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Print PortAudio devices and exit.",
    )
    parser.add_argument(
        "--device",
        default="default",
        help="Input device index, substring (e.g. blackhole), or 'default'.",
    )
    parser.add_argument(
        "--wake-word",
        default=DEFAULT_WAKE,
        help="Phrase that starts an Argo command (default: 'hey argo').",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every Cartesia turn event, including non-wake speech.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_devices:
        list_devices()
        return

    device = resolve_device(args.device)
    try:
        asyncio.run(stream_meeting(device, args.wake_word.lower(), args.verbose))
    except KeyboardInterrupt:
        print("\n[exit] stopped", flush=True)


if __name__ == "__main__":
    main()
