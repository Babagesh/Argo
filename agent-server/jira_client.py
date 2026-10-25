"""Authenticated Jira Cloud REST v3 client plus name -> ID resolvers.

Every Jira HTTP call Argo makes goes through `JiraClient.request`, so the
transport can later be swapped for the Forge webtrigger without touching the
executor.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
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

# Spoken sprint references that mean "whichever sprint is running" / "the one after".
_ACTIVE_SPRINT_WORDS = {"", "active", "current", "this", "thissprint", "currentsprint", "activesprint"}
_NEXT_SPRINT_WORDS = {"next", "nextsprint", "upcoming", "upcomingsprint"}

# Fuzzy assignee matching: minimum score to suggest someone, and how close a
# runner-up must be to be offered too. Equal sound-alike keys score PHONETIC_SCORE.
FUZZY_MIN = 0.6
FUZZY_TIE = 0.1
PHONETIC_SCORE = 0.9
# Spellings that sound the same, applied in order before vowels are dropped.
_PHONETIC_RULES = (
    (r"^kn", "n"),
    (r"^wr", "r"),
    (r"ph", "f"),
    (r"ck", "k"),
    (r"c(?=[eiy])", "s"),
    (r"[cq]", "k"),
    (r"x", "ks"),
    (r"z", "s"),
    (r"v", "f"),
    (r"dg", "j"),
    (r"gh", "g"),
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


def _phonetic(text: str) -> str:
    """Rough sound-alike key per word: "Jon"/"John" -> "jn", "Katelyn"/"Caitlin" -> "ktln"."""
    keys = []
    for word in (text or "").lower().split():
        w = re.sub(r"[^a-z]", "", word)
        for pattern, repl in _PHONETIC_RULES:
            w = re.sub(pattern, repl, w)
        if not w:
            continue
        # Keep the first letter, drop later vowels and silent-ish letters, collapse repeats.
        key = w[0] + re.sub(r"[aeiouyhw]", "", w[1:])
        keys.append(re.sub(r"(.)\1+", r"\1", key))
    return " ".join(keys)


def _name_score(spoken: str, display_name: str) -> float:
    """0..1 similarity of a spoken name to a Jira display name or any part of it."""
    wanted = norm(spoken)
    sound = _phonetic(spoken)
    best = 0.0
    for part in [display_name, *display_name.split()]:
        best = max(best, SequenceMatcher(None, wanted, norm(part)).ratio())
        if sound and sound == _phonetic(part):
            best = max(best, PHONETIC_SCORE)
    return best


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
        board_id: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.project_key = project_key.upper()
        self._board_id = board_id
        self._sprints: list[dict[str, Any]] | None = None
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
            board_id=int(os.environ["JIRA_BOARD_ID"]) if os.getenv("JIRA_BOARD_ID") else None,
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

        # Speech-to-text spells names its own way ("Jon", "Katelyn"). Find people whose
        # name looks or sounds close, but ask before acting on a guess.
        scored = sorted(
            (
                (_name_score(display_name, u.get("displayName") or ""), u.get("displayName"))
                for u in await self.assignable_users()
            ),
            key=lambda scored_user: -scored_user[0],
        )
        close = [name for score, name in scored if score >= FUZZY_MIN]
        if close:
            top = scored[0][0]
            close = [name for score, name in scored if score >= top - FUZZY_TIE][:3]
            question = (
                f"Was that a ticket for {close[0]}?"
                if len(close) == 1
                else f"Did you mean {', '.join(close[:-1])} or {close[-1]}?"
            )
            raise ResolutionError(
                f"No exact match for {display_name!r}; closest: {', '.join(close)}",
                question=question,
            )
        raise ResolutionError(
            f"No assignable user in {self.project_key} named {display_name!r}",
            question=f"I couldn't find {display_name} in {self.project_key}. Who should I assign it to?",
        )

    async def board_id(self) -> int:
        """The project's scrum board (JIRA_BOARD_ID overrides the lookup)."""
        if self._board_id is None:
            data = await self.request(
                "GET", "/rest/agile/1.0/board", query={"projectKeyOrId": self.project_key}
            )
            boards = (data or {}).get("values") or []
            scrum = [b for b in boards if b.get("type") == "scrum"]
            if not (scrum or boards):
                raise ResolutionError(f"No board for project {self.project_key}")
            self._board_id = int((scrum or boards)[0]["id"])
        return self._board_id

    async def open_sprints(self) -> list[dict[str, Any]]:
        if self._sprints is None:
            board = await self.board_id()
            data = await self.request(
                "GET",
                f"/rest/agile/1.0/board/{board}/sprint",
                query={"state": "active,future"},
            )
            self._sprints = (data or {}).get("values") or []
        return self._sprints

    async def sprint_id(self, name: str | None) -> tuple[int, str]:
        """Map a spoken sprint ("current", "next", "sprint 4") to (sprint id, sprint name)."""
        sprints = await self.open_sprints()
        wanted = norm(name)

        def found(s: dict[str, Any]) -> tuple[int, str]:
            return int(s["id"]), s.get("name") or f"sprint {s['id']}"

        if wanted in _ACTIVE_SPRINT_WORDS:
            matches = [s for s in sprints if s.get("state") == "active"]
        elif wanted in _NEXT_SPRINT_WORDS:
            future = sorted(
                (s for s in sprints if s.get("state") == "future"),
                key=lambda s: (s.get("startDate") or "~", s["id"]),
            )
            matches = future[:1]
        else:
            matches = [s for s in sprints if norm(s.get("name")) == wanted] or [
                s for s in sprints if wanted in norm(s.get("name"))
            ]
        if len(matches) == 1:
            return found(matches[0])

        options = [s.get("name") for s in (matches or sprints)]
        raise ResolutionError(
            f"No single open sprint matches {name or 'active'!r}; open: {', '.join(options) or 'none'}",
            question=f"Which sprint do you mean: {', '.join(options)}?" if options else None,
        )

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
