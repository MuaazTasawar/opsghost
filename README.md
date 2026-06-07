<div align="center">

# 👻 OpsGhost

### Your CI/CD pipeline's autonomous self-healing agent

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent_Runtime-FF6B35?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Webhook_Server-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Groq](https://img.shields.io/badge/Groq-LLM_Free_Tier-F55036?style=for-the-badge)](https://console.groq.com)
[![GitHub App](https://img.shields.io/badge/GitHub_App-Native_Integration-181717?style=for-the-badge&logo=github&logoColor=white)](https://docs.github.com/en/apps)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

**OpsGhost watches your GitHub Actions. When a pipeline fails, it reads the logs, finds the root cause, patches the file, and opens a Pull Request — all before you've even opened Slack.**

[How It Works](#how-it-works) · [Architecture](#architecture) · [Getting Started](#getting-started) · [Demo](#triggering-a-demo)

</div>

---

## The Problem

Engineers lose **1–3 hours per week** babysitting broken CI pipelines. A dependency version bumps to a non-existent tag, a Dockerfile base image gets deprecated, a test config is missing an env var — and suddenly someone's re-running builds, Googling cryptic error messages, and manually hunting through 800-line log files at 2AM.

Every existing DevOps tool *notifies* you that something broke. **OpsGhost fixes it.**

---

## What It Does

A GitHub Actions workflow fails. Within **60 seconds**, without any human input:

1. OpsGhost receives the failure webhook from GitHub
2. Downloads and preprocesses the raw CI log
3. An LLM agent classifies the failure type with a confidence score
4. A second LLM call selects the safest automated fix strategy
5. A third LLM call generates a precise file patch
6. OpsGhost creates a branch, commits the fix, and opens a Pull Request
7. The PR arrives with a full explanation: what broke, why, and what was changed

If the failure requires human judgment (broken test logic, API contract changes), OpsGhost opens a **diagnostic-only PR** — a structured incident report with the root cause analysis, affected files, and recommended fix — so the right person has everything they need the moment they look at their notifications.

---

## How It Works

```
 GitHub Actions run fails
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │              OpsGhost Webhook Server                │
 │   FastAPI · HMAC-verified · async background task  │
 └───────────────────┬─────────────────────────────────┘
                     │
                     ▼
 ┌─────────────────────────────────────────────────────┐
 │              LangGraph Agent Pipeline               │
 │                                                     │
 │  [1] fetch_logs        Download + preprocess log    │
 │          │                                          │
 │  [2] classify_failure  LLM → type + confidence      │
 │          │                                          │
 │  [3] select_strategy   LLM → safest fix strategy    │
 │          │                                          │
 │  [4] execute_fix       Branch + patch + commit      │
 │          │                                          │
 │  [5] open_pr           PR with diagnosis + diff     │
 └─────────────────────────────────────────────────────┘
                     │
                     ▼
       Pull Request appears on GitHub
       Developer reviews and merges
```

Every node has conditional routing — if a step fails or confidence is too low, the graph aborts safely or falls back to a diagnostic comment. No node can crash the server.

---

## Architecture

### Agent State Machine

OpsGhost uses a **typed dataclass state** (`OpsGhostState`) that flows through every LangGraph node. Each node reads from and writes to this shared state object — making the entire pipeline inspectable, testable, and debuggable at any point.

### Three Specialized LLM Calls

| Call | Role | Output |
|---|---|---|
| **Classifier** | Reads preprocessed logs, returns structured JSON with failure type + confidence | `{failure_type, failure_summary, confidence, root_cause_line}` |
| **Strategist** | Reads classification, selects safest fix strategy + risk level | `{fix_strategy, reasoning, target_files, risk_level}` |
| **Fixer** | Reads file contents + diagnosis, returns complete patched file + PR body | `{patched_content, pr_title, pr_body, diff_description}` |

All three prompts enforce **JSON-only output** with strict schemas — no markdown, no prose, no hallucinated file paths.

### Failure Classification

| Type | Examples |
|---|---|
| `dependency` | pip/npm package not found, version conflict, peer dep missing |
| `docker` | bad base image tag, missing COPY source, failed RUN step |
| `test` | assertion error, missing env var in test, test runner crash |
| `config` | missing env var, bad YAML syntax, wrong file path |
| `unknown` | ambiguous logs, transient network errors, GitHub outages |

### Fix Strategies

| Strategy | What It Does |
|---|---|
| `bump_dependency` | Patches `requirements.txt`, `package.json`, `go.mod`, etc. |
| `fix_dockerfile` | Patches `Dockerfile` or `docker-compose.yml` |
| `fix_test_config` | Patches `pytest.ini`, `jest.config.js`, `.env.test`, etc. |
| `add_comment_only` | Opens a diagnostic PR — no code changes, human fix required |
| `no_action` | Aborts silently — transient failure or confidence too low |

### Log Preprocessing Pipeline

Before a single token reaches the LLM, OpsGhost runs the raw log through a custom preprocessing pipeline:

- Strips ANSI escape codes and GitHub Actions timestamps
- Extracts the most relevant error section (first error, not cascading noise)
- Runs 20+ regex patterns to detect known error signatures
- Formats detected hints as structured evidence for the classifier prompt
- Truncates from the start (not the end) — failures are always at the bottom

This dramatically reduces hallucination risk and token usage.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Agent Runtime | Python + LangGraph | Best-in-class ReAct loop, typed state, conditional edges |
| LLM | Groq — `llama-3.3-70b-versatile` | Free tier, ~2s inference, structured output |
| GitHub Integration | PyGitHub + GitHub Apps | Per-repo auth, branch creation, file patching, PR API |
| Webhook Server | FastAPI + Uvicorn | Async, fast, HMAC-verified, background task dispatch |
| Log Preprocessing | Custom regex pipeline | Zero LLM cost for signal extraction |
| Deployment | Render / Railway / ngrok | Free tier deployable |
| Testing | pytest | 35+ tests across all layers |

---

## Key Engineering Decisions

**Why LangGraph over a simple LLM call?**
A single prompt can't safely classify, strategize, and patch in one shot without hallucinating. LangGraph's node-based graph enforces separation of concerns, lets each step validate its output independently, and makes the pipeline observable and testable at every stage.

**Why three separate LLM calls instead of one?**
Each call has a different temperature, token budget, and output schema. The classifier needs low temperature for consistency. The fixer needs a large token budget for complete file output. Combining them into one call produces worse results at higher cost.

**Why GitHub Apps instead of a personal access token?**
GitHub Apps authenticate per-installation, not per-user. This means OpsGhost can be installed on any repo by anyone, has scoped permissions, and doesn't break when someone rotates their PAT.

**Why preprocess logs before the LLM?**
Raw CI logs contain thousands of lines of timestamps, ANSI codes, and cascading error noise. Sending raw logs to an LLM wastes tokens, hits context limits, and produces worse classification. The preprocessing pipeline reduces a 50,000-character log to the 2,000 most diagnostic characters.

---

## Features

- Autonomous end-to-end pipeline — webhook in, Pull Request out, zero human steps
- Typed LangGraph state machine with 5 specialized nodes
- Three purpose-built LLM prompts with strict JSON output schemas
- Custom log preprocessing pipeline — ANSI stripping, error extraction, hint detection
- GitHub App authentication — per-repo installation, scoped permissions
- HMAC-SHA256 webhook signature verification on every request
- Conditional graph routing — safe abort at every node on failure
- Loop prevention — ignores runs triggered by OpsGhost's own fix branches
- Graceful degradation — every node fails safely, server never crashes
- Diagnostic-only PR fallback when auto-fix isn't safe
- 35+ unit tests covering log tools, graph routing, and webhook validation
- Render-ready `render.yaml` for one-click free deployment

---

## Project Structure

```
opsghost/
├── agent/
│   ├── graph.py                  ← LangGraph wiring + run_agent() entrypoint
│   ├── state.py                  ← OpsGhostState typed dataclass
│   ├── nodes/
│   │   ├── fetch_logs.py         ← Node 1: GitHub log download + preprocessing
│   │   ├── classify_failure.py   ← Node 2: LLM failure classification
│   │   ├── select_strategy.py    ← Node 3: LLM strategy selection
│   │   ├── execute_fix.py        ← Node 4: branch creation + file patch + commit
│   │   └── open_pr.py            ← Node 5: PR creation (fix or diagnostic)
│   └── tools/
│       ├── github_tools.py       ← GitHub App auth, log fetch, file ops, PR API
│       └── log_tools.py          ← Log cleaning, hint detection, error extraction
├── webhook/
│   ├── server.py                 ← FastAPI app + HMAC verification + routing
│   └── handlers.py               ← Payload validation + background task dispatch
├── prompts/
│   ├── classifier.py             ← Classifier system prompt + user prompt builder
│   ├── strategist.py             ← Strategist system prompt + user prompt builder
│   └── fixer.py                  ← Fixer system prompt + PR body templates
├── demo/
│   ├── workflows/
│   │   ├── fail_dependency.yml   ← Installs requests==99.99.99 (guaranteed fail)
│   │   ├── fail_docker.yml       ← Uses python:3.11.999-slim (does not exist)
│   │   └── fail_test.yml         ← Runs pytest with intentional assertion failure
│   └── seed_failures.md          ← All supporting files for each demo scenario
├── tests/
│   ├── test_classifier.py        ← 18 tests: log tools + preprocessing pipeline
│   ├── test_graph.py             ← 12 tests: state dataclass + routing functions
│   └── test_webhook.py           ← 10 tests: server endpoints + signature verification
├── .env.example
├── requirements.txt
└── render.yaml
```

---

## Getting Started

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| Git | any |
| GitHub Account | — |
| Groq Account (free) | https://console.groq.com |

### Clone & Install

```bash
git clone https://github.com/MuaazTasawar/opsghost.git
cd opsghost
python -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Fill in GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH,
# GITHUB_WEBHOOK_SECRET, and GROQ_API_KEY
```

### Run

```bash
uvicorn webhook.server:app --reload --port 8000
```

### Test

```bash
pytest tests/ -v
# 35 passed in ~2s
```

Full setup guide (GitHub App creation, webhook wiring, deployment) is in the [Getting Started](#getting-started) section below.

---

## Triggering a Demo

Three pre-seeded failure scenarios are included in `demo/workflows/`:

| Scenario | File | What Breaks | Expected OpsGhost Action |
|---|---|---|---|
| Dependency | `fail_dependency.yml` | `pip install requests==99.99.99` | Patches `requirements.txt`, opens fix PR |
| Docker | `fail_docker.yml` | `FROM python:3.11.999-slim` | Patches `Dockerfile`, opens fix PR |
| Test | `fail_test.yml` | `assert 2+2 == 5` | Opens diagnostic PR with root cause |

Copy any of these into `.github/workflows/` of a target repo, push a commit, and watch OpsGhost respond.

See `demo/seed_failures.md` for all supporting files.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_APP_ID` | ✅ | Numeric ID of your GitHub App |
| `GITHUB_APP_PRIVATE_KEY_PATH` | ✅ | Path to the downloaded `.pem` private key |
| `GITHUB_WEBHOOK_SECRET` | ✅ | HMAC secret set in GitHub App settings |
| `GROQ_API_KEY` | ✅ | From https://console.groq.com → API Keys |
| `LLM_MODEL` | ❌ | Default: `llama-3.3-70b-versatile` |
| `MAX_LOG_CHARS` | ❌ | Default: `12000` |
| `PR_BRANCH_PREFIX` | ❌ | Default: `opsghost/fix` |
| `ENVIRONMENT` | ❌ | `development` enables hot reload |

---

## Build History

| Phase | What Was Built |
|---|---|
| 0 — Init | Project scaffold, env config, Render deployment config |
| 1 — Skeleton | `OpsGhostState` typed dataclass, LangGraph graph builder, package structure |
| 2 — Tools | GitHub App auth, log fetching, PR creation, log preprocessing pipeline |
| 3 — LLM Nodes | Classifier + strategist + fixer prompts, `fetch_logs`, `classify_failure`, `select_strategy` |
| 4 — Fix Nodes | `execute_fix` with LLM patching + branch ops, `open_pr` with fix/diagnostic routing |
| 5 — Graph | Complete LangGraph wiring, conditional edges, node observability wrappers, `run_agent()` |
| 6 — Server | FastAPI webhook server, HMAC verification, background task dispatch |
| 7 — Tests | 3 demo failure scenarios, 35+ unit tests across all layers |

---

## License

MIT — use it, fork it, build on it.

---

<div align="center">

Built by [Muaaz Tasawar](https://github.com/MuaazTasawar)

*OpsGhost doesn't page you. It fixes it.*

</div>