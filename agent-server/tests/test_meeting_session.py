"""Meeting recording: ArgoAPI client + MeetingSession (no network, no Claude)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import meeting_session  # noqa: E402
from argo_api import ArgoAPI  # noqa: E402
from meeting_session import MeetingSession, action_title  # noqa: E402


class Recorder:
    """httpx transport that records requests and replies with a canned status."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.calls: list[tuple[str, str, dict, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body, request.headers.get("x-api-key", "")))
        return httpx.Response(self.status, json={"ok": True})


def make_api(recorder: Recorder) -> ArgoAPI:
    return ArgoAPI("https://db.example/v1/database/argo/route", "secret", transport=httpx.MockTransport(recorder))


def fake_result(**kw):
    base = dict(op="create_issue", issue_key="SCRUM-9", ok=True, detail=None, spoken="created SCRUM-9")
    return SimpleNamespace(**{**base, **kw})


def test_from_env_is_none_without_config(monkeypatch):
    monkeypatch.delenv("ARGO_API_BASE", raising=False)
    monkeypatch.delenv("ARGO_API_KEY", raising=False)
    assert ArgoAPI.from_env() is None


def test_post_sends_key_and_swallows_errors():
    rec = Recorder(status=500)
    api = make_api(rec)
    assert api.add_segment("m1", "hello", "2026-10-24T00:00:00Z") is False  # logged, not raised
    assert rec.calls[0][1].endswith("/route/segments")
    assert rec.calls[0][3] == "secret"

    def boom(request):
        raise httpx.ConnectError("down")

    dead = ArgoAPI("https://db.example", "k", transport=httpx.MockTransport(boom))
    assert dead.start_meeting("m1", "t", "2026-10-24T00:00:00Z") is False


def test_session_start_creates_live_meeting():
    rec = Recorder()
    session = MeetingSession(make_api(rec), title="Sprint planning")
    session.start()
    session.finish()  # flush the worker; summary is stubbed below in other tests
    first = rec.calls[0]
    assert first[1].endswith("/meetings")
    assert first[2]["id"] == session.id and first[2]["title"] == "Sprint planning"
    assert first[2]["status"] == "live"


def test_commands_and_actions_are_tied_to_the_meeting(monkeypatch):
    monkeypatch.setattr(
        meeting_session,
        "summarize_transcript",
        lambda text: {"summary": "S", "key_points": ["k"], "decisions": ["d"], "notes": "n"},
    )
    rec = Recorder()
    session = MeetingSession(make_api(rec))
    session.start()
    session.record_segment("we should fix login")
    uid = session.next_utterance_id()
    session.command_heard(uid, "create a ticket to fix login")
    session.action_recorder(uid)(
        {"op": "create_issue"},
        fake_result(),
        None,
        {"fields": {"summary": "Fix login"}},
        False,
    )
    session.command_done(uid, "create a ticket to fix login", plan={"intent": "create", "confidence": 0.9}, outcome="executed", spoken_reply="Done")
    session.finish()

    by_path = {}
    for _, path, body, _ in rec.calls:
        by_path.setdefault(path.rsplit("/", 1)[-1], []).append(body)

    assert by_path["segments"][0]["meetingId"] == session.id
    assert [c["outcome"] for c in by_path["commands"]] == ["pending", "executed"]
    action = by_path["actions"][0]
    assert action["meetingId"] == session.id and action["utteranceId"] == uid
    assert action["kind"] == "created" and action["title"] == "Fix login" and action["status"] == "done"
    final = by_path["meetings"][-1]
    assert final["status"] == "complete" and final["summary"] == "S" and final["endedAt"]


def test_dry_run_and_failed_actions_get_their_own_status():
    rec = Recorder()
    session = MeetingSession(make_api(rec))
    record = session.action_recorder("u1")
    record({"op": "add_comment"}, fake_result(op="add_comment"), None, None, True)
    record({"op": "transition_issue"}, fake_result(ok=False, detail="no such status"), {"summary": "Old"}, None, False)
    session._pool.shutdown(wait=True)
    statuses = [c[2]["status"] for c in rec.calls]
    assert statuses == ["dry_run", "failed"]
    assert rec.calls[1][2]["kind"] == "transitioned" and rec.calls[1][2]["title"] == "Old"


def test_session_without_api_is_a_quiet_no_op():
    session = MeetingSession(None)
    session.start()
    session.record_segment("hello")
    uid = session.next_utterance_id()
    session.command_heard(uid, "x")
    session.command_done(uid, "x", plan=None, outcome="error")
    session.action_recorder(uid)({"op": "create_issue"}, fake_result(), None, None, False)
    session.finish()
    assert session.transcript == ["hello"]


def test_action_title_prefers_request_then_pre_state_then_key():
    result = fake_result()
    assert action_title(result, {"summary": "Old"}, {"fields": {"summary": "New"}}) == "New"
    assert action_title(result, {"summary": "Old"}, None) == "Old"
    assert action_title(result, None, None) == "SCRUM-9"
