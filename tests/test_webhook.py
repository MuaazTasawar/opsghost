"""
OpsGhost Tests: Webhook Server & Handlers
Tests payload validation, signature verification, and handler routing
without hitting GitHub or the agent pipeline.
"""

import hmac
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock


# ── Fixtures ──────────────────────────────────────────────────────────────────

WEBHOOK_SECRET = "test-secret-abc123"

VALID_WORKFLOW_RUN_PAYLOAD = {
    "action": "completed",
    "workflow_run": {
        "id": 12345678,
        "name": "CI",
        "conclusion": "failure",
        "head_branch": "main",
        "head_sha": "abc123def456789",
    },
    "repository": {
        "full_name": "MuaazTasawar/demo-repo",
    },
}

SUCCESSFUL_RUN_PAYLOAD = {
    "action": "completed",
    "workflow_run": {
        "id": 99999999,
        "name": "CI",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": "abc123def456789",
    },
    "repository": {
        "full_name": "MuaazTasawar/demo-repo",
    },
}

OPSGHOST_BRANCH_PAYLOAD = {
    "action": "completed",
    "workflow_run": {
        "id": 11111111,
        "name": "CI",
        "conclusion": "failure",
        "head_branch": "opsghost/fix/run-99999",
        "head_sha": "abc123def456789",
    },
    "repository": {
        "full_name": "MuaazTasawar/demo-repo",
    },
}

PING_PAYLOAD = {
    "zen": "Responsive is better than fast.",
    "hook_id": 42,
}


def _make_signature(payload: dict, secret: str = WEBHOOK_SECRET) -> str:
    body = json.dumps(payload).encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


# ── Handler unit tests ────────────────────────────────────────────────────────

class TestHandlerValidation:
    """Tests for is_actionable_run and extract_state_from_payload."""

    def test_valid_failure_payload_is_actionable(self):
        from webhook.handlers import is_actionable_run
        ok, reason = is_actionable_run(VALID_WORKFLOW_RUN_PAYLOAD)
        assert ok is True
        assert reason == ""

    def test_successful_run_not_actionable(self):
        from webhook.handlers import is_actionable_run
        ok, reason = is_actionable_run(SUCCESSFUL_RUN_PAYLOAD)
        assert ok is False
        assert "success" in reason

    def test_opsghost_branch_not_actionable(self):
        from webhook.handlers import is_actionable_run
        ok, reason = is_actionable_run(OPSGHOST_BRANCH_PAYLOAD)
        assert ok is False
        assert "opsghost" in reason.lower() or "loop" in reason.lower()

    def test_in_progress_action_not_actionable(self):
        from webhook.handlers import is_actionable_run
        payload = {**VALID_WORKFLOW_RUN_PAYLOAD, "action": "in_progress"}
        ok, reason = is_actionable_run(payload)
        assert ok is False

    def test_missing_run_id_not_actionable(self):
        from webhook.handlers import is_actionable_run
        payload = {
            "action": "completed",
            "workflow_run": {
                "name": "CI",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "abc123",
                # id intentionally missing
            },
            "repository": {"full_name": "owner/repo"},
        }
        ok, reason = is_actionable_run(payload)
        assert ok is False

    def test_missing_repo_not_actionable(self):
        from webhook.handlers import is_actionable_run
        payload = {
            "action": "completed",
            "workflow_run": {
                "id": 123,
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "abc123",
            },
            "repository": {},  # full_name missing
        }
        ok, reason = is_actionable_run(payload)
        assert ok is False

    def test_extract_state_fields(self):
        from webhook.handlers import extract_state_from_payload
        state = extract_state_from_payload(VALID_WORKFLOW_RUN_PAYLOAD)
        assert state.repo_full_name == "MuaazTasawar/demo-repo"
        assert state.workflow_run_id == 12345678
        assert state.workflow_name == "CI"
        assert state.head_branch == "main"
        assert state.head_sha == "abc123def456789"


# ── Signature verification tests ──────────────────────────────────────────────

class TestSignatureVerification:
    def test_valid_signature_accepted(self):
        from webhook.server import verify_github_signature
        payload = b'{"test": "data"}'
        secret = "mysecret"
        mac = hmac.new(secret.encode(), payload, hashlib.sha256)
        sig = f"sha256={mac.hexdigest()}"
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": secret}):
            assert verify_github_signature(payload, sig) is True

    def test_invalid_signature_rejected(self):
        from webhook.server import verify_github_signature
        payload = b'{"test": "data"}'
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "mysecret"}):
            assert verify_github_signature(payload, "sha256=deadbeef") is False

    def test_missing_signature_rejected(self):
        from webhook.server import verify_github_signature
        payload = b'{"test": "data"}'
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "mysecret"}):
            assert verify_github_signature(payload, None) is False

    def test_wrong_prefix_rejected(self):
        from webhook.server import verify_github_signature
        payload = b'{"test": "data"}'
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "mysecret"}):
            assert verify_github_signature(payload, "md5=deadbeef") is False

    def test_empty_secret_allows_all(self):
        from webhook.server import verify_github_signature
        payload = b'{"test": "data"}'
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": ""}):
            # When secret is not set, verification is skipped (dev mode)
            assert verify_github_signature(payload, None) is True


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

class TestWebhookEndpoints:
    @pytest.fixture
    def client(self):
        with patch.dict("os.environ", {
            "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "GITHUB_APP_ID": "12345",
            "GROQ_API_KEY": "test-key",
            "GITHUB_APP_PRIVATE_KEY_PATH": "nonexistent.pem",
        }):
            from webhook.server import app
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c

    def test_health_check(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "OpsGhost"

    def test_ping_event(self, client):
        body = json.dumps(PING_PAYLOAD).encode()
        sig = f"sha256={hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": "test-delivery-1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pong"

    def test_invalid_signature_returns_401(self, client):
        body = json.dumps(VALID_WORKFLOW_RUN_PAYLOAD).encode()
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": "sha256=badsignature",
                "X-GitHub-Delivery": "test-delivery-2",
            },
        )
        assert response.status_code == 401

    def test_unsupported_event_returns_200(self, client):
        body = json.dumps({"action": "created"}).encode()
        sig = f"sha256={hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": "test-delivery-3",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    @patch("webhook.handlers.asyncio.create_task")
    def test_valid_failure_event_accepted(self, mock_create_task, client):
        body = json.dumps(VALID_WORKFLOW_RUN_PAYLOAD).encode()
        sig = f"sha256={hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": "test-delivery-4",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["run_id"] == 12345678

    @patch("webhook.handlers.asyncio.create_task")
    def test_successful_run_skipped(self, mock_create_task, client):
        body = json.dumps(SUCCESSFUL_RUN_PAYLOAD).encode()
        sig = f"sha256={hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": "test-delivery-5",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        mock_create_task.assert_not_called()