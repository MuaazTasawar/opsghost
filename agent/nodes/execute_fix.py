"""
OpsGhost Node: execute_fix
Applies the selected fix strategy by:
  1. Creating a fix branch off the failed commit
  2. Fetching the target file
  3. Calling the LLM fixer to generate patched content
  4. Committing the patched file to the fix branch

For add_comment_only strategy: skips file modification entirely.
Sets state.fix_applied, state.fix_branch_name, state.fix_diff on success.
"""

import os
import json
import logging
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from agent.state import OpsGhostState
from agent.tools.github_tools import (
    get_repo_client,
    create_fix_branch,
    get_file_contents,
    update_file_on_branch,
)
from prompts.fixer import FIXER_SYSTEM, build_fixer_prompt

logger = logging.getLogger(__name__)


# Maps fix strategy → list of candidate files to look for, in priority order
STRATEGY_TARGET_FILES: dict[str, list[str]] = {
    "bump_dependency": [
        "requirements.txt",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Pipfile",
    ],
    "fix_dockerfile": [
        "Dockerfile",
        "docker/Dockerfile",
        "Dockerfile.prod",
        "docker-compose.yml",
    ],
    "fix_test_config": [
        "pytest.ini",
        "setup.cfg",
        "pyproject.toml",
        "jest.config.js",
        "jest.config.ts",
        "vitest.config.ts",
        ".env.test",
        "conftest.py",
    ],
}


def _get_llm() -> ChatGroq:
    return ChatGroq(
        model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.1,
        max_tokens=4000,  # patched_content can be large
    )


def _parse_fixer_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
    return json.loads(cleaned)


def _build_fix_branch_name(run_id: int) -> str:
    prefix = os.getenv("PR_BRANCH_PREFIX", "opsghost/fix")
    return f"{prefix}/run-{run_id}"


def _find_target_file(
    repo,
    strategy: str,
    strategy_output: dict,
    base_branch: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Finds the first existing file that matches the strategy's target list.
    Also checks LLM-suggested target_files first.

    Returns:
        (file_path, file_content, file_sha) or (None, None, None)
    """
    # Start with LLM-suggested files, then fall back to known patterns
    llm_targets = strategy_output.get("target_files", [])
    candidates = llm_targets + STRATEGY_TARGET_FILES.get(strategy, [])

    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for f in candidates:
        if f and f not in seen:
            seen.add(f)
            ordered.append(f)

    for file_path in ordered:
        content, sha, err = get_file_contents(repo, file_path, base_branch)
        if content is not None and sha is not None:
            logger.info(f"[execute_fix] Found target file: {file_path}")
            return file_path, content, sha

    return None, None, None


def _generate_simple_diff(original: str, patched: str, file_path: str) -> str:
    """
    Generates a simple line-by-line diff description.
    Not a true unified diff — just a human-readable summary.
    """
    original_lines = original.splitlines()
    patched_lines = patched.splitlines()

    removed = [l for l in original_lines if l not in patched_lines]
    added = [l for l in patched_lines if l not in original_lines]

    parts = [f"File: {file_path}"]
    if removed:
        parts.append("Removed lines:")
        for l in removed[:10]:  # cap at 10 for readability
            parts.append(f"  - {l}")
    if added:
        parts.append("Added lines:")
        for l in added[:10]:
            parts.append(f"  + {l}")
    if not removed and not added:
        parts.append("No line-level changes detected (possible whitespace or encoding change).")

    return "\n".join(parts)


async def execute_fix_node(state: OpsGhostState) -> OpsGhostState:
    """
    Node 4: Execute the selected fix strategy.

    Strategies handled:
      - bump_dependency    → patch dependency file on new branch
      - fix_dockerfile     → patch Dockerfile on new branch
      - fix_test_config    → patch test config on new branch
      - add_comment_only   → skip file changes, mark fix_applied=False
                             (open_pr will handle the diagnostic PR)

    On success: state.fix_applied=True, state.fix_branch_name set
    On failure: state.fix_error set, state.should_abort=True
    """
    logger.info(
        f"[execute_fix] Executing strategy '{state.fix_strategy}' "
        f"for run {state.workflow_run_id}"
    )

    # ── add_comment_only: no file changes needed ─────────────────
    if state.fix_strategy == "add_comment_only":
        logger.info("[execute_fix] Strategy is add_comment_only — skipping file changes.")
        state.fix_applied = False
        state.fix_branch_name = ""
        state.fix_diff = "No file changes — diagnostic comment PR will be opened."
        return state

    # ── Get authenticated repo client ────────────────────────────
    repo, err = get_repo_client(state.repo_full_name)
    if err:
        logger.error(f"[execute_fix] Repo client error: {err}")
        state.fix_error = err
        state.should_abort = True
        state.abort_reason = f"Could not authenticate with GitHub: {err}"
        return state

    # ── Create fix branch ────────────────────────────────────────
    branch_name = _build_fix_branch_name(state.workflow_run_id)
    state.fix_branch_name = branch_name

    success, err = create_fix_branch(
        repo=repo,
        base_sha=state.head_sha,
        branch_name=branch_name,
    )
    if not success:
        logger.error(f"[execute_fix] Branch creation failed: {err}")
        state.fix_error = err
        state.should_abort = True
        state.abort_reason = f"Could not create fix branch '{branch_name}': {err}"
        return state

    logger.info(f"[execute_fix] Created branch: {branch_name}")

    # ── Find target file ─────────────────────────────────────────
    strategy_output = getattr(state, "_strategy_output", {})
    classifier_output = getattr(state, "_classifier_output", {})

    file_path, file_content, file_sha = _find_target_file(
        repo=repo,
        strategy=state.fix_strategy,
        strategy_output=strategy_output,
        base_branch=state.head_branch,
    )

    if file_path is None:
        logger.warning("[execute_fix] No target file found. Falling back to add_comment_only.")
        state.fix_strategy = "add_comment_only"
        state.fix_applied = False
        state.fix_branch_name = ""
        state.strategy_reasoning += (
            " (Downgraded to comment-only: no patchable file found on the branch.)"
        )
        state.fix_diff = "No target file found — no changes made."
        return state

    # ── Call LLM fixer to generate patched content ───────────────
    root_cause_line = classifier_output.get("root_cause_line", "Not identified")

    fixer_prompt = build_fixer_prompt(
        repo=state.repo_full_name,
        workflow_name=state.workflow_name,
        run_id=state.workflow_run_id,
        branch=state.head_branch,
        failure_type=state.failure_type,
        failure_summary=state.failure_summary,
        root_cause_line=root_cause_line,
        fix_strategy=state.fix_strategy,
        reasoning=state.strategy_reasoning,
        file_path=file_path,
        file_content=file_content,
        log_excerpt=state.raw_logs,
    )

    llm = _get_llm()
    messages = [
        SystemMessage(content=FIXER_SYSTEM),
        HumanMessage(content=fixer_prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        parsed = _parse_fixer_response(response.content)
    except json.JSONDecodeError as e:
        logger.error(f"[execute_fix] Fixer JSON parse error: {e}")
        state.fix_error = f"Fixer LLM returned invalid JSON: {e}"
        state.should_abort = True
        state.abort_reason = state.fix_error
        state.record_error("execute_fix", state.fix_error)
        return state
    except Exception as e:
        logger.error(f"[execute_fix] Fixer LLM error: {e}")
        state.fix_error = f"Fixer LLM call failed: {e}"
        state.should_abort = True
        state.abort_reason = state.fix_error
        state.record_error("execute_fix", state.fix_error)
        return state

    patched_content = parsed.get("patched_content", "")
    change_summary = parsed.get("change_summary", "No summary.")

    # ── Safety check: reject empty or unchanged patches ──────────
    if not patched_content or patched_content.strip() == file_content.strip():
        logger.warning("[execute_fix] LLM returned unchanged content. Falling back to comment-only.")
        state.fix_strategy = "add_comment_only"
        state.fix_applied = False
        state.fix_branch_name = ""
        state.fix_diff = "LLM patch was identical to original — no changes committed."
        state.strategy_reasoning += " (Downgraded to comment-only: generated patch was identical to original file.)"
        return state

    # ── Commit patched file to fix branch ────────────────────────
    commit_message = (
        f"fix({state.failure_type}): {change_summary[:60]} [OpsGhost run-{state.workflow_run_id}]"
    )

    committed, err = update_file_on_branch(
        repo=repo,
        file_path=file_path,
        new_content=patched_content,
        file_sha=file_sha,
        branch=branch_name,
        commit_message=commit_message,
    )

    if not committed:
        logger.error(f"[execute_fix] File commit failed: {err}")
        state.fix_error = err
        state.should_abort = True
        state.abort_reason = f"Could not commit fix to branch: {err}"
        state.record_error("execute_fix", err)
        return state

    # ── Record success ────────────────────────────────────────────
    state.fix_applied = True
    state.fix_diff = _generate_simple_diff(file_content, patched_content, file_path)

    # Store fixer output for PR node
    state._fixer_output = parsed   # type: ignore[attr-defined]
    state._fixed_file_path = file_path  # type: ignore[attr-defined]

    logger.info(
        f"[execute_fix] Fix committed to branch '{branch_name}'. "
        f"File: {file_path}. Summary: {change_summary}"
    )

    return state