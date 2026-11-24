# Business intelligence plan

This is the working plan for turning Capzoryx into a business-intelligence advisor: it learns the business once, watches its market, and warns about real risk using the owner's own numbers.

It is built in small phases. Each phase is one pull request that works on its own and is merged before the next starts, so work never depends on one long session. **Pick up from the first unchecked phase.**

## Ground rules

- **Reuse what exists.** Accounts, workspaces (one business each), roles, the encrypted ledger, forecasts, the notification queue (`notifications/service.py`), Telegram, web push, email, the free LLM chain (`chat/llm.py`), the FX feed (`fx.py`), and the audit log.
- **Free only.** No paid data feed. Market data comes from free public sources, prices the owner types in, and AI research with web-grounded citations (Gemini with Google Search grounding, when the key is set).
- **No invented numbers.** Every market figure stores its source, observed time, fetched time, scope, kind (`live`, `delayed`, `historical`, `owner`, `estimate`) and confidence. If something is missing, the UI says so.
- **Deterministic money.** All profit, break-even, exposure and scenario math is integer minor units in Python. The AI only explains numbers; it never calculates them.
- **Isolation.** Every table is keyed by `business_id`. Every read goes through `principal` and the role check, and payroll-style tests prove one business cannot read another's data.
- **Never act alone.** Alerts are automatic once the owner opts in. Selling, buying or paying always needs the owner's explicit approval.

## Data model

All tables are new and keyed by `business_id`. Free text is sealed with the workspace key.

| Table | Purpose |
|---|---|
| `biz_profile` | Identity (name, industry, niche, type, country, city, links, year, description), operations (channels, models, customers, branches, currency), capacity (investment, working capital, monthly revenue/expenses), goals, consents, `step` (0–5), `completed_at`, `updated_at`. One row per business. |
| `biz_products` | Structured products: category, name, variant, unit, quantity, unit cost, selling price, optional market key (e.g. `gold_22k_bhori`). |
| `biz_competitors` | Name, kind (direct, indirect, emerging, leader, benchmark), location, links, notes, `source` (owner or research) and evidence. |
| `market_observations` | Indicator key, value in minor units, currency, unit, source, URL, observed time, fetched time, scope, kind, confidence. Append-only. |
| `market_watch` | Indicators each business follows, how often they are checked, and the owner's thresholds. Rebuilt whenever products change. |
| `research_runs` | Status (`queued`, `running`, `done`, `unavailable`, `failed`), summary, findings with citations, error, timestamps. |
| `biz_alerts` | Level (1–3), indicator, numbers, dedupe key, cooldown, and delivery log through the existing notification jobs. |
| `recommendations` | Insight, strategy tier (A, B, C), cost, benefit, effort, risk, measure, the owner's response, and the outcome when measured. |

## Onboarding state machine

`none → identity(1) → products(2) → operations(3) → goals(4) → consent(5) → complete`

- Every step saves on its own, so a half-finished profile resumes at its step.
- Only step 1 has required fields. Steps 2–5 can be skipped, and skipped financial data marks which calculations are not yet possible.
- The wizard opens for owners without a completed profile. "Later" hides it for this session, and a resume banner stays on Overview.
- A completed profile never shows the wizard again. It is edited from Settings → Business.
- Saving products, location or industry re-runs `refresh_watch(business)` (Phase 2+).

## Phases

### Phase 1 — Business foundation ☑ (done)
- `api/app/business.py`: profile schema and routes for get, save step, products (add, edit, delete, CSV import), and completion.
- Industry and niche suggestions from a local list, matched as the user types, with no API call. Custom niches are kept verbatim and mapped to the nearest category.
- Web: `BusinessSetup.tsx` wizard (5 steps, progress bar, autosave, resume), Settings → Business editor, resume banner.
- Tests: first-time owner sees the wizard (A); profile and products are validated and saved (B); returning owner skips it (E); isolation between businesses (I).

### Phase 2 — Market data and research ☑ (done; BAJUS has no free API, so local dealer prices come from the owner or cited web research)
- Indicator catalog per industry (`market_catalog.py`): gold/silver (BDT per bhori, BAJUS reference plus international spot through FX), FX pairs, fuel, rice and other staples, with "no free source" marked honestly for other industries.
- Providers with a common interface `fetch() -> observations | Unavailable`. FX reuses `fx.py`. Owner-entered quotes are always available.
- Research runs: Gemini grounded search returns market notes and competitor candidates with source URLs. Without a key, or on failure, the run is `unavailable` with the reason; nothing is filled in.
- Dashboard "Market" and "Competitors" panels with research status, sources and freshness badges.
- Tests: C (research starts, honest status), H (provider down → limitation shown), F (new product updates the watch list).

### Phase 3 — Financial intelligence ☑ (done)
- Position per product: cost, reference value, gross, expenses, net, break-even resale price, and exposure. Reproduces the gold example: 194,000 → 200,000 with 7,000 costs = −1,000.
- Price-move scenarios at −20/−10/−5/+5/+10% on inventory.
- Growth from the ledger: month-over-month and year-over-year, gross and net margin. The baseline is guarded, so a zero or missing month shows "not enough history".
- Capability score as a weighted, explained breakdown; missing inputs lower the confidence instead of the score.
- Tests: fixed numeric cases for every formula.

### Phase 4 — Autonomous monitoring and alerts ☑ (done; email for market alerts left for Phase 6)
- Background loop started in `lifespan`, holding a DB lease so only one worker runs. UptimeRobot keeps the free Render instance awake. It runs the existing `tick` plus `market_tick`.
- `market_tick`: fetches the watched indicators that are due, stores observations, skips anything stale, evaluates thresholds, and classifies levels 1–3.
- Delivery uses the existing notification jobs (in-app, Telegram, push, email) with dedupe keys, cooldown per indicator and level, delivery logs, and retries.
- Tests: G (threshold crossed → right level and message), J (owner offline → alert queued and sent).

### Phase 5 — Advisor ☑ (done; strategies are rule-based templates filled with the owner's numbers, so they need no AI service)
- Competitor gap analysis and A/B/C strategies. The LLM is given the computed numbers and the cited findings only, and its numbers are checked against them.
- The assistant answers scenario questions ("price drops 10%?", "minimum selling price?") with deterministic math, best/base/worst cases and the confidence.
- Proactive insights card and a recommendation feedback log (accepted or dismissed, measured outcome).

### Phase 6 — Hardening ☑ (done)
- Acceptance tests A–J in one browser script, data export and deletion including new tables, a security scan, and performance checks with thousands of products.

## Acceptance tests

All ten pass. Where each one is checked:

| Test | Phase | Check |
|---|---|---|
| A | 1 | A new owner sees the wizard after signup. Covered by `scripts/check_business_browser.cjs`. |
| B | 1 | Niche, products, location and description are validated and saved. Covered by `api/tests/test_business.py`, browser check. |
| C | 2 | Research tasks start and show a real status. Covered by `api/tests/test_market.py`. |
| D | 2 | The dashboard uses this business's profile and market data. Covered by browser check (market panel). |
| E | 1 | Logging out and back in loads the profile; the wizard does not return. Covered by `test_business.py`, browser check. |
| F | 2 | A new product updates the watch list. Covered by `test_market.py`. |
| G | 4 | A threshold crossing gives the right level, numbers and delivery. Covered by `api/tests/test_monitor.py`. |
| H | 2 | A provider failure shows the limitation, never invented data. Covered by `test_market.py`, browser check. |
| I | 1 | One business cannot read another's profile, products or alerts. Covered by `test_business.py`, `test_market.py`, `test_monitor.py`. |
| J | 4 | An event while the owner is offline is evaluated and delivered. Covered by `test_monitor.py`. |

## Operations

- **Background jobs** run in the web process on Render (`CAPZORYX_BACKGROUND_JOBS`, on by default when `RENDER` is set). UptimeRobot keeps the free instance awake. One worker holds the `job_lease`.
- **Research** needs a Gemini key (`CAPZORYX_CHAT_GEMINI_KEY` or `SAFELEDGER_LLM_API_KEY`). Without one, runs are recorded as unavailable.
- **Email** for critical alerts uses the existing mailer and only reaches owners with a verified address. The email carries no amounts.
- **Erasing** a workspace's private data also removes its business profile, products, quotes, research, alerts and advisor feedback. The workspace export includes all of them.
- **Scale:** tested with 1,500 products. Profile, insights, advisor and market each answer well under a second.

## Any industry, not only gold

Gold was only the example in the brief. Every product of every business can have its own market price: what it sells for around the owner. The price comes from:
- the owner's quote, such as the shop next door, a wholesale market or an online listing
- web research with a citation

The 25 most valuable products are followed this way. Profit, break-even, scenarios, alerts and advice all use that price, and the advisor also compares it with the owner's own selling price (priced above or below the market). Gold and silver keep their built-in metal feeds as a special case.
