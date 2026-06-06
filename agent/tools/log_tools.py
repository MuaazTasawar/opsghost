"""
OpsGhost Log Tools
Utilities for preprocessing, cleaning, and extracting signal from raw CI logs.
These run before the LLM sees anything — cleaner input = better classification.
"""

import re
from typing import Optional


# ── Known error pattern signatures ──────────────────────────────────────────

# Maps regex pattern → (failure_type_hint, human_label)
ERROR_SIGNATURES: list[tuple[str, str, str]] = [
    # Dependency failures
    (r"npm ERR! 404", "dependency", "npm package not found (404)"),
    (r"npm ERR! code ETARGET", "dependency", "npm version target not found"),
    (r"ERROR: Could not find a version that satisfies the requirement", "dependency", "pip dependency not satisfied"),
    (r"ModuleNotFoundError: No module named", "dependency", "Python module missing"),
    (r"Cannot find module '([^']+)'", "dependency", "Node.js module missing"),
    (r"error: package `([^`]+)` .* not found", "dependency", "Cargo package not found"),
    (r"Could not resolve dependency", "dependency", "npm dependency conflict"),
    (r"peer dep missing", "dependency", "npm peer dependency missing"),

    # Docker/container failures
    (r"ERROR \[.*\] RUN", "docker", "Dockerfile RUN step failed"),
    (r"failed to solve: failed to read dockerfile", "docker", "Dockerfile not found"),
    (r"manifest for .* not found", "docker", "Docker image not found in registry"),
    (r"no such file or directory.*Dockerfile", "docker", "Dockerfile path wrong"),
    (r"COPY failed: file not found", "docker", "COPY instruction missing source file"),
    (r"executor failed running \[/bin/sh -c", "docker", "Shell command in Dockerfile failed"),

    # Test failures
    (r"FAIL\s+\S+\s+\(", "test", "Go test package failed"),
    (r"FAILED.*::.*FAILED", "test", "pytest test case failed"),
    (r"Tests\s+\d+\s+failed", "test", "Jest/Mocha tests failed"),
    (r"AssertionError:", "test", "Assertion failed in test"),
    (r"Expected.*received", "test", "Jest expect mismatch"),
    (r"\d+ test(s)? failed", "test", "Test suite failure"),
    (r"Error: .* is not a function", "test", "JS TypeError in test"),

    # Config / environment failures
    (r"error: environment variable .* is not set", "config", "Missing environment variable"),
    (r"Error: ENOENT: no such file or directory", "config", "Missing file referenced in config"),
    (r"Invalid configuration", "config", "Invalid config file"),
    (r"SyntaxError:.*\.yml", "config", "YAML syntax error"),
    (r"Error loading config", "config", "Config load failure"),
    (r"permission denied", "config", "File permission error"),
]


# ── Log cleaning ─────────────────────────────────────────────────────────────

def clean_log(raw_log: str) -> str:
    """
    Strips ANSI escape codes, GitHub Actions timestamps, and excessive whitespace.
    Returns clean plain text ready for LLM consumption.
    """
    # Remove ANSI escape sequences (colors, bold, etc.)
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    log = ansi_escape.sub("", raw_log)

    # Remove GitHub Actions timestamp prefix: "2024-01-15T12:34:56.7890000Z "
    timestamp_pattern = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*",
        re.MULTILINE,
    )
    log = timestamp_pattern.sub("", log)

    # Collapse 3+ consecutive blank lines into 2
    log = re.sub(r"\n{3,}", "\n\n", log)

    # Strip trailing whitespace per line
    lines = [line.rstrip() for line in log.splitlines()]
    log = "\n".join(lines)

    return log.strip()


def extract_error_section(log: str, context_lines: int = 30) -> str:
    """
    Finds the most relevant error block in the log.
    Prioritizes the last ERROR/FAIL/fatal section since failures
    cascade and the root cause is usually the first one that appears
    near the bottom.

    Returns the extracted section, or the full log if no pattern matches.
    """
    lines = log.splitlines()

    # Keywords that signal an error line
    error_keywords = re.compile(
        r"(error|err!|fail|fatal|exception|traceback|panic|abort)",
        re.IGNORECASE,
    )

    error_line_indices = [
        i for i, line in enumerate(lines)
        if error_keywords.search(line)
    ]

    if not error_line_indices:
        # No clear error found — return last 60 lines as context
        return "\n".join(lines[-60:])

    # Find the FIRST error occurrence (root cause, not cascading errors)
    first_error_idx = error_line_indices[0]
    start = max(0, first_error_idx - 5)
    end = min(len(lines), first_error_idx + context_lines)

    return "\n".join(lines[start:end])


def detect_failure_hints(log: str) -> list[dict]:
    """
    Scans the log for known error signatures and returns all matches.
    Used to give the LLM pre-computed hints, reducing hallucination risk.

    Returns a list of dicts:
        [{"pattern": str, "failure_type": str, "label": str, "match": str}, ...]
    """
    hints = []
    for pattern, failure_type, label in ERROR_SIGNATURES:
        matches = re.findall(pattern, log, re.IGNORECASE)
        if matches:
            # Take the first match text for context
            match_text = matches[0] if isinstance(matches[0], str) else str(matches[0])
            hints.append({
                "pattern": pattern,
                "failure_type": failure_type,
                "label": label,
                "match": match_text[:200],  # cap match length
            })

    return hints


def prepare_log_for_llm(
    raw_log: str,
    max_chars: int = 12000,
) -> tuple[str, list[dict]]:
    """
    Full preprocessing pipeline: clean → extract error section → detect hints.
    Returns (prepared_log_text, hints_list).

    This is the single entry point that nodes should call.
    """
    cleaned = clean_log(raw_log)
    error_section = extract_error_section(cleaned)
    hints = detect_failure_hints(cleaned)

    # If the error section is short, append the last 40 lines for context
    if len(error_section.splitlines()) < 20:
        last_lines = "\n".join(cleaned.splitlines()[-40:])
        combined = error_section + "\n\n--- Last 40 lines ---\n" + last_lines
    else:
        combined = error_section

    # Final truncation guard
    if len(combined) > max_chars:
        combined = combined[-max_chars:]

    return combined, hints


def format_hints_for_prompt(hints: list[dict]) -> str:
    """
    Formats the hints list into a readable block for injection into prompts.
    """
    if not hints:
        return "No known error signatures detected automatically."

    lines = ["Detected error signatures (pre-analysis):"]
    for h in hints:
        lines.append(f"  • [{h['failure_type'].upper()}] {h['label']}")
        if h["match"]:
            lines.append(f"    matched: {h['match'][:100]}")

    return "\n".join(lines)


def extract_file_references(log: str) -> list[str]:
    """
    Extracts file paths mentioned in the log (for targeted fix strategies).
    Useful for the fixer node to know which files to look at.
    """
    # Match common file path patterns
    path_pattern = re.compile(
        r"(?:^|[\s\"'`(])(\./[^\s\"'`)\n]+|[a-zA-Z0-9_\-]+\.[a-zA-Z]{2,5}(?::\d+)?)",
        re.MULTILINE,
    )
    matches = path_pattern.findall(log)

    # Deduplicate and filter noise
    seen = set()
    paths = []
    for match in matches:
        match = match.strip()
        # Filter out things that don't look like real file paths
        if len(match) > 3 and "." in match and match not in seen:
            seen.add(match)
            paths.append(match)

    return paths[:20]  # cap at 20 references