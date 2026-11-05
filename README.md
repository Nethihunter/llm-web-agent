# LLM Web Agent

> **A supervised LLM-driven browser agent with strict validation, cost control, and a persistent workflow cache.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: in development](https://img.shields.io/badge/status-in%20development-orange.svg)](docs/ROADMAP.md)

> 🚧 **Work in progress.** Documentation comes first and describes the target
> design. Implementation is in progress. Figures marked as *targets* will be
> replaced by measured values once the benchmarks are run.

---

## The Problem

Browser automation with LLMs is expensive and unreliable in production:

- **Expensive**: sending raw HTML to an LLM costs $0.05+ per step.
- **Unreliable**: LLMs hallucinate CSS selectors with high confidence.
- **Brittle**: every site redesign breaks the automation.
- **Unverifiable**: you only know it failed *after* the fact.

Most "LLM agents" demos ignore these problems. They send the full HTML,
trust the output, and hope for the best. That works for a demo. It does not
work when you need to run the same task 100 times.

This project takes the opposite approach: **every step is validated, every
byte is accounted for, and the second run of a task costs $0.**

---

## The Solution

Four design decisions separate this agent from a typical LLM wrapper:

### 1. HTML Reduction (96% cost savings)

The agent never sends raw HTML to the LLM. Before each call, `html_processor.py`:

- Removes scripts, styles, SVG, iframes' external content
- Keeps only interactive elements (buttons, inputs, links, forms)
- Compacts attributes (strips Tailwind noise from CSS classes)
- Reduces a typical 42 KB page to ~1.5 KB

The LLM sees a clean, structured view of the page. The target is a cost
reduction of about **96%**.
The cleaned HTML is also *easier* for the LLM to reason about.

### 2. Three-Layer Validation

LLMs are "confidently wrong" about CSS selectors. A model may propose
`[data-testid='submit']` with 0.9 confidence, when the attribute does not
exist. Clicking it silently fails.

Before any action is executed, the selector passes three checks:

1. **Static validation** (`validator.py`): does the selector match exactly
   one element in the DOM? BeautifulSoup, no browser needed.
2. **Dynamic validation** (`validator_dynamic.py`): does the real Playwright
   engine agree? Handles `:has-text()` and other Playwright-only selectors.
3. **Post-execution check**: did the action actually change the page?
   URL or DOM signature changed? If not, the step is marked as failed.

Failed validations are sent back to the LLM with a hint about what to fix.

### 3. Persistent Workflow Cache

When the agent successfully completes a task, it saves the workflow
(sequence of actions) to a per-domain SQLite database.

The next time the same task is requested on the same domain:

- The agent **skips the LLM entirely**.
- It replays the cached steps.
- Cost: **$0**.
- Time: **~5 seconds** instead of ~30.

If a cached step fails (site changed, selector broken), the agent
automatically falls back to LLM discovery and re-caches the workflow.

### 4. Multi-Provider, Multi-Task, Multi-Browser

- **LLM providers**: DeepSeek (default), OpenAI, Anthropic, Grok, Ollama.
  Each is 30-60 lines behind a common interface.
- **Tasks**: three demo scenarios covering different automation categories
  (transactions, research, extraction).
- **Browsers**: local Chrome for development, isolated Docker container
  for production (protects the host machine from Chrome's memory usage).

---

## Target Metrics

Targets for the three demo tasks (`saucedemo_checkout`, `booking_search`,
`wikipedia_extract`). These are design goals, **not yet measured results**:

| Metric | Naive baseline (estimate) | Target |
|---|---|---|
| Tokens per task (first run) | ~50,000 | ~3,000 |
| Cost per task (first run) | ~$0.02 | ~$0.001 |
| Cost per task (cached) | ~$0.02 | **$0** |
| Selector hallucination rate | ~40% | **< 2%** |
| Time per cached run | ~30 s | **~5 s** |

The baseline column is an estimate for a naive implementation that sends raw
HTML and trusts the LLM. The target column is what this project aims for.
A benchmark script will replace both with measured values (see
[ROADMAP](docs/ROADMAP.md)).

---

## Demo Tasks

Three tasks, three categories of real-world automation:

```bash
# 1. ✅ Test interactif — demande confirmation à chaque appel
python3 run.py --task saucedemo_checkout

# 1.a ⚡ Test automation — ne demande rien (CI, tests en boucle)
python3 run.py --task saucedemo_checkout --yes

# 2. Research — Booking.com search (read-only)
python3 run.py --task booking_search --yes --provider openai

# 3. Extraction — Wikipedia article (CC-BY-SA)
python3 run.py --task wikipedia_extract --yes
```

| Task | Category | What it proves | Site |
|---|---|---|---|
| `saucedemo_checkout` | Transactions | Multi-step forms, login, caching | [saucedemo.com](https://www.saucedemo.com) |
| `booking_search` | Research | Complex forms, structured extraction | [booking.com](https://www.booking.com) |
| `wikipedia_extract` | Extraction | Heavy DOM, targeted parsing | [en.wikipedia.org](https://en.wikipedia.org) |

Each site was chosen for its low legal risk:

- **Saucedemo**: official Sauce Labs testing site, made for automation.
- **Booking**: read-only search, no reservation, no account creation.
  Always check the site's Terms of Service and `robots.txt` before running
  it, and replace this task if automated access is not allowed.
- **Wikipedia**: CC-BY-SA content. No login. Attribution required.

See [docs/TASKS.md](docs/TASKS.md) for the full rationale and expected output
of each task.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  run.py --task saucedemo_checkout  OR  --url + --goal        │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  agent.py                                                    │
│  ├─ Mode 1: _execute_workflow()  (cache hit → $0)           │
│  └─ Mode 2: _discover_and_run()  (LLM → cache miss)         │
└──────┬────────┬────────┬────────┬────────┬────────┬──────────┘
       │        │        │        │        │        │
       ▼        ▼        ▼        ▼        ▼        ▼
   llm/*    validator  html_    tab_    browser/  workflow_
   (multi)  + dynamic  processor manager  (CDP)    manager
       │                  │        │                  │
       ▼                  ▼        ▼                  ▼
   DeepSeek /          Beautiful  Playwright       SQLite
   OpenAI /            Soup +     + popup          (or Postgres
   Anthropic /         iframes    handling         in v1.1)
   Grok / Ollama
```

The full state machine is documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Design Decisions

Every major choice is documented as an ADR (Architecture Decision Record)
in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Summary:

| Decision | Choice | Why |
|---|---|---|
| Database | SQLite by default, Postgres optional | Single-user CLI → zero setup. Postgres is 50 lines away if needed. |
| API framework | FastAPI (v1.1) | Native async for Playwright. Pydantic validation. Free OpenAPI docs. |
| UI framework | HTMX + Jinja2 (v1.1) | 30 KB of JS, no build step. React would add 500 MB of node_modules for a CRUD page. |
| Playwright mode | Sync, not async | Simpler. The agent runs one task at a time. |
| Local LLM | Phi-3 Mini Q4 (v1.2) | Only for selector validation. On CPU, ~5 tokens/sec is enough for 20-token prompts. |
| Browser | Local or Docker | Docker protects the host from Chrome's memory usage. See [docs/ISOLATION.md](docs/ISOLATION.md). |

---

## Stack

- **Python 3.11+**
- **Playwright** (sync API, CDP connection)
- **BeautifulSoup4** (static HTML validation)
- **SQLite** (workflow cache) / Postgres (optional, v1.1)
- **FastAPI** (API + dashboard, v1.1)
- **DeepSeek / OpenAI / Anthropic / Grok / Ollama** (LLM providers)

No GPU required. Runs on a 2018 i5 with 20 GB of RAM.

---

## Quick Start

### 1. ### One-line installation (Linux)

Tested on **Debian**, **Ubuntu**, **Linux Mint**, **Fedora**, and
**openSUSE** (Leap and Tumbleweed):

```bash
git clone https://github.com/your-username/llm-web-agent.git \
  && cd llm-web-agent \
  && ./scripts/install.sh
```

The installer:

- detects your distribution and installs the libraries the browser needs;
- creates a Python virtual environment in `.venv/`;
- installs the Python packages and the Playwright Chromium engine;
- downloads the dashboard's static assets;
- copies `key.json.example` to `key.json` so you can fill it in.

The only manual step left is your LLM API key (step 2 below).

> **Manual installation** (any distro, or if you prefer to control every step):
>
> ```bash
> python3 -m venv .venv && source .venv/bin/activate
> pip install -r requirements.txt -r requirements-server.txt
> playwright install --with-deps chromium
> cp key.json.example key.json     # then edit it
> ```


### 2. Configure your LLM provider

```bash
cp key.json.example key.json
# Edit key.json: set LLM_PROVIDER and the matching API key
```

Example (DeepSeek — cheapest; check your data-residency and compliance
requirements before using it in the EU):

```json
{
  "LLM_PROVIDER": "deepseek",
  "DEEPSEEK_API_KEY": "sk-..."
}
```

Example (OpenAI):

```json
{
  "LLM_PROVIDER": "openai",
  "OPENAI_API_KEY": "sk-...",
  "OPENAI_MODEL": "gpt-4o-mini"
}
```

See [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) for the full comparison.

### 3. Start Chrome

**Option A — Local Chrome** (fastest for development):

```bash
google-chrome --remote-debugging-port=9222
```

**Option B — Isolated Docker container** (recommended for long runs):

```bash
python3 -c "from browser.container_manager import BrowserContainerManager; \
            BrowserContainerManager().build_image() and \
            BrowserContainerManager().start()"
```

See [docs/ISOLATION.md](docs/ISOLATION.md) for why and when to use the container.

### 4. Run a demo task

```bash
# First run: discovery mode (uses LLM, ~$0.001)
python3 run.py --task saucedemo_checkout --yes

# Second run: replay mode (cached, $0)
python3 run.py --task saucedemo_checkout --yes
```

The second run should complete in a few seconds with **zero LLM calls**.

---

## CLI Reference

Full CLI reference in [docs/USAGE.md](docs/USAGE.md). Common commands:

```bash
# Run a predefined task
python3 run.py --task saucedemo_checkout --yes

# Run a manual task
python3 run.py --url "https://example.com" --goal "Find the pricing page" --yes

# Use a specific LLM provider
python3 run.py --task booking_search --provider anthropic \
  --model claude-3-5-haiku-20241022 --yes

# Use the isolated Docker browser
python3 run.py --task wikipedia_extract --browser container --yes

# List all cached workflows
python3 -c "from workflow_manager import WorkflowManager; \
            print(WorkflowManager().list_workflows())"
```

---

## Limitations (honest)

- **No multi-account support.** This is a single-user, single-browser tool.
  It does not rotate profiles, cookies, or proxies.
- **No anti-bot evasion.** The agent uses standard Playwright. Sites with
  advanced bot detection (Cloudflare Turnstile, hCaptcha) will block it.
- **No parallel execution.** One task at a time. The Playwright sync API is
  blocking by design.
- **Success detection is heuristic.** It relies on either an LLM judgment
  or a user-defined `success_indicator` selector. It is not 100% reliable
  on ambiguous pages.
- **Cached workflows can become stale.** If a site changes, the cached
  workflow fails and the agent re-discovers it. This is intentional
  (safe failure, no silent corruption).

---

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 4 pillars + 5 ADRs
- [AI_ASSISTED.md](docs/AI_ASSISTED.md) — 6 LLM failure modes and mitigations
- [TASKS.md](docs/TASKS.md) — the 3 demo tasks in detail
- [LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) — provider comparison
- [ISOLATION.md](docs/ISOLATION.md) — why Chrome runs in Docker
- [USAGE.md](docs/USAGE.md) — full CLI + troubleshooting
- [ROADMAP.md](docs/ROADMAP.md) — v1.1 and v1.2 plan

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

**Tompoherilala Andriamanamisaina**

Self-taught software engineer, 14+ years of freelance experience.
Python · JavaScript · Linux · AI-assisted development.

- LinkedIn: [tompoheriala-andriamanamisaina-10316b192](https://www.linkedin.com/in/tompoheriala-andriamanamisaina-10316b192/)
- Email: herysyfabay@gmail.com
