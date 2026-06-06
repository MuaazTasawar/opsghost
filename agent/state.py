"""
OpsGhost Agent State
Defines the shared state object that flows through every node in the LangGraph.
"""

from typing import Optional, Literal
from dataclasses import dataclass, field


FailureType = Literal["dependency", "docker", "test", "config", "unknown"]

FixStrategy = Literal[
    "bump_dependency",
    "fix_dockerfile",
    "fix_test_config",
    "add_comment_only",
    "no_action",
]


@dataclass
class OpsGhostState:
    # ── Webhook payload ──────────────────────────────────────────
    repo_full_name: str = ""          # e.g. "MuaazTasawar/opsghost"
    workflow_run_id: int = 0          # GitHub Actions run ID
    workflow_name: str = ""           # e.g. "CI"
    head_branch: str = ""             # branch that triggered the run
    head_sha: str = ""                # commit SHA of the failed run

    # ── Fetched log data ─────────────────────────────────────────
    raw_logs: str = ""                # truncated raw log text
    log_fetch_error: Optional[str] = None

    # ── Classification ───────────────────────────────────────────
    failure_type: FailureType = "unknown"
    failure_summary: str = ""         # 1–2 sentence human-readable diagnosis
    failure_confidence: float = 0.0   # 0.0 – 1.0

    # ── Strategy selection ───────────────────────────────────────
    fix_strategy: FixStrategy = "no_action"
    strategy_reasoning: str = ""

    # ── Fix execution ────────────────────────────────────────────
    fix_applied: bool = False
    fix_branch_name: str = ""         # e.g. "opsghost/fix/run-12345"
    fix_diff: str = ""                # unified diff of what was changed
    fix_error: Optional[str] = None

    # ── PR output ────────────────────────────────────────────────
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_error: Optional[str] = None

    # ── Agent control ────────────────────────────────────────────
    should_abort: bool = False        # set to True to skip remaining nodes
    abort_reason: str = ""
    node_errors: list = field(default_factory=list)  # accumulated non-fatal errors

    def record_error(self, node: str, error: str) -> None:
        self.node_errors.append({"node": node, "error": error})

    def to_summary_dict(self) -> dict:
        """Returns a clean dict for logging and post-mortem generation."""
        return {
            "repo": self.repo_full_name,
            "run_id": self.workflow_run_id,
            "workflow": self.workflow_name,
            "branch": self.head_branch,
            "sha": self.head_sha,
            "failure_type": self.failure_type,
            "failure_summary": self.failure_summary,
            "confidence": self.failure_confidence,
            "fix_strategy": self.fix_strategy,
            "fix_applied": self.fix_applied,
            "fix_branch": self.fix_branch_name,
            "pr_url": self.pr_url,
            "aborted": self.should_abort,
            "abort_reason": self.abort_reason,
            "node_errors": self.node_errors,
        }