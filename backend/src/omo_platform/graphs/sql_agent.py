"""SQL agent graph (Run-A + Run-B).

Run-A:
- If no `approved_sql` exists in the input state, propose a safe SELECT-only SQL
  and emit a UI approval card (`SqlApprovalCard`) via `push_ui_message`.

Run-B:
- If `approved_sql` exists in the input state, execute it against the local
  Chinook SQLite DB in read-only mode and emit a `ResultTableCard` via
  `push_ui_message`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.ui import AnyUIMessage, push_ui_message, ui_message_reducer

import omo_platform.sql_safety as sql_safety


class State(TypedDict, total=False):
    # Use LangGraph's message reducer so returned messages are appended.
    messages: Annotated[list[AnyMessage], add_messages]
    # Persist UI cards (UIMessage) into state/history so refetch doesn't drop them.
    ui: Annotated[list[AnyUIMessage], ui_message_reducer]
    # Run-B will provide this; Run-A only proposes when this is missing.
    approved_sql: str
    # Optional: included by UI when starting Run-B.
    approval_id: str


def _latest_human_text(messages: list[AnyMessage]) -> str:
    """Best-effort: return most recent HumanMessage text content."""

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            # HumanMessage.content can be a str or a structured payload.
            if isinstance(msg.content, str):
                return msg.content
            return str(msg.content)
    return ""


def _propose_sql_from_prompt(prompt: str) -> tuple[str, list[str], str]:
    """Deterministic, SELECT-only SQL proposal.

    Keep this deliberately simple and safe. We only produce a hardcoded SELECT
    query, optionally picking a plausible table name based on keywords.
    """

    prompt_lc = (prompt or "").lower()
    # Tiny heuristic so the card feels relevant without any DB access.
    for table in ("users", "orders", "products"):
        if table in prompt_lc:
            sql = f"SELECT * FROM {table} LIMIT 100"
            reason = f"基于你的问题包含关键词 '{table}'，先对该表做只读抽样查询以便确认字段与数据范围。"
            return sql, [table], reason

    sql = "SELECT 1 AS ok"
    reason = "未识别到明确的表名；先用最安全的只读探测查询作为占位，等待你确认或补充需求。"
    return sql, [], reason


def _run_a_request_sql_approval(state: State) -> dict:
    """Run-A node: propose SQL and request human approval."""

    # If Run-B already injected an approved SQL, we still do NOT execute it here.
    approved_sql = state.get("approved_sql")
    if approved_sql:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "已收到 `approved_sql`，但当前图仅实现 Run-A（不执行 SQL）。"
                    )
                )
            ]
        }

    messages = state.get("messages", [])
    prompt = _latest_human_text(messages)
    sql, tables, reason = _propose_sql_from_prompt(prompt)

    approval_id = str(uuid4())
    message_id = f"msg_sql_approval_{approval_id}"

    ai_message = AIMessage(
        id=message_id,
        content=(
            "需要你确认后才能继续执行 SQL（当前 Run-A 仅生成候选 SQL，不会访问数据库）。\n"
            f"候选 SQL: {sql}"
        ),
    )

    props = {
        "approval_id": approval_id,
        "sql": sql,
        "tables": tables,
        "max_rows": 100,
        "read_only": True,
        "reason": reason,
    }

    # UIMessage cards rely on metadata.message_id matching the AI message id.
    push_ui_message(name="SqlApprovalCard", props=props, message=ai_message)

    # End the run by only appending this AI message.
    return {"messages": [ai_message]}


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        infra_dir = parent / "infra"
        backend_dir = parent / "backend"
        if infra_dir.is_dir() and backend_dir.is_dir():
            return parent
    # Fallback: assume standard layout backend/src/...
    return here.parents[4] if len(here.parents) > 4 else here.parent


def _json_safe_cell(value: object) -> object:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}>"
    return value


def _run_b_execute_sql(state: State) -> dict:
    """Run-B node: execute approved SQL (read-only) and emit result UI card."""

    approved_sql = state.get("approved_sql")
    approval_id = state.get("approval_id")
    suffix = approval_id or str(uuid4())
    message_id = f"msg_sql_result_{suffix}"

    if not approved_sql:
        ai_message = AIMessage(id=message_id, content="Missing approved_sql; nothing to execute.")
        return {"messages": [ai_message]}

    repo_root = _find_repo_root()
    db_path = repo_root / "infra" / "chinook" / "chinook.db"
    if not db_path.exists():
        ai_message = AIMessage(
            id=message_id,
            content=(
                "SQLite DB file not found: "
                f"{db_path}. Expected repo-relative infra/chinook/chinook.db"
            ),
        )
        return {"messages": [ai_message]}

    try:
        max_rows = 100
        result = sql_safety.execute_readonly_query(str(db_path), approved_sql, max_rows=max_rows)
        columns = list(result.columns)
        rows = [[_json_safe_cell(cell) for cell in row] for row in result.rows]
        returned_row_count = len(rows)
        truncated = returned_row_count == max_rows

        props = {
            "columns": columns,
            "rows": rows,
            "row_count": returned_row_count,
            "returned_row_count": returned_row_count,
            "truncated": truncated,
            "duration_ms": int(result.duration_ms),
            "sql": approved_sql,
        }

        ai_message = AIMessage(
            id=message_id,
            content=f"SQL executed. Returned {returned_row_count} row(s).",
        )
        push_ui_message(name="ResultTableCard", props=props, message=ai_message)
        return {"messages": [ai_message]}
    except Exception as e:
        ai_message = AIMessage(id=message_id, content=f"SQL execution failed: {e}")
        return {"messages": [ai_message]}


def _route_start(state: State) -> str:
    if state.get("approved_sql"):
        return "run_b_execute_sql"
    return "run_a_request_sql_approval"


_builder = StateGraph(State)
_builder.add_node("run_a_request_sql_approval", _run_a_request_sql_approval)
_builder.add_node("run_b_execute_sql", _run_b_execute_sql)
_builder.add_conditional_edges(
    START,
    _route_start,
    {
        "run_a_request_sql_approval": "run_a_request_sql_approval",
        "run_b_execute_sql": "run_b_execute_sql",
    },
)
_builder.add_edge("run_a_request_sql_approval", END)
_builder.add_edge("run_b_execute_sql", END)

# Exported for LangGraph Server (e.g. `./path/to/file.py:graph`).
graph = _builder.compile()
