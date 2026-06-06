"""
OpsGhost Webhook Handlers
Processes validated GitHub webhook payloads.
Extracts the relevant fields and fires the agent pipeline
as a background task so GitHub's 10-second webhook timeout
is never hit.
"""

import logging
import asyncio
from agent.state import OpsGhostState
from agent.graph import run_agent

logger = logging.getLogger(__name__)


# ── Payload validation helpers ────────────────────────────────────────────────

def is_actionable_run(payload: dict) -> tuple[bool, str]:
    """
    Determines whether a workflow_run webhook payload should trigger
    the OpsGhost pipeline.

    Returns:
        (should_process, reason_if_not)
    """
    action = payload.get("action", "")
    if action != "completed":
        return False, f"Action is '{action}', not 'completed' — ignoring."

    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion", "")

    if conclusion != "failure":
        return False, f"Conclusion is '{conclusion}', not 'failure' — ignoring."

    # Ignore runs triggered by OpsGhost's own fix branches to prevent loops
    head_branch: str = workflow_run.get("head_branch", "")
    if head_branch.startswith("opsghost/"):
        return False, f"Run triggered by OpsGhost branch '{head_branch}' — skipping to prevent loop."

    # Must have essential identifiers
    run_id = workflow_run.get("id")
    head_sha = workflow_run.get("head_sha", "")
    repo = payload.get("repository", {}).get("full_name", "")

    if not run_id:
        return False, "Payload missing workflow_run.id."
    if not head_sha:
        return False, "Payload missing workflow_run.head_sha."
    if not repo:
        return False, "Payload missing repository.full_name."

    return True, ""


def extract_state_from_payload(payload: dict) -> OpsGhostState:
    """
    Builds an OpsGhostState from a validated workflow_run webhook payload.
    All fields are extracted defensively with safe defaults.
    """
    workflow_run = payload.get("workflow_run", {})
    repository = payload.get("repository", {})

    return OpsGhostState(
        repo_full_name=repository.get("full_name", ""),
        workflow_run_id=int(workflow_run.get("id", 0)),
        workflow_name=workflow_run.get("name", "unknown"),
        head_branch=workflow_run.get("head_branch", ""),
        head_sha=workflow_run.get("head_sha", ""),
    )


# ── Background pipeline runner ────────────────────────────────────────────────

async def run_pipeline_background(state: OpsGhostState) -> None:
    """
    Runs the full OpsGhost agent pipeline in the background.
    Catches all exceptions so background task failures are logged
    but never crash the server.
    """
    try:
        logger.info(
            f"[handler] Pipeline started | "
            f"repo={state.repo_full_name} | "
            f"run_id={state.workflow_run_id}"
        )
        final_state = await run_agent(state)
        summary = final_state.to_summary_dict()

        if final_state.pr_url:
            logger.info(
                f"[handler] ✅ Pipeline complete | "
                f"PR: {final_state.pr_url} | "
                f"strategy: {final_state.fix_strategy}"
            )
        elif final_state.should_abort:
            logger.warning(
                f"[handler] ⚠️  Pipeline aborted | "
                f"reason: {final_state.abort_reason}"
            )
        else:
            logger.info(
                f"[handler] ℹ️  Pipeline finished without PR | "
                f"strategy: {final_state.fix_strategy}"
            )

        # Log any accumulated non-fatal errors
        if summary["node_errors"]:
            logger.warning(
                f"[handler] Non-fatal node errors: {summary['node_errors']}"
            )

    except Exception as exc:
        logger.error(
            f"[handler] Unhandled exception in background pipeline for "
            f"run {state.workflow_run_id}: {exc}",
            exc_info=True,
        )


# ── Main dispatch handler ─────────────────────────────────────────────────────

async def handle_workflow_run(payload: dict) -> dict:
    """
    Entry point called by the webhook server for workflow_run events.

    Validates the payload, extracts state, and fires the pipeline
    as a non-blocking background task.

    Returns a response dict for the HTTP 200 acknowledgement.
    """
    should_process, skip_reason = is_actionable_run(payload)

    if not should_process:
        logger.info(f"[handler] Skipping event: {skip_reason}")
        return {
            "status": "skipped",
            "reason": skip_reason,
        }

    state = extract_state_from_payload(payload)

    logger.info(
        f"[handler] Dispatching pipeline | "
        f"repo={state.repo_full_name} | "
        f"run_id={state.workflow_run_id} | "
        f"workflow={state.workflow_name} | "
        f"branch={state.head_branch}"
    )

    # Fire and forget — GitHub expects a response within 10 seconds
    # The pipeline can take 30-90 seconds so we must not await it here
    asyncio.create_task(run_pipeline_background(state))

    return {
        "status": "accepted",
        "run_id": state.workflow_run_id,
        "repo": state.repo_full_name,
        "workflow": state.workflow_name,
        "message": "OpsGhost pipeline dispatched.",
    }