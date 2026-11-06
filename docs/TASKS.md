# Demo Tasks

Three tasks, three categories of real-world automation.
Each task runs on a legitimate site, chosen for a specific capability.

---

## 1. Saucedemo Checkout (`saucedemo_checkout`)

**What it does**: logs in, adds a product to the cart, completes checkout.

**Why it matters**: proves the agent can handle a **linear multi-step flow**
with form filling and verification. This is the most common use case in
e-commerce, SaaS onboarding, and internal tooling.

**Skills demonstrated**:

- Login with credentials
- Multi-step form filling
- Success verification
- Full workflow caching (2nd run = 0 LLM calls)

**Legal**: [saucedemo.com](https://www.saucedemo.com) is the official
Sauce Labs testing site, designed specifically for automation.
No ToS violation. No real transaction.

**Run**:

```bash
python3 run.py --task saucedemo_checkout --yes
```

**Expected output** (first run):

```
📋 Loaded task: saucedemo_checkout
👣 Step 1/12 | URL: https://www.saucedemo.com
💭 Thought: The page shows a login form. I need to fill username and password.
✅ fill on #user-name
👣 Step 2/12 | URL: https://www.saucedemo.com
✅ fill on #password
👣 Step 3/12 | URL: https://www.saucedemo.com
✅ click on #login-button
...
🎉 GOAL REACHED!
💾 Workflow 'checkout' saved (7 steps, id=1)
```

**Expected output** (second run):

```
💾 Workflow 'checkout' found for saucedemo.com (7 steps, used 1 times).
▶️ Step 1/7: fill on #user-name
▶️ Step 2/7: fill on #password
▶️ Step 3/7: click on #login-button
...
🎉 GOAL REACHED!
```

Note: **no LLM call on the second run**. Check `runs` table: mode = `replay`,
llm_calls = 0.

---

## 2. Booking Search (`booking_search`)

**What it does**: searches for hotels in Paris, extracts the first 5 results
as JSON.

**Why it matters**: proves the agent can handle a **complex form with dates**
and **extract structured data** from results. This is the foundation of
ethical scraping, market research, and competitive intelligence.

**Skills demonstrated**:

- Complex form (dates, guest count, destination)
- Search result parsing
- Structured JSON extraction
- Read-only (no booking, no account creation)

**Legal**: this task is read-only. It only reads publicly visible results
(hotel names, prices). No reservation, no account.
Many travel sites restrict automated access in their Terms of Service.
**Check booking.com's current ToS and `robots.txt` before running this task**,
and replace it with another site if automated access is not allowed.
The project does not endorse violating any site's terms.

**Note**: large sites may block some requests or IP ranges. If you get
blocked, do not try to bypass it (see "What is NOT planned" in the roadmap).

**Run**:

```bash
python3 run.py --task booking_search --yes --provider openai
```

**Expected output**:

```json
{
  "hotels": [
    {"name": "Hôtel Example 1", "price": "€120", "rating": "8.5"},
    {"name": "Hôtel Example 2", "price": "€135", "rating": "8.2"},
    ...
  ]
}
```

---

## 3. Wikipedia Extraction (`wikipedia_extract`)

**What it does**: reads the Web scraping article on Wikipedia, extracts the
definition, the list of legal concerns, and the last edit date.

**Why it matters**: proves the agent can handle a **heavy DOM (500 KB+)**
and perform **targeted extraction**. This is the foundation of RAG, document
search, and knowledge retrieval.

**Skills demonstrated**:

- HTML reduction (target: ~500 KB → ~20 KB before the LLM call, about 96%)
- Targeted text extraction
- Structured output from unstructured content
- No login, no form (pure navigation + extraction)

**Legal**: [wikipedia.org](https://en.wikipedia.org) content is CC-BY-SA.
Extraction is allowed with attribution. No account, no form, no ToS
violation.

**Run**:

```bash
python3 run.py --task wikipedia_extract --yes
```

**Expected output**:

```json
{
  "definition": "Web scraping is the automated extraction of data from websites...",
  "legal_concerns": [
    "Copyright infringement",
    "Violation of terms of service",
    "Server load and denial of service",
    "Privacy violations"
  ],
  "last_edited": "2025-03-15"
}
```

---

## Why these three?

They cover the three most common categories of browser automation:

| Category | Task | Real-world use |
|---|---|---|
| **Transactions** | Saucedemo checkout | E-commerce, SaaS signup, billing |
| **Research** | Booking search | Market research, competitive intel, price monitoring |
| **Extraction** | Wikipedia article | RAG, document search, data pipelines |

Together, they show a **generalist agent**, not a single-purpose bot.

---

## Running all three in sequence

```bash
#!/bin/bash
# scripts/run_all_demos.sh
set -e

echo "=== 1. Transactions ==="
python3 run.py --task saucedemo_checkout --yes

echo "=== 2. Research ==="
python3 run.py --task booking_search --yes --provider openai

echo "=== 3. Extraction ==="
python3 run.py --task wikipedia_extract --yes

echo "=== Done. Check dashboard: http://localhost:8000/dashboard ==="
```
