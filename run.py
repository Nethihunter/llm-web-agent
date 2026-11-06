"""CLI entry point for llm-web-agent.

Two ways to run a task:

- ``--task <name>``: a predefined demo scenario (URL, goal, variables and
  ``max_steps`` come from ``tasks/<name>.py``). The workflow cache key is
  the task's ``name`` field, so the second run replays it.
- ``--url <url> --goal <text>``: a manual task. Add ``--workflow <name>``
  to enable caching; without it, the run is always in discovery mode and
  nothing is saved.

Flags override the task defaults. ``--var key=value`` takes precedence
over the variables declared in the task module.

Exit codes (documented in ``docs/USAGE.md``):

- ``0``  task completed
- ``1``  task failed (max steps, browser error, unrecoverable LLM error)
- ``2``  configuration error (missing key, unknown provider)
- ``130`` interrupted by the user (Ctrl-C, 128 + SIGINT)

Set ``DEBUG=1`` to re-raise the original exception with a full traceback
instead of the short ``TypeError: ...`` message. ``DEBUG=0``, ``DEBUG=``
and ``DEBUG=false`` are all treated as "off".
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from agent import WebAgent
from dashboard.helpers import TASK_MODULES
from llm.base import LLMConfigError

# Truthy values of DEBUG. Anything else (including "0", "false", "", "no")
# disables the traceback. This avoids the classic bug where DEBUG=0 in a
# shell export would still enable debug mode ("0" is a non-empty string).
_DEBUG_TRUTHY = {"1", "true", "yes", "on"}


def _debug_enabled() -> bool:
    """Return True only for explicit truthy values of ``$DEBUG``."""
    return os.environ.get("DEBUG", "").strip().lower() in _DEBUG_TRUTHY


def load_task(name: str) -> dict:
    """Import ``tasks.<name>`` and return its ``TASK`` dict.

    Raises:
        SystemExit(1): if the module does not exist, if it exists but does
            not expose a ``TASK`` dict, or if importing it raised for any
            other reason (broken task file). The message distinguishes
            "unknown task" from "this task is broken", so a contributor
            who adds a file does not think it was silently ignored.
    """
    module_path = f"tasks.{name}"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        # ``e.name`` is the module that could not be found. If it is the
        # task module itself, the name is wrong. If it is something the
        # task imports, the task exists but is broken — report that.
        if e.name == module_path:
            raise SystemExit(
                f"Unknown task: '{name}'. Available: {', '.join(TASK_MODULES)}"
            ) from e
        raise SystemExit(
            f"Task '{name}' exists but failed to import: {e}"
        ) from e
    except ImportError as e:
        raise SystemExit(f"Task '{name}' has a broken import: {e}") from e

    task = getattr(module, "TASK", None)
    if not isinstance(task, dict):
        raise SystemExit(
            f"Task '{name}' does not define a TASK dict "
            f"(found: {type(task).__name__})."
        )
    return task


def parse_vars(pairs: list[str], parser: argparse.ArgumentParser) -> dict:
    """Parse repeated ``--var key=value`` pairs into a dict.

    Empty values are allowed (``--var password=``), because some tasks
    legitimately need to clear a field. Duplicate keys: the last one wins,
    which matches argparse's own behavior for repeated flags.

    Calls ``parser.error`` (which exits with code 2) on a malformed pair,
    rather than raising: argparse is the tool that formats CLI errors.
    """
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            parser.error(f"--var expects key=value, got: {pair!r}")
        k, v = pair.split("=", 1)
        k = k.strip()
        if not k:
            parser.error(f"--var expects a non-empty key, got: {pair!r}")
        out[k] = v.strip()
    return out


def _positive_int(value: str) -> int:
    """argparse ``type=`` for ``--max-steps``: integer, >= 1."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LLM-driven browser automation with workflow caching.",
        epilog="Exit codes: 0 success, 1 task failed, 2 config error, 130 interrupted.",
    )
    parser.add_argument(
        "--task", choices=TASK_MODULES,
        help="run a predefined demo task (URL, goal and variables come from tasks/<name>.py)",
    )
    parser.add_argument(
        "--url",
        help="start URL of a manual task (requires --goal unless --workflow hits the cache)",
    )
    parser.add_argument(
        "--goal",
        help="natural-language description of what to achieve",
    )
    parser.add_argument(
        "--workflow",
        help="cache key. Enables replay on the next run; omit to disable caching",
    )
    parser.add_argument(
        "--var", action="append", default=[], metavar="KEY=VALUE",
        help="task variable, repeatable. Overrides the task's defaults",
    )
    parser.add_argument(
        "--browser", choices=["local", "container", "remote"], default=None,
        help="local = Chrome on host; container = start Chrome via Docker; "
             "remote = connect to BROWSER_CDP_URL (auto-detected in Docker)",
    )
    parser.add_argument(
        "--provider", choices=["deepseek", "openai", "anthropic", "grok", "ollama"],
        help="LLM provider. Defaults to LLM_PROVIDER in key.json",
    )
    parser.add_argument(
        "--model",
        help="model name, overrides the provider's default (never written to key.json)",
    )
    parser.add_argument(
        "--max-steps", type=_positive_int, default=None,
        help="discovery loop cap. Defaults to the task's max_steps, or 10",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="auto-confirm every LLM call (no interactive y/n prompt). Required for CI",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="ignore the workflow cache and force discovery mode",
    )
    args = parser.parse_args()

    cli_vars = parse_vars(args.var, parser)

    if args.task:
        task = load_task(args.task)
        url = task["url"]
        goal = task["goal"]
        workflow = task["name"]
        task_name = args.task
        variables = dict(task["variables"])
        max_steps = args.max_steps or task.get("max_steps", 10)
        print(f"📋 Task: {args.task}")
    else:
        if not args.url:
            parser.error("--url is required when --task is not given")
        # A manual run needs a goal, unless it can be replayed from the
        # cache (in which case the goal is stored in the workflow). We
        # cannot check the cache here without loading key.json, so we
        # accept --workflow alone and let the agent decide.
        if not args.goal and not args.workflow:
            parser.error("--goal is required when --url is given without --workflow")
        url = args.url
        goal = args.goal
        workflow = args.workflow
        task_name = None
        variables = {}
        max_steps = args.max_steps or 10

    variables.update(cli_vars)             # CLI wins over the task defaults

    try:
        agent = WebAgent(
            auto_confirm=args.yes,
            browser_mode=args.browser,
            use_workflow_cache=not args.no_cache,
            provider=args.provider,
            model=args.model,
        )
    except LLMConfigError as e:
        print(f"❌ Configuration error: {e}", file=sys.stderr)
        raise SystemExit(2)

    try:
        success = agent.execute_task(
            url=url,
            goal=goal,
            workflow_name=workflow,
            task_name=task_name,
            variables=variables,
            max_steps=max_steps,
            save_on_success=True,
        )
    except KeyboardInterrupt:
        # 128 + SIGINT (2) = 130, the Unix convention for "interrupted".
        raise SystemExit(130)
    except Exception as e:                  # browser/network/config problems
        if _debug_enabled():
            raise
        print(f"❌ {type(e).__name__}: {e}\n(set DEBUG=1 for the full traceback)",
              file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0 if success else 1)
