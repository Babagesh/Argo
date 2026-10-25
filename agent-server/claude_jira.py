"""Translate Cartesia Hey-Argo transcripts into a Jira operation plan via Claude."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

from jira_op_schema import (
    EMIT_JIRA_PLAN_TOOL,
    OPERATION_TEMPLATES,
    SCHEMA_VERSION,
    empty_plan,
    text_to_adf,
    validate_plan,
)

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = """You are Argo's Jira planner for a standup voice agent.

The user already said the wake phrase. `transcript` is only the command after
"Hey Argo". Speech-to-text is messy: "scrum dash twelve" or "scrum 12" means
SCRUM-12. Fix ticket keys, issue types, and status names.

Return a plan by calling emit_jira_plan. That JSON is forwarded to an Atlassian
REST handler. Follow the templates exactly:

CRUD
- create_issue  POST /rest/api/3/issue
- get_issue     GET  /rest/api/3/issue/{issueIdOrKey}
- search_issues POST /rest/api/3/search/jql   (read when the key is unknown)
- update_issue  PUT  /rest/api/3/issue/{issueIdOrKey}
- delete_issue  DELETE /rest/api/3/issue/{issueIdOrKey}

Standup extras (still go through the same handler)
- transition_issue  POST .../transitions   (To Do / In Progress / Done)
- add_comment       POST .../comment
- assign_issue      PUT  .../assignee

Rules
- Put Jira Cloud v3 bodies under rest.body. Descriptions and comments MUST be
  ADF ({"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":"..."}]}]}).
- Never invent a ticket key. If they describe a ticket without a key, use
  search_issues (JQL with project + text) rather than get_issue.
- Use context.default_project_key when creating or searching.
- Spoken "move to in progress / done / to do" is transition_issue, not update_issue.
- Leave rest.body.transition.id as "RESOLVE_FROM_NAME" and set
  resolve.transition_name to the Jira status/transition name.
- Leave rest.body.accountId as "RESOLVE_FROM_DISPLAY_NAME" and set
  resolve.assignee_display_name unless context already has an account id.
- delete_issue only when they clearly say delete/remove. Set confirm=true.
  If they are vague, needs_clarification=true and operations=[].
- Multiple actions in one sentence become multiple operations, in order.
- needs_clarification=true ⇒ operations must be []. Set clarification_question
  and spoken_reply to a short question the TTS can read.
- spoken_reply is one sentence confirming what you will do (or the question).
- Omit optional Jira fields you were not told to change.
- Do not wrap the tool input in markdown.
"""


def _adf_or_text(value: Any) -> Any:
    if isinstance(value, str):
        return text_to_adf(value)
    return value


def _normalize_operation(op: dict[str, Any]) -> dict[str, Any]:
    rest = dict(op.get("rest") or {})
    rest.setdefault("path_params", {})
    rest.setdefault("query", {})
    if "body" not in rest:
        rest["body"] = None
    issue_key = op.get("issue_key")
    if issue_key and "{issueIdOrKey}" in str(rest.get("path", "")):
        rest["path_params"] = {
            **rest["path_params"],
            "issueIdOrKey": rest["path_params"].get("issueIdOrKey") or issue_key,
        }
    name = op.get("op")
    body = rest.get("body")
    if name in {"create_issue", "update_issue"} and isinstance(body, dict):
        fields = body.get("fields") or {}
        if isinstance(fields.get("description"), str):
            fields = {**fields, "description": text_to_adf(fields["description"])}
            body = {**body, "fields": fields}
            rest["body"] = body
    if name == "add_comment" and isinstance(body, dict) and isinstance(body.get("body"), str):
        rest["body"] = {**body, "body": text_to_adf(body["body"])}
    if name == "transition_issue" and isinstance(body, dict):
        update = body.get("update") or {}
        comments = (update.get("comment") or []) if isinstance(update, dict) else []
        fixed = []
        for item in comments:
            add = (item or {}).get("add") or {}
            inner = add.get("body")
            if isinstance(inner, str):
                add = {**add, "body": text_to_adf(inner)}
                item = {**item, "add": add}
            fixed.append(item)
        if comments:
            rest["body"] = {**body, "update": {**update, "comment": fixed}}
    return {**op, "rest": rest}


def _normalize_plan(tool_input: dict[str, Any], transcript: str, utterance_id: str | None) -> dict[str, Any]:
    ops = [_normalize_operation(op) for op in (tool_input.get("operations") or [])]
    clarify = bool(tool_input.get("needs_clarification"))
    question = tool_input.get("clarification_question")
    spoken = tool_input.get("spoken_reply") or ""
    if clarify:
        ops = []
        if not question:
            question = spoken or "What Jira change should I make?"
        if not spoken:
            spoken = question
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"transcript": transcript, "utterance_id": utterance_id},
        "intent": tool_input.get("intent") or ("clarify" if clarify else "get_issue"),
        "confidence": float(tool_input.get("confidence") or 0),
        "needs_clarification": clarify,
        "clarification_question": question,
        "spoken_reply": spoken,
        "operations": ops,
    }


def plan_from_transcript(
    transcript: str,
    *,
    context: dict[str, Any] | None = None,
    utterance_id: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Map a Cartesia command string to a validated Jira plan dict."""
    load_dotenv()
    text = (transcript or "").strip()
    if not text:
        plan = empty_plan(transcript=text, utterance_id=utterance_id)
        plan["clarification_question"] = "I only heard the wake word. What should I do on Jira?"
        plan["spoken_reply"] = plan["clarification_question"]
        return plan

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in the environment or a .env file.")

    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic: pip install anthropic") from exc

    user_payload = {
        "transcript": text,
        "context": context or {},
        "templates": OPERATION_TEMPLATES,
    }
    client = Anthropic(api_key=key)
    response = client.messages.create(
        model=model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[EMIT_JIRA_PLAN_TOOL],
        # Some Anthropic models (including the configured default) do not
        # support forced tool selection. The system prompt asks for this tool;
        # auto lets compatible models invoke it without rejecting the request.
        tool_choice={"type": "auto"},
        messages=[
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, default=str),
            }
        ],
    )

    tool_input = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_jira_plan":
            tool_input = block.input
            break
    if not isinstance(tool_input, dict):
        plan = empty_plan(transcript=text, utterance_id=utterance_id)
        plan["clarification_question"] = "I couldn't parse that into a Jira action."
        plan["spoken_reply"] = plan["clarification_question"]
        return plan

    plan = _normalize_plan(tool_input, text, utterance_id)
    errors = validate_plan(plan)
    if errors:
        plan = empty_plan(transcript=text, utterance_id=utterance_id)
        plan["clarification_question"] = "I need a clearer Jira request."
        plan["spoken_reply"] = plan["clarification_question"]
        plan["validation_errors"] = errors
        plan["raw_model_output"] = tool_input
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn a Hey Argo transcript into a Jira operation JSON plan."
    )
    parser.add_argument("transcript", nargs="?", help="Command text after the wake word.")
    parser.add_argument("--project", default=os.getenv("JIRA_PROJECT_KEY", "SCRUM"))
    parser.add_argument(
        "--current-issue",
        default=os.getenv("JIRA_CURRENT_ISSUE"),
        help="Issue key in focus if the user says 'this ticket'.",
    )
    parser.add_argument(
        "--known-issue",
        action="append",
        default=[],
        help="Known issue keys (repeatable) to help STT correction.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    transcript = args.transcript
    if not transcript:
        transcript = sys.stdin.read()
    context = {
        "default_project_key": args.project,
        "default_issue_type": "Task",
        "current_issue_key": args.current_issue,
        "known_issue_keys": args.known_issue or ([args.current_issue] if args.current_issue else []),
    }
    plan = plan_from_transcript(transcript, context=context)
    json.dump(plan, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    if plan.get("needs_clarification") or plan.get("validation_errors"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
