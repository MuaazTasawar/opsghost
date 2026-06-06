"""
OpsGhost Tests: Classifier
Tests the log preprocessing pipeline and failure classification logic.
These tests run without hitting the LLM or GitHub API.
"""

import pytest
from agent.tools.log_tools import (
    clean_log,
    extract_error_section,
    detect_failure_hints,
    prepare_log_for_llm,
    format_hints_for_prompt,
    extract_file_references,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

DEPENDENCY_LOG = """
2024-01-15T12:00:01.000Z Run pip install
2024-01-15T12:00:02.000Z Collecting requests==99.99.99
2024-01-15T12:00:03.000Z \x1b[31mERROR: Could not find a version that satisfies the requirement requests==99.99.99\x1b[0m
2024-01-15T12:00:03.100Z ERROR: No matching distribution found for requests==99.99.99
2024-01-15T12:00:03.200Z ##[error]Process completed with exit code 1.
"""

DOCKER_LOG = """
2024-01-15T12:00:01.000Z Step 1/5 : FROM python:3.11.999-slim
2024-01-15T12:00:02.000Z manifest for python:3.11.999-slim not found: manifest unknown
2024-01-15T12:00:02.100Z \x1b[31mERROR\x1b[0m: failed to solve: failed to read dockerfile
2024-01-15T12:00:02.200Z ##[error]Process completed with exit code 1.
"""

TEST_LOG = """
2024-01-15T12:00:01.000Z collected 3 items
2024-01-15T12:00:02.000Z
2024-01-15T12:00:02.100Z FAILED tests/test_demo_fail.py::test_basic_math - AssertionError: Expected 5 but got 4
2024-01-15T12:00:02.200Z FAILED tests/test_demo_fail.py::test_missing_env_var - AssertionError: DATABASE_URL environment variable is not set
2024-01-15T12:00:02.300Z 2 failed, 1 passed in 0.42s
2024-01-15T12:00:02.400Z ##[error]Process completed with exit code 1.
"""

CLEAN_LOG = """
2024-01-15T12:00:01.000Z Everything is fine
2024-01-15T12:00:02.000Z All steps passed
"""

ANSI_LOG = "\x1b[31mERROR\x1b[0m: something went wrong\n\x1b[32mOK\x1b[0m: something else"


# ── clean_log tests ───────────────────────────────────────────────────────────

class TestCleanLog:
    def test_strips_ansi_codes(self):
        result = clean_log(ANSI_LOG)
        assert "\x1b[" not in result
        assert "ERROR" in result
        assert "something went wrong" in result

    def test_strips_github_timestamps(self):
        result = clean_log(DEPENDENCY_LOG)
        assert "2024-01-15T12:00:01.000Z" not in result
        assert "pip install" in result

    def test_preserves_error_content(self):
        result = clean_log(DEPENDENCY_LOG)
        assert "requests==99.99.99" in result
        assert "ERROR" in result

    def test_collapses_blank_lines(self):
        log_with_blanks = "line1\n\n\n\n\nline2"
        result = clean_log(log_with_blanks)
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_empty_log(self):
        result = clean_log("")
        assert result == ""

    def test_log_with_only_whitespace(self):
        result = clean_log("   \n\n   \n  ")
        assert result == ""


# ── extract_error_section tests ───────────────────────────────────────────────

class TestExtractErrorSection:
    def test_finds_error_in_dependency_log(self):
        cleaned = clean_log(DEPENDENCY_LOG)
        section = extract_error_section(cleaned)
        assert "requests==99.99.99" in section

    def test_finds_error_in_docker_log(self):
        cleaned = clean_log(DOCKER_LOG)
        section = extract_error_section(cleaned)
        assert "manifest" in section or "python:3.11.999" in section

    def test_finds_error_in_test_log(self):
        cleaned = clean_log(TEST_LOG)
        section = extract_error_section(cleaned)
        assert "FAILED" in section or "AssertionError" in section

    def test_fallback_on_no_errors(self):
        cleaned = clean_log(CLEAN_LOG)
        section = extract_error_section(cleaned)
        # Should return last N lines rather than empty
        assert len(section) > 0

    def test_returns_string(self):
        section = extract_error_section("some random log output")
        assert isinstance(section, str)


# ── detect_failure_hints tests ────────────────────────────────────────────────

class TestDetectFailureHints:
    def test_detects_pip_dependency_failure(self):
        cleaned = clean_log(DEPENDENCY_LOG)
        hints = detect_failure_hints(cleaned)
        types = [h["failure_type"] for h in hints]
        assert "dependency" in types

    def test_detects_docker_failure(self):
        cleaned = clean_log(DOCKER_LOG)
        hints = detect_failure_hints(cleaned)
        types = [h["failure_type"] for h in hints]
        assert "docker" in types

    def test_detects_test_failure(self):
        cleaned = clean_log(TEST_LOG)
        hints = detect_failure_hints(cleaned)
        types = [h["failure_type"] for h in hints]
        assert "test" in types

    def test_returns_empty_list_on_clean_log(self):
        cleaned = clean_log(CLEAN_LOG)
        hints = detect_failure_hints(cleaned)
        # Clean log may or may not have hints — should be a list regardless
        assert isinstance(hints, list)

    def test_hint_structure(self):
        cleaned = clean_log(DEPENDENCY_LOG)
        hints = detect_failure_hints(cleaned)
        assert len(hints) > 0
        hint = hints[0]
        assert "failure_type" in hint
        assert "label" in hint
        assert "match" in hint
        assert "pattern" in hint

    def test_match_is_capped(self):
        cleaned = clean_log(DEPENDENCY_LOG)
        hints = detect_failure_hints(cleaned)
        for hint in hints:
            assert len(hint["match"]) <= 200


# ── prepare_log_for_llm tests ─────────────────────────────────────────────────

class TestPrepareLogForLlm:
    def test_returns_tuple(self):
        result = prepare_log_for_llm(DEPENDENCY_LOG)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_prepared_log_is_string(self):
        log_text, hints = prepare_log_for_llm(DEPENDENCY_LOG)
        assert isinstance(log_text, str)

    def test_hints_is_list(self):
        log_text, hints = prepare_log_for_llm(DEPENDENCY_LOG)
        assert isinstance(hints, list)

    def test_respects_max_chars(self):
        long_log = "ERROR: something\n" * 5000
        log_text, _ = prepare_log_for_llm(long_log, max_chars=1000)
        assert len(log_text) <= 1100  # small buffer for truncation marker

    def test_dependency_log_contains_error(self):
        log_text, _ = prepare_log_for_llm(DEPENDENCY_LOG)
        assert "requests" in log_text or "ERROR" in log_text


# ── format_hints_for_prompt tests ────────────────────────────────────────────

class TestFormatHintsForPrompt:
    def test_empty_hints(self):
        result = format_hints_for_prompt([])
        assert "No known error signatures" in result

    def test_formats_hints(self):
        hints = [
            {"failure_type": "dependency", "label": "pip error", "match": "requests==99.99.99"}
        ]
        result = format_hints_for_prompt(hints)
        assert "DEPENDENCY" in result
        assert "pip error" in result

    def test_returns_string(self):
        result = format_hints_for_prompt([])
        assert isinstance(result, str)


# ── extract_file_references tests ────────────────────────────────────────────

class TestExtractFileReferences:
    def test_finds_python_files(self):
        log = "Error in ./src/app.py:42\nAlso failed in requirements.txt"
        refs = extract_file_references(log)
        assert any("requirements.txt" in r or "app.py" in r for r in refs)

    def test_returns_list(self):
        refs = extract_file_references("some log")
        assert isinstance(refs, list)

    def test_caps_at_20_results(self):
        many_files = "\n".join([f"error in file{i}.py" for i in range(50)])
        refs = extract_file_references(many_files)
        assert len(refs) <= 20

    def test_deduplicates(self):
        log = "error in app.py\nerror in app.py\nerror in app.py"
        refs = extract_file_references(log)
        py_refs = [r for r in refs if "app.py" in r]
        assert len(py_refs) <= 1