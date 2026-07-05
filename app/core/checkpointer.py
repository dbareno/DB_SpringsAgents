"""
app/core/checkpointer.py
─────────────────────────────────────────────────────────────────────────────
LangGraph checkpointer factory for the Spring Design Agent workflow.

Provides a single, process-wide ``AsyncSqliteSaver`` so the graph can persist
state across turns and resume from an ``interrupt()`` boundary instead of
re-running from ``START`` (Phase 3 — multi-turn conversation).

The checkpoint database lives in the writable per-user data directory
resolved by :mod:`app.core.paths` (``./data/checkpoints`` in server/dev mode,
``%LOCALAPPDATA%/SpringDesignAgent/checkpoints`` in the frozen ``.exe``),
never next to the (potentially read-only) install directory.

Known risk — Pydantic types in checkpointed state
──────────────────────────────────────────────────
``AgentState`` stores plain Pydantic models (``UserRequirements``,
``SpringGeometry``, ``MaterialProperties``, ``ComplianceReport``,
``CommercialScore``, ``LLMProviderStatus``) directly as state values.
LangGraph's default ``JsonPlusSerializer`` currently falls back to msgpack
+ pickle for these unregistered types with a deprecation WARNING (not an
error). If a future LangGraph release flips ``LANGGRAPH_STRICT_MSGPACK`` to
default `true`, checkpoint round-trips for THIS state shape will start
raising instead of warning. Round-trip today is verified in
``tests/test_conversation_flow.py::TestCheckpointPersistence`` (real
``SqliteSaver``, fresh-process simulation) — still fully functional as of
langgraph 1.2.7 / langgraph-checkpoint 4.1.1. If the warning becomes an
error after an upgrade, register the affected classes via
``allowed_msgpack_modules`` on a custom ``AsyncSqliteSaver(conn, serde=...)``
construction instead of ``from_conn_string`` (which doesn't expose ``serde``).
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.paths import get_data_dir

logger = logging.getLogger(__name__)

_CHECKPOINT_DB_NAME = "checkpoints.sqlite"

# Module-level singletons. Populated lazily by ``get_checkpointer()`` and torn
# down by ``close_checkpointer()`` (called from the FastAPI lifespan).
_saver: AsyncSqliteSaver | None = None
_exit_stack: AsyncExitStack | None = None


def get_checkpoint_db_path() -> str:
    """
    Return the absolute path to the checkpoint SQLite database, creating the
    parent ``checkpoints`` directory if necessary.
    """
    checkpoints_dir = get_data_dir() / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    return str(checkpoints_dir / _CHECKPOINT_DB_NAME)


async def get_checkpointer() -> AsyncSqliteSaver:
    """
    Return the process-wide ``AsyncSqliteSaver`` instance, creating it (and
    the underlying SQLite connection) on first call.

    The connection is kept open for the lifetime of the process via an
    ``AsyncExitStack`` — call :func:`close_checkpointer` during application
    shutdown to release it cleanly.
    """
    global _saver, _exit_stack

    if _saver is not None:
        return _saver

    db_path = get_checkpoint_db_path()
    _exit_stack = AsyncExitStack()
    _saver = await _exit_stack.enter_async_context(
        AsyncSqliteSaver.from_conn_string(db_path)
    )
    logger.info("[Checkpointer] AsyncSqliteSaver opened at %s", db_path)
    return _saver


async def close_checkpointer() -> None:
    """Release the checkpointer's SQLite connection (call on app shutdown)."""
    global _saver, _exit_stack

    if _exit_stack is not None:
        await _exit_stack.aclose()
        logger.info("[Checkpointer] AsyncSqliteSaver connection closed.")

    _saver = None
    _exit_stack = None
