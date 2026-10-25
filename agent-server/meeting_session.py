"""One listener run == one meeting.

Starting the process creates a meeting row (status "live"). While it runs we save
every finished speech turn, every "Hey Argo" command, and every Jira operation a
command performed, all tagged with this meeting's id. Stopping the process
summarizes the transcript and closes the meeting (status "complete").

Writes go through a single background worker so (a) they stay in order and
(b) a slow network never blocks the live audio loop.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from argo_api import ArgoAPI
from meeting_summary import summarize_transcript

KIND_BY_OP = {
    "create_issue": "created",
    "add_comment": "commented",
    "transition_issue": "transitioned",
    "assign_issue": "assigned",
    "move_to_sprint": "moved",
    "move_to_backlog": "moved",
    "update_issue": "updated",
    "delete_issue": "deleted",
    "get_issue": "searched",
    "search_issues": "searched",
}

# ExecutionResult.status -> command outcome stored in the database.
OUTCOME_BY_STATUS = {
    "ok": "executed",
    "partial": "partial",
    "failed": "failed",
    "clarify": "clarify",
    "dry_run": "dry_run",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def action_title(result: Any, pre_state: Any, request: Any) -> str:
    """Best human label for the issue an operation touched."""
    if isinstance(request, dict):
        summary = (request.get("fields") or {}).get("summary")
        if summary:
            return str(summary)
    if isinstance(pre_state, dict) and pre_state.get("summary"):
        return str(pre_state["summary"])
    return str(getattr(result, "issue_key", "") or "")


class MeetingSession:
    def __init__(self, api: ArgoAPI | None, title: str | None = None) -> None:
        local = datetime.now()
        self.api = api
        self.id = "meeting-" + local.strftime("%Y%m%d-%H%M%S")
        self.title = title or local.strftime("Meeting %b %d, %H:%M")
        self.started_at = datetime.now(timezone.utc)
        self.transcript: list[str] = []
        self._turns = 0
        self._finished = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="argo-api")

    # -- plumbing -------------------------------------------------------------

    def _submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if self.api is not None and not self._finished:
            self._pool.submit(fn, *args, **kwargs)

    def next_utterance_id(self) -> str:
        self._turns += 1
        return f"{self.id}-u{self._turns:03d}"

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self.api is None:
            print("[meeting] recording off (set ARGO_API_BASE and ARGO_API_KEY to save meetings)", flush=True)
            return
        self._submit(self.api.start_meeting, self.id, self.title, self.started_at.isoformat())
        print(f"[meeting] started {self.id!r} ({self.title})", flush=True)

    def finish(self) -> None:
        """Flush pending writes, summarize the transcript, and close the meeting."""
        if self._finished:
            return
        self._pool.shutdown(wait=True)  # let queued writes land first
        self._finished = True
        if self.api is None:
            return
        ended_at = _utc_iso()
        seconds = int((datetime.now(timezone.utc) - self.started_at).total_seconds())
        print(f"[meeting] ending after {seconds}s, summarizing...", flush=True)
        summary = summarize_transcript("\n".join(self.transcript))
        ok = self.api.finish_meeting(
            self.id,
            ended_at,
            summary=summary["summary"],
            key_points=summary["key_points"],
            decisions=summary["decisions"],
            notes=summary["notes"],
        )
        print("[meeting] saved" if ok else "[meeting] could not save the summary", file=sys.stderr if not ok else sys.stdout, flush=True)
        self.api.close()

    # -- during the meeting ---------------------------------------------------

    def record_segment(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.transcript.append(text)
        if self.api is not None:
            self._submit(self.api.add_segment, self.id, text, _utc_iso())

    def command_heard(self, utterance_id: str, command: str) -> None:
        """Save the command the moment it is heard, before Claude or Jira run."""
        if self.api is not None:
            self._submit(
                self.api.save_command,
                self.id,
                utterance_id,
                transcript=command,
                outcome="pending",
                at=_utc_iso(),
            )

    def command_done(
        self,
        utterance_id: str,
        command: str,
        *,
        plan: dict[str, Any] | None,
        outcome: str,
        spoken_reply: str = "",
    ) -> None:
        if self.api is not None:
            plan = plan or {}
            self._submit(
                self.api.save_command,
                self.id,
                utterance_id,
                transcript=command,
                intent=str(plan.get("intent") or ""),
                confidence=float(plan.get("confidence") or 0.0),
                outcome=outcome,
                spokenReply=spoken_reply,
                plan=plan,
            )

    def action_recorder(self, utterance_id: str) -> Callable[..., None]:
        """Callback for jira_executor: save each Jira operation under this command."""

        def record(op: dict[str, Any], result: Any, pre_state: Any, request: Any, dry_run: bool) -> None:
            if self.api is None:
                return
            if dry_run:
                status = "dry_run"
            else:
                status = "done" if result.ok else "failed"
            op_name = str(op.get("op") or "")
            self._submit(
                self.api.save_action,
                self.id,
                utterance_id,
                op=op_name,
                kind=KIND_BY_OP.get(op_name, "updated"),
                issueKey=str(result.issue_key or ""),
                title=action_title(result, pre_state, request),
                detail=str(result.spoken or result.detail or "")[:500],
                status=status,
                preState=pre_state,
                request=request,
                at=_utc_iso(),
            )

        return record
