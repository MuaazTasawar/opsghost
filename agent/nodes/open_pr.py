"""
OpsGhost Node: open_pr
Opens a GitHub Pull Request with the fix (or diagnostic report).

For fix strategies with file changes:
  - Opens a PR from fix branch → head_branch
  - PR body describes the failure and what was changed

For add_comment_only strategy:
  - Opens a PR from head_branch with a diagnostic-only body
  - No code changes, just structured failure report

Populates state.pr_url and state.pr_number on success.
"""

import logging
from agent.state import OpsGhostState
from agent.tools.github_tools import (
    get_repo_client,
    create_pull_request,
    post_comment_on_run,
)
from prompts.fixer import build_comment_only_body

logger = logging.getLogger(__name__)

# Maximum PR title length GitHub allows
MAX_PR_TITLE_LEN = 256


def _truncate_title(title: str) -> str:
    if len(title) > MAX_PR_TITLE_LEN:
        return title[:MAX_PR_TITLE_LEN - 3] + "..."
    return title


def _build_fix_pr_title(fixer_output: dict, failure_type: str, run_id: int) -> str:
    """Builds the PR title from fixer LLM output or a safe fallback."""
    title = fixer_output.get("pr_title", "")
    if title and title.startswith("fix:"):
        return _truncate_title(title)
    # Fallback
    return f"fix({failure_type}): OpsGhost auto-fix for run #{run_id}"


def _build_comment_only_pr_title(failure_type: str, run_id: int) -> str:
    return f"chore: OpsGhost diagnostic report — {failure_type} failure in run #{run_id}"


async def open_pr_node(state: OpsGhostState) -> OpsGhostState:
    """
    Node 5: Open a GitHub Pull Request.

    Branches:
      A) fix_applied=True  → PR from fix branch, includes code change
      B) fix_applied=False → PR from opsghost/report branch OR commit comment

    On success: state.pr_url, state.pr_number populated
    On failure: state.pr_error set (non-fatal — we log and move on)
    """
    logger.info(
        f"[open_pr] Opening PR for run {state.workflow_run_id}. "
        f"fix_applied={state.fix_applied}, strategy={state.fix_strategy}"
    )

    # ── Get authenticated repo client ────────────────────────────
    repo, err = get_repo_client(state.repo_full_name)
    if err:
        logger.error(f"[open_pr] Repo client error: {err}")
        state.pr_error = err
        state.record_error("open_pr", err)
        return state

    classifier_output = getattr(state, "_classifier_output", {})
    fixer_output = getattr(state, "_fixer_output", {})
    root_cause_line = classifier_output.get("root_cause_line", "Not identified")
    suggested_fix_hint = classifier_output.get("suggested_fix_hint", "Manual review required.")

    # ── Branch A: Fix was applied — open a real fix PR ───────────
    if state.fix_applied and state.fix_branch_name:
        pr_title = _build_fix_pr_title(
            fixer_output=fixer_output,
            failure_type=state.failure_type,
            run_id=state.workflow_run_id,
        )

        # Use LLM-generated body if available, else build a fallback
        pr_body = fixer_output.get("pr_body", "")
        if not pr_body:
            pr_body = (
                f"## 🤖 OpsGhost Auto-Fix\n\n"
                f"**Workflow:** {state.workflow_name}\n"
                f"**Run:** #{state.workflow_run_id}\n"
                f"**Branch:** {state.head_branch}\n\n"
                f"### What broke\n{state.failure_summary}\n\n"
                f"### Root cause\n```\n{root_cause_line}\n```\n\n"
                f"### What was fixed\n{state.strategy_reasoning}\n\n"
                f"### Diff summary\n```\n{state.fix_diff}\n```\n\n"
                f"---\n*This PR was opened automatically by "
                f"[OpsGhost](https://github.com/MuaazTasawar/opsghost). "
                f"Review before merging.*"
            )

        pr_url, pr_number, err = create_pull_request(
            repo=repo,
            title=pr_title,
            body=pr_body,
            head_branch=state.fix_branch_name,
            base_branch=state.head_branch,
        )

        if err:
            logger.error(f"[open_pr] PR creation failed: {err}")
            state.pr_error = err
            state.record_error("open_pr", err)

            # Fallback: post a commit comment so the failure isn't silent
            comment = (
                f"🤖 **OpsGhost** attempted to fix this failure but could not open a PR.\n\n"
                f"**Reason:** {err}\n\n"
                f"**Diagnosis:** {state.failure_summary}\n"
                f"**Strategy attempted:** {state.fix_strategy}"
            )
            posted, comment_err = post_comment_on_run(repo, state.head_sha, comment)
            if not posted:
                logger.warning(f"[open_pr] Commit comment fallback also failed: {comment_err}")
        else:
            state.pr_url = pr_url
            state.pr_number = pr_number
            logger.info(f"[open_pr] PR opened: {pr_url} (#{pr_number})")

        return state

    # ── Branch B: No file fix — open diagnostic comment PR ───────
    logger.info("[open_pr] No file fix applied — opening diagnostic report PR.")

    pr_title = _build_comment_only_pr_title(
        failure_type=state.failure_type,
        run_id=state.workflow_run_id,
    )

    pr_body = build_comment_only_body(
        workflow_name=state.workflow_name,
        run_id=state.workflow_run_id,
        branch=state.head_branch,
        failure_summary=state.failure_summary,
        root_cause_line=root_cause_line,
        reasoning=state.strategy_reasoning,
        suggested_fix_hint=suggested_fix_hint,
        failure_type=state.failure_type,
        confidence=state.failure_confidence,
        fix_strategy=state.fix_strategy,
    )

    # For comment-only PRs we need a branch with at least one commit.
    # We create a tiny no-op branch: add an empty .opsghost-report file.
    report_branch = f"opsghost/report/run-{state.workflow_run_id}"

    from agent.tools.github_tools import create_fix_branch

    branch_ok, branch_err = create_fix_branch(
        repo=repo,
        base_sha=state.head_sha,
        branch_name=report_branch,
    )

    if not branch_ok:
        # Can't create branch → fall back to a commit comment
        logger.warning(f"[open_pr] Report branch creation failed: {branch_err}. Falling back to commit comment.")
        comment = (
            f"🤖 **OpsGhost Diagnostic Report**\n\n"
            f"**Failure type:** {state.failure_type} (confidence: {state.failure_confidence:.0%})\n\n"
            f"**Diagnosis:** {state.failure_summary}\n\n"
            f"**Root cause line:**\n```\n{root_cause_line}\n```\n\n"
            f"**Suggested fix:** {suggested_fix_hint}\n\n"
            f"**Why OpsGhost didn't auto-fix:** {state.strategy_reasoning}"
        )
        posted, comment_err = post_comment_on_run(repo, state.head_sha, comment)
        if posted:
            logger.info("[open_pr] Fallback commit comment posted successfully.")
        else:
            logger.error(f"[open_pr] Commit comment fallback failed: {comment_err}")
            state.pr_error = f"Both PR and commit comment failed: {comment_err}"
            state.record_error("open_pr", state.pr_error)
        return state

    # Commit the report marker file so the branch has a diff vs base
    try:
        report_marker_content = (
            f"# OpsGhost Report\n"
            f"Run: {state.workflow_run_id}\n"
            f"Failure: {state.failure_type}\n"
            f"Confidence: {state.failure_confidence:.2f}\n"
        )
        repo.create_file(
            path=".opsghost-report",
            message=f"chore: OpsGhost diagnostic report for run #{state.workflow_run_id}",
            content=report_marker_content,
            branch=report_branch,
        )
    except Exception as e:
        logger.warning(f"[open_pr] Could not create report marker file: {e}")
        # Non-fatal — attempt PR anyway

    pr_url, pr_number, err = create_pull_request(
        repo=repo,
        title=pr_title,
        body=pr_body,
        head_branch=report_branch,
        base_branch=state.head_branch,
    )

    if err:
        logger.error(f"[open_pr] Diagnostic PR creation failed: {err}")
        state.pr_error = err
        state.record_error("open_pr", err)
    else:
        state.pr_url = pr_url
        state.pr_number = pr_number
        logger.info(f"[open_pr] Diagnostic PR opened: {pr_url} (#{pr_number})")

    return state