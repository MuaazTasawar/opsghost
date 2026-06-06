# OpsGhost Demo Seed Files

This document contains the supporting files needed to trigger each
demo workflow failure scenario. Copy these into your target test
repository alongside the workflow files.

---

## Scenario 1: Dependency Failure

**Workflow:** `fail_dependency.yml`
**Trigger:** Push to main

No extra files needed — the workflow installs `requests==99.99.99`
which does not exist on PyPI. This reliably triggers a pip dependency
error that OpsGhost will classify and attempt to fix by patching
`requirements.txt`.

**Create this file in your target repo:**

`requirements.txt`
requests==99.99.99
flask==3.0.0
python-dotenv==1.0.1
**Expected OpsGhost behavior:**
- Failure type: `dependency`
- Strategy: `bump_dependency`
- Fix: bumps `requests` to a valid version (e.g. `2.31.0`)
- Opens PR: `fix(dependency): bump requests from 99.99.99 to valid version`

---

## Scenario 2: Docker Failure

**Workflow:** `fail_docker.yml`
**Trigger:** Push to main

**Create this file in your target repo:**

`Dockerfile`
```dockerfile
# Intentionally broken — python:3.11.999-slim does not exist
FROM python:3.11.999-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]
```

**Expected OpsGhost behavior:**
- Failure type: `docker`
- Strategy: `fix_dockerfile`
- Fix: replaces `3.11.999-slim` with `3.11-slim`
- Opens PR: `fix(docker): correct Python base image tag in Dockerfile`

---

## Scenario 3: Test Failure

**Workflow:** `fail_test.yml`
**Trigger:** Push to main

**Create this file in your target repo:**

`tests/test_demo_fail.py`
```python
"""
Intentionally failing test for OpsGhost demo.
The assertion is wrong — 2 + 2 is not 5.
OpsGhost should detect this as a test failure and open a diagnostic PR.
"""


def test_basic_math():
    """This test will always fail."""
    result = 2 + 2
    assert result == 5, f"Expected 5 but got {result}"


def test_string_ops():
    """This test passes — showing OpsGhost identifies the specific failure."""
    greeting = "hello"
    assert greeting.upper() == "HELLO"


def test_missing_env_var():
    """
    This test fails if DATABASE_URL env var is not set.
    Simulates a config-type test failure.
    """
    import os
    db_url = os.getenv("DATABASE_URL")
    assert db_url is not None, "DATABASE_URL environment variable is not set"
```

**Expected OpsGhost behavior:**
- Failure type: `test`
- Strategy: `add_comment_only` (logic error, not config)
- Opens diagnostic PR explaining which tests failed and why

---

## Quick Setup Checklist

Before running any demo scenario:

- [ ] GitHub App created at https://github.com/settings/apps/new
- [ ] Webhook URL set to your Render deployment + `/webhook`
- [ ] `workflow_run` event enabled on the GitHub App
- [ ] App installed on your target demo repository
- [ ] `.env` populated with all values from `.env.example`
- [ ] `private-key.pem` downloaded and placed in project root
- [ ] OpsGhost deployed to Render (free tier)

## Local Testing (no Render needed)

You can trigger the agent manually without a real GitHub webhook:

```python
import asyncio
from agent.state import OpsGhostState
from agent.graph import run_agent

state = OpsGhostState(
    repo_full_name="MuaazTasawar/your-demo-repo",
    workflow_run_id=1234567890,   # real run ID from a failed workflow
    workflow_name="CI",
    head_branch="main",
    head_sha="abc123def456",      # real commit SHA
)

final = asyncio.run(run_agent(state))
print(final.to_summary_dict())
```