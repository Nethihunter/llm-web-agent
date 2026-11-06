"""WebAgent: orchestrates discovery (LLM) and replay (cache) modes.

Two modes:
- **Discovery**: no workflow in cache → call the LLM step by step.
  On success, save the workflow to SQLite (or Postgres).
- **Replay**: workflow in cache → replay each step. If a step fails
  (stale selector, site changed), fall back to discovery.

Every run is logged to the ``runs`` table (success/failure, tokens, mode).

Architecture note:
    ``sync_playwright()`` is opened **only once** in ``execute_task``.
    ``_replay`` and ``_discover`` receive the already-opened
    ``context`` and ``page`` — they must NOT open their own Playwright
    context. Doing so raises "Playwright Sync API inside the asyncio loop"
    because Playwright refuses nested sync contexts.
"""
from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright

from browser.browser_utils import (
    connect_container_chrome,
    connect_local_chrome,
    connect_remote_chrome,
)
from html_processor import extract_clean_html, get_dom_signature
from llm.base import LLMError
from llm.factory import create_client, load_config
from tab_manager import safe_click_with_popup_handling
from validator import validate_llm_output
from validator_dynamic import is_playwright_selector, validate_selector_in_browser
from workflow_manager import Step, Workflow, WorkflowManager


def _connect_browser(pw, mode: str, url: str):
    """Select the right browser connection strategy.

    Modes:
    - "local":     Chrome already running on host (dev).
    - "container": Start a Chrome container via docker CLI (prod on host).
    - "remote":    Chrome already running elsewhere (CDP URL). Used when
                   the agent runs inside Docker and Chrome is in another
                   container.
    """
    if mode == "remote":
        cdp_url = os.environ.get("BROWSER_CDP_URL", "http://browser:9222")
        return connect_remote_chrome(pw, cdp_url, url=url)
    if mode == "container":
        return connect_container_chrome(pw, url=url)
    return connect_local_chrome(pw, url=url)


def _is_page_alive(page) -> bool:
    """``page.is_closed()`` raises if the CDP context is already gone."""
    if page is None:
        return False
    try:
        return not page.is_closed()
    except Exception:
        return False


class WebAgent:
    def __init__(
        self,
        auto_confirm: bool = False,
        config_path: str = "key.json",
        browser_mode: str | None = None,
        use_workflow_cache: bool = True,
        provider: str | None = None,
        model: str | None = None,
    ):
        config = load_config(config_path)
        self.llm = create_client(
            config, auto_confirm=auto_confirm,
            provider=provider, model=model,
        )
        self.wf_manager = WorkflowManager()

        # Auto-detect: in Docker, BROWSER_CDP_URL is set → use "remote".
        # Otherwise, default to "local".
        if browser_mode is None:
            browser_mode = "remote" if os.environ.get("BROWSER_CDP_URL") else "local"
        self.browser_mode = browser_mode
        self.use_workflow_cache = use_workflow_cache

    # ==================================================================
    # Main entry point — opens the single Playwright context
    # ==================================================================
    def execute_task(
        self,
        url: str,
        goal: str | None = None,
        workflow_name: str | None = None,
        task_name: str | None = None,
        variables: dict | None = None,
        max_steps: int = 10,
        save_on_success: bool = True,
    ) -> bool:
        variables = variables or {}

        # --- Try the cache first ---
        wf = None
        if self.use_workflow_cache and workflow_name:
            wf = self.wf_manager.load_workflow(url, workflow_name)
            if wf:
                print(
                    f"💾 Workflow '{workflow_name}' found for {wf.domain} "
                    f"({len(wf.steps)} steps, "
                    f"used {self._get_use_count(url, workflow_name)} times)."
                )

        # --- Single Playwright context for the whole run ---
        with sync_playwright() as p:
            browser, context, page = _connect_browser(p, self.browser_mode, url)

            # --- Try replay ---
            if wf:
                success = self._replay(context, page, wf, variables)
                if success:
                    self.wf_manager.touch_workflow_usage(wf.id)
                    self._log_run(
                        url, wf.id, task_name, "replay", "success",
                        steps=len(wf.steps), llm_calls=0,
                        tokens_in=0, tokens_out=0,
                    )
                    return True
                print("⚠️ Replay failed, falling back to discovery.")

            # --- Fall back to discovery ---
            if not goal:
                raise ValueError(
                    "`goal` is required when no workflow is in cache "
                    "or replay fails."
                )
            return self._discover(
                context=context, page=page,
                url=url, goal=goal, workflow_name=workflow_name,
                task_name=task_name, variables=variables,
                max_steps=max_steps, save_on_success=save_on_success,
            )

    # ==================================================================
    # Replay mode — receives context/page, opens NO Playwright context
    # ==================================================================
    def _replay(self, context, page, wf: Workflow, variables: dict) -> bool:
        # Already on success page?
        if wf.success_indicator and self._success_visible(
            page, wf.success_indicator
        ):
            print("🎉 Already on success page.")
            return True

        for step in sorted(wf.steps, key=lambda s: s.order):
            if self.wf_manager.is_step_stale(wf.id, step.order):
                print(f"⚠️ Step {step.order} is stale → fallback to discovery.")
                return False

            print(
                f"▶️ Step {step.order}/{len(wf.steps)}: "
                f"{step.action} on {step.selector}"
            )
            try:
                if step.action == "click":
                    page = safe_click_with_popup_handling(
                        context, page, step.selector
                    )
                elif step.action == "fill":
                    value = self._render(step.value_template, variables)
                    page.fill(step.selector, value)
                    time.sleep(0.5)
                self.wf_manager.mark_step_success(wf.id, step.order)
                time.sleep(0.8)
            except Exception as e:
                self.wf_manager.mark_step_failure(wf.id, step.order)
                print(f"   ❌ {type(e).__name__}: {e}")
                return False

        if wf.success_indicator:
            return self._success_visible(page, wf.success_indicator)
        return True

    # ==================================================================
    # Discovery mode — receives context/page, opens NO Playwright context
    # ==================================================================
    def _discover(
        self,
        context,
        page,
        url: str,
        goal: str,
        workflow_name: str | None,
        task_name: str | None,
        variables: dict,
        max_steps: int,
        save_on_success: bool,
    ) -> bool:
        print(f"\n🚀 Goal: {goal}")
        run_id = self._log_run_start(url, None, task_name, "discovery")

        history: list[str] = []
        recorded_steps: list[Step] = []
        last_signature = None
        last_llm_output = None
        current_url = page.url
        llm_calls = 0
        tokens_in = 0
        tokens_out = 0
        success_indicator = None
        status = "failed"
        error_msg = None
        last_step = 0

        try:
            for step in range(1, max_steps + 1):
                last_step = step

                if not _is_page_alive(page):
                    page = self._recover_page(context)
                    if page is None:
                        error_msg = "no_tabs_available"
                        break

                print(f"\n👣 Step {step}/{max_steps} | URL: {page.url}")
                raw_html = page.content()
                clean_html = extract_clean_html(raw_html)
                signature = get_dom_signature(raw_html)

                if signature == last_signature and last_llm_output:
                    print("♻️ DOM unchanged — reusing previous plan.")
                    llm_output = last_llm_output
                else:
                    print("🧠 LLM thinking...")
                    try:
                        resp = self.llm.plan_next_step(
                            clean_html, goal, history,
                            previous_error=None,
                            variable_names=list(variables.keys()),
                        )
                    except LLMError as e:
                        error_msg = str(e)
                        print(f"⏹️ LLM error: {e}")
                        break
                    llm_output = resp.data
                    llm_calls += 1
                    tokens_in += resp.input_tokens
                    tokens_out += resp.output_tokens
                    last_signature = signature
                    last_llm_output = llm_output

                print(f"💭 Thought: {llm_output['thought']}")

                if llm_output.get("is_goal_reached"):
                    print("🎉 GOAL REACHED!")
                    success_indicator = self._guess_success_indicator(page)
                    status = "success"
                    break

                if not llm_output.get("action"):
                    print("⏹️ LLM has no action.")
                    break

                # --- Validation ---
                validation = validate_llm_output(raw_html, llm_output)
                if (not validation.valid) or is_playwright_selector(
                    llm_output.get("selector") or ""
                ):
                    dyn = validate_selector_in_browser(
                        page, llm_output["selector"], llm_output["action"]
                    )
                    if dyn.valid:
                        validation = type(validation)(
                            valid=True, reason="OK_browser", match_count=1
                        )

                if not validation.valid:
                    print(f"⚠️ Validation failed: {validation.reason}")
                    history.append(f"failed: {validation.reason}")
                    last_llm_output = None
                    continue

                # --- Execute ---
                try:
                    if llm_output["action"] == "click":
                        page = safe_click_with_popup_handling(
                            context, page, llm_output["selector"]
                        )
                    elif llm_output["action"] == "fill":
                        value = self._render_value(
                            llm_output.get("value") or "", variables
                        )
                        page.fill(llm_output["selector"], value)
                        time.sleep(0.5)
                    else:
                        history.append(
                            f"failed: unknown action {llm_output['action']}"
                        )
                        continue

                    recorded_steps.append(Step(
                        order=len(recorded_steps) + 1,
                        action=llm_output["action"],
                        selector=llm_output["selector"],
                        value_template=self._reverse_render(
                            llm_output.get("value") or "", variables
                        ) if llm_output["action"] == "fill" else None,
                    ))
                    history.append(
                        f"ok: {llm_output['action']} on {llm_output['selector']}"
                    )
                    last_llm_output = None

                    new_url = page.url
                    if new_url != current_url:
                        print(f"🔄 Redirected: {new_url}")
                        current_url = new_url
                    time.sleep(1.5)

                except Exception as e:
                    print(f"❌ Playwright error: {e}")
                    history.append(f"error: {e}")
                    last_llm_output = None

            # --- End-of-loop message (conditional) ---
            if status == "success":
                print(f"\n✅ Discovery finished ({len(recorded_steps)} steps).")
            elif error_msg:
                print(f"\n❌ Discovery failed: {error_msg}")
            else:
                print(f"\n⚠️ Discovery stopped at step {last_step}/{max_steps}.")

        finally:
            # Save workflow if success
            if status == "success" and save_on_success and workflow_name:
                self.wf_manager.save_workflow(
                    url,
                    Workflow(
                        name=workflow_name, goal=goal,
                        steps=recorded_steps,
                        success_indicator=success_indicator,
                    ),
                )
                print(
                    f"💾 Workflow '{workflow_name}' saved "
                    f"({len(recorded_steps)} steps)."
                )

            self._log_run_end(
                run_id, status,
                steps=len(recorded_steps),
                llm_calls=llm_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=error_msg,
            )
            print("Chrome stays open.")

        return status == "success"

    # ==================================================================
    # Helpers
    # ==================================================================
    def _log_run_start(self, url, workflow_id, task_name, mode):
        return self.wf_manager.log_run_start(url, workflow_id, task_name, mode)

    def _log_run(self, url, workflow_id, task_name, mode, status,
                 steps, llm_calls, tokens_in, tokens_out, error=None):
        run_id = self._log_run_start(url, workflow_id, task_name, mode)
        self._log_run_end(
            run_id, status, steps, llm_calls, tokens_in, tokens_out, error
        )

    def _log_run_end(self, run_id, status, steps, llm_calls,
                     tokens_in, tokens_out, error=None):
        self.wf_manager.log_run_end(
            run_id, status, steps, llm_calls, tokens_in, tokens_out, error,
        )

    def _get_use_count(self, url, name):
        for row in self.wf_manager.list_workflows(
            self.wf_manager._normalize_domain(url)
        ):
            if row["name"] == name:
                return row["use_count"]
        return 0

    def _success_visible(self, page, selector: str, timeout: int = 5000) -> bool:
        try:
            page.locator(selector).first.wait_for(
                state="visible", timeout=timeout
            )
            return True
        except Exception:
            return False

    def _guess_success_indicator(self, page) -> str | None:
        candidates = [
            "h1:has-text('Dashboard')", "h1:has-text('Welcome')",
            "h2:has-text('Welcome')", "[data-testid='dashboard']",
            "[data-test='checkout-complete']",
            "a:has-text('Logout')", "button:has-text('Logout')",
            "a:has-text('Sign out')", "button:has-text('Sign out')",
            "h1:has-text('Thank you')", "h2:has-text('Thank you')",
        ]
        for sel in candidates:
            try:
                if page.locator(sel).count() >= 1:
                    return sel
            except Exception:
                continue
        return None

    def _recover_page(self, context):
        for p in context.pages:
            try:
                if not p.is_closed():
                    p.bring_to_front()
                    return p
            except Exception:
                continue
        try:
            return context.new_page()
        except Exception:
            return None

    @staticmethod
    def _render(template: str | None, variables: dict) -> str:
        if not template:
            return ""
        try:
            return template.format(**variables)
        except (KeyError, ValueError):
            # KeyError: the variable is not provided.
            # ValueError: the template is malformed (e.g. "{")
            # In both cases, return the raw template so the fill step fails
            # visibly instead of silently writing an empty string.
            return template

    @staticmethod
    def _render_value(value: str, variables: dict) -> str:
        return WebAgent._render(value, variables)

    @staticmethod
    def _reverse_render(value: str, variables: dict) -> str:
        """Convert a concrete value back to a template when possible."""
        for k, v in variables.items():
            if v and str(v) == value:
                return "{" + k + "}"
        return value
