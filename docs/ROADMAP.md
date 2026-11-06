# Roadmap

The project is developed in small, shippable versions. Each version has a
clear goal and a defined scope. No version is delayed to add more features.

---

## v1.0 — Core agent (✅ Shipped)

**Goal**: A working agent with strict validation, workflow caching, and
three demo tasks.

**Implemented**:

- `agent.py` with discovery and replay modes
- Multi-provider LLM support (DeepSeek, OpenAI, Anthropic, Grok)
- Three-layer validation (`validator.py`, `validator_dynamic.py`, post-check)
- HTML reduction with iframe extraction (`html_processor.py`)
- SQLite workflow cache (`workflow_manager.py`)
- Docker browser isolation (optional, `--browser container`)
- Three demo tasks (Saucedemo, Booking, Wikipedia)
- CLI (`run.py`) with `--task` presets
- Full documentation (`docs/` folder)
- One-line installation script for Linux (`scripts/install.sh`)

---

## v1.1 — Web dashboard and API (✅ Shipped)

**Goal**: Make the project visible and demoable without a terminal.

**Implemented**:

- `api.py` (FastAPI)
- `dashboard/` with HTMX + Jinja2 (no build step, no Node.js)
  - Overview page (stats, recent runs, charts)
  - Workflows list, details, and delete actions
  - Runs history
  - "Run" button (background task)
- REST API (`/api/*`)
- OpenAPI docs at `/docs` (free with FastAPI)
- Postgres backend (via `DB_BACKEND=postgres` env var)
- Live Chrome view via noVNC (`http://localhost:6080`)

**Documentation updates (done)**:
- `ARCHITECTURE.md`: ADR-002 (FastAPI) and ADR-005 (HTMX) marked as "Implemented"
- `USAGE.md`: dashboard section added
- `README.md`: dashboard section added

---

## v1.2 — Local LLM and Polish (🚧 In Progress)

**Goal**: Demonstrate graceful degradation — the agent works even with no
LLM API (or no internet connection for planning).

**Planned**:

- `llm/ollama.py` implementation (Qwen 3 or Phi-3 via local Ollama)
- `--validate-locally` flag: use a local 3B model for selector validation
  (not for planning), saving cloud API calls.
- Benchmark script: latency, memory, and agreement rate vs cloud providers.
- Complete the test suite (`tests/` for LLM providers and agent logic).
- Dashboard improvements:
  - "New task" form (launch discovery mode from the UI)
  - Theme toggle (Dark/Light)

**Documentation updates**:
- `LLM_PROVIDERS.md`: Ollama section with CPU-only benchmarks
- `ARCHITECTURE.md`: ADR-006 (local LLM for validation)
- New `docs/BENCHMARKS.md` with measured metrics (replacing the *targets*
  from the README).

---

## v1.3 — Community and Packaging (📅 Planned)

**Goal**: Make the project easy to install, run in CI, and contribute to.

**Planned**:

- Published on PyPI (`pip install llm-web-agent`)
- GitHub Actions CI (tests + `ruff` lint + `mypy` type check)
- Pre-built Docker image on Docker Hub
- `CONTRIBUTING.md` and `CHANGELOG.md`
- More demo tasks (e.g., GitHub search, structured data extraction)

---

## What is NOT planned

These are explicitly out of scope, because they either violate ToS or are
outside the project's positioning:

- **Multi-account orchestration**: no profile rotation, no proxy pool, no
  cookie isolation per user.
- **Anti-bot evasion**: no fingerprint spoofing, no Turnstile bypass.
- **Multi-session parallelism**: one browser, one task at a time.
- **Cloud deployment**: the agent is designed for a developer workstation,
  not a server cluster.

If you need any of the above, this project is the wrong tool. Consider
commercial solutions like Browserbase, Browserless, or ScrapingBee.

---

## How versions are decided

Each version is scoped to fit in **1-2 weeks of part-time work** (10-20
hours). If a feature does not fit, it moves to the next version. This
ensures steady, visible progress.

**Rule**: a version is shipped when its README section is written and
its demo works on a fresh clone. No version is "almost ready".
