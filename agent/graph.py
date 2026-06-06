"""
OpsGhost LangGraph — Skeleton
Full wiring happens in Phase 5. This module defines the graph builder
function and the routing logic. Importing it here validates the structure.
"""

from langgraph.graph import StateGraph, END
from agent.state import OpsGhostState


def _should_abort(state: OpsGhostState) -> str:
    """
    Conditional edge: if any node set should_abort=True, jump to END.
    Otherwise continue to the next node.
    """
    if state.should_abort:
        return "abort"
    return "continue"


def _after_classify(state: OpsGhostState) -> str:
    """
    Conditional edge after classification.
    If failure_type is unknown with low confidence → comment only and end.
    """
    if state.failure_type == "unknown" and state.failure_confidence < 0.4:
        return "low_confidence"
    return "proceed"


def build_graph() -> StateGraph:
    """
    Builds and compiles the OpsGhost LangGraph.
    Nodes are imported here (not at module level) to avoid circular imports
    before Phase 5 wires everything together.
    """
    from agent.nodes.fetch_logs import fetch_logs_node
    from agent.nodes.classify_failure import classify_failure_node
    from agent.nodes.select_strategy import select_strategy_node
    from agent.nodes.execute_fix import execute_fix_node
    from agent.nodes.open_pr import open_pr_node

    builder = StateGraph(OpsGhostState)

    # ── Register nodes ───────────────────────────────────────────
    builder.add_node("fetch_logs", fetch_logs_node)
    builder.add_node("classify_failure", classify_failure_node)
    builder.add_node("select_strategy", select_strategy_node)
    builder.add_node("execute_fix", execute_fix_node)
    builder.add_node("open_pr", open_pr_node)

    # ── Entry point ──────────────────────────────────────────────
    builder.set_entry_point("fetch_logs")

    # ── Edges ────────────────────────────────────────────────────
    # fetch_logs → classify_failure (abort check)
    builder.add_conditional_edges(
        "fetch_logs",
        _should_abort,
        {
            "abort": END,
            "continue": "classify_failure",
        },
    )

    # classify_failure → select_strategy or END if low confidence
    builder.add_conditional_edges(
        "classify_failure",
        _after_classify,
        {
            "low_confidence": END,
            "proceed": "select_strategy",
        },
    )

    # select_strategy → execute_fix (abort check)
    builder.add_conditional_edges(
        "select_strategy",
        _should_abort,
        {
            "abort": END,
            "continue": "execute_fix",
        },
    )

    # execute_fix → open_pr (abort check)
    builder.add_conditional_edges(
        "execute_fix",
        _should_abort,
        {
            "abort": END,
            "continue": "open_pr",
        },
    )

    # open_pr → END always
    builder.add_edge("open_pr", END)

    return builder.compile()


# Module-level compiled graph instance (lazy — only built when imported)
# Used by webhook/handlers.py
graph = None


def get_graph():
    """Returns a cached compiled graph, building it on first call."""
    global graph
    if graph is None:
        graph = build_graph()
    return graph