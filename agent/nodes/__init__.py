"""
OpsGhost Agent Nodes
Each node is a pure async function: (state: OpsGhostState) -> OpsGhostState
Nodes must never raise — they set should_abort=True on fatal errors.
"""