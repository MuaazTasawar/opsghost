"""
OpsGhost Node: classify_failure
Sends preprocessed logs to the LLM and classifies the failure type.
Populates state.failure_type, state.failure_summary, state.failure_confidence.
"""

import os
import json
import logging
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from agent.state import OpsGhostState
from prompts.classifier import CLASSIFIER_SYSTEM, build_classifier_prompt
from agent.tools.log_tools import format_hints_for_prompt

logger = logging.getLogger(__name__)


def _get_llm() -> ChatGroq:
    return ChatGroq(
        model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.1,  # low temp for consistent structured output
        max_tokens=1000,
    )


def _parse_classifier_response(raw: str) -> dict:
    """
    Parses the LLM's JSON response. Strips any accidental markdown fences.
    Raises ValueError on invalid JSON.
    """
    cleaned = raw.strip()
    # Strip ```json ... ``` if the model added them despite instructions
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
    return json.loads(cleaned)


async def classify_failure_node(state: OpsGhostState) -> OpsGhostState:
    """
    Node 2: Classify the failure using the LLM.

    On success: populates failure_type, failure_summary, failure_confidence
    On LLM error: sets failure_type='unknown', confidence=0.0, does NOT abort
    """
    logger.info(f"[classify_failure] Classifying failure for run {state.workflow_run_id}")

    hints = getattr(state, "_log_hints", [])
    hints_text = format_hints_for_prompt(hints)

    prompt = build_classifier_prompt(
        repo=state.repo_full_name,
        workflow_name=state.workflow_name,
        branch=state.head_branch,
        log_text=state.raw_logs,
        hints_text=hints_text,
    )

    llm = _get_llm()
    messages = [
        SystemMessage(content=CLASSIFIER_SYSTEM),
        HumanMessage(content=prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw_content = response.content

        logger.debug(f"[classify_failure] Raw LLM response: {raw_content[:500]}")

        parsed = _parse_classifier_response(raw_content)

        # Validate required fields
        failure_type = parsed.get("failure_type", "unknown")
        if failure_type not in ("dependency", "docker", "test", "config", "unknown"):
            logger.warning(f"[classify_failure] Invalid failure_type '{failure_type}', defaulting to unknown")
            failure_type = "unknown"

        state.failure_type = failure_type
        state.failure_summary = parsed.get("failure_summary", "No summary provided.")
        state.failure_confidence = float(parsed.get("confidence", 0.0))

        # Store extra fields for downstream nodes
        state._classifier_output = parsed  # type: ignore[attr-defined]

        logger.info(
            f"[classify_failure] Result: type={state.failure_type} "
            f"confidence={state.failure_confidence:.2f}"
        )

    except json.JSONDecodeError as e:
        logger.error(f"[classify_failure] JSON parse error: {e}")
        state.failure_type = "unknown"
        state.failure_summary = "OpsGhost could not parse the LLM classification response."
        state.failure_confidence = 0.0
        state.record_error("classify_failure", f"JSON parse error: {str(e)}")

    except Exception as e:
        logger.error(f"[classify_failure] LLM call failed: {e}")
        state.failure_type = "unknown"
        state.failure_summary = f"LLM classification failed: {str(e)}"
        state.failure_confidence = 0.0
        state.record_error("classify_failure", str(e))

    return state