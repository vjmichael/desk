# Design Document: 24/7 Automated Trading Desk

**Version:** 0.6 (AI market intelligence, bounded execution, self-recovering)
**Date:** 2026-11-18
**Status:** Design only. Not production-ready. Not financial advice.
**Builds on:** v0.5 (cloud hosting and recovery) and restores the research-first intent of v0.3, especially Addendum #3, A_Pirate_I’s “Trading Desk-in-a-Box.” The original X posts remain in v0.3; this revision retains their links. This is a design revision only, not implementation authorization.
**Goal:** A small, always-on cloud trading desk that continuously turns news, social information, economic events and market observations into useful, structured trading intelligence. A cloud AI discovers and evaluates opportunities; an independent deterministic engine validates and authorizes any execution. The application owns its state, recovers safely from failures, sends alerts, and provides phone-friendly controls without requiring a chat session or routine command-line operation.

---

## 0. Bottom Line

- **The AI is the intelligence of the trading desk. Freqtrade is its normal execution engine. The deterministic risk gate is the final authority.**
- v0.5 intentionally limited the AI to vetoing rule-generated signals and made news optional. That loses the original research-first objective. In v0.6, AI continuously converts unstructured information into evidence-linked market intelligence and can originate new trading proposals within a human-approved universe and strategy families.
- The AI may propose an asset, direction, setup, horizon, rationale and candidate levels; it cannot authorize an order, specify an executable stake, alter limits, loosen live stops, unlock live mode or hold trade keys. Code independently computes or validates all executable levels and sizing.
- Preserve the lean cloud architecture: one primary VPS, a cloud AI API, SQLite, Freqtrade, protective supervision and a separate Recovery Console. The five AI roles are workflows, not five separately hosted agents. Use a single primary AI provider and an optional qualified backup.
- Keep v0.5’s operational recovery: start PAUSED, reconcile, bounded restarts, phone controls, DOWN / RECOVERING / RECOVERED / NEEDS YOU, two alert channels.
- Begin with paper trading. Measure whether AI-derived information improves portfolio outcomes after *all* costs, not whether the model can produce convincing commentary.

**What changes in v0.6**

| v0.5 | v0.6 |
|---|---|
| Rules originate all trades; AI only allows, shrinks or blocks | AI can discover research hypotheses and originate proposals; independent rules and risk engine authorize or reject them |
| Optional headlines only when a rule signal exists | Dedicated continuous ingestion, event-driven analysis and hourly market synthesis even without a rule signal |
| One AI decision call per candidate cycle | Specialized Scout, Researcher, Analyst, Strategist and Reviewer workflows sharing one provider interface |
| Minimal reason codes | Evidence-linked event, intelligence and proposal records with timestamps, conflicts, uncertainty and expiry |
| Intents file and veto shadow trades | Durable intent ledger, point-in-time evidence archive and paired simulated portfolios |
| Telegram-first operation | Mobile-friendly dashboard plus retained Desk and Recovery notifications and emergency controls |
| Research output largely discarded after the cycle | Persistent historical research and independently measured outcomes; no model training needed |

The distinction is between **proposing risk** and **authorizing risk**. The former is research; the latter remains independently constrained by deterministic software and the human’s previously approved policy. No free-text article, model response or confidence score can bypass the Gate.

---

## 1. The Three Posts, Reviewed

| Post | What it actually is | Take | Leave |
|---|---|---|---|
| **1. savipww, "Grok Bot Trencher Setup"** | A Pump.fun memecoin dip-buying desk. Five bots on Grok Bot's own computer click Buy and Sell in the FOMO web app with a browser cursor, using an unofficial FOMO API to read and verify. Contains a referral link; the "$36 to $3,125 overnight" claim is unverified | Missing data is never a pass. The social signal may contribute evidence but cannot by itself authorize an order. Verify every order with three independent witnesses (screen, API, chain) and never re-click an unverified order. Stops only tighten. Nightly loss stop, ticket-size cap, trade-count cap. Paper first. The venue is truth, the local database is a copy | Browser-cursor execution. Unofficial APIs that "can break any day." Memecoin rug-pull markets. An agent platform as the runtime. Approval prompts that expire. Dependence on web-login sessions |
| **2. bloggersarvesh, "Claude + SEO"** | A local-SEO marketing playbook with an agency sales pitch. Nothing to do with trading. v0.3 flagged this correctly | Only the habit of loading context once and keeping one prompt per task | Everything else |
| **3. A_Pirate_I, "Trading Desk-in-a-Box"** | The closest match to our design: a research agent, a bounded-choice decision layer, an existing bot (Freqtrade) as the only thing that can place orders, five autonomy levels, hard limits in the bot's config, a read-only bridge | Restore SCOUT’s ongoing news, social, funding and open-interest research and CHART’s evidence-based trade planning. Do not rebuild the trading bot. The decision step picks from a fixed menu, not free text. "No fill, no done." Autonomy levels. Test with failures: routing accuracy, signal escapes, false closes, needless human interruptions, recovery. Automate one proven loop at a time | Per the post, an open-source Freqtrade MCP server exposes tools that place trades and reload config; never hand that to a chat agent. The paid third-party decision service is optional; a structured cloud-API workflow can implement the bounded decision interface, subject to validation and testing |

---

## 2. Why Grok Bot Sessions Expire, and How This Design Avoids It

Post 1's own prompt shows the failure sources. Every one has a fix that comes from moving the work off the agent platform.

| What breaks on an agent platform | Evidence | v0.6 answer |
|---|---|---|
| Work lives in a vendor-run session or VM that updates, restarts, or drops installed packages | Post 1 reruns a setup script after platform updates and needs an hourly health routine | The app runs on a server you rent and control, with automatic restart. No vendor VM in the loop |
| Plan usage runs out and the agent stops | Post 1: usage is spent per routine run, the desk stops at zero, "nobody guards a LIVE night on an empty pool" | The AI is a metered API with your own hard spend cap. When the AI budget is gone, the app keeps running with no new entries |
| Approval prompts time out | Post 1: approval cards expire after 30 minutes, "a stop waiting for approval is a missed stop" | Stops rest on the exchange and never wait for a human or an AI |
| Web logins and session tokens expire (about an hour for FOMO's token) | Post 1 | Official exchange API keys. No browser, no cursor, no web login |
| Unofficial APIs break | Post 1 says so | Only official, documented exchange APIs through CCXT and Freqtrade |
| Beta product and capacity limits | Engadget (early beta); MQL5 post (Grok API capacity) | A primary AI provider and optional qualified backup behind one interface. AI outage blocks new live entries; existing protective management continues independently |
| State lives in a chat | General | State lives in the app's database and on the exchange. Each AI call is a fresh request carrying everything it needs |

**Trading must not depend on a chat session.** API credentials, provider limits, data freshness and ordinary service outages still require explicit handling.

---

## 3. Non-Negotiables

1. **AI researches and proposes; it never places, changes, cancels or authorizes orders.** Freqtrade owns routine execution. A separately constrained watchdog may perform emergency protection only under the exclusive execution ownership rules in Section 6.
2. **Research is proactive.** News, relevant social signals and economic/market data are continuously collected even if no rule candidate exists. The AI can propose new opportunities but cannot expand the live whitelist, change strategies, increase authorized size, loosen protective stops or unlock live mode. Its suggested prices are hypotheses only; code derives or independently validates executable levels.
3. **Limits live in code, exchange settings and a capped account, never in prompts.** Missing, stale, invalid or unverifiable critical evidence fails closed. No model confidence is treated as a calibrated probability without measurement.
4. **The exchange is the truth for live orders and fills.** Reconcile on every start and regularly during operation; local records are a cache and audit trail.
5. **Every filled exposure must have exchange-confirmed protective coverage as soon as supported by the venue.** Stops are not guaranteed fills or loss caps. A missing or inadequate stop triggers immediate protective handling, no new entries and escalation if protection cannot be restored. Stops never depend on an AI call or human approval.
6. **Fail closed means no new entries; existing risk management continues.** Data/AI outages, unverified orders, expired proposals and unavailable risk checks must not lead to new exposure.
7. **No fill, no done.** Reports of orders, positions and profit/loss use actual exchange records (or explicitly labeled simulated records in paper mode), not model assertions.
8. **The human owns capital, strategy families, venue, whitelist, risk limits and going live.** Sensitive changes require strong authentication and explicit confirmation; automatic research cannot promote its own strategy.
9. **Every start begins PAUSED.** Persist the prior pause/lock reason separately from runtime health. Clean reconciliation is necessary, not sufficient, to resume.
10. **Recovery is automatic first, a button second, and always announced.** Recovery cannot remove a human pause or risk lock. Independent recovery verification distinguishes service availability from confirmed position protection.
11. **Audit intelligence as well as transactions.** Preserve point-in-time inputs, evidence IDs, model/prompt versions, hypotheses, gate results and realized outcomes. Never silently revise past forecasts.
12. **Paper first.** New research, strategy or provider changes must be evaluated without real-money authority before being promoted. No profit guarantee.

---

## 4. The App

```text
PUBLIC DATA SOURCES (read-only)
  Official news and releases | financial/world news | X/social | economic calendar
  Exchange candles/order book | funding/open interest | selected blockchain feeds
                          |
                          v
ONE SMALL CLOUD SERVER (always on, static IP, every start begins PAUSED)

  brain (Python service / container)
    collectors -> normalization, deduplication, point-in-time archive (SQLite)
                             |
                             v
    Cloud AI API: Scout -> Researcher -> Analyst -> Strategist
                 -> evidence-linked intelligence -> expiring trade proposals
                             |
    deterministic strategies (baseline and independent market checks) --+
                             |                                         |
                             v                                         v
    independent decision check -> deterministic Gate (limits, levels, sizing)
                             |
                    durable authorized intent ledger
                             |
                             v
  freqtrade (container; routine order owner, separate trade key)
    final authorization, order lifecycle, fills, exchange-side protection
                             |
                             v
  exchange (live truth; paper execution clearly separated)
                             |
    fills, market outcomes -> audit store -> Reviewer -> performance reports

  watchdog (separate container, read key; emergency key only if qualified)
    checks exposure, stop coverage, risk limits and health; restricts risk
    emergency operation only with verified exclusive execution custody

  supervisor (container, no trade keys)
    health checks, ordered restarts, durable pause/lock handling
    polls external Recovery Console; no trading authority

  dashboard / Desk notifications (read and authorized controls)

OUTSIDE THE SERVER
  RECOVERY CONSOLE (different provider) -> cloud reboot/power-cycle API
  Recovery bot and authenticated phone-friendly controls
  External heartbeat and independent health/protection verification
  Second critical alert channel
  Cloud AI API (primary; qualified backup optional)
  Exchange-side protective orders (venue-dependent)
```

The brain may run the collectors, workflow scheduler and research roles as modules in one service. Separate credentials and restricted interfaces matter more than proliferating containers. The Gate runs as deterministic code and the execution adapter repeats its critical checks; research cannot write authorized intents directly. The research database and bot state are logically separated with controlled single-writer ownership for each ledger. The dashboard is a thin application interface, not a second execution engine.

### 4.1 Hosting

| Option | Verdict |
|---|---|
| **Plain VPS** (e.g., Hetzner, DigitalOcean, Vultr, Linode) running Docker Compose | Recommended starting architecture: static outbound IP, persistent disk, automatic restart. Verify provider capabilities. |
| Managed container platform | Only on a paid always-on tier with persistent storage, supported recovery operations and fixed outbound IP. |
| Free or sleeping tiers | Avoid for the trading core. |
| Home PC | Optional research/paper experiments only, not a dependency. |
| Chat-agent platform | Not the runtime. |

Initial sizing estimate: 2 vCPU and 2–4 GB RAM for a limited watchlist, excluding AI inference (cloud API). Measure ingestion volume, retained data, dashboard, database and bot memory under sustained load before fixing capacity. Choose region and hosting only after venue eligibility and API requirements are known. Keep infrastructure and security updates separate from strategy changes. Provider backup, restart and power-cycle behavior must be tested.

### 4.2 Data ingestion and point-in-time evidence

Start with a few reliable, permitted sources for each necessary category; expand only if a source demonstrates additional value. Prefer official releases, supported APIs, licensed feeds or permitted RSS. Provider-specific access, coverage, licensing, redistribution and cost need verification; do not assume X or news content is available through an ordinary model API call.

| Information | Initial use | Collecting method / notes |
|---|---|---|
| Financial and crypto news | Regulatory events, listings, hacks, institutional flows and project developments | Official notices and reputable news feeds; record original publication and revision times |
| World news | Geopolitical shocks, sanctions, supply disruptions and risk-off events | Licensed/permitted news feed; associate indirect exposure cautiously |
| Economic news and calendar | Rates, inflation, jobs, macro surprises and event risk | Official releases and calendar; distinguish expected, previous and actual values |
| X/social and optional Reddit | Early reports, changing narratives, sentiment and rumor discovery | Authorized API or provider search access; low-trust until corroborated |
| Exchange observations | OHLCV, bid/ask, spread, order book, liquidity, volume | Official API / supported CCXT adapter; code computes indicators |
| Derivatives observations | Funding, open interest, liquidation activity | Public permitted market-data endpoints; research context does not authorize derivatives trading |
| Blockchain observations | Transfers, exchange flows and protocol events | Public chain data or permitted provider; distinguish observed transfers from inferred intent |
| Historical evidence | Comparable events, previous forecast errors and market reactions | Point-in-time archive from the desk; optional licensed historical data |

Pipeline: fetch -> authenticate source where possible -> normalize timestamps and asset/entity names -> deduplicate syndicated/copy-pasted stories -> tag source and event -> preserve permitted raw input or durable evidence reference -> relevance and urgency filter -> queue AI work. Use source attribution and canonical event IDs so fifty reposts do not appear to be fifty independent confirmations. Store `published_at`, `observed_at` (first available to desk), `ingested_at`, source, original URL/record ID, revision, content hash, reliability class and applicable data-use constraints. Keep contradictory and revised observations; never overwrite the original decision-time record.

Source labels: official/primary, independent reporting, secondary/copy, social/unverified. These are evidence categories, not automatic truth scores. Missing or stale data remains unknown. Use an explicit required-source policy per setup, with optional feeds marked degraded when inaccessible. Social posts, articles and web responses are untrusted data, never executable instructions.

### 4.3 AI research workflows (the core of v0.6)

These are logical jobs invoked through the same cloud AI API with task-specific prompts, bounded inputs and typed outputs. No unrestricted agent framework, shared autonomous chat session or execution credentials are required. The scheduler owns state and controls permitted data retrieval; a model can ask for more evidence from approved read-only sources through typed requests, with bounded retries and budget.

| Workflow | Original article relationship | Responsibility | Output |
|---|---|---|---|
| **Scout** | SCOUT | Monitor new events, social narratives and market anomalies; identify relevant assets and urgency | Event candidates with evidence IDs and relevance |
| **Researcher** | SCOUT verification expanded | Cross-check claims, find primary sources, group duplicates, identify conflicts and request missing evidence | Verified/unverified event records and evidence links |
| **Analyst** | CHART and contextual research | Combine event implications, chart/indicator data, liquidity, funding/open interest and macro context; consider alternative interpretations and time horizons | Structured market intelligence per asset and horizon |
| **Strategist** | CHART | Originate research-driven setups within approved strategy families and whitelist; compare against deterministic candidates | Expiring research proposals or explicit NO_TRADE |
| **Reviewer** | DESK MANAGER and Jev review | Compare previous hypotheses/decisions with measured outcomes and verified fills; identify weak evidence and potential improvements | Review records and research-only change suggestions |

Independent signal evaluation (inspired by Jev) returns ACCEPT_FOR_GATE, VERIFY_MORE or REJECT. A separate AI call can help detect contradictory evidence, but is not an independent mathematical proof. Code verifies evidence references, freshness, strategy eligibility and numeric constraints. VERIFY_MORE goes back to the Researcher with a named gap, bounded by expiry and budget. No result means no authorized trade. The scheduler, not an unrestricted model, coordinates the jobs and produces status reports.

### 4.4 Structured market intelligence and research proposals

Maintain separate record types: observed source evidence, normalized event, intelligence assessment, untrusted trade proposal, independent decision and authorized intent. Collectors establish observed source evidence; code assigns provenance and valid IDs. The AI produces interpretive event fields, assessments and proposals only from supplied evidence, with code validating all references. Only the Gate can create an authorized intent. A report or prose summary is never parsed as an order.

**Intelligence record (illustrative fields; schema to be finalized before development):**

| Field | Meaning |
|---|---|
| `assessment_id`, `snapshot_id`, `event_ids`, `evidence_ids` | Immutable references to the exact available input |
| `created_at`, `as_of`, `valid_until` | Decision-time timestamp, observation cutoff and expiry |
| `assets`, `event_type`, `regime` | Canonical whitelist/related assets, event classification and market context |
| `facts`, `inferences`, `unknowns` | Separate observed claims, model interpretation and missing information |
| `directional_bias`, `horizon`, `expected_volatility_change` | Bullish/bearish/neutral/mixed/unknown, timeframe and qualitative market impact |
| `supporting_evidence`, `contradictory_evidence`, `alternatives` | IDs and reasons, including already-priced-in or opposite-response hypotheses |
| `source_quality`, `uncertainty`, `freshness` | Verified provenance, qualitative uncertainty and data status |
| `suggested_next_step` | Monitor / investigate setup / verify more / stand down |
| `model_id`, `prompt_version`, `schema_version`, `input_hash` | Audit and replay metadata |

**Trading proposal:** `proposal_id`, origin (rules or AI research), assessment/evidence IDs, whitelisted symbol, proposed direction, strategy/setup family, horizon, candidate entry condition and invalidation/stop/target hypotheses, expected mechanism, supporting and conflicting evidence, expiry, and reject/stand-down reasons. A proposal is NOT an executable order. Numeric values from a model are tentative; code recalculates or verifies them against an approved, testable strategy rule and current market data. Reject if a setup cannot be evaluated deterministically. Size and authorized prices/limits are never accepted merely because a model supplied them.

Example (fictional, not a recommendation): an unexpected central bank decision, a verified rise in BTC volume and reported outflows form a potential risk-on hypothesis for the next 4–24 hours. The Analyst must also evaluate whether the rate change reflects economic distress, whether a transfer is merely an internal wallet move, and whether price already reflects the news. The Strategist may propose investigating a BTC setup; only a configured market trigger plus Gate approval may create an intent. If evidence conflicts or the trigger never occurs, no entry.

Do not convert model-generated confidence into position size or numerical probabilities. Any probability model used later must be separately calibrated against forward outcomes. Sentiment alone is not an authorization criterion.

### 4.5 Cadence and the complete trading cycle

The following are adjustable starting design targets, not provider rate-limit promises. Collecting data and interpreting it are distinct; coalesce related events, prioritize material changes, and use cached assessments until expiry.

| Activity | Proposed cadence |
|---|---|
| Price/order/position observation | Continuous supported stream or bounded polling; reconciliation at least every 5 minutes and on critical transitions |
| News and official announcements | Every 1–5 minutes or supported feed pushes |
| Social observations | Every 5–15 minutes, subject to licensing and cost |
| AI event research | On a material new or changed event; also expire stale assessments |
| Market synthesis | Hourly, even without rule-generated candidates |
| Trade evaluation | At strategy-defined candle close or preapproved event-trigger window |
| Daily and weekly reviews | Daily verified report; weekly comparative performance and intelligence review |

1. Collect and archive observations; verify sources and eliminate duplicate events. Keep research running while entry trading is paused where health and budget permit.
2. Run Scout/Researcher when a material event arrives. Analyst updates asset-specific intelligence with market, economic and historical context.
3. Strategist generates zero or more proposals. Deterministic rule strategies also generate candidates independently for the baseline. No candidate is a valid outcome.
4. Independent decision workflow asks for more evidence, rejects, or advances a proposal to the Gate. The Gate independently validates all critical fields, recalculates levels and stake, and authorizes or rejects; it can require human review.
5. Record all proposals and no-trade decisions, including blocked candidates for shadow evaluation. Only authorized, expiring intents enter the execution ledger. No research worker has write authority to the order interface.
6. Freqtrade claims an intent exactly once in local state, rechecks current policy and market state and submits only under confirmed execution ownership. Exchange submission outcomes are reconciled; unknown status never triggers blind resubmission.
7. Position protection and emergency handling operate without the AI. Research does not directly modify open orders or stops; risk policy owns all exits/protection.
8. Record fills, market returns, prediction/decision outcomes, data and AI costs for Reviewer. Historical records may inform future research but automatic live rule or model changes are prohibited.

### 4.6 What the AI sees; API and budget rules

| Sees | Never sees |
|---|---|
| Permitted public source extracts, public market observations, code-computed indicators, prior public research and outcomes, whitelist/setup policy labels, evidence IDs | Exchange/API secrets, account IDs, balances, raw currency stake, execution credentials, private strategy source code, risk thresholds not needed for research |

- Pin exact model, prompt and schema versions where possible; record any provider-side changes. Temperature 0 where supported does not guarantee identical model responses. Replay the recorded response to reproduce the deterministic decision; do not regenerate it and claim exact replay.
- Structured output plus local validation, permitted enums and evidence-ID checks. External text is hostile; models cannot issue commands or add new sources without allow-list validation. Reject unknown IDs, fabricated references and unverified critical claims.
- Bounded timeouts (initially 30–60 seconds), one retry, optional qualified backup. Record provider and differences. If necessary AI inputs/outputs are unavailable or expired, no new entries; existing protection and operational recovery continue.
- Use role-specific prompts and compact retrieved context. Deduplicate, batch, cache by snapshot/version, and cap requests, tokens and spending. Enforce a local reservation/accounting budget before dispatch; provider spending notifications may not be hard caps. Reserve capacity for critical health alerts independent of AI.
- Track data subscriptions and AI costs separately from hosting and exchange fees. Estimate monthly variable cost from request frequency, input/output tokens, search/data charges and retry rates. Alert at 50% and 80%; block new live entries at cap; independent rules-only paper benchmarks may continue. No unapproved charge escalation.
- Select one primary provider by testing evidence extraction, typed output quality, latency, cost and permitted access to fresh data; qualify a backup before using its output live. Candidates include Grok (potential X access), OpenAI, Claude and Gemini. The API is not a news feed by default. Verify current search features, data terms, retention, access and pricing before selection.

### 4.7 Durable research memory and operating interface

The brain keeps an append-only history of events, claims, assessments, proposals, gate results, outcomes and model/prompt/config hashes. Link each event to market snapshots and predeclared outcome horizons; the Reviewer retrieves relevant previous cases and identifies recurring errors. Preserve original observations and outcomes separately, including news corrections. Never let future outcomes leak into historical replay. This is retrieval and empirical feedback, not fine-tuning or automatic strategy modification.

Use a small authenticated responsive web dashboard for desktop/phone (no required terminal): Dashboard, Market Research (sources, facts versus inference, conflicting evidence and expired assessments), Trades (actual vs simulated, verified protection and fills), Strategies, Performance (parallel portfolio comparisons), Settings. Paper/live state, running/paused state, data freshness, last research, spend and alerts are visible. The Recovery Console remains external; show unavailable/unknown when the core cannot confirm status. Telegram is for notifications and low-risk control requests, not plaintext secrets. Sensitive controls use a strongly authenticated console. Dashboard and reports cannot independently place trades; they use the same authorization and recovery policies.

---

## 5. The Gate (6 Checks, in Order)

The decision check may classify a proposal ACCEPT_FOR_GATE, VERIFY_MORE, REJECT or STAND_DOWN. It is advisory. ACCEPT_FOR_GATE is not order authorization. The independent deterministic Gate is the only producer of executable intents. It evaluates BOTH research-originated and rules-originated proposals. Every failure returns REJECT with a specific reason; missing data is not a pass.

1. **Fresh, valid and supported.** Validate schemas, origin, known evidence/snapshot IDs, required source coverage, timestamps, expiry and active whitelist. Check that evidence is available as of the decision and conflicts/unknowns are handled according to policy.
2. **Allowed to act.** Confirm RUN plus paper/live authorization, no active pause/lock, approved strategy family and direction, no event blackout or loss cooldown. An unapproved new setup remains research-only. AI stand-down, missing mandatory AI output or unresolved verification blocks entry.
3. **Market is sane.** Verify current spread, liquidity, volatility, data continuity, exchange operational state and permitted instrument; funding and leverage constraints where relevant. Recheck current conditions at execution time.
4. **Executable levels independently established.** Verify the specific strategy trigger, recompute or reject proposed entry/stop/target levels using deterministic market rules, exchange precision and minimum distances. No AI-generated price is trusted as an executable value. No valid trigger or formal invalidation means no entry.
5. **Worth it.** Use a prevalidated strategy-specific edge/cost model, accounting for fees, spread, slippage, funding if applicable and uncertainty. Never manufacture expected return from model confidence or a target/stop ratio. Untested hypotheses stay paper-only.
6. **Within risk, then sized.** Code calculates stake from stop distance, equity, per-trade risk, exposure/correlation, max positions, daily/weekly loss and drawdown; account for pending orders and partial fills. AI may recommend reduced risk or no trade but cannot increase an otherwise permitted stake. Revalidate after quantity rounding and exchange min/max constraints; reject rather than enlarge to meet a minimum.

**Outputs:** REJECT (reason), APPROVE_PAPER, APPROVE_LIVE (only under authorized policy), or ASK_HUMAN for discretionary policy requirements. Human approvals expire (initial target: 15 minutes); expiry/no answer rejects the proposal. Human approval cannot override a detected hard-limit breach. Protective actions never wait for an AI call or approval.

**Intent contract:** Unique intent ID, proposal/snapshot IDs, mode, approved symbol/direction, authorized quantity or maximum notional, exact permitted levels/constraints, policy hash, created/expiry timestamps, execution ownership epoch and durable lifecycle status. Atomically claim and persist the intent before submission. Expiry or changed policy invalidates an unsubmitted intent; submitted/unknown orders must be reconciled, never silently dropped. Freqtrade revalidates authorization and actual order parameters before submission. No error fallback can substitute an unapproved quantity or entry.

---

## 6. Execution and Safety

### 6.1 Order Rules

- Freqtrade is the sole routine order owner. Its interface is private, forced entry is disabled, and the research workflows have no trade credentials or execution API access. The watchdog can request PAUSE/protection; emergency direct exchange operations are allowed only under verified exclusive custody (below).
- Each intent has a unique ID and durable claimed/submitting/submitted/unknown/partially-filled/filled/terminal state. Derive a client order ID where supported; verify venue-specific lookup and uniqueness semantics. Persist before submission, reconcile ambiguous acknowledgments, and do not assume exactly-once external execution.
- Entries use limit or post-only orders with a timeout and a slippage cap. Market orders are for emergency exits only.
- Prefer venue-native attached/bracket protection where supported; otherwise submit the exchange-side stop immediately for actual filled quantity. Track partial fills and stop coverage, trigger mode, submitted/confirmed/triggered/fully exited separately. Reconcile cancellations/replacements and stop coverage under ownership. Reject venues or products that cannot satisfy the tested protection requirements. A price gap or outage may delay or worsen a fill; stop placement is not a guarantee of execution.
- Never blindly retry a submission. Check by client order ID first. Never re-submit an order whose status is unknown; reconcile first. Never average down.
- Respect rate limits, back off between retries, and honor exchange maintenance windows.
- Freqtrade keeps its own protections (stoploss guard, max drawdown, cooldowns) as a second line behind the Gate. Qualify the integration with actual exchange requests and test fallback/rounding/exception paths. If the stock strategy interface cannot enforce the exact authorized intent, use a narrowly scoped, tested execution adapter or choose a compatible engine; never depend solely on a prompt or strategy callback.
- **Exchange cancel-all-after timers:** do not arm one on a directional account unless you have confirmed your protective stops survive it. Many venues' timers cancel stops along with everything else.

**Execution ownership and emergency watchdog:** Use one active order writer per account/symbol and a persistent ownership epoch. The watchdog normally requests restrictive actions through this owner and independently verifies exchange state. An emergency takeover must stop/fence the previous writer, reconcile in-flight/unknown orders and obtain exclusive custody before sending protective orders. Limit emergency operations to reducing exposure or restoring protection; use exchange-side reduce-only constraints where supported and validate spot balances/quantities. Exchange API keys may not provide enforceable reduce-only permissions; do not enable a second writer if exclusivity or safety cannot be demonstrated. If takeover is unsafe or unreachable, alert the human, preserve existing exchange protection, and never claim that flattening succeeded. Test old-host fencing during restore; do not allow both old and replacement hosts to trade.

### 6.2 Reconciliation (on every start, then every 5 minutes)

1. Block new entries.
2. Pull open orders, positions, and balances from the exchange.
3. Compare with the app's records.
4. Flag every mismatch. Adopt or cancel unknown orders only under the previously authorized reconciliation policy; unresolved orders/positions keep entries paused and may need human review.
5. Confirm every position has live, adequate stop coverage. The authorized owner restores missing coverage or reduces exposure under emergency policy; verify the exchange result.
6. Resume only after all checks are clean, the prior pause reason permits it, and current authorization is valid.

### 6.3 Modes

Three operational modes, independent of account authorization (PAPER / LIVE). The watchdog and supervisor may restrict risk, never increase authorization. Store pause reasons, locks, policy version and live authorization durably. Recovery may clear only a transient operational pause under the preapproved policy; it cannot remove human, limit, security or unresolved reconciliation locks.

| Mode | Meaning | Entered when | Leaves when |
|---|---|---|---|
| **RUN** | Normal operation within limits | Explicit unlock or qualified transient recovery | On pause, fault or flatten |
| **PAUSED** | No new entries. Working entry orders cancelled. Stops stay. Exits and stop management continue | Any start or restart, stale data, reconciliation mismatch, exchange trouble, AI unavailable, loss limit hit, repeated errors, your pause command | After a restart or a transient cause, automatic recovery may return to RUN once health checks pass and reconciliation is clean. Human pauses, limit breaches, unresolved mismatches, security incidents and anything after FLATTEN need authorized human review and strong authentication (a separate preapproved daily cooldown policy may expire only its own lock) |
| **FLATTEN** | Cancel working entries, reduce existing exposure while preserving protection until verified flat, then cancel obsolete stops and lock | Drawdown lock, position without a stop that cannot be fixed, suspected key compromise, your flatten command | You only, after review |

### 6.4 Kill Layers

1. **Exchange-side stops.** Confirmed venue-hosted stops can remain active during host failure, subject to venue availability, order semantics and liquidity.
2. **Watchdog.** A separate container with read-only access and, only after qualification, restricted emergency execution custody. It can cover app/process failures but not a dead host. Exchange-side protection and the external Recovery Console cover host silence to the extent possible. Before scaling, evaluate external watchdog placement with verified exclusive order ownership.
3. **You, from a phone, by buttons.** Safe reset, Pause, or Flatten in the Recovery bot (Section 7.4). If those fail: exchange app cancel and close; revoke the API key; move funds out of the sub-account.

### 6.5 Starting Limits

Conservative placeholders for you to set deliberately. They are not recommendations for your capital.

| Control | Where enforced | Starting default |
|---|---|---|
| Risk per trade, measured at the stop | Gate and Freqtrade | 0.25 to 0.5% of equity |
| Max open positions | Gate and Freqtrade | 3 |
| Max gross leverage | Freqtrade and the exchange account setting | Initial design recommendation: unleveraged spot; derivatives/leverage deferred pending separate eligibility, risk and execution qualification |
| Daily loss | Freqtrade protections and watchdog | 1.5% of equity: PAUSED until the next UTC day |
| Weekly loss | Watchdog | 4%: PAUSED until you resume |
| Peak-to-trough lock | Watchdog | 8%: FLATTEN, then locked |
| Position without a stop | Watchdog | Immediate protective handling; alert by 30 seconds, attempt qualified emergency reduction by 60 if protection cannot be restored |
| Slippage anomaly | Gate | Realized slippage above 3x the model over a recent window: PAUSED |
| Capital cap | Segregated account balance where supported | Only what you can afford to lose entirely; no borrowing or leverage in the initial test. This is a containment measure, not a guarantee against all losses. |
| Withdrawals | Exchange key permissions and withdrawal address allow-list | Disabled |
| AI spend | Provider console and app | Monthly cap plus daily token cap |

### 6.6 Who May Do What

| Level | Actions | Who | Approval |
|---|---|---|---|
| 0 Read | Prices, balances, status, trade history | Brain, watchdog | Automatic |
| 1 Prepare | Continuous research, intelligence records, proposals, reports | Brain / AI | Automatic; no order access |
| 2 Restrict and recover (safe direction) | Pause entries, block a symbol, flatten, restart app, reboot server | Recovery bot buttons, watchdog, supervisor | Automatic under qualified safety policy; human pause one tap, authenticated confirmation for restart, reboot and flatten |
| 3 Trade | Open a position inside limits | Freqtrade only | Paper: automatic. Live: only after you unlock |
| 4 Money or irreversible | Raise size or leverage, loosen a stop, new key, withdraw, go live, resume after a limit breach | You only | Strong authentication through a separate authenticated console (e.g., passkey or authenticator challenge, never a code pasted into chat). Withdrawals are disabled for bot keys. |

---

## 7. Staying Up and Recovering

**Goal:** the system should not die. If it does, it recovers itself and tells you at each step. If it cannot, you reset it with one button. No command line is ever needed.

### 7.1 Staying Up

- **Restart basics:** every container restarts automatically after a crash, Docker starts on server boot, and each container has a health check that catches hangs as well as crashes.
- **State survives:** the database lives on the server's persistent disk. A nightly encrypted copy goes to off-server storage, plus the provider's automated snapshots. **Test a restore every month.**
- **AI trouble:** primary to backup provider, then no new entries. Alert after two failed cycles. Open positions are unaffected.
- **AI budget:** alerts at 50% and 80% of the cap. At the cap AI calls and new live entries stop; rules-only paper benchmarks may continue, as may protection and reconciliation.
- **Updates:** pin every version. Never auto-update Freqtrade or the brain. Apply those updates only in PAUSED with no open positions. Operating-system security updates may be automatic within a qualified maintenance policy; reboot only under the safety/recovery constraints in Section 7.2.
- **Exchange changes:** watch the exchange's status page and announcements. Unexpected API errors send an alert and move to PAUSED.
- **Time and disk:** UTC everywhere with the server's time sync on. Rotate logs and alert at 80% disk. A full disk is a classic silent killer.

### 7.2 Self-Recovery

Three layers, tried in order. You do nothing unless the last one fails.

| Layer | Fixes | Done by |
|---|---|---|
| **1. Container recovery** | A crashed or hung service | Docker restart policies, health checks, and the supervisor on the server |
| **2. Server recovery** | A frozen, unreachable, or unbootable server | The Recovery Console outside the server, using the cloud provider's reboot and power-cycle API |
| **3. You, by button** | Anything the first two could not fix | The Recovery bot buttons (Section 7.4) |

**Recovery rules**

1. **Every start begins in PAUSED.** After any crash, restart, or reboot, new entries require reconciliation (Section 6.2), health checks, valid authorization and no unresolved lock.
2. **Recovery never loosens risk.** It will not return to RUN after a human pause, FLATTEN, a locked loss-limit pause, an unresolved reconciliation mismatch, or suspected key compromise. Those need authorized human review. A preapproved daily cooldown may clear only its own timed lock.
3. **Restarts happen in order:** database check, Freqtrade, brain, watchdog. Research may operate in log-only mode during recovery; the brain cannot produce authorized entry intents before Freqtrade has reconciled and the watchdog/Gate are healthy.
4. **Crash-loop guard:** if the same service restarts more than 3 times in 30 minutes, automatic restarts stop and you are told. A loop means a bug or bad config, and restarting forever hides it.
5. **Restart with open positions only under the qualified recovery policy.** Confirm venue-hosted protection where reachable, persist pause/lock state, fence old execution ownership and reconcile uncertain submissions before any new orders. Stops and client IDs alone cannot guarantee absence of loss or duplicates.
6. **Every step is logged and announced** (Section 7.3).

**Timeline, server reachable (the app is hung or crashed).** Times are adjustable defaults.

| Step | When | Action | Push you receive |
|---|---|---|---|
| Detect | Within 2 minutes of a failed health check or missing heartbeat | Enter PAUSED | DOWN |
| Attempt 1 | Immediately | Restart the failed service | RECOVERING 1 of 3 |
| Verify | 3 minutes later | Healthy, clean reconcile, 3 good heartbeats | RECOVERED, or on to attempt 2 |
| Attempt 2 | 5 minutes after attempt 1 | Restart the whole stack in order | RECOVERING 2 of 3 |
| Attempt 3 | 15 minutes after attempt 1 | Reboot the server | RECOVERING 3 of 3 |
| Give up | 30 minutes after detection | Stay PAUSED | NEEDS YOU, repeating |

**Timeline, server silent (frozen, off, or unreachable).**

| Step | When | Action | Push you receive |
|---|---|---|---|
| Detect | Heartbeat silent for 3 minutes, confirmed by a second check | None possible from inside | DOWN |
| Attempt 1 | Right after confirming | Soft reboot through the provider API | RECOVERING 1 of 2 |
| Attempt 2 | 10 minutes after detection | Hard power-cycle through the provider API | RECOVERING 2 of 2 |
| Verify | After each attempt | The server boots into PAUSED, the supervisor reconciles, heartbeats resume | RECOVERED, or on to the next step |
| Give up | 20 minutes after detection | Nothing more automatic | NEEDS YOU, repeating |

### 7.3 Push Notifications

Four messages tell you where you stand. Each one says what is happening in plain words.

| Message | Contents |
|---|---|
| **DOWN** | What failed, since when, current mode, open positions and whether their stops are confirmed on the exchange (if known), and what the system will try next |
| **RECOVERING (n of N)** | The action being tried and when the next check happens |
| **RECOVERED** | How long it was down, the cause category, current mode, confirmation that positions were reconciled and stops are in place, and how many checks passed |
| **NEEDS YOU** | What failed, what was tried, the state of your positions, and the buttons to press |

**Rules for notifications**

- **RECOVERED is never self-declared.** It is sent by the Recovery Console after it sees steady heartbeats, the supervisor's clean reconcile result, current ownership and, for live operation, independently checks exchange exposure/protection via read-only access. If exchange truth is unavailable it reports service recovery separately from unverified trading safety. The thing that was broken does not get to announce that it is fixed.
- If the system is back but waiting for you, the message says so: "Running but PAUSED, your OK is needed." It is never a plain "recovered."
- **Two channels.** Telegram carries every stage. A second channel (for example Pushover, whose emergency priority repeats until you acknowledge it, or ntfy, SMS, or email) carries DOWN and NEEDS YOU. Telegram can be muted or have an outage, and a muted alert looks like silence.
- One thread per incident, one push per stage. NEEDS YOU repeats every 15 minutes until acknowledged. No alert storms.

### 7.4 Reset by Button (No Command Line)

The buttons live in the **Recovery bot**, a Telegram bot that runs on the Recovery Console **outside** the trading server. This matters: the thing that tells you the server is dead, and the buttons that fix it, cannot live on the server. Two Telegram bots exist:

- **Desk bot:** reports, daily summary, trade approvals. Lives on the server.
- **Recovery bot:** recovery alerts and the buttons below. Lives outside the server.

| Button | What it does | Confirmation |
|---|---|---|
| **Status** | Server, app, mode, last heartbeat, open positions and their stop status, AI budget | None |
| **Safe reset** | Pause, restart the app, reconcile, report. If that fails, reboot the server, reconcile, report. Sends the same RECOVERING and RECOVERED pushes as automatic recovery | Authenticated console, one confirm |
| **Restart app** | Restart the services in order; boots into PAUSED and reconciles | Authenticated console, one confirm |
| **Reboot server** | Soft reboot through the provider API | Authenticated console, one confirm |
| **Power-cycle server** | Hard power cycle, the last resort before the provider console | Authenticated console, confirm with a warning |
| **Pause** | Switch to PAUSED | One tap |
| **Flatten** | Request coordinated exposure reduction and lock; verify actual closure | Strong authentication and confirm |
| **Resume** | Request to leave PAUSED; requires healthy reconciliation and explicit lock review | Confirm in authenticated console (passkey or authenticator challenge) |
| **Open provider console** | Link to the provider's own mobile console for a manual reboot or snapshot restore | Link |

**How the buttons reach the server**

- App-level commands travel by outbound polling: the supervisor on the server asks the Recovery Console for pending commands. The trading server needs no open inbound port for this.
- Server-level commands go through the cloud provider's API. The provider token lives only in the Recovery Console, in a provider project that contains only this server. A leaked token could harm the server but cannot move funds, since withdrawals are disabled at the exchange.
- The Recovery Console should hold a separately scoped **read-only** exchange credential for independent live protection verification. If this is unsupported, live recovery remains unverified/PAUSED until an authorized check. It cannot flatten positions directly with read-only access. A remote command remains pending or unavailable if the core cannot receive it; show this explicitly and direct the user to the exchange’s own app when necessary.
- Provide the same controls through a small mobile web page served by the external Recovery Console, behind a passkey or authenticator login. Signed, expiring, replay-resistant command IDs bind user authorization to the exact action and target; persist acknowledgments and verified results.

**Safety of the buttons**

- The Recovery bot answers only your Telegram user ID. Turn on Telegram's two-step verification for your account.
- Restart and reboot must follow the qualified recovery procedure; confirmed protective orders can survive host loss, but unknown order state or missing stops require escalation, not reassurance.
- A hijacked Telegram account could disrupt service or request financially harmful actions. Require independent strong authentication for flatten, restart/reboot and resume; Telegram identity alone is insufficient for these controls. A one-tap pause may still disrupt trading, so rate-limit and audit it. Never treat disabled withdrawals as protection against trading losses.

**If the buttons fail too (last-resort order):** Safe reset, then Power-cycle, then the provider console (restore the latest snapshot to a new server in a few clicks; it boots into PAUSED and reconciles), then the exchange app (cancel orders and close positions), then revoke the API key, then move funds out of the sub-account. Keep the Recovery bot chat, the exchange app, and the provider console link on your phone's home screen.

---

## 8. Strategy and Evaluation

### 8.1 Strategies and research discovery

Start with a narrow, human-approved whitelist of liquid pairs and an hours-scale trend/momentum rules baseline. Initial recommendation: spot with no leverage, subject to local eligibility, venue support and human approval. Include a separately defined AI research-assisted setup family (e.g., event/context-supported trend or momentum entry with an independently confirmed price/volume trigger). The AI can discover hypotheses and propose setups without waiting for a rule-generated candidate; a setup with no preapproved, deterministic executable definition remains a research-only experiment. Define entry trigger, invalidation, exit, holding horizon and cost assumptions before evaluation. No memecoin, market-making, pure social sentiment or automated strategy promotion in v0.6.

### 8.2 Shadow trading and AI contribution

Measure both the AI's ability to discover new opportunities and its ability to reject or reduce poor candidates. Preserve all proposals, including rejected ones, and simulate counterfactual entries with the same time-consistent execution model. Shadow trades are not real fills. A veto does not necessarily improve portfolio return merely because blocked trades performed worse; opportunity cost and interactions matter. Compare portfolios, not just average isolated trades. Reviewer evaluates the original hypothesis and observed outcome without rewriting the original record. It may suggest future changes but cannot alter active live policy or promote itself.

### 8.3 Parallel simulated portfolios

| Portfolio | Purpose |
|---|---|
| Cash | No-trade benchmark |
| Buy and hold of whitelist | Exposure benchmark |
| Rules-only | Isolate performance without AI |
| Rules + AI veto | Measure contextual filtering |
| Rules + AI veto and reduction | Measure risk restriction contribution |
| AI research-assisted proposal strategy | Test whether AI-originated, Gate-validated opportunities add value |
| Random/time-matched entries with same exits and sizing constraints | Check against luck and market drift |

Use shared point-in-time evidence, time window, initial capital, consistent cost models, comparable exposure and risk constraints. Independent virtual account ledgers account for competing capital, pending orders, overlapping positions, missed trades and exposure. Allocate actual data/AI expenses consistently and report shared versus marginal costs separately.

### 8.4 Honest testing and intelligence quality

- Deterministic rules can be tested on historical data with walk-forward splits. AI judgments over historical events may be contaminated by training data or retrieval of later information; forward paper testing is preferred. Archive as-of inputs and exact responses. Replay recorded responses and deterministic decisions, not fresh AI guesses. Never use revised news timestamps, future prices or outcomes as earlier features.
- Dry-run fills are optimistic (queue position, latency, liquidity and impact are not replicated perfectly). Qualify supported testnet where available and model conservative fees, spread, slippage, funding, rejection/partial-fill and missed-fill assumptions. A testnet does not prove real liquidity or profit.
- Evaluate intelligence itself: factual claim verification against original sources, evidence coverage, event duplication, asset association accuracy, detection latency, false alarms, handling of uncertainty/conflicts, follow-through of directional/horizon hypotheses, and actual incremental trading value. Calibrate model probabilities only if a defined target/outcome and sufficient forward measurements exist.
- Compare predicted stance and event impact to market outcomes at predeclared horizons using consistently defined metrics. Include negative and no-trade cases. Never equate narrative coherence, raw sentiment or AI confidence with edge.

### 8.5 Pre-registered criteria

Write criteria and analysis plan before the forward evaluation window, not after viewing results. Freeze strategy versions and proposal families; material changes start a new evaluation cohort.

**Operational (all required):** target uptime; zero unauthorized/duplicate submissions; no unprotected exposure beyond the qualified mitigation window; all drills passed; clean reconciliation; verified recovery and reliable user alerts; correct provenance and expiry; sufficient valid research/AI outputs and budget adherence. Distinguish paper acceptance from live stop/order capability verification.

**Performance (all required before promotion):**

- Initial floor from v0.5: roughly 90 days and 200 closed trades covering multiple regimes; extend when necessary. This is a minimum design target, not a guarantee of statistical adequacy.
- Positive net expectancy after exchange costs with confidence interval excluding zero, consistent net portfolio performance and drawdown within the predefined budget. Include data, AI, hosting and other operating costs when assessing economic viability.
- Compare with cash, buy-and-hold, rules-only, randomized and AI variants. Predefine risk-adjusted metrics and acceptable trade-offs (total return, drawdown, exposure, turnover, missed winners, capital utilization, fees and operational burden).
- Use time-block or comparable dependence-aware resampling when trades cluster and report uncertainty across regimes. Avoid multiple-testing selection bias; require fresh forward evidence for the chosen variant. If AI discovery/veto does not improve the predeclared criteria relative to its baseline, do not promote the AI-assisted variant. Retain research as research-only, simplify or stop.

### 8.6 Live canary and stop rule

Only after all paper and venue-specific acceptance gates, and only with explicit human authorization: capped segregated account, smallest viable size, qualified protection and risk limits. Keep the original v0.5 canary target of at least the shortest paper evaluation window, subject to predeclared policy. Scale only in pre-written steps. Stop if the defined edge disappears (e.g., failure to outperform baselines over the agreed window), a hard limit is breached, operational acceptance regresses, or cost/risk exceeds policy. No automatic capital increase or model-driven promotion.

---

## 9. Build Plan

Design-only sequencing; no implementation authorization is implied. Each phase retains v0.5's gate-before-progression approach.

**Phase 0: Foundation and capability qualification.** Confirm eligible venue/instrument, documented API behavior, execution/stop semantics, permitted data access and provider limits. Cloud server, Docker Compose, Freqtrade dry-run, durable intent ledger with dummy intent, Desk notifications, supervisor, external Recovery Console, heartbeat, second channel, backups and minimal authenticated dashboard.
*Done when:* 7 days unattended, safe reboot/restore, dual-channel silence alert, phone-only Safe reset and verified recovery, no trade-enabled research interface. Verify intent consumption and exact authorization against a non-money execution fixture.

**Phase 1: Continuous research brain, log-only.** Collect and archive sources; add Scout, Researcher, Analyst, Strategist and Reviewer workflows; typed evidence/events/intelligence/proposals, bounded verification, deterministic baseline and Gate; research memory and replay, event-driven research plus hourly synthesis, dashboard evidence view. Do not transmit executable intents to live trading.
*Done when:* at least two weeks of operation with ≥99% valid AI responses among required completed calls (also report availability/timeouts separately); news or social events can produce evidence-linked intelligence and proposals even without preexisting rules candidates; stale/duplicate/conflicting/fabricated evidence and prompt injection handled properly; historical replay reproduces deterministic decisions; outages and spend caps respected; independently labeled research sample meets preregistered quality threshold. Freeze criteria before evaluating it.

**Phase 2: Closed paper loop and drills.** Authorized intents flow through Freqtrade dry-run or supported testnet, with exact sizing/ownership, order tracking, watchdog/reconciliation, simulated portfolio comparisons, verified daily reports and all drills. Demonstrate qualifying AI-originated proposals reaching the paper engine as well as properly rejected ones.
*Done when:* all drills pass; two weeks of unattended closed-loop behavior; coverage of accepted/rejected/verify-more/expired proposals; no duplicate or unauthorized orders; protection/partial-fill and recovery behavior qualified in the appropriate environment, with any venue-specific gaps blocking live promotion.

**Phase 3: Evaluate, then decide.** Execute frozen forward comparison (Section 8); assess research quality, incremental net portfolio value, operating costs and risk. Optional separately approved live canary only after all capability/safety gates and human review. Scale or stop according to prewritten rules.

**Drills (paper/testnet or explicit non-money fixtures; pass means safe and reconciled, not just a successful process restart):**

1. Kill Freqtrade mid-order; reconcile exact state without a duplicate submission, including missing acknowledgment and partial fill.
2. Power off host with a simulated/testnet protected position; confirm external DOWN/RECOVERING/reboot/reconcile/RECOVERED and actual test-venue protection status. Verify that an old restored machine cannot also become order owner.
3. Disable both AI providers, separately exhaust AI budget; no new live entries (independent rules-only paper benchmarks may continue), alert, continue protection and reconcile.
4. Feed stale market/news data, malformed output, invalid evidence IDs and prompt-injected articles; reject unsafe proposals. Duplicate/syndicated stories must not count as independent evidence.
5. Delete protection in the test venue; restore adequate coverage or reduce exposure under verified ownership, escalate if unable. Simulate simultaneous normal and emergency exit requests; prove no unintended reversal or oversell.
6. Revoke trade key; separately simulate exchange outage. PAUSED, no blind retries, alert and mark unknown state accurately.
7. Restore to new server from backup using phone/provider console; fence old host, preserve locks, ledger and mode authorization; reconcile before any entry.
8. Freeze brain; supervisor detects hang, restarts it; DOWN/RECOVERING/RECOVERED messages truthful and ordered. Research resumes without automatically replaying expired proposals.
9. Make recovery impossible; NEEDS YOU via both channels, repeated until acknowledged; controls show pending/unavailable when the host cannot receive them.
10. Exercise each Recovery button via mobile data; verify authenticated actions, confirmation, expiry/replay resistance and exchange-confirmed completion where applicable.
11. Provide synthetic material news while deterministic rules produce no proposal. AI independently generates a supported research hypothesis/proposal or explicit no-trade; unauthorized family or missing trigger stays research-only. Verify contradictory and corrected information produces linked revisions, not rewritten records.
12. Exercise VERIFY_MORE, timeouts, model disagreement, invalid confidence and API fallback; do not let any bypass the Gate. Verify sizing clamps, min-notional and exception paths cannot enlarge an authorized stake.
13. Replay a saved cycle, using only information available then and recorded model outputs; identical deterministic decision. Verify parallel portfolios do not share or accidentally double-count positions/capital.
14. Simulate human pause, timed cooldown and hard risk/security lock through reboot/restore; no unauthorized automatic resume or live unlock. Verify recovery monitor distinguishes healthy services from unknown exchange protection.

---

## 10. Stack

| Need | Pick |
|---|---|
| Execution | **Freqtrade** (dry-run first, on-exchange stop, protections). Later option if you outgrow it: NautilusTrader |
| Exchange connectivity | **CCXT** (inside Freqtrade, and for the brain's read-only data) |
| Brain | Plain Python service: collectors, scheduler, five AI workflows, deterministic rules/Gate, Pydantic validation, Reviewer. No agent framework needed |
| Data | Permitted news/RSS/API, official release/calendar sources, supported social access, exchange streams/CCXT, selected chain/derivatives feeds; qualify before committing |
| AI | One cloud AI API with forced JSON output, plus a second provider as backup |
| Storage | **SQLite** (write-ahead mode) on persistent disk; explicit single-writer domains, point-in-time evidence and durable intents/outcomes; supported consistent backups. PostgreSQL only if measured scale warrants it |
| Packaging | **Docker Compose** with automatic restart |
| Host | Small Linux VPS with static IP and provider backups |
| Alerts and controls | **Telegram**, two bots: Desk (on server) and Recovery (outside), plus responsive authenticated dashboard and external recovery web console. Sensitive authorization outside chat |
| Supervisor | Docker restart policies and health checks, plus a small supervisor container that restarts failed services in order and polls the Recovery Console |
| Recovery Console | A small always-on function or tiny server at a different provider: Recovery bot, silence detection, cloud-provider reboot and power-cycle API. Healthchecks.io webhooks can trigger it |
| Second alert channel | **Pushover** (emergency priority repeats until acknowledged), or ntfy, SMS, or email |
| Heartbeat | **Healthchecks.io** (hosted), or Uptime Kuma on another host. Route its alerts to the second channel as well as Telegram |
| Secrets | Env files outside the repository with tight permissions, or Docker secrets. SOPS with age if you want them encrypted in git |
| Backups | Provider snapshots plus nightly **restic** to off-server object storage |
| Reports (optional) | QuantStats |

Add no unnecessary infrastructure until Phase 2 is green; select only the initial sources and workflows required to test the original article’s research-to-execution loop.

---

## 11. Decisions You Must Make

1. Venue and asset class, subject to eligibility where you live.
2. The maximum loss you can accept entirely. This is the sub-account cap.
3. The exact numbers in Section 6.5.
4. The initial whitelist, venue/product, baseline rules strategy, approved AI-assisted setup family and deterministic trigger/level definitions.
5. Primary and qualified backup AI providers; data/search subscriptions, coverage/licensing, monthly spend cap and daily local budget.
6. The pre-registered promotion and stop criteria, written before Phase 3.
7. A cloud provider whose API can reboot and power-cycle servers.
8. A second alert channel, separate from Telegram.
9. Qualification of separate read-only Recovery Console access and exclusive emergency watchdog authority; if unsupported, recovery cannot independently confirm protection and live activation remains gated.
10. Required news/social/market sources per strategy, evidence freshness/expiry policy, event importance thresholds and research quality targets.
11. Dashboard access/authentication and retention/licensing policy for raw evidence.
12. Predeclare research and portfolio evaluation criteria, including the AI-assisted variant and operating costs; no capital commitment yet.

---

## 12. Known Limits, and What Not to Add

- **Recovery has limits.** It fixes crashes, hangs, and server faults. It cannot fix an exchange outage, a revoked key, a provider-wide outage, a bad strategy, or a logic bug, and it will not restart trading by itself after a limit breach, a mismatch, or FLATTEN. If both alert channels are down at once, you will not be told. Exchange-side stops can remain active when the host is down, but depend on venue availability, order type and market liquidity. They do not guarantee prices or complete exits.
- **No design guarantees profit.** The likely outcomes include a rules strategy with no edge after costs, and AI research or filtering that adds nothing. This design handles that by measuring, then simplifying or stopping.
- **More revision will not settle** whether your strategy has an edge, whether your venue is open to you, or your tax and legal position. Running Phases 0 to 2 will settle the technical questions faster than another draft.
- **This is a design-only proposal.** The next step, if authorized, is capability qualification and a task ledger for Phase 0 followed by the log-only intelligence loop. No implementation, capital funding or live trading is authorized by this revision.
- **Deliberately left out:** home GPU and local models, separately hosted multi-agent infrastructure, chat-agent trading tools (including any Freqtrade MCP server with order tools), market making, memecoins, browser automation, Kubernetes, and workflow engines.
- **Verify before relying:** tool and exchange behaviors here reflect general knowledge as of late 2026. Check current documentation for Freqtrade, each exchange's key permissions, testnet behavior, and stop-order semantics, and for each AI provider’s structured-output support, search/data access, data terms, pricing and actual spend-limit controls. No provider, venue, cost or performance claims are verified in this revision.

---

## Appendix A: Sources

**Design lineage:** `24-7_Automated_Trading_Desk_Design_v0.5.md` (Sections 2–12, recovery and execution foundation); `24-7_Automated_Trading_Desk_Design_v0.3.md` (Sections 1–5 and Addendum #3, research-first intent). Source posts and ecosystem links below are inherited from those documents, not newly independently verified. The v0.6 workflows, schemas, frequencies and acceptance criteria are proposed design choices, not claims made by the original articles.

**Original X posts (full text is in the v0.3 addenda)**
- https://x.com/savipww/status/2121634572626452627
- https://x.com/bloggersarvesh/status/2032130279494853118
- https://x.com/A_Pirate_I/status/2116889572982284516

**Coverage and mirrors of the same ecosystem**
- Engadget on Grok Bot early beta: https://www.engadget.com/2259931/spacexai-grok-bot-early-beta-how-to-try/
- OnlineMarketing.de on Grok Bot 24/7 X monitoring: https://onlinemarketing.de/technologie/grok-bot-ganz-x-durchsuchen-konkurrenzmodelle
- Market-making desk guide mirror: https://youmind.com/landing/x-viral-articles/build-ai-market-making-desk
- Research-desk guide mirror: https://youmind.com/landing/x-viral-articles/grok-bot-ai-hedge-fund-research
- Bitrue on limits and risks of Grok-style trading bots: https://www.bitrue.com/blog/can-grok-ai-trading-bot-really-make-money
- ForkLog on AI trading bot setup: https://forklog.com/en/news/ai-trading-step-by-step-guides-bot-setup-pros-and-cons
- MQL5 community post on Grok API capacity: https://www.mql5.com/en/blogs/post/764627
- Khaleej Times commentary: https://www.khaleejtimes.com/business/commentary-ai-bots-promise-profits-while-you-sleep-but-risks-remain-wide-awake

---

## Appendix B: Plain-Language Glossary

- **Veto:** a contextual filter that can reject or recommend lower risk for a candidate; distinct from the AI’s research-only ability to originate proposals.
- **Intelligence record:** structured, evidence-linked interpretation of market information, not an order.
- **Trading proposal:** an untrusted hypothesis that a setup might be worthwhile; no execution authority.
- **Authorized intent:** a durable, expiring record created only after independent deterministic approval.
- **Point-in-time data:** evidence actually available to the desk when a decision was made, before later revisions/outcomes.
- **Research memory:** searchable history of original research and measured outcomes, not model training.
- **Execution ownership:** independently controlled right to submit orders; emergency takeover must prevent simultaneous order writers.
- **Shadow trade:** a trade the AI blocked, simulated anyway, so you can see whether the block was right.
- **Reconciliation:** comparing your records with the exchange's and fixing every difference.
- **Expiry (TTL):** a time stamp after which an instruction is ignored automatically.
- **Slippage:** the gap between the price you expected and the price you got.
- **Bootstrap confidence interval:** resampling trade results or time blocks to estimate uncertainty, with dependence-aware methods where appropriate. An interval including zero does not establish reliably positive expectancy.
- **Cancel-all-after (dead-man switch):** an exchange timer that cancels your open orders if you stop checking in. It can cancel protective stops too.
- **Heartbeat:** a small "I'm alive" signal sent on a schedule. Silence is the alarm.
- **Supervisor:** a small program whose only job is to notice failed services and restart them in the right order.
- **Crash loop:** a service that keeps dying and restarting. Automatic restarts stop after a few tries so the problem is not hidden.
- **Idempotent:** doing the same thing twice has the same intended effect; venue-specific order identification, durable state and reconciliation are necessary to avoid duplicate external orders.
- **Ablation:** removing one part to see whether results get worse. If not, the part was not earning its place.

---

*End of Design Document v0.6*
