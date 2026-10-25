"""Summarize a finished meeting transcript with Claude."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from claude_jira import DEFAULT_MODEL

# Meetings longer than this are summarized from the most recent part only.
MAX_TRANSCRIPT_CHARS = 60_000

SYSTEM_PROMPT = """You summarize a meeting from a raw speech-to-text transcript.

The transcript has no speaker names and may contain recognition errors. Write for
teammates who missed the meeting. Only state what the transcript supports; do not
invent names, numbers or decisions. Call the emit_meeting_summary tool."""

SUMMARY_TOOL = {
    "name": "emit_meeting_summary",
    "description": "Return the meeting summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentence overview."},
            "key_points": {"type": "array", "items": {"type": "string"}},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string", "description": "Anything else worth remembering."},
        },
        "required": ["summary", "key_points", "decisions", "notes"],
    },
}


def empty_summary(reason: str) -> dict[str, Any]:
    return {"summary": reason, "key_points": [], "decisions": [], "notes": ""}


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def summarize_transcript(
    transcript: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return {summary, key_points, decisions, notes}. Never raises."""
    load_dotenv()
    text = (transcript or "").strip()
    if not text:
        return empty_summary("No speech was captured in this meeting.")

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return empty_summary("Summary unavailable: ANTHROPIC_API_KEY is not set.")

    try:
        from anthropic import Anthropic

        response = Anthropic(api_key=key).messages.create(
            model=model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[SUMMARY_TOOL],
            # Same reason as claude_jira: forced tool choice is not supported by every model.
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": text[-MAX_TRANSCRIPT_CHARS:]}],
        )
    except Exception as exc:
        return empty_summary(f"Summary unavailable: {exc}")

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == SUMMARY_TOOL["name"]:
            data = block.input if isinstance(block.input, dict) else {}
            return {
                "summary": str(data.get("summary", "")),
                "key_points": _str_list(data.get("key_points")),
                "decisions": _str_list(data.get("decisions")),
                "notes": str(data.get("notes", "")),
            }
    return empty_summary("Summary unavailable: the model returned no summary.")
