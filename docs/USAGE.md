# Usage

Complete CLI reference for `llm-web-agent`.

---

## Installation

### Supported platforms

The one-line installer supports:

| Family | Distributions |
|---|---|
| Debian-based | Debian 12+, Ubuntu 22.04+, Linux Mint 21+ |
| Fedora-based | Fedora 39+, RHEL 9+, CentOS Stream 9 |
| SUSE-based   | openSUSE Leap 15.5+, openSUSE Tumbleweed |

macOS and Windows are **not** officially supported by the installer. The
agent runs on them if you install Python 3.11+, the system libraries for
Playwright, and Chrome manually — see the manual method below.

### Recommended: one-line install

```bash
git clone https://github.com/your-username/llm-web-agent.git \
  && cd llm-web-agent \
  && ./scripts/install.sh
```

What it does, in order:

1. Checks Python ≥ 3.11 and detects your distribution.
2. Creates a virtual environment in `.venv/`.
3. Installs the Python dependencies (agent and dashboard).
4. Installs the browser system libraries via `playwright install-deps`,
   with a manual fallback for each distribution family.
5. Downloads the Playwright Chromium engine.
6. Copies `key.json.example` to `key.json` (skipped if `key.json` exists).
7. Downloads the dashboard's static assets.
8. Runs a smoke test that imports every top-level module.

If any step fails, the script stops with a clear message; nothing is
half-installed.

### Manual install (any Linux, macOS, Windows)

```bash
git clone https://github.com/your-username/llm-web-agent.git
cd llm-web-agent

python3 -m venv .venv
source .venv/bin/activate            # macOS/Linux
# .venv\Scripts\activate             # Windows

pip install -r requirements.txt
pip install -r requirements-server.txt     # optional: dashboard + API

playwright install --with-deps chromium
cp key.json.example key.json
```

Edit `key.json` and set your API key (see [LLM_PROVIDERS.md](LLM_PROVIDERS.md)).

### Which browser is used?

`scripts/start_local_chrome.sh` looks for, in order:

1. `google-chrome-stable` or `google-chrome` (system install);
2. `chromium` or `chromium-browser` (distribution package);
3. the Chromium binary that Playwright downloaded.

Because Playwright's Chromium is a full Chrome-for-Testing build, it works
with the agent's CDP connection without any extra system package. **You do
not have to install Chrome separately.**

---

## Configuration

Create `key.json` from the example:

```bash
cp key.json.example key.json
```

Set `LLM_PROVIDER` and the corresponding API key:

```json
{
  "LLM_PROVIDER": "openai",
  "OPENAI_API_KEY": "sk-...",
  "OPENAI_MODEL": "gpt-4o-mini"
}
```

`key.json` is in `.gitignore`. Never commit it.

---

## Running a predefined task

```bash
python3 run.py --task saucedemo_checkout --yes
python3 run.py --task booking_search --yes --provider openai
python3 run.py --task wikipedia_extract --yes
```

Available tasks:

- `saucedemo_checkout`
- `booking_search`
- `wikipedia_extract`

See [TASKS.md](TASKS.md) for details.

---

## Running a manual task

```bash
python3 run.py \
  --url "https://example.com" \
  --goal "Find the pricing page and extract the three plan names" \
  --workflow pricing \
  --yes
```

The `--workflow` flag enables caching. Omit it to run in discovery mode
without saving.

---

## Passing variables

For tasks that fill forms, pass variables with `--var`:

```bash
python3 run.py \
  --url "https://www.saucedemo.com" \
  --goal "Login and complete checkout" \
  --workflow checkout \
  --var username=standard_user \
  --var password=secret_sauce \
  --var first_name=Test \
  --var last_name=User \
  --var postal_code=75001 \
  --yes
```

Variables are injected into `value_template` fields using Python's
`str.format()`. A step with `value_template: "{username}"` becomes
`standard_user` at runtime.

**Security**: the workflow cache stores the **template** (`{password}`),
not the value. The actual password is never written to disk.

---

## Provider and model

```bash
# Override provider and model
python3 run.py --task saucedemo_checkout \
  --provider anthropic \
  --model claude-3-5-haiku-20241022 \
  --yes
```

See [LLM_PROVIDERS.md](LLM_PROVIDERS.md).

---

## Browser mode

```bash
# Local Chrome (default, fastest)
python3 run.py --task saucedemo_checkout --browser local --yes

# Isolated Docker container (recommended for long runs)
python3 run.py --task saucedemo_checkout --browser container --yes
```

See [ISOLATION.md](ISOLATION.md).

---

## Execution options

| Flag | Default | Description |
|---|---|---|
| `--max-steps N` | 10 (or task-specific) | Maximum number of discovery steps |
| `--yes` | off | Auto-confirm all LLM API calls (no `y/n` prompt) |
| `--no-cache` | off | Ignore the workflow cache, force discovery mode |

---

## Inspecting the cache

```bash
# List all workflows
python3 -c "from workflow_manager import WorkflowManager; \
            [print(row) for row in WorkflowManager().list_workflows()]"

# List workflows for one domain
python3 -c "from workflow_manager import WorkflowManager; \
            [print(row) for row in WorkflowManager().list_workflows('https://saucedemo.com')]"

# Show recent runs
python3 -c "from workflow_manager import WorkflowManager; \
            [print(r) for r in WorkflowManager().list_runs(limit=20)]"

# Delete a workflow
python3 -c "from workflow_manager import WorkflowManager; \
            WorkflowManager().delete_workflow('https://saucedemo.com', 'checkout')"
```

---

## Dashboard (v1.1)

Once the dashboard is installed:

```bash
uvicorn api:app --reload --port 8000
```

Open [http://localhost:8000/dashboard](http://localhost:8000/dashboard).

The dashboard shows:

- Total domains, workflows, runs
- Success rate
- Cache savings (LLM calls avoided)
- Recent runs (with mode and status)
- Workflow details (steps, selectors)

The JSON API is at `/api/*` and the OpenAPI docs at `/docs`.

---

## Troubleshooting

### "Cannot connect to Chrome at 127.0.0.1:9222"

Start Chrome with remote debugging:

```bash
google-chrome --remote-debugging-port=9222
```

If using the container:

```bash
python3 -c "from browser.container_manager import BrowserContainerManager; \
            BrowserContainerManager().start()"
```

### "DEEPSEEK_API_KEY missing in key.json"

You copied `key.json.example` but forgot to fill it. Edit `key.json` and
set the API key.

### "no_match: '<selector>' does not match any element"

The LLM proposed a selector that does not exist. The validator rejected it.
Check the logs: the LLM will receive the error and try again on the next step.

If it happens repeatedly, run with `--max-steps 3` and inspect the HTML
manually to understand what the LLM is seeing.

### "Workflow found but step failed"

The cached workflow is stale. The agent will fall back to discovery mode
automatically on the next run.

To force re-discovery:

```bash
python3 run.py --task saucedemo_checkout --no-cache --yes
```

### "JSONDecodeError: Expecting value"

The LLM returned malformed JSON. The agent retries with a larger `max_tokens`.
If it happens every time, check the model name — some models don't support
JSON mode.

### Slow performance

- If using local Chrome: switch to container mode (`--browser container`).
- If using a large model (gpt-4o, claude-sonnet): switch to a smaller one
  (gpt-4o-mini, claude-haiku).
- Check cache hit rate in the logs: low cache → prompt order issue.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Task completed successfully |
| 1 | Task failed (max steps, unrecoverable error, user cancel) |
| 2 | Configuration error (missing key, unknown provider) |
