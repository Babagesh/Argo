"""Execute a Claude Jira plan (see jira_op_schema.py) against Jira Cloud REST v3.

The plan comes from meeting speech via Claude, so it is treated as untrusted:
routes come from REST_ROUTES (never from the plan), keys must belong to the
configured project, fields are allowlisted, and deletes need ALLOW_DELETE=1.

Chaining: a keyed op whose issue_key is "$LAST" (or the template's "PROJ-123")
acts on the issue created, fetched, or uniquely found by the previous op.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

from jira_client import JiraClient, JiraError, ResolutionError, brief_issue
from jira_op_schema import REST_ROUTES, SPRINT_OPS, text_to_adf, validate_plan

CHAIN_REFS = {"$LAST", "LAST", "PREVIOUS", "NEW", "PROJ-123"}
TEMPLATE_PLACEHOLDERS = {
    "SHORT_TITLE",
    "DESCRIPTION",
    "COMMENT_TEXT",
    "PROJECT_KEY",
    "SEARCH_TERMS",
    "RESOLVE_FROM_NAME",
    "RESOLVE_FROM_DISPLAY_NAME",
}
ALLOWED_FIELDS = {"summary", "description", "issuetype", "priority", "labels", "assignee", "duedate"}
# Fields a 400 retry must never drop â€” without them the request is meaningless.
REQUIRED_FIELDS = {"summary", "project", "issuetype"}
UNASSIGN_WORDS = {"none", "nobody", "noone", "unassigned", "unassign"}
DRY_RUN_NEW_KEY = "<new issue>"
DEFAULT_AUDIT_LOG = Path(__file__).with_name("audit.jsonl")
# (op, result, pre_state, request, dry_run) -> None
ActionCallback = Callable[[dict[str, Any], "OpResult", Any, Any, bool], None]


@dataclass
class OpResult:
    op: str
    issue_key: str | None = None
    ok: bool = False
    http_status: int | None = None
    detail: Any = None
    warnings: list[str] = field(default_factory=list)
    spoken: str = ""


@dataclass
class ExecutionResult:
    status: str  # ok | partial | failed | clarify | dry_run
    results: list[OpResult] = field(default_factory=list)
    spoken_result: str = ""
    clarification_question: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Clarify(Exception):
    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


class _OpFailed(Exception):
    pass


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def adf_text(node: Any) -> str:
    if isinstance(node, dict):
        return (node.get("text") or "") + "".join(adf_text(c) for c in node.get("content") or [])
    if isinstance(node, list):
        return "".join(adf_text(c) for c in node)
    return ""


def is_placeholder(value: Any) -> bool:
    """True when Claude left template filler (SHORT_TITLE, OPTIONAL_*, ...) in place."""
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return not text or text in TEMPLATE_PLACEHOLDERS or text.startswith("OPTIONAL")
    if isinstance(value, dict):
        if value.get("type") == "doc":
            return is_placeholder(adf_text(value))
        if set(value) & {"name", "key", "id", "accountId"}:
            return all(is_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return all(is_placeholder(v) for v in value)
    return False


def scope_jql(jql: str, project_key: str) -> str:
    """Pin a model-written JQL query to the configured project."""
    jql = re.sub(r"\bPROJECT_KEY\b", project_key, jql or "")
    parts = re.split(r"\border\s+by\b", jql, maxsplit=1, flags=re.IGNORECASE)
    core = parts[0].strip()
    order = parts[1].strip() if len(parts) > 1 else "updated DESC"
    scoped = f"project = {project_key}" + (f" AND ({core})" if core else "")
    return f"{scoped} ORDER BY {order}"


def _verb_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class _PlanRun:
    def __init__(
        self,
        plan: dict[str, Any],
        client: JiraClient,
        *,
        dry_run: bool,
        allow_delete: bool,
        audit_log: Path | None,
        on_action: ActionCallback | None = None,
    ) -> None:
        self.plan = plan
        self.client = client
        self.dry_run = dry_run
        self.allow_delete = allow_delete
        self.audit_log = audit_log
        self.on_action = on_action
        self.last_key: str | None = None
        self.last_search: list[dict[str, Any]] | None = None
        self.key_re = re.compile(rf"^{re.escape(client.project_key)}-\d+$")

    # -- helpers ------------------------------------------------------------

    def _key(self, op: dict[str, Any]) -> str:
        rest = op.get("rest") or {}
        issues = (rest.get("body") or {}).get("issues") if op.get("op") in SPRINT_OPS else None
        raw = (
            (rest.get("path_params") or {}).get("issueIdOrKey")
            or op.get("issue_key")
            or (issues[0] if isinstance(issues, list) and issues else "")
        )
        key = str(raw).strip().upper()
        if not key or key in CHAIN_REFS:
            if self.last_key:
                return self.last_key
            if self.last_search:
                options = ", ".join(f"{i['key']} {i['summary']}" for i in self.last_search[:4])
                raise _Clarify(f"Which ticket did you mean: {options}?")
            if self.last_search is not None:
                raise _Clarify("I couldn't find a matching ticket. Which one did you mean?")
            raise _OpFailed("no issue key given and no earlier issue in this plan")
        if not self.key_re.match(key):
            raise _OpFailed(f"{key} is not an issue in project {self.client.project_key}")
        return key

    def _path(self, op_name: str, key: str | None = None) -> tuple[str, str]:
        method, path = REST_ROUTES[op_name]
        if key is not None:
            path = path.format(issueIdOrKey=quote(key, safe=""))
        return method, path

    def _virtual(self, key: str | None) -> bool:
        return key == DRY_RUN_NEW_KEY

    async def _send(
        self,
        method: str,
        path: str,
        result: OpResult,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        mutating: bool = True,
    ) -> Any:
        if mutating and self.dry_run:
            print(f"[dry-run] {method} {path}", file=sys.stderr)
            if query:
                print(f"          query={json.dumps(query)}", file=sys.stderr)
            if body is not None:
                print(f"          body={json.dumps(body, ensure_ascii=False)}", file=sys.stderr)
            return None
        return await self.client.request(method, path, query=query, body=body)

    async def _send_fields(
        self, method: str, path: str, body: dict[str, Any], result: OpResult
    ) -> Any:
        """Send a create/update; on a 400 naming optional fields, retry once without them."""
        try:
            return await self._send(method, path, result, body=body)
        except JiraError as exc:
            fields = body.get("fields") or {}
            bad = set(exc.field_errors) & set(fields)
            if exc.status != 400 or not bad or bad & REQUIRED_FIELDS or bad != set(exc.field_errors):
                raise
            for name in bad:
                result.warnings.append(f"dropped {name}: {exc.field_errors[name]}")
            retry = {**body, "fields": {k: v for k, v in fields.items() if k not in bad}}
            if not retry["fields"]:
                raise
            return await self._send(method, path, result, body=retry)

    async def _pre_state(self, key: str) -> dict[str, Any] | None:
        if self._virtual(key):
            return None
        return await self.client.get_issue_brief(key)

    def _audit(self, op: dict[str, Any], result: OpResult, pre_state: Any, request: Any) -> None:
        if self.on_action:
            # Runs for dry runs too, so a demo without real Jira changes is still recorded.
            try:
                self.on_action(op, result, pre_state, request, self.dry_run)
            except Exception as exc:  # recording must never break execution
                print(f"[on_action] {exc}", file=sys.stderr)
        if self.dry_run or not self.audit_log:
            return
        source = self.plan.get("source") or {}
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "utterance_id": source.get("utterance_id"),
            "transcript": source.get("transcript"),
            "op": op.get("op"),
            "issue_key": result.issue_key,
            "pre_state": pre_state,
            "request": request,
            "ok": result.ok,
            "detail": result.detail,
        }
        with self.audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    async def _resolve_assignee(self, value: Any) -> tuple[str | None, str]:
        """Return (accountId or None for unassign, display name)."""
        if isinstance(value, dict):
            if value.get("accountId") and not is_placeholder(value["accountId"]):
                return value["accountId"], value.get("displayName") or value["accountId"]
            value = value.get("displayName") or value.get("name")
        name = str(value or "").strip()
        if not name or is_placeholder(name):
            raise _OpFailed("no assignee given")
        if name.lower().replace(" ", "").replace("-", "") in UNASSIGN_WORDS:
            return None, "nobody"
        return await self.client.account_id(name)

    def _clean_fields(self, fields: dict[str, Any], result: OpResult) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for name, value in (fields or {}).items():
            if name not in ALLOWED_FIELDS:
                if name != "project":
                    result.warnings.append(f"ignored field {name}")
                continue
            if name == "labels" and isinstance(value, list):
                value = [v for v in value if isinstance(v, str) and not is_placeholder(v)]
                if not value:
                    continue
            if is_placeholder(value):
                continue
            if name == "description" and isinstance(value, str):
                value = text_to_adf(value)
            cleaned[name] = value
        return cleaned

    # -- op handlers --------------------------------------------------------

    async def create_issue(self, op: dict[str, Any], result: OpResult) -> None:
        body = deepcopy((op.get("rest") or {}).get("body") or {})
        fields = self._clean_fields(body.get("fields") or {}, result)
        if "summary" not in fields:
            raise _OpFailed("create_issue needs a summary")
        fields["project"] = {"key": self.client.project_key}
        fields.setdefault("issuetype", {"name": "Task"})
        assignee = fields.pop("assignee", None) or (op.get("resolve") or {}).get(
            "assignee_display_name"
        )
        if assignee:
            account, _ = await self._resolve_assignee(assignee)
            if account:
                fields["assignee"] = {"accountId": account}
        method, path = self._path("create_issue")
        payload = {"fields": fields}
        data = await self._send_fields(method, path, payload, result)
        key = (data or {}).get("key") or DRY_RUN_NEW_KEY
        result.issue_key = key
        result.detail = {"created": key, "summary": fields["summary"]}
        result.spoken = f"created {key if not self._virtual(key) else 'a ticket'}: {fields['summary']}"
        self.last_key = key
        self._audit(op, result, None, payload)

    async def get_issue(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        self.last_key = key
        if self._virtual(key):
            result.detail = {"key": key}
            result.spoken = "the new ticket isn't created in a dry run"
            return
        query = (op.get("rest") or {}).get("query") or {}
        method, path = self._path("get_issue", key)
        data = await self._send(
            method,
            path,
            result,
            query={"fields": query.get("fields") or "summary,status,assignee,priority,issuetype"},
            mutating=False,
        )
        issue = brief_issue(data or {})
        result.detail = issue
        who = f", assigned to {issue['assignee']}" if issue.get("assignee") else ", unassigned"
        result.spoken = f"{key} \"{issue.get('summary')}\" is {issue.get('status')}{who}"

    async def search_issues(self, op: dict[str, Any], result: OpResult) -> None:
        body = (op.get("rest") or {}).get("body") or {}
        jql = scope_jql(str(body.get("jql") or ""), self.client.project_key)
        try:
            limit = max(1, min(int(body.get("maxResults") or 5), 10))
        except (TypeError, ValueError):
            limit = 5
        method, path = self._path("search_issues")
        data = await self._send(
            method,
            path,
            result,
            body={
                "jql": jql,
                "maxResults": limit,
                "fields": ["summary", "status", "assignee", "priority", "issuetype"],
            },
            mutating=False,
        )
        hits = [brief_issue(i) for i in (data or {}).get("issues") or []]
        result.detail = {"jql": jql, "issues": hits}
        self.last_search = hits
        self.last_key = hits[0]["key"] if len(hits) == 1 else None
        if hits:
            result.issue_key = self.last_key
            listed = ", ".join(f"{h['key']} {h['summary']}" for h in hits[:3])
            result.spoken = f"found {len(hits)} ticket{'s' if len(hits) != 1 else ''}: {listed}"
        else:
            result.spoken = "found no matching tickets"

    async def update_issue(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        body = deepcopy((op.get("rest") or {}).get("body") or {})
        fields = self._clean_fields(body.get("fields") or {}, result)
        if "assignee" in fields:
            account, _ = await self._resolve_assignee(fields["assignee"])
            fields["assignee"] = {"accountId": account}
        if not fields:
            raise _OpFailed(f"nothing to update on {key}")
        pre = await self._pre_state(key)
        method, path = self._path("update_issue", key)
        payload = {"fields": fields}
        await self._send_fields(method, path, payload, result)
        changed = sorted(fields)
        result.detail = {"updated": changed, "pre_state": pre}
        result.spoken = f"updated {_verb_list(changed)} on {key}"
        self.last_key = key
        self._audit(op, result, pre, payload)

    async def delete_issue(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        if not op.get("confirm"):
            raise _OpFailed("delete_issue requires confirm=true")
        if not self.allow_delete:
            raise _OpFailed(f"deleting is disabled (set ALLOW_DELETE=1); {key} was left alone")
        pre = None
        if not self._virtual(key):
            pre = await self.client.request(
                "GET", self._path("get_issue", key)[1], query={"fields": "*all"}
            )
        query = (op.get("rest") or {}).get("query") or {}
        subtasks = "true" if str(query.get("deleteSubtasks")).lower() == "true" else "false"
        method, path = self._path("delete_issue", key)
        await self._send(method, path, result, query={"deleteSubtasks": subtasks})
        result.detail = {"deleted": key}
        result.spoken = f"deleted {key}"
        self.last_key = None
        self._audit(op, result, pre, {"deleteSubtasks": subtasks})

    async def transition_issue(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        body = (op.get("rest") or {}).get("body") or {}
        name = (op.get("resolve") or {}).get("transition_name")
        given_id = (body.get("transition") or {}).get("id")
        comments = [
            (item.get("add") or {}).get("body")
            for item in ((body.get("update") or {}).get("comment") or [])
            if isinstance(item, dict)
        ]
        comments = [c for c in comments if not is_placeholder(c)]

        if self._virtual(key):
            transition_id, target = "?", name or str(given_id)
        elif name and not is_placeholder(name):
            transition_id, target = await self.client.transition_id(key, name)
        elif given_id and not is_placeholder(given_id):
            transition_id, target = str(given_id), f"transition {given_id}"
        else:
            raise _OpFailed("transition_issue needs a target status")

        pre = await self._pre_state(key)
        method, path = self._path("transition_issue", key)
        payload = {"transition": {"id": transition_id}}
        await self._send(method, path, result, body=payload)
        self.client.forget_transitions(key)
        # Transition screens often reject inline comments, so post them separately.
        for comment in comments:
            _, comment_path = self._path("add_comment", key)
            await self._send("POST", comment_path, result, body={"body": comment})
        result.detail = {"to": target, "from": (pre or {}).get("status"), "comments": len(comments)}
        result.spoken = f"moved {key} to {target}"
        self.last_key = key
        self._audit(op, result, pre, payload)

    async def add_comment(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        comment = ((op.get("rest") or {}).get("body") or {}).get("body")
        if isinstance(comment, str):
            comment = text_to_adf(comment)
        if is_placeholder(comment):
            raise _OpFailed("add_comment needs comment text")
        method, path = self._path("add_comment", key)
        payload = {"body": comment}
        data = await self._send(method, path, result, body=payload)
        result.detail = {"comment_id": (data or {}).get("id"), "text": adf_text(comment)}
        result.spoken = f"commented on {key}"
        self.last_key = key
        self._audit(op, result, None, payload)

    async def assign_issue(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        resolve = op.get("resolve") or {}
        body = (op.get("rest") or {}).get("body") or {}
        if resolve.get("assignee_account_id"):
            account, who = resolve["assignee_account_id"], resolve.get(
                "assignee_display_name"
            ) or resolve["assignee_account_id"]
        elif body.get("accountId") and not is_placeholder(body["accountId"]):
            account, who = body["accountId"], resolve.get("assignee_display_name") or body["accountId"]
        else:
            account, who = await self._resolve_assignee(resolve.get("assignee_display_name"))
        pre = await self._pre_state(key)
        method, path = self._path("assign_issue", key)
        payload = {"accountId": account}
        await self._send(method, path, result, body=payload)
        result.detail = {"assignee": who, "from": (pre or {}).get("assignee")}
        result.spoken = f"unassigned {key}" if account is None else f"assigned {key} to {who}"
        self.last_key = key
        self._audit(op, result, pre, payload)

    async def move_to_sprint(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        name = (op.get("resolve") or {}).get("sprint_name")
        if is_placeholder(name) or str(name).strip().upper() == "ACTIVE":
            name = None
        # The sprint id always comes from the board, never from the plan's path_params.
        sprint_id, sprint = await self.client.sprint_id(name)
        pre = await self._pre_state(key)
        method, path = REST_ROUTES["move_to_sprint"]
        path = path.format(sprintId=sprint_id)
        payload = {"issues": [key]}
        if not self._virtual(key):
            await self._send(method, path, result, body=payload)
        result.detail = {"sprint": sprint, "sprint_id": sprint_id}
        result.spoken = f"moved {key} into {sprint}"
        self.last_key = key
        self._audit(op, result, pre, payload)

    async def move_to_backlog(self, op: dict[str, Any], result: OpResult) -> None:
        key = self._key(op)
        result.issue_key = key
        pre = await self._pre_state(key)
        method, path = self._path("move_to_backlog")
        payload = {"issues": [key]}
        if not self._virtual(key):
            await self._send(method, path, result, body=payload)
        result.detail = {"backlog": key}
        result.spoken = f"moved {key} to the backlog"
        self.last_key = key
        self._audit(op, result, pre, payload)

    # -- driver -------------------------------------------------------------

    async def run(self) -> ExecutionResult:
        errors = validate_plan(self.plan)
        if errors:
            return ExecutionResult(
                status="failed",
                errors=errors,
                spoken_result="That Jira plan was malformed, so I didn't change anything.",
            )
        if self.plan.get("needs_clarification"):
            question = self.plan.get("clarification_question") or self.plan.get("spoken_reply")
            return ExecutionResult(
                status="clarify", clarification_question=question, spoken_result=question or ""
            )

        operations = self.plan.get("operations") or []
        results: list[OpResult] = []
        failure: str | None = None
        question: str | None = None
        for op in operations:
            name = op.get("op")
            result = OpResult(op=name)
            results.append(result)
            if failure or question:
                result.detail = "skipped"
                continue
            # Handlers call _audit() as their last step, so success has to be set before
            # they run (the except branches below reset it). Setting it afterwards made
            # every successful op look failed to the audit log and the on_action hook.
            result.ok = True
            try:
                await getattr(self, name)(op, result)
            except (_Clarify, ResolutionError) as exc:
                result.ok = False
                question = getattr(exc, "question", None)
                if question:
                    result.detail = question
                else:
                    failure = result.detail = str(exc)
            except (_OpFailed, JiraError) as exc:
                result.ok = False
                failure = result.detail = str(exc)
                if isinstance(exc, JiraError):
                    result.http_status = exc.status
            if not result.ok:
                self._audit(op, result, None, None)

        done = [r.spoken for r in results if r.ok and r.spoken]
        spoken = _verb_list(done)
        if failure:
            spoken = (f"{spoken}, but " if spoken else "") + f"I couldn't finish: {failure}"
        elif question:
            spoken = (f"{spoken}. " if spoken else "") + question
        spoken = spoken[:1].upper() + spoken[1:] + ("" if spoken.endswith(("?", ".")) else ".")

        if question:
            status = "clarify"
        elif failure:
            status = "partial" if any(r.ok for r in results) else "failed"
        else:
            status = "dry_run" if self.dry_run else "ok"
        return ExecutionResult(
            status=status,
            results=results,
            spoken_result=spoken if (done or failure or question) else "",
            clarification_question=question,
        )


async def execute_plan(
    plan: dict[str, Any],
    *,
    client: JiraClient,
    dry_run: bool | None = None,
    allow_delete: bool | None = None,
    audit_log: Path | None = DEFAULT_AUDIT_LOG,
    on_action: ActionCallback | None = None,
) -> ExecutionResult:
    """Run `plan["operations"]` in order; stop at the first failure.

    `on_action(op, result, pre_state, request, dry_run)` is called once per
    mutating operation (see meeting_session.MeetingSession.action_recorder).
    """
    load_dotenv()
    run = _PlanRun(
        plan,
        client,
        dry_run=_env_flag("JIRA_DRY_RUN") if dry_run is None else dry_run,
        allow_delete=_env_flag("ALLOW_DELETE") if allow_delete is None else allow_delete,
        audit_log=audit_log,
        on_action=on_action,
    )
    return await run.run()


def execute_plan_sync(plan: dict[str, Any], **kwargs: Any) -> ExecutionResult:
    """Blocking wrapper that builds a JiraClient from the environment."""

    async def _go() -> ExecutionResult:
        async with JiraClient.from_env() as client:
            return await execute_plan(plan, client=client, **kwargs)

    return asyncio.run(_go())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute a Jira operation plan JSON (from claude_jira.py) against Jira."
    )
    parser.add_argument("plan", nargs="?", default="-", help="Plan JSON file, or - for stdin.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Print mutating requests instead of sending them (reads still run).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.plan == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.plan).read_text(encoding="utf-8")
    result = execute_plan_sync(json.loads(raw), dry_run=args.dry_run)
    json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    if result.spoken_result:
        print(f"[argo] {result.spoken_result}", file=sys.stderr)
    raise SystemExit({"ok": 0, "dry_run": 0, "clarify": 2}.get(result.status, 1))


if __name__ == "__main__":
    main()
