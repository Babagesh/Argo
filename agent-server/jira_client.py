"""Authenticated Jira Cloud REST v3 client plus name -> ID resolvers.

Every Jira HTTP call Argo makes goes through `JiraClient.request`, so the
transport can later be swapped for the Forge webtrigger without touching the
executor.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

BRIEF_FIELDS = ["summary", "status", "assignee", "priority", "issuetype"]

# Spoken status words that should land on the same workflow column.
_STATUS_SYNONYMS = (
    {"todo", "backlog", "open", "new", "selectedfordevelopment"},
    {"inprogress", "doing", "started", "indevelopment", "working"},
    {"inreview", "review", "codereview", "qa", "testing"},
    {"done", "complete", "completed", "closed", "resolved", "finished", "shipped"},
)


class JiraError(Exception):
    """Non-2xx response from Jira, with its errorMessages / errors parsed out."""

    def __init__(
        self,
        status: int,
        messages: list[str] | None = None,
        field_errors: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.messages = messages or []
        self.field_errors = field_errors or {}
        parts = self.messages + [f"{k}: {v}" for k, v in self.field_errors.items()]
        super().__init__(f"Jira {status}: {'; '.join(parts) or 'request failed'}")


class ResolutionError(Exception):
    """A spoken name (status, person) didn't map to exactly one Jira ID.

    `question` is set when the user could fix it by picking a candidate.
    """

    def __init__(self, message: str, *, question: str | None = None) -> None:
        super().__init__(message)
        self.question = question


def norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _synonym_group(word: str) -> set[str]:
    for group in _STATUS_SYNONYMS:
        if word in group:
            return group
    return {word}


def brief_issue(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "status": (fields.get("status") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "priority": (fields.get("priority") or {}).get("name"),
        "issuetype": (fields.get("issuetype") or {}).get("name"),
    }


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str = "SCRUM",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.project_key = project_key.upper()
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=10.0,
            transport=transport,
        )
        self._transitions: dict[str, list[dict[str, Any]]] = {}
        self._users: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def from_env(cls) -> JiraClient:
        load_dotenv()
        missing = [
            name
            for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(f"Set {', '.join(missing)} in the environment or a .env file.")
        return cls(
            os.environ["JIRA_BASE_URL"],
            os.environ["JIRA_EMAIL"],
            os.environ["JIRA_API_TOKEN"],
            os.getenv("JIRA_PROJECT_KEY", "SCRUM"),
        )

    async def __aenter__(self) -> JiraClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        response = await self._http.request(
            method, path, params=query or None, json=body if body is not None else None
        )
        data: Any = None
        if response.content:
            try:
                data = response.json()
            except ValueError:
                data = response.text
        if response.is_success:
            return data
        if isinstance(data, dict):
            raise JiraError(
                response.status_code,
                list(data.get("errorMessages") or []),
                dict(data.get("errors") or {}),
            )
        raise JiraError(response.status_code, [str(data or response.reason_phrase)])

    # -- issues -------------------------------------------------------------

    async def get_issue_brief(self, key: str) -> dict[str, Any]:
        issue = await self.request(
            "GET",
            f"/rest/api/3/issue/{quote(key, safe='')}",
            query={"fields": ",".join(BRIEF_FIELDS)},
        )
        return brief_issue(issue)

    async def search(self, jql: str, *, max_results: int = 50) -> list[dict[str, Any]]:
        data = await self.request(
            "POST",
            "/rest/api/3/search/jql",
            body={"jql": jql, "maxResults": max_results, "fields": BRIEF_FIELDS},
        )
        return [brief_issue(issue) for issue in (data or {}).get("issues") or []]

    # -- resolvers ----------------------------------------------------------

    async def transition_id(self, key: str, name: str) -> tuple[str, str]:
        """Map a spoken status ("done", "in-progress") to (transition id, target status)."""
        transitions = self._transitions.get(key)
        if transitions is None:
            data = await self.request(
                "GET", f"/rest/api/3/issue/{quote(key, safe='')}/transitions"
            )
            transitions = (data or {}).get("transitions") or []
            self._transitions[key] = transitions

        wanted = norm(name)

        def labels(t: dict[str, Any]) -> list[str]:
            return [norm(t.get("name")), norm((t.get("to") or {}).get("name"))]

        def found(t: dict[str, Any]) -> tuple[str, str]:
            return str(t["id"]), (t.get("to") or {}).get("name") or t.get("name") or name

        for t in transitions:
            if wanted in labels(t):
                return found(t)
        group = _synonym_group(wanted)
        matches = [t for t in transitions if group & set(labels(t))]
        if not matches:
            matches = [
                t for t in transitions if any(wanted and wanted in label for label in labels(t))
            ]
        if len(matches) == 1:
            return found(matches[0])

        options = sorted({(t.get("to") or {}).get("name") or t.get("name") for t in transitions})
        raise ResolutionError(
            f"No transition {name!r} for {key}; available: {', '.join(options) or 'none'}",
            question=f"Which status should {key} move to: {', '.join(options)}?"
            if options
            else None,
        )

    def forget_transitions(self, key: str) -> None:
        """Available transitions depend on the current status; drop them after a move."""
        self._transitions.pop(key, None)

    async def assignable_users(self, query: str = "") -> list[dict[str, Any]]:
        cache_key = norm(query)
        if cache_key not in self._users:
            params: dict[str, Any] = {"project": self.project_key, "maxResults": 50}
            if query:
                params["query"] = query
            data = await self.request(
                "GET", "/rest/api/3/user/assignable/search", query=params
            )
            self._users[cache_key] = [
                u for u in data or [] if u.get("accountType", "atlassian") == "atlassian"
            ]
        return self._users[cache_key]

    async def account_id(self, display_name: str) -> tuple[str, str]:
        """Map a spoken name ("Sam") to (accountId, displayName)."""
        wanted = norm(display_name)
        users = await self.assignable_users(display_name)
        if not users:
            # The query matches name prefixes only; fall back to the full list.
            users = await self.assignable_users()

        def pick(u: dict[str, Any]) -> tuple[str, str]:
            return u["accountId"], u.get("displayName") or display_name

        # Most specific tier wins: full name, then a whole word ("Alex" -> "Alex Kim"
        # over "Alexa Chen"), then a prefix.
        tiers = [
            [u for u in users if norm(u.get("displayName")) == wanted],
            [
                u
                for u in users
                if wanted in {norm(part) for part in (u.get("displayName") or "").split()}
            ],
            [u for u in users if norm(u.get("displayName")).startswith(wanted)],
        ]
        candidates: list[str] = []
        for tier in tiers:
            if len(tier) == 1:
                return pick(tier[0])
            if tier:
                candidates = [u.get("displayName") for u in tier]
                break

        if candidates:
            raise ResolutionError(
                f"{display_name!r} matches several people: {', '.join(candidates)}",
                question=f"Which {display_name} do you mean: {', '.join(candidates)}?",
            )
        raise ResolutionError(f"No assignable user in {self.project_key} named {display_name!r}")

    # -- context for Claude -------------------------------------------------

    async def snapshot_context(self) -> dict[str, Any]:
        """Board state for `claude_jira.plan_from_transcript(context=...)`."""
        issues = await self.search(f"project = {self.project_key} ORDER BY updated DESC")
        statuses: list[str] = []
        try:
            data = await self.request(
                "GET", f"/rest/api/3/project/{self.project_key}/statuses"
            )
            for issue_type in data or []:
                for status in issue_type.get("statuses") or []:
                    if status.get("name") not in statuses:
                        statuses.append(status["name"])
        except JiraError:
            statuses = sorted({i["status"] for i in issues if i.get("status")})
        users = await self.assignable_users()
        return {
            "default_project_key": self.project_key,
            "default_issue_type": "Task",
            "known_issue_keys": [i["key"] for i in issues],
            "known_issues": issues,
            "statuses": statuses,
            "team_members": [u.get("displayName") for u in users],
        }
