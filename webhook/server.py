"""
OpsGhost Webhook Server
FastAPI application that:
  1. Receives GitHub webhook POST requests
  2. Validates the HMAC-SHA256 signature
  3. Routes events to the appropriate handler
  4. Returns 200 immediately so GitHub doesn't retry

Run locally:
    uvicorn webhook.server:app --reload --port 8000

GitHub webhook setup:
  - Payload URL: https://<your-render-url>/webhook
  - Content type: application/json
  - Secret: value of GITHUB_WEBHOOK_SECRET in .env
  - Events: Workflow runs (workflow_run)
"""

import os
import hmac
import hashlib
import logging
import logging.config
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from webhook.handlers import handle_workflow_run
from agent.graph import get_graph

load_dotenv()

# ── Logging configuration ─────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "level": LOG_LEVEL,
        "handlers": ["console"],
    },
})

logger = logging.getLogger(__name__)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Warms up the LangGraph on startup so the first webhook
    isn't slow due to graph compilation.
    """
    logger.info("OpsGhost starting up...")
    try:
        get_graph()
        logger.info("LangGraph compiled and ready.")
    except Exception as e:
        logger.error(f"Failed to compile graph on startup: {e}")
        # Don't crash — the graph will be compiled on first request instead
    yield
    logger.info("OpsGhost shutting down.")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="OpsGhost",
    description="Autonomous CI/CD failure remediation agent",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Signature verification ────────────────────────────────────────────────────

def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """
    Verifies the GitHub webhook HMAC-SHA256 signature.
    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>

    Returns True if valid, False otherwise.
    Constant-time comparison prevents timing attacks.
    """
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET is not set. "
            "Skipping signature verification (insecure — set in production)."
        )
        return True  # Allow in dev; enforce in prod via env

    if not signature_header:
        logger.warning("Request missing X-Hub-Signature-256 header.")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning(f"Unexpected signature format: {signature_header[:20]}")
        return False

    expected_sig = signature_header[7:]  # strip "sha256="
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    )
    computed_sig = mac.hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def health_check():
    """Health check endpoint for Render and uptime monitors."""
    return {
        "status": "ok",
        "service": "OpsGhost",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():
    """Detailed health check — verifies env config is present."""
    issues = []

    if not os.getenv("GITHUB_APP_ID"):
        issues.append("GITHUB_APP_ID not set")
    if not os.getenv("GROQ_API_KEY"):
        issues.append("GROQ_API_KEY not set")
    if not os.getenv("GITHUB_WEBHOOK_SECRET"):
        issues.append("GITHUB_WEBHOOK_SECRET not set (webhook unprotected)")

    private_key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "private-key.pem")
    if not os.path.exists(private_key_path):
        issues.append(f"Private key not found at '{private_key_path}'")

    if issues:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "issues": issues,
            },
        )

    return {"status": "healthy", "issues": []}


@app.post("/webhook")
async def github_webhook(request: Request):
    """
    Main webhook endpoint.

    GitHub sends POST requests here for every subscribed event.
    We:
      1. Read the raw body (needed for HMAC verification)
      2. Verify the signature
      3. Parse JSON
      4. Route to the correct handler based on X-GitHub-Event header
      5. Return 200 immediately
    """
    # Read raw body before parsing so we can verify signature
    payload_bytes = await request.body()

    # Verify HMAC signature
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(payload_bytes, signature):
        logger.warning(
            f"[webhook] Invalid signature from {request.client.host if request.client else 'unknown'}. "
            "Rejecting request."
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception:
        logger.error("[webhook] Failed to parse JSON payload.")
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

    logger.info(
        f"[webhook] Received event: {event_type} | "
        f"delivery: {delivery_id}"
    )

    # ── Route by event type ───────────────────────────────────────
    if event_type == "workflow_run":
        response_data = await handle_workflow_run(payload)
        return JSONResponse(
            status_code=200,
            content={
                "delivery_id": delivery_id,
                "event": event_type,
                **response_data,
            },
        )

    elif event_type == "ping":
        # GitHub sends a ping when you first configure the webhook
        zen = payload.get("zen", "")
        hook_id = payload.get("hook_id", "")
        logger.info(f"[webhook] Ping received. hook_id={hook_id}, zen='{zen}'")
        return JSONResponse(
            status_code=200,
            content={
                "status": "pong",
                "hook_id": hook_id,
                "message": "OpsGhost webhook configured successfully.",
            },
        )

    else:
        # Acknowledge but ignore unsupported events
        logger.info(f"[webhook] Unsupported event type '{event_type}' — acknowledged.")
        return JSONResponse(
            status_code=200,
            content={
                "status": "ignored",
                "event": event_type,
                "message": f"OpsGhost does not handle '{event_type}' events.",
            },
        )


# ── Dev server entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("ENVIRONMENT", "development") == "development"

    logger.info(f"Starting OpsGhost on port {port} (reload={reload})")
    uvicorn.run(
        "webhook.server:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level=LOG_LEVEL.lower(),
    )