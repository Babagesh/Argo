"""Executor tests against a fake Jira (httpx.MockTransport). Run: pytest agent-server/tests"""

from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jira_client import JiraClient  # noqa: E402
from jira_executor import execute_plan, scope_jql  # noqa: E402
from jira_op_schema import OPERATION_TEMPLATES, SCHEMA_VERSION, text_to_adf  # noqa: E402

TRANSITIONS = [
    {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
    {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
    {"id": "31", "name": "Done", "to": {"name": "Done"}},
]
USERS = [
    {"accountId": "acc-sam", "displayName": "Sam Rivera", "accountType": "atlassian"},
    {"accountId": "acc-alex", "displayName": "Alex Kim", "accountType": "atlassian"},
    {"accountId": "acc-alexa", "displayName": "Alexa Chen", "accountType": "atlassian"},
]


def issue(key: str, summary: str = "Thing", status: str = "To Do") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "assignee": None,
            "priority": {"name": "Medium"},
            "issuetype": {"name": "Task"},
        },
    }


class FakeJira:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, object]] = []
        self.search_hits = [issue("SCRUM-3", "Payment Webhook Timeout")]
        self.reject_fields: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path, method = request.url.path, request.method
        self.calls.append((method, path, dict(request.url.params), body))

        if method == "POST" and path == "/rest/api/3/issue":
            bad = {k: v for k, v in self.reject_fields.items() if k in body["fields"]}
            if bad:
                return httpx.Response(400, json={"errorMessages": [], "errors": bad})
            return httpx.Response(201, json={"id": "10007", "key": "SCRUM-7"})
        if method == "POST" and path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": self.search_hits})
        if method == "GET" and path == "/rest/api/3/user/assignable/search":
            q = request.url.params.get("query", "").lower()
            return httpx.Response(
                200, json=[u for u in USERS if u["displayName"].lower().startswith(q)]
            )
        if path.endswith("/transitions"):
            if method == "GET":
                return httpx.Response(200, json={"transitions": TRANSITIONS})
            return httpx.Response(204)
        if path.endswith("/comment"):
            return httpx.Response(201, json={"id": "c1"})
        if path.endswith("/assignee") or method in {"PUT", "DELETE"}:
            return httpx.Response(204)
        if method == "GET" and path.startswith("/rest/api/3/issue/"):
            key = path.rsplit("/", 1)[-1]
            if key == "SCRUM-404":
                return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
            return httpx.Response(200, json=issue(key))
        return httpx.Response(500, json={"errorMessages": [f"unhandled {method} {path}"]})

    def mutations(self) -> list[tuple[str, str, dict, object]]:
        return [c for c in self.calls if c[0] != "GET" and not c[1].endswith("/search/jql")]


@pytest.fixture
def jira():
    return FakeJira()


def run(plan: dict, jira: FakeJira, **kwargs):
    async def go():
        async with JiraClient(
            "https://example.atlassian.net", "me@x.com", "tok", "SCRUM",
            transport=httpx.MockTransport(jira),
        ) as client:
            return await execute_plan(plan, client=client, audit_log=None, **kwargs)

    kwargs.setdefault("dry_run", False)
    kwargs.setdefault("allow_delete", False)
    return asyncio.run(go())


def op(name: str, key: str | None = None, **changes) -> dict:
    o = deepcopy(OPERATION_TEMPLATES[name])
    if key:
        o["issue_key"] = key
        o["rest"]["path_params"]["issueIdOrKey"] = key
    for path, value in changes.items():
        target = o
        parts = path.split("__")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return o


def plan(*ops: dict, intent: str | None = None, **extra) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"transcript": "test", "utterance_id": "u1"},
        "intent": intent or (ops[0]["op"] if ops else "clarify"),
        "confidence": 0.9,
        "needs_clarification": False,
        "clarification_question": None,
        "spoken_reply": "ok",
        "operations": list(ops),
        **extra,
    }


def test_create_forces_project_and_strips_placeholders(jira):
    create = op("create_issue")
    create["rest"]["body"]["fields"].update(
        project={"key": "OTHER"}, summary="Redis connection leak", customfield_1="x"
    )
    result = run(plan(create), jira)
    assert result.status == "ok", result
    method, path, _, body = jira.mutations()[0]
    fields = body["fields"]
    assert (method, path) == ("POST", "/rest/api/3/issue")
    assert fields["project"] == {"key": "SCRUM"}
    assert "description" not in fields  # still the DESCRIPTION placeholder
    assert "labels" not in fields and "customfield_1" not in fields
    assert result.results[0].issue_key == "SCRUM-7"
    assert "SCRUM-7" in result.spoken_result


def test_create_requires_real_summary(jira):
    result = run(plan(op("create_issue")), jira)  # summary is SHORT_TITLE
    assert result.status == "failed"
    assert not jira.mutations()


def test_transition_resolves_spoken_status_and_splits_comment(jira):
    t = op("transition_issue", "scrum-1", resolve={"transition_name": "finished"})
    t["rest"]["body"]["update"]["comment"][0]["add"]["body"] = text_to_adf("Shipped it")
    result = run(plan(t), jira)
    assert result.status == "ok", result
    muts = jira.mutations()
    assert muts[0][1:] == ("/rest/api/3/issue/SCRUM-1/transitions", {}, {"transition": {"id": "31"}})
    assert muts[1][1] == "/rest/api/3/issue/SCRUM-1/comment"
    assert result.spoken_result == "Moved SCRUM-1 to Done."


def test_transition_placeholder_comment_is_dropped(jira):
    t = op("transition_issue", "SCRUM-2", resolve={"transition_name": "in-progress"})
    result = run(plan(t), jira)
    assert result.status == "ok"
    assert [m[1] for m in jira.mutations()] == ["/rest/api/3/issue/SCRUM-2/transitions"]
    assert jira.mutations()[0][3] == {"transition": {"id": "21"}}


def test_assign_by_first_name_and_ambiguity(jira):
    result = run(plan(op("assign_issue", "SCRUM-1", resolve={"assignee_display_name": "sam"})), jira)
    assert result.status == "ok"
    assert jira.mutations()[0][3] == {"accountId": "acc-sam"}

    jira2 = FakeJira()
    result = run(plan(op("assign_issue", "SCRUM-1", resolve={"assignee_display_name": "Alex"})), jira2)
    # "Alex Kim" is the only exact first-name hit even though "Alexa" shares the prefix.
    assert result.status == "ok"
    assert jira2.mutations()[0][3] == {"accountId": "acc-alex"}


def test_assign_ambiguous_returns_clarify(jira):
    result = run(plan(op("assign_issue", "SCRUM-1", resolve={"assignee_display_name": "Al"})), jira)
    assert result.status == "clarify"
    assert "Alex Kim" in result.clarification_question
    assert not jira.mutations()


def test_unassign(jira):
    result = run(plan(op("assign_issue", "SCRUM-1", resolve={"assignee_display_name": "nobody"})), jira)
    assert result.status == "ok"
    assert jira.mutations()[0][3] == {"accountId": None}


def test_foreign_project_key_rejected(jira):
    result = run(plan(op("add_comment", "HR-9", rest__body={"body": text_to_adf("hi")})), jira)
    assert result.status == "failed"
    assert "not an issue in project SCRUM" in result.results[0].detail
    assert not jira.mutations()


def test_delete_blocked_without_allow_delete(jira):
    d = op("delete_issue", "SCRUM-5")
    result = run(plan(d), jira)
    assert result.status == "failed"
    assert not jira.mutations()

    result = run(plan(d), jira, allow_delete=True)
    assert result.status == "ok"
    assert jira.mutations()[-1][:3] == ("DELETE", "/rest/api/3/issue/SCRUM-5", {"deleteSubtasks": "false"})


def test_create_then_assign_chains_new_key(jira):
    create = op("create_issue")
    create["rest"]["body"]["fields"]["summary"] = "Redis connection leak"
    assign = op("assign_issue", "$LAST", resolve={"assignee_display_name": "Sam Rivera"})
    result = run(plan(create, assign), jira)
    assert result.status == "ok", result
    assert jira.mutations()[-1][1] == "/rest/api/3/issue/SCRUM-7/assignee"
    assert result.spoken_result.startswith("Created SCRUM-7") and "assigned SCRUM-7 to Sam Rivera" in result.spoken_result


def test_search_then_transition_uses_single_hit(jira):
    search = op("search_issues", rest__body={"jql": 'text ~ "payment webhook" ORDER BY created DESC'})
    t = op("transition_issue", "$LAST", resolve={"transition_name": "In Progress"})
    result = run(plan(search, t), jira)
    assert result.status == "ok", result
    search_call = next(c for c in jira.calls if c[1].endswith("/search/jql"))
    assert search_call[3]["jql"] == 'project = SCRUM AND (text ~ "payment webhook") ORDER BY created DESC'
    assert jira.mutations()[0][1] == "/rest/api/3/issue/SCRUM-3/transitions"


def test_ambiguous_search_returns_clarify(jira):
    jira.search_hits = [issue("SCRUM-2", "Redis Rate Limiter"), issue("SCRUM-5", "Redis Cluster")]
    search = op("search_issues", rest__body={"jql": 'text ~ "redis"'})
    t = op("transition_issue", "$LAST", resolve={"transition_name": "Done"})
    result = run(plan(search, t), jira)
    assert result.status == "clarify"
    assert "SCRUM-2" in result.clarification_question and "SCRUM-5" in result.clarification_question
    assert not jira.mutations()


def test_rejected_priority_is_retried_without_it(jira):
    jira.reject_fields = {"priority": "Field 'priority' cannot be set."}
    create = op("create_issue")
    create["rest"]["body"]["fields"]["summary"] = "Webhook retries"
    result = run(plan(create), jira)
    assert result.status == "ok", result
    creates = [m for m in jira.mutations() if m[1] == "/rest/api/3/issue"]
    assert len(creates) == 2 and "priority" not in creates[1][3]["fields"]
    assert result.results[0].warnings


def test_failure_skips_remaining_ops(jira):
    bad = op("update_issue", "SCRUM-404")
    bad["rest"]["body"]["fields"] = {"priority": {"name": "High"}}
    comment = op("add_comment", "SCRUM-1", rest__body={"body": text_to_adf("hi")})
    result = run(plan(bad, comment), jira)
    assert result.status == "failed"
    assert result.results[1].detail == "skipped"
    assert not jira.mutations()


def test_update_only_sends_real_fields(jira):
    u = op("update_issue", "SCRUM-4")
    u["rest"]["body"]["fields"] = {
        "summary": "OPTIONAL_NEW_TITLE",
        "description": text_to_adf("OPTIONAL_NEW_DESCRIPTION"),
        "priority": {"name": "High"},
        "labels": ["OPTIONAL"],
    }
    result = run(plan(u), jira)
    assert result.status == "ok"
    assert jira.mutations()[0][3] == {"fields": {"priority": {"name": "High"}}}


def test_dry_run_makes_no_mutations(jira):
    create = op("create_issue")
    create["rest"]["body"]["fields"]["summary"] = "Dry"
    t = op("transition_issue", "SCRUM-1", resolve={"transition_name": "Done"})
    result = run(plan(create, t), jira, dry_run=True)
    assert result.status == "dry_run"
    assert not jira.mutations()


def test_clarification_plan_does_nothing(jira):
    p = plan(intent="clarify", needs_clarification=True, clarification_question="Which ticket?")
    result = run(p, jira)
    assert result.status == "clarify" and result.clarification_question == "Which ticket?"
    assert not jira.calls


def test_scope_jql_fills_template_project():
    assert scope_jql('project = PROJECT_KEY AND text ~ "x"', "SCRUM") == (
        'project = SCRUM AND (project = SCRUM AND text ~ "x") ORDER BY updated DESC'
    )
