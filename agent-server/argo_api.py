"""Client for the Argo meetings API (SpacetimeDB HTTP handlers, see ../spacetimedb).

The listener runs live in a meeting, so saving data must never break it:
every call swallows errors (after logging) and the client is a no-op when
ARGO_API_BASE / ARGO_API_KEY are not set.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

TIMEOUT_S = 10.0


class ArgoAPI:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={"x-api-key": api_key, "content-type": "application/json"},
            timeout=TIMEOUT_S,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> ArgoAPI | None:
        """Return a client, or None when recording is not configured."""
        base = os.getenv("ARGO_API_BASE", "").strip()
        key = os.getenv("ARGO_API_KEY", "").strip()
        if not base or not key:
            return None
        return cls(base, key)

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = self._client.post(f"{self.base_url}{path}", json=body)
            if response.is_success:
                return response.json()
            print(f"[argo-api] POST {path} -> {response.status_code}: {response.text[:200]}", file=sys.stderr)
        except Exception as exc:  # network down, bad JSON, ...
            print(f"[argo-api] POST {path} failed: {exc}", file=sys.stderr)
        return None

    # -- meeting lifecycle ----------------------------------------------------

    def start_meeting(self, meeting_id: str, title: str, started_at: str) -> bool:
        body = {"id": meeting_id, "title": title, "startedAt": started_at, "status": "live"}
        return self._post("/meetings", body) is not None

    def finish_meeting(
        self,
        meeting_id: str,
        ended_at: str,
        *,
        summary: str = "",
        key_points: list[str] | None = None,
        decisions: list[str] | None = None,
        notes: str = "",
    ) -> bool:
        body = {
            "id": meeting_id,
            "status": "complete",
            "endedAt": ended_at,
            "summary": summary,
            "keyPoints": key_points or [],
            "decisions": decisions or [],
            "notes": notes,
        }
        return self._post("/meetings", body) is not None

    # -- things that happen during a meeting ----------------------------------

    def add_segment(self, meeting_id: str, text: str, at: str) -> bool:
        return self._post("/segments", {"meetingId": meeting_id, "text": text, "at": at}) is not None

    def save_command(self, meeting_id: str, utterance_id: str, **fields: Any) -> bool:
        """Create or update a command. Fields: transcript, intent, confidence, outcome, spokenReply, plan, at."""
        body = {"meetingId": meeting_id, "utteranceId": utterance_id, **fields}
        return self._post("/commands", body) is not None

    def save_action(self, meeting_id: str, utterance_id: str, **fields: Any) -> bool:
        """Record one Jira operation. Fields: op, kind, issueKey, title, detail, status, preState, request, at."""
        body = {"meetingId": meeting_id, "utteranceId": utterance_id, **fields}
        return self._post("/actions", body) is not None
