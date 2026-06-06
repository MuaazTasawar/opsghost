"""
OpsGhost Node: select_strategy
Uses the LLM to select the best fix strategy given the classification result.
Populates state.fix_strategy and state.strategy_reasoning.
"""

import os
import json
import logging
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from agent.state import OpsGhostState
from prompts.strategist import STRATEGIST_SYSTEM, build_strategist_prompt

logger = logging.getLogger(__name__)


# Strategies that are always safe to attempt automatically
AUTO_SAFE_STRATEGIES = {"bump_dependency", "fix_test_config", "add_comment_only"}

# Strategies that require extra validation before proceeding
CAREFUL_STRATEGIES = {"fix_dockerfile"}

# Strategies that mean we do nothing
TERMINAL_STRATEGIES = {"no_action"}


def _get_llm() -> ChatGroq:
    return ChatGroq(
        model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.1,
        max_tokens=800,
    )


def _parse_strategy_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
    return json.loads(cleaned)


async def select_strategy_node(state: OpsGhostState) -> OpsGhostState:
    """
    Node 3: Select the best fix strategy using the LLM.

    On success: populates fix_strategy, strategy_reasoning
    On no_action: sets should_abort=True so remaining nodes are skipped
    On error: defaults to add_comment_only (safe fallback)
    """
    logger.info(
        f"[select_strategy] Selecting strategy for {state.failure_type} failure "
        f"(confidence={state.failure_confidence:.2f})"
    )

    classifier_output = getattr(state, "_classifier_output", {})
    root_cause_line = classifier_output.get("root_cause_line", "Not identified")
    affected_files = classifier_output.get("affected_files", [])
    suggested_fix_hint = classifier_output.get("suggested_fix_hint", "")

    prompt = build_strategist_prompt(
        repo=state.repo_full_name,
        failure_type=state.failure_type,
        failure_summary=state.failure_summary,
        confidence=state.failure_confidence,
        root_cause_line=root_cause_line,
        affected_files=affected_files,
        suggested_fix_hint=suggested_fix_hint,
        log_excerpt=state.raw_logs,
    )

    llm = _get_llm()
    messages = [
        SystemMessage(content=STRATEGIST_SYSTEM),
        HumanMessage(content=prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw_content = response.content

        logger.debug(f"[select_strategy] Raw LLM response: {raw_content[:500]}")

        parsed = _parse_strategy_response(raw_content)

        fix_strategy = parsed.get("fix_strategy", "add_comment_only")
        valid_strategies = {
            "bump_dependency", "fix_dockerfile", "fix_test_config",
            "add_comment_only", "no_action"
        }
        if fix_strategy not in valid_strategies:
            logger.warning(f"[select_strategy] Invalid strategy '{fix_strategy}', defaulting to add_comment_only")
            fix_strategy = "add_comment_only"

        state.fix_strategy = fix_strategy
        state.strategy_reasoning = parsed.get("reasoning", "No reasoning provided.")

        # Store full strategy output for downstream nodes
        state._strategy_output = parsed  # type: ignore[attr-defined]

        logger.info(
            f"[select_strategy] Strategy: {state.fix_strategy} | "
            f"Risk: {parsed.get('risk_level', 'unknown')} | "
            f"Success prob: {parsed.get('estimated_success_probability', 0):.2f}"
        )

        # If no_action selected, abort gracefully
        if fix_strategy in TERMINAL_STRATEGIES:
            state.should_abort = True
            state.abort_reason = (
                f"Strategy 'no_action' selected. Reason: {state.strategy_reasoning}"
            )
            logger.info(f"[select_strategy] Aborting: {state.abort_reason}")

    except json.JSONDecodeError as e:
        logger.error(f"[select_strategy] JSON parse error: {e}. Defaulting to add_comment_only.")
        state.fix_strategy = "add_comment_only"
        state.strategy_reasoning = "Strategy selection failed due to LLM parse error. Falling back to diagnostic comment."
        state.record_error("select_strategy", f"JSON parse error: {str(e)}")

    except Exception as e:
        logger.error(f"[select_strategy] LLM call failed: {e}. Defaulting to add_comment_only.")
        state.fix_strategy = "add_comment_only"
        state.strategy_reasoning = f"Strategy selection failed: {str(e)}. Falling back to diagnostic comment."
        state.record_error("select_strategy", str(e))

    return state