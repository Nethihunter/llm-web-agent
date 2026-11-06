# Roadmap

The project is developed in small, shippable versions. Each version has a
clear goal and a defined scope. No version is delayed to add more features.

---

## v1.0 — Core agent (current)

**Status**: in development.

**Goal**: a working agent with strict validation, workflow caching, and
three demo tasks.

**Included**:

- `agent.py` with discovery and replay modes
- Multi-provider LLM support (DeepSeek, OpenAI, Anthropic, Grok)
- Three-layer validation
- HTML reduction with iframe extraction
- SQLite workflow cache
- Docker browser isolation (optional)
- Three demo tasks (Saucedemo, Booking, Wikipedia)
- CLI (`run.py`) with `--task` presets
- Full documentation (this folder)
- Benchmark run on the demo tasks, to replace the *target* metrics in the
  README with measured values

**Not included**:

- Web dashboard
- REST API
- Postgres backend
- Local LLM (Phi-3)
- Tests beyond the essential

---

## v1.1 — Web dashboard and API (2 weeks after v1.0)

**Goal**: make the project visible and demoable without a terminal.

**Added**:

- `api.py` (FastAPI)
- `dashboard/` with HTMX + Jinja2
  - Overview page (stats, recent runs)
  - Workflows list
  - Workflow details (steps, selectors)
  - Runs history
  - "Run" button (background task)
  - "Delete" button
- REST API (`/api/*`)
- OpenAPI docs at `/docs` (free with FastAPI)
- Postgres backend (`--backend postgres`)
- Migration script (`migrate_sqlite_to_pg.py`)
- Screenshot in README

**Documentation updates**:

- `ARCHITECTURE.md`: ADR-002 (FastAPI) and ADR-005 (HTMX) become "Implemented"
- `USAGE.md`: dashboard section
- New `README.md` section: "Dashboard"

---

## v1.2 — Local LLM and polish (4 weeks after v1.1)

**Goal**: demonstrate graceful degradation — the agent works even with no
LLM API.

**Added**:

- `local_llm.py` (Phi-3 Mini Q4 via `llama-cpp-python`)
- `--validate-locally` flag: use the local 3B model for selector validation
  (not for planning)
- Benchmark script: latency, memory, agreement rate vs cloud providers
- Tests: full unit test suite (validators, workflow manager, HTML processor)
- Dashboard improvements:
  - "New task" form (launch discovery mode from the UI)
  - Stats charts (success rate over time, cost over time)
  - Dark/light theme toggle

**Documentation updates**:

- `LLM_PROVIDERS.md`: Ollama section with CPU-only benchmarks
- `ARCHITECTURE.md`: ADR-006 (local LLM for validation)
- New `docs/BENCHMARKS.md`

---

## v1.3 — Community and packaging (6 weeks after v1.2)

**Goal**: make the project easy to install and contribute to.

**Added**:

- Published on PyPI (`pip install llm-web-agent`)
- GitHub Actions CI (tests + lint + type check)
- Docker image on Docker Hub
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- More tasks (example: LinkedIn profile extraction, GitHub repo search)

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
