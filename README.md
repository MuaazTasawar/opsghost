# OpsGhost 👻

> A self-healing CI/CD pipeline agent that reads failed GitHub Actions logs, diagnoses the root cause, and opens a fix PR — autonomously.

---

## Overview

OpsGhost is a GitHub App + AI agent system that listens for `workflow_run` failure events. When a pipeline breaks, OpsGhost fetches the raw logs, runs them through a 5-node LangGraph ReAct pipeline, classifies the failure type, selects a fix strategy, patches the offending file on a new branch, and opens a Pull Request — all without human intervention.

Built with Python, LangGraph, Groq (free tier), and the GitHub API. Deployable to Render for free.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Runtime | Python + LangGraph |
| LLM | Groq API — `llama-3.3-70b-versatile` (free tier) |
| GitHub Integration | PyGitHub + GitHub Apps + Webhooks |
| Webhook Server | FastAPI + Uvicorn |
| Log Preprocessing | Custom regex pipeline (no external deps) |
| Deployment | Render.com (free tier) |
| Testing | pytest |

---

## How It Works

```
GitHub Actions fails
        │
        ▼
OpsGhost receives webhook (workflow_run → completed → failure)
        │
        ▼
[Node 1] fetch_logs      — Downloads & preprocesses raw CI log
        │
        ▼
[Node 2] classify_failure — LLM classifies: dependency | docker | test | config | unknown
        │
        ▼
[Node 3] select_strategy  — LLM selects: bump_dependency | fix_dockerfile | fix_test_config | add_comment_only | no_action
        │
        ▼
[Node 4] execute_fix      — Creates branch, patches file, commits fix
        │
        ▼
[Node 5] open_pr          — Opens PR with diagnosis + diff
        │
        ▼
Developer reviews & merges
```

---

## Features

- **Autonomous log analysis** — cleans, preprocesses, and extracts the error root cause from raw GitHub Actions logs
- **LLM-powered classification** — categorizes failures into 5 types with confidence scoring
- **Intelligent strategy selection** — picks the safest automated fix or falls back to a diagnostic comment
- **File patching** — directly modifies `requirements.txt`, `Dockerfile`, test configs etc. via the GitHub API
- **Auto PR opening** — creates a properly described PR from a fix branch, or a diagnostic-only PR if human intervention is needed
- **Loop prevention** — ignores runs triggered by OpsGhost's own branches
- **HMAC signature verification** — validates every webhook request from GitHub
- **Graceful degradation** — every node fails safely; the agent never crashes the server
- **35+ unit tests** — covers log tools, graph routing, and webhook validation

---

## Project Structure

```
opsghost/
├── agent/
│   ├── __init__.py
│   ├── graph.py                  ← LangGraph wiring + run_agent()
│   ├── state.py                  ← OpsGhostState dataclass
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── fetch_logs.py         ← Node 1: fetch + preprocess logs
│   │   ├── classify_failure.py   ← Node 2: LLM classification
│   │   ├── select_strategy.py    ← Node 3: LLM strategy selection
│   │   ├── execute_fix.py        ← Node 4: branch + file patch + commit
│   │   └── open_pr.py            ← Node 5: PR creation
│   └── tools/
│       ├── __init__.py
│       ├── github_tools.py       ← GitHub App auth, log fetch, PR API
│       └── log_tools.py          ← Log cleaning, hint detection, preprocessing
├── webhook/
│   ├── __init__.py
│   ├── server.py                 ← FastAPI app + HMAC verification
│   └── handlers.py               ← Payload validation + background dispatch
├── prompts/
│   ├── classifier.py             ← Classifier system prompt + builder
│   ├── strategist.py             ← Strategist system prompt + builder
│   └── fixer.py                  ← Fixer system prompt + PR templates
├── demo/
│   ├── workflows/
│   │   ├── fail_dependency.yml   ← Demo: pip install broken version
│   │   ├── fail_docker.yml       ← Demo: invalid Docker base image
│   │   └── fail_test.yml         ← Demo: intentionally failing pytest
│   └── seed_failures.md          ← Supporting files for each demo scenario
├── tests/
│   ├── test_classifier.py        ← Log tool + preprocessing tests
│   ├── test_graph.py             ← State + routing logic tests
│   └── test_webhook.py           ← Server + handler + signature tests
├── .env.example
├── .gitignore
├── requirements.txt
├── render.yaml
└── README.md
```

---

## Getting Started

### Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | https://python.org |
| Git | any | https://git-scm.com |
| pip | latest | bundled with Python |
| GitHub Account | — | https://github.com |
| Groq Account (free) | — | https://console.groq.com |

---

### 1. Clone the Repo

```bash
git clone https://github.com/MuaazTasawar/opsghost.git
cd opsghost
```

---

### 2. Create a Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 4. Create a GitHub App

This is the most important setup step. OpsGhost runs as a GitHub App so it can authenticate per-repo and open PRs.

1. Go to **https://github.com/settings/apps/new**
2. Fill in:
   - **GitHub App name:** `OpsGhost` (or any unique name)
   - **Homepage URL:** `https://github.com/MuaazTasawar/opsghost`
   - **Webhook URL:** `https://your-render-url.onrender.com/webhook` *(set this after deploying — use a placeholder for now)*
   - **Webhook secret:** generate a random string and save it
3. Under **Permissions → Repository permissions**, enable:
   - **Actions:** Read-only
   - **Contents:** Read & write
   - **Pull requests:** Read & write
   - **Metadata:** Read-only
4. Under **Subscribe to events**, check: **Workflow runs**
5. Click **Create GitHub App**
6. On the app page, click **Generate a private key** → download the `.pem` file
7. Note your **App ID** (shown at the top of the app page)
8. Click **Install App** → install it on your target demo repository

---

### 5. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in every value:

```env
GITHUB_APP_ID=123456                          # from your GitHub App page
GITHUB_APP_PRIVATE_KEY_PATH=private-key.pem   # path to downloaded .pem file
GITHUB_WEBHOOK_SECRET=your-secret-here        # the secret you set in GitHub App
GROQ_API_KEY=gsk_...                          # from console.groq.com → API Keys
LLM_MODEL=llama-3.3-70b-versatile
MAX_LOG_CHARS=12000
PR_BRANCH_PREFIX=opsghost/fix
PORT=8000
ENVIRONMENT=development
```

Move your downloaded `.pem` file into the project root:
```bash
# Place private-key.pem in opsghost/ directory
```

---

### 6. Run the Server Locally

```bash
uvicorn webhook.server:app --reload --port 8000
```

You should see:
```
OpsGhost starting up...
LangGraph compiled and ready.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Visit **http://localhost:8000** → `{"status": "ok", "service": "OpsGhost"}`
Visit **http://localhost:8000/health** → config validation check

---

### 7. Expose Localhost to GitHub (for local testing)

GitHub needs a public URL to send webhooks to. Use **ngrok** (free):

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8000
```

Copy the `https://xxxx.ngrok.io` URL and paste it into your GitHub App's webhook URL field:
```
https://xxxx.ngrok.io/webhook
```

---

### 8. Run the Tests

```bash
pytest tests/ -v
```

Expected output:
```
tests/test_classifier.py::TestCleanLog::test_strips_ansi_codes PASSED
tests/test_classifier.py::TestCleanLog::test_strips_github_timestamps PASSED
...
tests/test_webhook.py::TestWebhookEndpoints::test_valid_failure_event_accepted PASSED
35 passed in 2.31s
```

---

## Triggering a Demo Failure

### Option A: Trigger via a real repo (recommended for the wow moment)

1. Create a new GitHub repo (e.g. `opsghost-demo`)
2. Make sure OpsGhost GitHub App is installed on it
3. Copy the demo files into it:

```
# Copy one of these into .github/workflows/ of your demo repo:
demo/workflows/fail_dependency.yml

# Create requirements.txt in the demo repo root:
requests==99.99.99
flask==3.0.0
```

4. Push a commit → GitHub Actions runs → fails → OpsGhost webhook fires → PR appears within 60 seconds

### Option B: Trigger manually without a real webhook

Create a small script `trigger_demo.py` in the project root:

```python
import asyncio
from dotenv import load_dotenv
from agent.state import OpsGhostState
from agent.graph import run_agent

load_dotenv()

state = OpsGhostState(
    repo_full_name="MuaazTasawar/opsghost-demo",  # your demo repo
    workflow_run_id=1234567890,                    # real run ID from a failed run
    workflow_name="CI",
    head_branch="main",
    head_sha="paste-real-commit-sha-here",
)

final = asyncio.run(run_agent(state))
print(final.to_summary_dict())
```

```bash
python trigger_demo.py
```

To get a real `workflow_run_id` and `head_sha`:
- Go to your repo → Actions tab → click a failed run
- The run ID is in the URL: `github.com/owner/repo/actions/runs/`**`1234567890`**
- The SHA is shown on the run page under the commit

---

## Deploy to Render (Free Tier)

1. Push all code to GitHub (already done if you followed the phases)
2. Go to **https://render.com** → New → Web Service
3. Connect your GitHub repo `MuaazTasawar/opsghost`
4. Render will auto-detect `render.yaml` and configure everything
5. Add environment variables in the Render dashboard (same as your `.env`)
6. For the private key: paste the entire `.pem` file content as an env var `GITHUB_APP_PRIVATE_KEY` and update `github_tools.py` to read from env instead of file, or use Render's Secret Files feature to upload the `.pem`
7. Deploy → copy the public URL → update your GitHub App's webhook URL

---

## Environment Variables Reference

| Variable | Required | Description | Where to get it |
|---|---|---|---|
| `GITHUB_APP_ID` | ✅ | Numeric ID of your GitHub App | GitHub App settings page |
| `GITHUB_APP_PRIVATE_KEY_PATH` | ✅ | Path to the `.pem` private key file | Downloaded when creating GitHub App |
| `GITHUB_WEBHOOK_SECRET` | ✅ | HMAC secret for webhook verification | Set by you in GitHub App settings |
| `GROQ_API_KEY` | ✅ | Groq API key for LLM calls | https://console.groq.com → API Keys |
| `LLM_MODEL` | ❌ | Groq model to use | Default: `llama-3.3-70b-versatile` |
| `MAX_LOG_CHARS` | ❌ | Max log characters sent to LLM | Default: `12000` |
| `PR_BRANCH_PREFIX` | ❌ | Prefix for fix branches | Default: `opsghost/fix` |
| `PORT` | ❌ | Server port | Default: `8000` |
| `ENVIRONMENT` | ❌ | `development` enables hot reload | Default: `development` |

---

## Phase Build History

| Phase | Name | What Was Built |
|---|---|---|
| 0 | Project Init & Config | `.gitignore`, `.env.example`, `requirements.txt`, `render.yaml` |
| 1 | State & Agent Skeleton | `OpsGhostState` dataclass, LangGraph skeleton, package stubs |
| 2 | GitHub & Log Tools | GitHub App auth, log fetching, PR creation, log preprocessing pipeline |
| 3 | Prompts & LLM Nodes | Classifier/strategist/fixer prompts, `fetch_logs`, `classify_failure`, `select_strategy` nodes |
| 4 | Fix Execution & PR Nodes | `execute_fix` with LLM patching, `open_pr` with fix/diagnostic branch logic |
| 5 | LangGraph Wiring | Complete graph with wrapped nodes, conditional routing, `run_agent()` entrypoint |
| 6 | Webhook Server | FastAPI app, HMAC signature verification, `workflow_run` routing, background dispatch |
| 7 | Demo Workflows & Tests | 3 seeded failure scenarios, 35+ tests across classifier/graph/webhook |

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Run tests before pushing: `pytest tests/ -v`
4. Open a PR against `main`

---

## License

MIT