"""
OpsGhost Node: fetch_logs
Fetches and preprocesses the raw GitHub Actions log for the failed run.
Sets state.raw_logs and state.should_abort on fatal errors.
"""

import os
import logging
from agent.state import OpsGhostState
from agent.tools.github_tools import fetch_workflow_run_logs
from agent.tools.log_tools import prepare_log_for_llm

logger = logging.getLogger(__name__)


async def fetch_logs_node(state: OpsGhostState) -> OpsGhostState:
    """
    Node 1: Fetch and preprocess workflow run logs.

    On success: populates state.raw_logs
    On failure: sets state.should_abort = True
    """
    logger.info(
        f"[fetch_logs] Fetching logs for run {state.workflow_run_id} "
        f"in {state.repo_full_name}"
    )

    max_chars = int(os.getenv("MAX_LOG_CHARS", "12000"))

    # Fetch raw logs from GitHub
    raw_log, err = await fetch_workflow_run_logs(
        repo_full_name=state.repo_full_name,
        run_id=state.workflow_run_id,
        max_chars=max_chars * 2,  # fetch more, then trim after preprocessing
    )

    if err:
        logger.error(f"[fetch_logs] Log fetch failed: {err}")
        state.log_fetch_error = err
        state.should_abort = True
        state.abort_reason = f"Could not fetch logs: {err}"
        return state

    if not raw_log or len(raw_log.strip()) < 50:
        logger.warning("[fetch_logs] Logs were empty or too short to analyze.")
        state.should_abort = True
        state.abort_reason = "Fetched logs were empty. Nothing to analyze."
        return state

    # Preprocess: clean, extract error section, detect hints
    prepared_log, hints = prepare_log_for_llm(raw_log, max_chars=max_chars)

    state.raw_logs = prepared_log
    # Store hints on state for downstream nodes via a temporary attribute
    # We use a plain attribute since OpsGhostState is a dataclass (extensible)
    state._log_hints = hints  # type: ignore[attr-defined]

    logger.info(
        f"[fetch_logs] Logs ready. "
        f"Length: {len(prepared_log)} chars. "
        f"Hints detected: {len(hints)}"
    )

    return state