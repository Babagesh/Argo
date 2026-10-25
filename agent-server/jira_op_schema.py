"""Canonical Jira operation envelope for the Atlassian REST handler.

Claude fills this JSON. The handler should execute `operations[]` in order,
resolving any `resolve` hints (assignee display name, transition name) before
calling Jira Cloud REST v3.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

OPS = (
    "create_issue",
    "get_issue",
    "search_issues",
    "update_issue",
    "delete_issue",
    "transition_issue",
    "add_comment",
    "assign_issue",
    "move_to_sprint",
    "move_to_backlog",
)

# Agile ops that act on one issue but carry its key in body.issues, not the path.
SPRINT_OPS = ("move_to_sprint", "move_to_backlog")

# Paths the Atlassian handler should call. `{issueIdOrKey}` is filled from
# path_params. Transition IDs and accountIds are instance-specific — the
# handler must resolve names when `resolve` is present.
REST_ROUTES = {
    "create_issue": ("POST", "/rest/api/3/issue"),
    "get_issue": ("GET", "/rest/api/3/issue/{issueIdOrKey}"),
    "search_issues": ("POST", "/rest/api/3/search/jql"),
    "update_issue": ("PUT", "/rest/api/3/issue/{issueIdOrKey}"),
    "delete_issue": ("DELETE", "/rest/api/3/issue/{issueIdOrKey}"),
    "transition_issue": ("POST", "/rest/api/3/issue/{issueIdOrKey}/transitions"),
    "add_comment": ("POST", "/rest/api/3/issue/{issueIdOrKey}/comment"),
    "assign_issue": ("PUT", "/rest/api/3/issue/{issueIdOrKey}/assignee"),
    "move_to_sprint": ("POST", "/rest/agile/1.0/sprint/{sprintId}/issue"),
    "move_to_backlog": ("POST", "/rest/agile/1.0/backlog/issue"),
}


def text_to_adf(text: str) -> dict[str, Any]:
    """Jira Cloud v3 description/comment body (Atlassian Document Format)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}] if text else [],
            }
        ],
    }


def empty_plan(*, transcript: str = "", utterance_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"transcript": transcript, "utterance_id": utterance_id},
        "intent": "clarify",
        "confidence": 0.0,
        "needs_clarification": True,
        "clarification_question": "I didn't catch a Jira action. What should I do?",
        "spoken_reply": "I didn't catch a Jira action. What should I do?",
        "operations": [],
    }


# ---------------------------------------------------------------------------
# CRUD templates — copy these in the Atlassian handler as request shapes.
# Placeholders in ALL_CAPS are filled by Claude / the handler.
# ---------------------------------------------------------------------------

CREATE_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "create_issue",
    "issue_key": None,
    "rest": {
        "method": "POST",
        "path": "/rest/api/3/issue",
        "path_params": {},
        "query": {},
        "body": {
            "fields": {
                "project": {"key": "PROJECT_KEY"},
                "summary": "SHORT_TITLE",
                "description": text_to_adf("DESCRIPTION"),
                "issuetype": {"name": "Task"},
                "labels": [],
                "priority": {"name": "Medium"},
            }
        },
    },
}

GET_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "get_issue",
    "issue_key": "PROJ-123",
    "rest": {
        "method": "GET",
        "path": "/rest/api/3/issue/{issueIdOrKey}",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {"fields": "summary,status,assignee,priority,description,comment"},
        "body": None,
    },
}

SEARCH_ISSUES_TEMPLATE: dict[str, Any] = {
    "op": "search_issues",
    "issue_key": None,
    "rest": {
        "method": "POST",
        "path": "/rest/api/3/search/jql",
        "path_params": {},
        "query": {},
        "body": {
            "jql": 'project = PROJECT_KEY AND text ~ "SEARCH_TERMS" ORDER BY updated DESC',
            "maxResults": 5,
            "fields": ["summary", "status", "assignee", "priority"],
        },
    },
}

UPDATE_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "update_issue",
    "issue_key": "PROJ-123",
    "rest": {
        "method": "PUT",
        "path": "/rest/api/3/issue/{issueIdOrKey}",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {},
        "body": {
            "fields": {
                "summary": "OPTIONAL_NEW_TITLE",
                "description": text_to_adf("OPTIONAL_NEW_DESCRIPTION"),
                "priority": {"name": "High"},
                "labels": ["OPTIONAL"],
            }
        },
    },
}

DELETE_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "delete_issue",
    "issue_key": "PROJ-123",
    "confirm": True,
    "rest": {
        "method": "DELETE",
        "path": "/rest/api/3/issue/{issueIdOrKey}",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {"deleteSubtasks": "false"},
        "body": None,
    },
}

TRANSITION_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "transition_issue",
    "issue_key": "PROJ-123",
    "resolve": {"transition_name": "In Progress"},
    "rest": {
        "method": "POST",
        "path": "/rest/api/3/issue/{issueIdOrKey}/transitions",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {},
        "body": {
            "transition": {"id": "RESOLVE_FROM_NAME"},
            "update": {
                "comment": [
                    {
                        "add": {
                            "body": text_to_adf("OPTIONAL_COMMENT"),
                        }
                    }
                ]
            },
        },
    },
}

ADD_COMMENT_TEMPLATE: dict[str, Any] = {
    "op": "add_comment",
    "issue_key": "PROJ-123",
    "rest": {
        "method": "POST",
        "path": "/rest/api/3/issue/{issueIdOrKey}/comment",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {},
        "body": {"body": text_to_adf("COMMENT_TEXT")},
    },
}

ASSIGN_ISSUE_TEMPLATE: dict[str, Any] = {
    "op": "assign_issue",
    "issue_key": "PROJ-123",
    "resolve": {"assignee_display_name": "Alex"},
    "rest": {
        "method": "PUT",
        "path": "/rest/api/3/issue/{issueIdOrKey}/assignee",
        "path_params": {"issueIdOrKey": "PROJ-123"},
        "query": {},
        "body": {"accountId": "RESOLVE_FROM_DISPLAY_NAME"},
    },
}

MOVE_TO_SPRINT_TEMPLATE: dict[str, Any] = {
    "op": "move_to_sprint",
    "issue_key": "PROJ-123",
    "resolve": {"sprint_name": "ACTIVE"},
    "rest": {
        "method": "POST",
        "path": "/rest/agile/1.0/sprint/{sprintId}/issue",
        "path_params": {"sprintId": "RESOLVE_FROM_NAME"},
        "query": {},
        "body": {"issues": ["PROJ-123"]},
    },
}

MOVE_TO_BACKLOG_TEMPLATE: dict[str, Any] = {
    "op": "move_to_backlog",
    "issue_key": "PROJ-123",
    "rest": {
        "method": "POST",
        "path": "/rest/agile/1.0/backlog/issue",
        "path_params": {},
        "query": {},
        "body": {"issues": ["PROJ-123"]},
    },
}

OPERATION_TEMPLATES: dict[str, dict[str, Any]] = {
    "create_issue": CREATE_ISSUE_TEMPLATE,
    "get_issue": GET_ISSUE_TEMPLATE,
    "search_issues": SEARCH_ISSUES_TEMPLATE,
    "update_issue": UPDATE_ISSUE_TEMPLATE,
    "delete_issue": DELETE_ISSUE_TEMPLATE,
    "transition_issue": TRANSITION_ISSUE_TEMPLATE,
    "add_comment": ADD_COMMENT_TEMPLATE,
    "assign_issue": ASSIGN_ISSUE_TEMPLATE,
    "move_to_sprint": MOVE_TO_SPRINT_TEMPLATE,
    "move_to_backlog": MOVE_TO_BACKLOG_TEMPLATE,
}

# Anthropic tool input_schema for forced structured output.
_OPERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["op", "rest"],
    "properties": {
        "op": {"type": "string", "enum": list(OPS)},
        "issue_key": {"type": "string"},
        "confirm": {"type": "boolean"},
        "resolve": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "transition_name": {"type": "string"},
                "assignee_display_name": {"type": "string"},
                "assignee_account_id": {"type": "string"},
                "sprint_name": {"type": "string"},
            },
        },
        "rest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["method", "path"],
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                },
                "path": {"type": "string"},
                "path_params": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "query": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "body": {"description": "Jira REST JSON body, or omit/null for GET/DELETE"},
            },
        },
    },
}

EMIT_JIRA_PLAN_TOOL: dict[str, Any] = {
    "name": "emit_jira_plan",
    "description": "Emit the Jira operation plan for a spoken Argo command.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intent",
            "confidence",
            "needs_clarification",
            "spoken_reply",
            "operations",
        ],
        "properties": {
            "intent": {
                "type": "string",
                "enum": list(OPS) + ["clarify"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "needs_clarification": {"type": "boolean"},
            "clarification_question": {"type": "string"},
            "spoken_reply": {"type": "string"},
            "operations": {"type": "array", "items": _OPERATION_SCHEMA},
        },
    },
}


def validate_plan(plan: dict[str, Any]) -> list[str]:
    """Return human-readable problems; empty means the handler can consume it."""
    errors: list[str] = []
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    intent = plan.get("intent")
    if intent not in OPS and intent != "clarify":
        errors.append(f"unknown intent {intent!r}")
    ops = plan.get("operations")
    if not isinstance(ops, list):
        errors.append("operations must be a list")
        return errors
    if plan.get("needs_clarification") and ops:
        errors.append("clarification plans must not include operations")
    if intent == "delete_issue":
        for op in ops:
            if op.get("op") == "delete_issue" and not op.get("confirm"):
                errors.append("delete_issue requires confirm=true")
    for i, op in enumerate(ops):
        name = op.get("op")
        if name not in OPS:
            errors.append(f"operations[{i}].op unknown: {name!r}")
            continue
        method, path = REST_ROUTES[name]
        rest = op.get("rest") or {}
        if rest.get("method") != method:
            errors.append(f"operations[{i}] method must be {method}")
        if rest.get("path") != path:
            errors.append(f"operations[{i}] path must be {path}")
        if "{issueIdOrKey}" in path:
            key = (rest.get("path_params") or {}).get("issueIdOrKey") or op.get(
                "issue_key"
            )
            if not key:
                errors.append(f"operations[{i}] missing issueIdOrKey")
        if name in SPRINT_OPS:
            issues = (rest.get("body") or {}).get("issues") or []
            if not op.get("issue_key") and not (isinstance(issues, list) and issues):
                errors.append(f"operations[{i}] {name} needs issue_key")
        if name == "create_issue":
            fields = ((rest.get("body") or {}).get("fields") or {})
            if not (fields.get("project") or {}).get("key"):
                errors.append(f"operations[{i}] create_issue needs fields.project.key")
            if not fields.get("summary"):
                errors.append(f"operations[{i}] create_issue needs fields.summary")
            if not (fields.get("issuetype") or {}).get("name"):
                errors.append(f"operations[{i}] create_issue needs fields.issuetype.name")
        if name == "transition_issue":
            has_id = ((rest.get("body") or {}).get("transition") or {}).get("id")
            has_name = (op.get("resolve") or {}).get("transition_name")
            if not has_id and not has_name:
                errors.append(
                    f"operations[{i}] transition needs resolve.transition_name or body.transition.id"
                )
        if name == "assign_issue":
            account = (rest.get("body") or {}).get("accountId")
            resolve = op.get("resolve") or {}
            if not account and not resolve.get("assignee_display_name") and not resolve.get(
                "assignee_account_id"
            ):
                errors.append(f"operations[{i}] assign_issue needs an assignee")
        if name == "add_comment":
            if not (rest.get("body") or {}).get("body"):
                errors.append(f"operations[{i}] add_comment needs rest.body.body")
        if name == "search_issues":
            if not (rest.get("body") or {}).get("jql"):
                errors.append(f"operations[{i}] search_issues needs rest.body.jql")
    return errors
