from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import os
import re
import uuid

from openai import OpenAI

from sqlalchemy import select

import omo_platform.sql_safety as sql_safety
from omo_platform.db.event_log_v1 import append_event
from omo_platform.db.models_v1 import ApprovalV1, RunV1
from omo_platform.db.session import SessionLocal


def _env_any(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            v = v.strip()
            if v:
                return v
    return None


def _get_deepseek_client() -> tuple[OpenAI, str, str]:
    # Prefer explicit DeepSeek vars, but tolerate common variants.
    api_key = _env_any("DEEPSEEK_API_KEY", "deepseek_api_key", "OPENAI_API_KEY", "openai_api_key")
    base_url = _env_any(
        "DEEPSEEK_BASE_URL",
        "deepseek_base_url",
        "OPENAI_BASE_URL",
        "openai_base_url",
    ) or "https://api.deepseek.com/v1"
    model = _env_any("DEEPSEEK_MODEL", "deepseek_model", "OPENAI_MODEL", "openai_model") or "deepseek-chat"

    if not api_key:
        raise RuntimeError("Missing DeepSeek API key (DEEPSEEK_API_KEY or OPENAI_API_KEY)")

    return OpenAI(api_key=api_key, base_url=base_url), base_url, model


def _extract_sql(text: str) -> str:
    """Extract a single SQL statement from LLM output.

    We accept:
    - plain SQL
    - ```sql fenced blocks
    - leading/trailing commentary
    """

    if not isinstance(text, str):
        return ""

    m = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        text = m.group(1)

    text = text.strip()
    # Prefer first semicolon-terminated statement if present.
    semi = text.find(";")
    if semi >= 0:
        text = text[: semi + 1]
    return text.strip()


def _latest_user_prompt(input_json: dict | None) -> str:
    if not isinstance(input_json, dict):
        return ""
    msgs = input_json.get("messages")
    if not isinstance(msgs, list):
        return ""
    for m in reversed(msgs):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        return str(c)
    return ""


def execute_run_a(*, project_id: uuid.UUID, thread_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Run-A: propose SQL via DeepSeek, request approval, emit canonical events."""

    with SessionLocal() as session:
        run = (
            session.get(RunV1, {"project_id": project_id, "run_id": run_id})
        )
        if run is None:
            return

        # Idempotent lifecycle events.
        append_event(
            session,
            project_id=project_id,
            run_id=run_id,
            event_type="run.started",
            payload={},
            dedupe_key="run.started",
        )

        prompt = _latest_user_prompt(run.input_json)

        client, base_url, model = _get_deepseek_client()
        llm_prompt = (
            "You are a SQL assistant. Output ONLY one SQLite SELECT query. "
            "No explanations. No markdown. Must be a single statement. "
            "Always include a LIMIT 200 (or smaller).\n\n"
            f"User request: {prompt}"
        )

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": llm_prompt}],
                temperature=0,
                stream=False,
            )
            content = resp.choices[0].message.content or ""
        except Exception as e:
            # Emit a deterministic error event; do not leak secrets.
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="run.error",
                payload={"error": f"llm call failed: {type(e).__name__}"},
                dedupe_key="run.error",
            )
            run.status = "failed"
            run.error = f"llm call failed: {type(e).__name__}"
            run.updated_at = datetime.datetime.now(datetime.timezone.utc)
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="run.finished",
                payload={},
                dedupe_key="run.finished",
            )
            session.commit()
            return

        proposed_raw = _extract_sql(content)
        try:
            proposed_sql = sql_safety.validate_and_rewrite_sql(proposed_raw, max_rows=200)
        except Exception as e:
            proposed_sql = "SELECT 1 AS ok LIMIT 1"
            reason = f"model output rejected: {type(e).__name__}"
        else:
            reason = ""

        approval_id = uuid.uuid4()
        message_id = f"msg_sql_approval_{approval_id}"

        session.add(
            ApprovalV1(
                project_id=project_id,
                approval_id=approval_id,
                thread_id=thread_id,
                run_id_request=run_id,
                run_id_execute=None,
                status="pending",
                sql_proposed=proposed_sql,
                sql_approved=None,
                decided_at=None,
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )

        append_event(
            session,
            project_id=project_id,
            run_id=run_id,
            event_type="message.text",
            payload={"content": "需要你确认后才能继续执行 SQL。"},
            dedupe_key="message.text",
        )
        append_event(
            session,
            project_id=project_id,
            run_id=run_id,
            event_type="ui.card",
            payload={
                "name": "SqlApprovalCard",
                "props": {
                    "approval_id": str(approval_id),
                    "sql": proposed_sql,
                    "tables": [],
                    "max_rows": 200,
                    "read_only": True,
                    "reason": reason,
                    "llm": {"provider": "deepseek", "base_url": base_url, "model": model},
                },
                "message_id": message_id,
            },
            dedupe_key=f"ui.card:{message_id}",
        )
        append_event(
            session,
            project_id=project_id,
            run_id=run_id,
            event_type="run.finished",
            payload={},
            dedupe_key="run.finished",
        )

        run.status = "requires_approval"
        run.updated_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()


def execute_run_b(*, project_id: uuid.UUID, run_id: uuid.UUID, chinook_path: str) -> None:
    """Run-B: execute approved SQL and emit result card."""

    with SessionLocal() as session:
        run = session.get(RunV1, {"project_id": project_id, "run_id": run_id})
        if run is None:
            return

        approval_id = run.approval_id
        if approval_id is None:
            run.status = "failed"
            run.error = "missing approval_id"
            run.updated_at = datetime.datetime.now(datetime.timezone.utc)
            session.commit()
            return

        approval = (
            session.execute(
                select(ApprovalV1)
                .where(ApprovalV1.project_id == project_id)
                .where(ApprovalV1.approval_id == approval_id)
            )
            .scalars()
            .first()
        )
        if approval is None or not approval.sql_approved:
            run.status = "failed"
            run.error = "missing approved sql"
            run.updated_at = datetime.datetime.now(datetime.timezone.utc)
            session.commit()
            return

        message_id = f"msg_sql_result_{approval_id}"

        append_event(
            session,
            project_id=project_id,
            run_id=run_id,
            event_type="run.started",
            payload={},
            dedupe_key="run.started",
        )

        try:
            result = sql_safety.execute_readonly_query(
                chinook_path, approval.sql_approved, max_rows=200
            )
            columns = list(result.columns)
            rows = [list(r) for r in result.rows]
            returned = len(rows)
            truncated = returned == 200

            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="message.text",
                payload={"content": f"SQL executed. Returned {returned} row(s)."},
                dedupe_key="message.text",
            )
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="ui.card",
                payload={
                    "name": "ResultTableCard",
                    "props": {
                        "columns": columns,
                        "rows": rows,
                        "row_count": returned,
                        "returned_row_count": returned,
                        "truncated": truncated,
                        "duration_ms": int(result.duration_ms),
                        "sql": approval.sql_approved,
                    },
                    "message_id": message_id,
                },
                dedupe_key=f"ui.card:{message_id}",
            )
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="run.finished",
                payload={},
                dedupe_key="run.finished",
            )
            run.status = "succeeded"
            run.error = None
            run.updated_at = datetime.datetime.now(datetime.timezone.utc)
            session.commit()
        except Exception as e:
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="run.error",
                payload={"error": str(e)[:200]},
                dedupe_key="run.error",
            )
            append_event(
                session,
                project_id=project_id,
                run_id=run_id,
                event_type="run.finished",
                payload={},
                dedupe_key="run.finished",
            )
            run.status = "failed"
            run.error = str(e)[:200]
            run.updated_at = datetime.datetime.now(datetime.timezone.utc)
            session.commit()
