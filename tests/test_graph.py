"""
OpsGhost Tests: Graph & State
Tests state initialization, routing logic, and graph structure
without invoking the LLM or GitHub API.
"""

import pytest
from agent.state import OpsGhostState


# ── OpsGhostState tests ───────────────────────────────────────────────────────

class TestOpsGhostState:
    def test_default_initialization(self):
        state = OpsGhostState()
        assert state.repo_full_name == ""
        assert state.workflow_run_id == 0
        assert state.failure_type == "unknown"
        assert state.fix_strategy == "no_action"
        assert state.should_abort is False
        assert state.fix_applied is False
        assert state.pr_url is None
        assert state.node_errors == []

    def test_full_initialization(self):
        state = OpsGhostState(
            repo_full_name="MuaazTasawar/opsghost",
            workflow_run_id=12345678,
            workflow_name="CI",
            head_branch="main",
            head_sha="abc123def456",
        )
        assert state.repo_full_name == "MuaazTasawar/opsghost"
        assert state.workflow_run_id == 12345678
        assert state.workflow_name == "CI"
        assert state.head_branch == "main"
        assert state.head_sha == "abc123def456"

    def test_record_error_appends(self):
        state = OpsGhostState()
        state.record_error("fetch_logs", "Connection timeout")
        state.record_error("classify_failure", "JSON parse error")
        assert len(state.node_errors) == 2
        assert state.node_errors[0]["node"] == "fetch_logs"
        assert state.node_errors[1]["node"] == "classify_failure"

    def test_record_error_structure(self):
        state = OpsGhostState()
        state.record_error("test_node", "test error message")
        err = state.node_errors[0]
        assert "node" in err
        assert "error" in err
        assert err["node"] == "test_node"
        assert err["error"] == "test error message"

    def test_to_summary_dict_keys(self):
        state = OpsGhostState(
            repo_full_name="owner/repo",
            workflow_run_id=999,
        )
        summary = state.to_summary_dict()
        expected_keys = [
            "repo", "run_id", "workflow", "branch", "sha",
            "failure_type", "failure_summary", "confidence",
            "fix_strategy", "fix_applied", "fix_branch",
            "pr_url", "aborted", "abort_reason", "node_errors",
        ]
        for key in expected_keys:
            assert key in summary, f"Missing key: {key}"

    def test_to_summary_dict_values(self):
        state = OpsGhostState(
            repo_full_name="owner/repo",
            workflow_run_id=42,
            workflow_name="Deploy",
            head_branch="feature/x",
            head_sha="deadbeef",
        )
        state.failure_type = "dependency"
        state.failure_confidence = 0.95
        state.fix_applied = True
        state.pr_url = "https://github.com/owner/repo/pull/7"

        summary = state.to_summary_dict()
        assert summary["repo"] == "owner/repo"
        assert summary["run_id"] == 42
        assert summary["failure_type"] == "dependency"
        assert summary["confidence"] == 0.95
        assert summary["fix_applied"] is True
        assert summary["pr_url"] == "https://github.com/owner/repo/pull/7"
        assert summary["aborted"] is False

    def test_abort_state(self):
        state = OpsGhostState()
        state.should_abort = True
        state.abort_reason = "Log fetch failed"
        summary = state.to_summary_dict()
        assert summary["aborted"] is True
        assert summary["abort_reason"] == "Log fetch failed"


# ── Graph routing function tests ──────────────────────────────────────────────

class TestGraphRouting:
    """
    Tests the conditional edge functions directly without building
    the full graph. Imports are local to avoid LangGraph compilation
    at test collection time.
    """

    def test_route_after_fetch_aborts(self):
        from agent.graph import _route_after_fetch
        state = OpsGhostState()
        state.should_abort = True
        state.abort_reason = "Logs empty"
        assert _route_after_fetch(state) == "abort"

    def test_route_after_fetch_continues(self):
        from agent.graph import _route_after_fetch
        state = OpsGhostState()
        state.should_abort = False
        assert _route_after_fetch(state) == "continue"

    def test_route_after_classify_low_confidence(self):
        from agent.graph import _route_after_classify
        state = OpsGhostState()
        state.failure_type = "unknown"
        state.failure_confidence = 0.2
        assert _route_after_classify(state) == "low_confidence"

    def test_route_after_classify_proceeds_on_known_type(self):
        from agent.graph import _route_after_classify
        state = OpsGhostState()
        state.failure_type = "dependency"
        state.failure_confidence = 0.9
        assert _route_after_classify(state) == "proceed"

    def test_route_after_classify_proceeds_on_unknown_high_confidence(self):
        from agent.graph import _route_after_classify
        state = OpsGhostState()
        state.failure_type = "unknown"
        state.failure_confidence = 0.6  # above 0.4 threshold
        assert _route_after_classify(state) == "proceed"

    def test_route_after_classify_aborts_on_should_abort(self):
        from agent.graph import _route_after_classify
        state = OpsGhostState()
        state.should_abort = True
        state.failure_type = "dependency"
        state.failure_confidence = 0.9
        assert _route_after_classify(state) == "abort"

    def test_route_after_strategy_aborts(self):
        from agent.graph import _route_after_strategy
        state = OpsGhostState()
        state.should_abort = True
        assert _route_after_strategy(state) == "abort"

    def test_route_after_strategy_continues(self):
        from agent.graph import _route_after_strategy
        state = OpsGhostState()
        state.should_abort = False
        state.fix_strategy = "bump_dependency"
        assert _route_after_strategy(state) == "continue"

    def test_route_after_execute_aborts(self):
        from agent.graph import _route_after_execute
        state = OpsGhostState()
        state.should_abort = True
        assert _route_after_execute(state) == "abort"

    def test_route_after_execute_continues(self):
        from agent.graph import _route_after_execute
        state = OpsGhostState()
        state.should_abort = False
        state.fix_applied = True
        assert _route_after_execute(state) == "continue"

    def test_route_after_execute_continues_on_comment_only(self):
        from agent.graph import _route_after_execute
        # add_comment_only sets fix_applied=False but NOT should_abort
        state = OpsGhostState()
        state.should_abort = False
        state.fix_applied = False
        state.fix_strategy = "add_comment_only"
        assert _route_after_execute(state) == "continue"