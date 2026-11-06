# LLM Failure Modes in Browser Automation

This project was developed in collaboration with LLMs (DeepSeek, OpenAI,
Anthropic). Rather than accepting generated code blindly, every failure
mode encountered during development is documented here.

Each entry follows the same structure:

- **Symptom**: what was observed (log, behavior)
- **Root cause**: why the LLM made this mistake
- **Fix**: the code change that addressed it
- **Transferable rule**: the general principle

The goal is to extract **rules that apply beyond this codebase**.

---

## Failure Mode 1: Confidently Wrong Selectors

**Symptom**: the LLM proposed `[data-testid="submit"]` with confidence 0.9.
The attribute did not exist in the DOM. The click succeeded (Playwright
dispatched the event) but nothing happened. The agent reported "success"
and moved to the next step, on the wrong page.

**Root cause**: LLMs are trained on documentation and tutorials where
`data-testid` is a *recommended* attribute. They generalize that it is
*always* present. The confidence score is not calibrated against reality.

**Fix**: three-layer validation (`validator.py`, `validator_dynamic.py`,
`_post_step_check`). A selector is only executed if it matches exactly one
element in the **real** DOM.

**Transferable rule**: never trust an LLM's confidence for anything you
can verify. Verify it.

---

## Failure Mode 2: Truncated JSON

**Symptom**: with `max_tokens=600`, approximately 8% of responses were cut
mid-JSON. `json.loads()` threw `JSONDecodeError`, breaking the agent loop.

**Root cause**: LLMs do not "plan" a JSON length. They generate until
`max_tokens` or end-of-sequence. Complex plans (long `thought`, many fields)
exceed 600 tokens.

**Fix**: two-layer defense.

1. `max_tokens=800` (empirically sufficient for 95% of plans).
2. Retry with `max_tokens=1200` on `JSONDecodeError`.

**Transferable rule**: for structured output, always implement retry with
an increasing token budget on parse failure.

---

## Failure Mode 3: Cache Poisoning via Field Order

**Symptom**: DeepSeek's prompt cache hit rate was 0% despite a stable
`SYSTEM_PROMPT`. Cost was 10x expected.

**Root cause**: the `user` message had `goal` (stable) → `history`
(variable) → `html` (semi-stable). Since `history` changed every step,
the *entire suffix* was invalidated on each call.

**Fix**: reorder to `goal` → `html` → `history`. The stable prefix
(`SYSTEM_PROMPT` + `goal` + `html`) is now identical between consecutive
calls on the same page. Cache hit rate: 0% → ~30% in discovery mode.

**Transferable rule**: for prompt caching, put **stable content first,
variable content last**. The cache is a prefix cache, not a content hash.

---

## Failure Mode 4: Silent Success

**Symptom**: the agent clicked "Sign Up", but the page did not navigate.
It reported "success" because the click did not throw. The next step was
executed on the wrong page.

**Root cause**: `page.click()` succeeds if the click is dispatched, even
if the target element does nothing (missing JS handler, wrong button,
overlay intercepting the click).

**Fix**: `_post_step_check` after each click verifies one of:

- the URL changed, or
- the DOM signature changed, or
- the `success_indicator` selector appeared.

If none, the step is marked as failed.

**Transferable rule**: never treat "the call did not throw" as success.
Define a positive success signal.

---

## Failure Mode 5: The Ghost Tab

**Symptom**: the agent connected via CDP to an existing Chrome instance
but operated on a stale tab (an old Wikipedia page) instead of the target
URL.

**Root cause**: `context.pages[0]` returns the *oldest* tab, not the
active one. The user's Chrome had multiple tabs open.

**Fix**: `_pick_active_page` selects the page whose hostname matches the
target URL, then calls `page.bring_to_front()`. If none matches, it forces
navigation.

**Transferable rule**: in CDP-based automation, never assume tab order.
Always match by content (URL, title) or force navigation.

---

## Failure Mode 6: Iframe Blindness

**Symptom**: the agent could not find a button that was visible in the
browser. The button was inside an iframe.

**Root cause**: `page.content()` returns only the main frame. Iframe
contents are loaded separately and are not included.

**Fix**: `extract_iframe_contents` iterates over `page.frames`, reads each
accessible frame (same-origin only), and appends its cleaned HTML to the
context sent to the LLM. Cross-origin frames (ads) are skipped — which is
also what we want.

**Transferable rule**: `page.content()` is not the full page. Frames,
shadow DOM, and dynamically loaded content require separate handling.

---

## What This Project Does Not Solve

- **Anti-bot detection**: Cloudflare Turnstile, hCaptcha, and similar
  systems will block the agent. Bypassing them is out of scope and often
  violates the site's ToS.
- **Multi-account orchestration**: this is a single-user tool. Profile
  rotation, cookie isolation, and proxy management are not supported.
- **Long-running autonomous sessions**: the agent runs one task at a time
  with a hard `max_steps` limit. It is not designed for indefinite
  operation.
