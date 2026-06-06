"""
OpsGhost Fixer Prompt
Given a fix strategy and file contents, generates the exact patched
file content and a PR description. Returns structured JSON.
"""


FIXER_SYSTEM = """You are OpsGhost, an expert DevOps AI agent that generates precise file patches to fix CI/CD failures.

You will be given the current content of a file and a diagnosis of what needs to change.
You must generate the corrected file content and a pull request description.

You MUST respond with ONLY a valid JSON object. No markdown. No explanation. No code fences.
The JSON must exactly match this schema:

{
  "patched_content": "<complete corrected file content as a single string with \\n for newlines>",
  "change_summary": "<one sentence describing exactly what was changed and why>",
  "pr_title": "<concise PR title, max 72 chars, starting with 'fix:'>",
  "pr_body": "<full PR body in markdown format describing the failure, root cause, and fix applied>",
  "diff_description": "<human-readable description of the diff: what line changed from what to what>"
}

Critical rules:
- patched_content must be the COMPLETE file — not a diff, not a snippet, not truncated
- Only change what is necessary to fix the failure — do not refactor or clean up unrelated code
- If you are bumping a dependency version, use the latest stable version you know of
- The pr_body should be written as if OpsGhost is a team member reporting an incident:
  explain what broke, why it broke, and what was done to fix it
- pr_title must start with "fix:" and reference the failure type
- If you cannot generate a safe patch (the fix requires understanding business logic),
  set patched_content to the original content unchanged and explain in change_summary

PR body template to follow (fill in the blanks):
## 🤖 OpsGhost Auto-Fix

**Workflow:** {workflow_name}
**Run:** #{run_id}
**Branch:** {branch}

### What broke
[Describe the failure in plain English]

### Root cause
[The specific line or condition that caused the failure]

### What was fixed
[Describe the exact change made]

### Risk assessment
[Low/Medium/High — explain why]

---
*This PR was opened automatically by [OpsGhost](https://github.com/MuaazTasawar/opsghost). Review before merging.*
"""


def build_fixer_prompt(
    repo: str,
    workflow_name: str,
    run_id: int,
    branch: str,
    failure_type: str,
    failure_summary: str,
    root_cause_line: str,
    fix_strategy: str,
    reasoning: str,
    file_path: str,
    file_content: str,
    log_excerpt: str,
) -> str:
    return f"""Generate a patch to fix this CI failure.

Repository: {repo}
Workflow: {workflow_name}
Run ID: {run_id}
Branch: {branch}
Failure Type: {failure_type}
Diagnosis: {failure_summary}
Root Cause Line: {root_cause_line}
Fix Strategy: {fix_strategy}
Strategy Reasoning: {reasoning}

File to patch: {file_path}
--- CURRENT FILE CONTENT ---
{file_content}
--- END OF FILE ---

Relevant log excerpt:
{log_excerpt[:1500]}

Respond with ONLY the JSON object described in your instructions.
Remember: patched_content must be the COMPLETE corrected file."""


COMMENT_ONLY_PR_TEMPLATE = """## 🤖 OpsGhost Diagnostic Report

**Workflow:** {workflow_name}
**Run:** #{run_id}
**Branch:** {branch}

### What broke
{failure_summary}

### Root cause
{root_cause_line}
### Why OpsGhost didn't auto-fix
{reasoning}

### Recommended action
{suggested_fix_hint}

### Full diagnosis
- **Failure type:** {failure_type}
- **Confidence:** {confidence:.0%}
- **Strategy selected:** {fix_strategy}

---
*This PR was opened automatically by [OpsGhost](https://github.com/MuaazTasawar/opsghost) to document the failure. A human fix is required.*
"""


def build_comment_only_body(
    workflow_name: str,
    run_id: int,
    branch: str,
    failure_summary: str,
    root_cause_line: str,
    reasoning: str,
    suggested_fix_hint: str,
    failure_type: str,
    confidence: float,
    fix_strategy: str,
) -> str:
    return COMMENT_ONLY_PR_TEMPLATE.format(
        workflow_name=workflow_name,
        run_id=run_id,
        branch=branch,
        failure_summary=failure_summary,
        root_cause_line=root_cause_line,
        reasoning=reasoning,
        suggested_fix_hint=suggested_fix_hint,
        failure_type=failure_type,
        confidence=confidence,
        fix_strategy=fix_strategy,
    )