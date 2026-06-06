"""
OpsGhost LangGraph — Complete Wiring
Builds and compiles the full agent graph with all nodes, edges,
conditional routing, and observability hooks.

Graph flow:
  fetch_logs
      │
      ├── [abort] ──────────────────────────────────────────► END
      │
  classify_failure
      │
      ├── [low_confidence] ──────────────────────────────────► END
      │
  select_strategy
      │
      ├── [abort / no_action] ───────────────────────────────► END
      │
  execute_fix
      │
      ├── [abort] ──────────────────────────────────────────► END
      │
  open_pr
      │
      └──────────────────────────────────────────────────────► END
"""

import logging
import time
from typing import Any
from langgraph.graph import StateGraph, END
from agent.state import OpsGhostState

logger = logging.getLogger(__name__)


# ── Conditional edge functions ────────────────────────────────────────────────

def _route_after_fetch(state: OpsGhostState) -> str:
    """
    After fetch_logs:
      - abort if log fetch failed or logs were empty
      - continue to classification otherwise
    """
    if state.should_abort:
        logger.warning(
            f"[graph] Aborting after fetch_logs. Reason: {state.abort_reason}"
        )
        return "abort"
    return "continue"


def _route_after_classify(state: OpsGhostState) -> str:
    """
    After classify_failure:
      - abort if should_abort was set inside the node
      - low_confidence if failure_type is unknown AND confidence < 0.4
        (graph sends these straight to END — nothing safe to do)
      - proceed otherwise
    """
    if state.should_abort:
        logger.warning(
            f"[graph] Aborting after classify_failure. Reason: {state.abort_reason}"
        )
        return "abort"

    if state.failure_type == "unknown" and state.failure_confidence < 0.4:
        logger.info(
            f"[graph] Low confidence ({state.failure_confidence:.2f}) on unknown failure — "
            "skipping fix attempt."
        )
        return "low_confidence"

    return "proceed"


def _route_after_strategy(state: OpsGhostState) -> str:
    """
    After select_strategy:
      - abort if no_action was selected (should_abort set by node)
        or if a fatal error occurred inside the node
      - continue otherwise
    """
    if state.should_abort:
        logger.info(
            f"[graph] Aborting after select_strategy. Reason: {state.abort_reason}"
        )
        return "abort"
    return "continue"


def _route_after_execute(state: OpsGhostState) -> str:
    """
    After execute_fix:
      - abort if a fatal error occurred (branch creation failed, LLM error, etc.)
        Note: add_comment_only sets fix_applied=False but does NOT abort —
        open_pr handles that case gracefully.
      - continue to PR opening otherwise
    """
    if state.should_abort:
        logger.warning(
            f"[graph] Aborting after execute_fix. Reason: {state.abort_reason}"
        )
        return "abort"
    return "continue"


# ── Node wrappers with timing and structured logging ─────────────────────────

def _wrap_node(name: str, node_fn):
    """
    Returns an async wrapper around a node function that:
      - Logs entry and exit
      - Times execution
      - Catches unexpected exceptions and sets should_abort safely
    """
    async def wrapped(state: OpsGhostState) -> OpsGhostState:
        start = time.monotonic()
        logger.info(f"[graph] ► Entering node: {name}")
        try:
            result = await node_fn(state)
            elapsed = time.monotonic() - start
            logger.info(f"[graph] ✓ Exited node: {name} ({elapsed:.2f}s)")
            return result
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(
                f"[graph] ✗ Unhandled exception in node '{name}' "
                f"after {elapsed:.2f}s: {exc}",
                exc_info=True,
            )
            state.should_abort = True
            state.abort_reason = f"Unhandled exception in {name}: {str(exc)}"
            state.record_error(name, str(exc))
            return state

    wrapped.__name__ = f"wrapped_{name}"
    return wrapped


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph() -> Any:
    """
    Builds and compiles the complete OpsGhost LangGraph.

    Returns a compiled CompiledGraph ready to call with:
        await graph.ainvoke(initial_state)

    All node imports are local to avoid circular import issues at
    module load time.
    """
    from agent.nodes.fetch_logs import fetch_logs_node
    from agent.nodes.classify_failure import classify_failure_node
    from agent.nodes.select_strategy import select_strategy_node
    from agent.nodes.execute_fix import execute_fix_node
    from agent.nodes.open_pr import open_pr_node

    builder = StateGraph(OpsGhostState)

    # ── Register nodes (wrapped for observability) ────────────────
    builder.add_node("fetch_logs",       _wrap_node("fetch_logs",       fetch_logs_node))
    builder.add_node("classify_failure", _wrap_node("classify_failure", classify_failure_node))
    builder.add_node("select_strategy",  _wrap_node("select_strategy",  select_strategy_node))
    builder.add_node("execute_fix",      _wrap_node("execute_fix",      execute_fix_node))
    builder.add_node("open_pr",          _wrap_node("open_pr",          open_pr_node))

    # ── Entry point ───────────────────────────────────────────────
    builder.set_entry_point("fetch_logs")

    # ── fetch_logs → classify_failure ─────────────────────────────
    builder.add_conditional_edges(
        "fetch_logs",
        _route_after_fetch,
        {
            "abort":    END,
            "continue": "classify_failure",
        },
    )

    # ── classify_failure → select_strategy ───────────────────────
    builder.add_conditional_edges(
        "classify_failure",
        _route_after_classify,
        {
            "abort":          END,
            "low_confidence": END,
            "proceed":        "select_strategy",
        },
    )

    # ── select_strategy → execute_fix ─────────────────────────────
    builder.add_conditional_edges(
        "select_strategy",
        _route_after_strategy,
        {
            "abort":    END,
            "continue": "execute_fix",
        },
    )

    # ── execute_fix → open_pr ─────────────────────────────────────
    builder.add_conditional_edges(
        "execute_fix",
        _route_after_execute,
        {
            "abort":    END,
            "continue": "open_pr",
        },
    )

    # ── open_pr → END (always terminal) ──────────────────────────
    builder.add_edge("open_pr", END)

    compiled = builder.compile()
    logger.info("[graph] OpsGhost LangGraph compiled successfully.")
    return compiled


# ── Module-level singleton ────────────────────────────────────────────────────

_graph_instance = None


def get_graph():
    """
    Returns the cached compiled graph.
    Thread-safe for typical async usage — builds once on first call.
    """
    global _graph_instance
    if _graph_instance is None:
        logger.info("[graph] Building OpsGhost graph for the first time...")
        _graph_instance = build_graph()
    return _graph_instance


async def run_agent(initial_state: OpsGhostState) -> OpsGhostState:
    """
    Top-level entry point for running the full OpsGhost agent pipeline.

    Args:
        initial_state: OpsGhostState pre-populated from webhook payload

    Returns:
        Final OpsGhostState after all nodes have run (or aborted).

    Usage:
        from agent.graph import run_agent
        from agent.state import OpsGhostState

        state = OpsGhostState(
            repo_full_name="owner/repo",
            workflow_run_id=12345678,
            workflow_name="CI",
            head_branch="main",
            head_sha="abc123...",
        )
        final = await run_agent(state)
        print(final.to_summary_dict())
    """
    graph = get_graph()

    logger.info(
        f"[graph] Starting OpsGhost run | "
        f"repo={initial_state.repo_full_name} | "
        f"run_id={initial_state.workflow_run_id} | "
        f"workflow={initial_state.workflow_name} | "
        f"branch={initial_state.head_branch}"
    )

    start = time.monotonic()

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error(f"[graph] Graph invocation failed: {exc}", exc_info=True)
        initial_state.should_abort = True
        initial_state.abort_reason = f"Graph invocation error: {str(exc)}"
        return initial_state

    elapsed = time.monotonic() - start

    summary = final_state.to_summary_dict()
    logger.info(
        f"[graph] Run complete in {elapsed:.2f}s | "
        f"type={summary['failure_type']} | "
        f"strategy={summary['fix_strategy']} | "
        f"fix_applied={summary['fix_applied']} | "
        f"pr={summary['pr_url'] or 'none'} | "
        f"aborted={summary['aborted']}"
    )

    return final_state