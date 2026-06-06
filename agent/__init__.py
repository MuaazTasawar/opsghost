"""
OpsGhost Agent Package
Exports the compiled LangGraph and the state type for external use.
"""

from agent.graph import build_graph
from agent.state import OpsGhostState

__all__ = ["build_graph", "OpsGhostState"]