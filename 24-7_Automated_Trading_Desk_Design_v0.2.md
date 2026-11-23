# Design Document: 24/7 Automated Trading Desk

**Version:** 0.2 (revision of Grok's v0.1)
**Date:** 2026-11-17
**Status:** Design only. Not production-ready. Not financial advice.
**Goal:** An always-on research → decision → execution system that keeps working, or stops safely, when no chat session, laptop, or human is available.

---

## 0. What Changed From v0.1 (Read This First)

v0.1's skeleton is sound and is kept: AI never places orders, paper first, risk limits in code, human approves going live. The gaps are in what makes a system actually survive 24/7, and in how it is judged.

| Area | v0.1 | Problem | v0.2 fix |
|---|---|---|---|
| "Fail closed" | Do nothing when something fails | Doing nothing leaves open positions unprotected | Protective stops live on the exchange itself; a degraded-mode ladder; an independent watchdog |
| Order handoff | Decision layer "sends an order intent" to Freqtrade | Freqtrade is strategy-driven, not an intent receiver; the handoff is unspecified | Intent table that a Freqtrade strategy reads and can still veto; thin adapter so the engine can be swapped |
| Paper trading | Freqtrade dry-run | Dry-run fills are optimistic (no queue position, latency, or market impact) | Exchange testnet plus an explicit cost/slippage model; paper phase is a systems test first, a performance test second |
| Promotion to live | 30+ days and some number of trades | Too little data to tell skill from luck | Pre-registered, baseline-compared criteria with confidence intervals, then a capped live canary |
| LLM backtesting | Not addressed | The model has already "seen" history, so historical tests leak the answer | Forward-only evaluation, or post-training-cutoff data; archive every input point-in-time |
| LLM authority | "Rules decide" | No rule about which direction the LLM may push risk | Asymmetric authority: the LLM may tighten risk, never loosen it |
| Untrusted text | Not addressed | News and X posts are attacker-controlled input to a model that influences trades | Research model has no tools; outputs are enums and IDs; per-signal size caps |
| One-box assumption | One host | If the GPU box is off, everything is off | Two planes; research packets carry an expiry so stale research cannot trade |
| State truth | Internal logs | Restarts and retries cause duplicate or orphaned orders | Exchange is the source of truth; idempotent order IDs; mandatory reconciliation on startup |
| Kill switch | "Supervisor" | Lives on the same machine that failed | Four-layer kill: software, exchange, independent watchdog, human |
| Monitoring | Telegram alerts | A dead system sends no alerts | External heartbeat monitor on a third host that alerts on silence |
| Strategy | Undefined | A pipeline with no defined edge is infrastructure, not a trading system | Honest strategy menu and a required test: does the LLM add anything over plain rules? |
| API keys | Withdrawals disabled | Necessary, not sufficient | Capped sub-account, IP allow-list, separate watchdog key, no secrets in LLM-reachable containers |

---

## 1. Sources Examined

**Important limitation:** the three X posts listed at the bottom of v0.1 could not be opened. X blocks automated access, so I could not verify what they say. I worked from coverage and mirrors of the same ecosystem instead (URLs in Appendix B). Treat the notes below as the pattern those posts most likely belong to, not as a summary of the posts themselves.

**What the ecosystem shows**

- **Grok Bot** is xAI/SpaceXAI's persistent-agent product, introduced in August 2026 and described by Engadget as early beta. Bots you create share one cloud computer (browser, filesystem, terminal) and keep working while you are away. It also supports 24/7 monitoring agents for X topics.
- **The market-making desk guide** (RohOnChain, Aug 2026) describes six cooperating bots on Grok Bot running an Avellaneda–Stoikov quoting model on venues such as Hyperliquid and Polymarket, plus a "Macro Filter Bot" that tells the quoting bot to pull quotes for a window when a material post or data release fires.
- **The research-desk guide** (Roan) describes a swarm of parallel research prompts run across an entire watchlist.
- **Independent coverage** is sober: Grok analyzes and drafts but does not itself execute; you must build the connection to an exchange (Bitrue, ForkLog). One community post on MQL5 reports Grok API capacity issues as a problem for automated trading. A Khaleej Times commentary cites findings that mixed manual-plus-automated trading did better than heavy reliance on robots (not verified here).

**What to keep**

- Asymmetric authority: the macro filter may only pull or widen quotes, never add risk. This is a good pattern and is now a core principle (Section 3).
- Deterministic quoting or trading math, with the LLM doing context only.
- Fan-out research across a watchlist, run on a schedule.
- 24/7 event monitoring as an input.

**What to reject**

- Running the trading core on a vendor's beta cloud agent platform. You do not control uptime, the VM, or pricing, and an agent platform is not an execution engine.
- Adjusting risk by chatting with a "Risk Bot." Risk limits belong in code and on the exchange, never in conversation. v0.1 was right to forbid this.
- The "alpha while you sleep" framing. These guides are engagement content with no independently verified results.

**Where Grok Bot can still fit:** as one optional research input (X-native sentiment), consumed through an API or a scheduled export, treated as untrusted text like any other feed, never in the order path.

---

## 2. Stack at a Glance

Existing tools to start from, so nothing here is built from scratch that does not need to be. Versions and features change; verify each against current documentation before committing.

| Layer | Start here | Alternatives / later |
|---|---|---|
| Execution engine (crypto) | **Freqtrade** (dry-run, REST API, webhooks, Telegram, FreqUI, protections, FreqAI) | **NautilusTrader** (event-driven, same code for backtest and live, built-in risk engine, multi-venue); **Hummingbot** (market making and arbitrage, prebuilt strategies including an Avellaneda–Stoikov one); **Jesse** |
| Multi-asset engine | NautilusTrader | **QuantConnect LEAN** (open source, stocks/futures/crypto, many brokers) |
| Exchange connectivity | **CCXT** (bundled in Freqtrade) | Venue SDKs: Hyperliquid Python SDK, py-clob-client (Polymarket) |
| Equities / futures path | **Alpaca** (paper trading built in) | **Interactive Brokers** via ib_async; LEAN |
| Market data (free) | Exchange REST/websocket via CCXT; Binance public data dumps (data.binance.vision); FRED for macro | Paid: **Tardis.dev** (tick history), **CoinGlass** (derivatives data), Kaiko, **Databento** (equities/futures) |
| News / social | RSS feeds, CryptoPanic API, GDELT | xAI API for X-native search; official X API (costly) |
| Local LLM serving | **llama.cpp server** (ROCm or Vulkan build for the R9700) with JSON-schema-constrained output | Ollama, vLLM (ROCm), LM Studio |
| Local models | Qwen (what you already run) for synthesis; **FinBERT** for bulk headline sentiment (small, fast, cheap) | Time-series foundation models: **Chronos / Chronos-Bolt**, **TimesFM**, **Moirai** (treat as experiments) |
| Output validation | **Pydantic** | Instructor, Outlines |
| Classical ML (optional) | FreqAI; LightGBM / XGBoost with purged cross-validation | scikit-learn, skfolio |
| Research / backtesting | **VectorBT**, backtesting.py; **QuantStats** for performance reports | NautilusTrader backtest engine |
| Database | **PostgreSQL** (+ TimescaleDB extension if time-series volume grows) | DuckDB + Parquet for research files; QuestDB or ClickHouse at scale |
| Messaging between services | Postgres tables + LISTEN/NOTIFY | NATS or Redis Streams; **Temporal** (durable workflows) if complexity demands it |
| Scheduling | systemd timers | Prefect, Dagster, APScheduler |
| Process supervision | Docker Compose with restart policies, managed by systemd | |
| Metrics / logs | **Prometheus + Grafana + Loki**; Alertmanager | Netdata (lighter) |
| Alert channel | Telegram bot (allow-listed to your user ID) | ntfy, Pushover |
| External heartbeat | **Healthchecks.io** or self-hosted **Uptime Kuma** on a different host | |
| Secrets | **SOPS + age**, Docker secrets | HashiCorp Vault (overkill early) |
| Backups | **pgBackRest** or pg_dump, plus **restic** to off-site storage (Backblaze B2, Hetzner Storage Box) | |
| Private networking | **WireGuard** or **Tailscale** | |
| Hosting | Small Linux VPS (Hetzner, OVH, Vultr, DigitalOcean) | A cloud region near your venue if latency matters (many Asia-based venues sit near Tokyo) |
| Time sync | **chrony** | |

---

## 3. Design Principles (Revised)

1. **AI never places orders.** Models research, score, and recommend. Only the execution engine submits orders.
2. **Asymmetric authority.** Any model output may cause a rejection, a smaller size, a delay, tighter risk, or reduce-only mode. No model output may increase size beyond the rules, loosen a stop, raise a limit, change the whitelist, or move the system toward live mode.
3. **Hard limits live outside prompts**: in the execution engine, in the exchange account setup, and in a watchdog on its own credentials.
4. **The exchange is the source of truth.** Internal state is a cache. On every start, and on a schedule, reconcile against the exchange before doing anything else.
5. **Protection must survive our own failure.** Every open position has a stop resting on the exchange. "Fail closed" means stop opening new risk, not abandon open risk.
6. **Research has an expiry.** Every research packet and approved intent carries a time-to-live. If the pipeline stalls, stale ideas die on their own.
7. **Design for graceful degradation, not for 100% uptime.** One small server will go down occasionally. The question is what the system does while it is down.
8. **Every decision is reproducible.** Log inputs, model version, prompt version, config hash, and reason code. Any decision can be replayed.
9. **Measure honestly.** Decide success criteria in advance, compare against baselines, and test whether the LLM adds value at all.
10. **The human owns capital, strategy changes, and live mode**, through a channel no model can reach.

---

## 4. Architecture

### 4.1 Two Planes

The research plane is flexible and may go offline. The trading plane is small, boring, and always on.

```text
RESEARCH PLANE (home GPU box; may be offline)
  Data collectors -> point-in-time store -> local classifiers + local Qwen
  -> schema-validated, signed, expiring Research Packets
                          |
                          |  over WireGuard / Tailscale
                          v
TRADING PLANE (small VPS; always on)
  Decision Gateway (gates, sizing, reason codes)
        |  approved intents (with expiry)
        v
  Execution Adapter -> Freqtrade (trade key, sub-account, no withdrawals)
        |
        v
  EXCHANGE  <- protective stops rest here

SAFETY LAYER (independent of the above)
  Watchdog: own container, own key, reconciles and can flatten
  Supervisor: restarts services, can only make the system MORE restrictive
  External heartbeat monitor: third host, alerts on silence
```

If the research plane dies, packets expire and no new research-driven entries happen. Open positions are protected by exchange-side stops and managed by the engine's normal exit logic.

### 4.2 Who Holds Which Credentials

| Component | Host | Credentials | Can | Cannot |
|---|---|---|---|---|
| Research plane | Home GPU box | Data-source keys only | Produce research packets | Touch the exchange, the trade key, or trading config |
| Decision Gateway | VPS | None | Read packets and account snapshots; write intents | Send orders |
| Execution engine | VPS | Trade key (no withdrawal, IP-locked, capped sub-account) | Place orders within its own risk config | Have its risk config changed by any model |
| Watchdog | Separate container or host | Its own key on the same sub-account | Observe, alert, flatten | Open new positions |
| Supervisor | VPS | None | Restart services; move modes toward more restrictive | Set LIVE |
| Human CLI | VPS | TOTP | Unlock live, change limits | n/a |

---

## 5. Component Details

### 5.1 Data Layer

**Responsibility:** collect market and news data and store it exactly as it looked at the time, so any decision can be replayed and so evaluation is honest.

- **Market data:** candles, order book top-of-book and depth snapshots, funding rates, open interest, exchange status, via CCXT or venue websockets.
- **Macro and events:** an economic calendar (rate decisions, inflation and jobs releases), FRED series, exchange maintenance and listing notices.
- **News and social (optional):** RSS, CryptoPanic, GDELT; X-native search through the xAI API if wanted.
- **Point-in-time archive:** every dataset snapshot a research cycle used is stored with an ID and timestamp. Never overwrite history, never "fix" old rows.
- **Data quality checks** run before research: gaps, stale feeds, impossible prices, clock drift. A failed check blocks the cycle and raises an alert.

### 5.2 Research Plane

**Responsibility:** turn data into structured research. It never decides to trade and holds no exchange credentials.

**Model roles**

| Role | Tool | Why |
|---|---|---|
| Bulk scoring of headlines and posts | FinBERT or another small local classifier | Fast, cheap, deterministic, good enough for sentiment labeling |
| Synthesis into a structured packet | Local Qwen served by llama.cpp with JSON-schema-constrained output | Constrained decoding makes malformed output nearly impossible |
| Adversarial review (optional) | A second prompt or second local model asked "why would this idea fail?" | Veto-only; a disagreement downgrades or rejects, never upgrades |
| Numeric forecasting (experimental) | Chronos / TimesFM / LightGBM | Treat as one more weak signal to be tested, not trusted |

**Operating rules**

- Temperature 0, fixed prompt templates under version control, model file hash recorded in every packet.
- Fan-out per symbol in parallel, within a per-cycle budget (maximum symbols, maximum tokens, maximum wall-clock time).
- **Cadence:** every 60 minutes for hours-scale strategies, plus event triggers (scheduled macro releases, volatility spikes, exchange status changes). Do not research faster than the strategy can act.
- **No tools:** the research model cannot browse, run code, or call APIs on its own. Code fetches data; the model reads what it is handed.
- **Fallback chain:** local Qwen, then (optional) a cloud model with minimized prompts, then rules-only mode. Rules-only means no research-driven entries; any deterministic strategy with its own tested edge keeps running.
- **What must never go to a cloud model:** API keys, account identifiers, balances, strategy code, exact risk parameters. Prompts contain only market data and public text.

**Research Packet (structured output)**

| Field | Content |
|---|---|
| packet ID, created at (UTC), valid until (UTC) | Expiry is mandatory; typical validity is one research cycle |
| model ID and file hash, prompt version, data snapshot ID | For reproducibility |
| regime label | From a fixed list: trend-up, trend-down, range, high-volatility, stress, unknown |
| candidates | Symbol (must be on the whitelist), direction, setup type (fixed list), confidence bucket (low / medium / high, not free-form numbers), invalidation level, time horizon, evidence IDs |
| rejected ideas | Symbol and reason code |
| signature | Lets the trading plane confirm the packet came from the research plane unmodified |

**Numbers from the model are hints, not facts.** LLMs are unreliable at prices and arithmetic. The gateway recomputes entries, stops, and targets from deterministic rules and from the data snapshot.

**Prompt-injection defense**

- News and social text is attacker-controlled. Assume someone will plant text designed to move your bot.
- The research model has no tools and its outputs are enums and IDs, so injected instructions have nothing to act on.
- Free-text fields (notes) are for human reading only and are never fed to another model or parsed for commands.
- Sentiment-driven entries get smaller size caps and shorter maximum holding periods than structural setups.
- Thin-liquidity filter: pump-and-dump bait on small tokens is the classic abuse of sentiment bots.

### 5.3 Decision Gateway

**Responsibility:** decide whether a candidate may become an order intent. Deterministic code, not a model.

**Gates, in order. Failing any gate produces REJECT with a reason code.**

1. **Authenticity and freshness:** valid signature, packet not expired, data snapshot within the allowed age.
2. **Validity:** schema valid, symbol on the whitelist, all enums recognized.
3. **Mode and degraded state:** the current mode (Section 5.5) permits new entries.
4. **Market filters:** spread, liquidity floor, volatility band, exchange status, scheduled-event blackout windows, extreme funding rate, session filters.
5. **Independent recomputation:** entry, stop, and target from deterministic rules (for example, volatility-based stops). Reject if the model's levels disagree with the recomputation beyond a tolerance.
6. **Cost check:** expected edge after fees, spread, slippage, and funding must clear a minimum multiple of total cost.
7. **Portfolio checks:** per-trade risk, concurrent positions, correlated-exposure cap (grouping assets that move together), gross leverage, drawdown state, cooldown after losses, per-symbol cooldown.
8. **Sizing:** risk-based position size from the stop distance, scaled by volatility, capped by venue limits and by a small fraction of visible order book depth.
9. **Output:**
   - `REJECT` with reason code
   - `PAPER_APPROVE`
   - `NEED_HUMAN` only for defined conditions: first trade in a new symbol, size above a threshold, unknown regime, or disagreement between the model and the gateway
   - `LIVE_APPROVE`, only when mode is LIVE

The reason-code histogram is the tuning instrument: it shows which gates do the work and which never fire.

### 5.4 Execution Engine

**Responsibility:** the only component that places, modifies, or cancels orders.

**Choosing the engine**

| Engine | Strengths | Weak spots | Fit |
|---|---|---|---|
| **Freqtrade** | Mature, huge community, dry-run, FreqUI, Telegram, REST API, protections (stoploss guard, max drawdown, cooldowns), on-exchange stop-loss on supported venues, FreqAI | Strategy-in-process model; candle-driven, so suited to minutes-to-hours holding; one exchange per instance; fill simulation is optimistic | **Start here** for directional, low-to-medium frequency |
| **NautilusTrader** | Event-driven; same strategy code in backtest and live; built-in risk engine with active / reducing / halted trading states; multi-venue; tick-level | Steeper learning curve; more of the plumbing is yours | Move here for tick-level data, multiple venues, or proper simulation |
| **Hummingbot** | Built for market making and arbitrage; prebuilt Avellaneda–Stoikov strategy; many connectors; has its own API and paper mode | Market making is a competitive game where retail usually loses to adverse selection | Use if market making is the chosen strategy |
| **QuantConnect LEAN** | Multi-asset, many brokers, strong backtesting | Heavier, C#-core | Use if you move into stocks or futures |

**Handoff pattern (recommended)**

- The gateway writes approved intents, each with an ID and expiry, to a database table.
- A custom Freqtrade strategy reads unexpired approved intents and applies its **own** checks, then enters. Freqtrade stays the final authority and uses its own protections.
- Avoid pushing orders through the engine's forced-entry API; it bypasses strategy-level checks.
- Keep the engine behind a thin **Execution Adapter** with a small, stable set of operations: submit intent, get status, cancel, flatten, get state, set restrictive mode. Swapping Freqtrade for NautilusTrader later then touches one component.

**Order handling rules**

- Every intent carries a unique ID, and the exchange client order ID is derived from it, so a retry can never double-fill (idempotency).
- Entries use limit or post-only orders with a timeout and a slippage cap. Market orders are for emergency exits only.
- A protective stop is placed on the exchange at fill time (bracket or attached orders where supported). A filled position with no resting stop for more than a short window triggers the watchdog.
- Never blindly retry an order submission. Query by client order ID first, then decide.
- Respect rate limits; back off with jitter; honor exchange maintenance windows.
- Handle partial fills explicitly: stop size tracks filled quantity.

**Startup reconciliation (mandatory before trading resumes)**

1. Block new entries.
2. Pull open orders, positions, and balances from the exchange.
3. Compare with the internal ledger.
4. Adopt or cancel unknown orders according to policy; flag every mismatch.
5. Confirm every position has a live stop; place one if missing.
6. Only then resume. Repeat the check on a schedule (for example every few minutes) while running.

### 5.5 Risk and Safety

**Starting defaults.** These numbers are conservative placeholders for the operator to set deliberately; they are not recommendations for your capital.

| Control | Enforced in | Starting default | Notes |
|---|---|---|---|
| Risk per trade | Gateway sizing and engine max stake | 0.25–0.5% of equity | Measured at the stop, not at notional |
| Max open positions | Engine and gateway | 3 | |
| Max gross leverage | Engine and exchange account setting | 2x or less | Use isolated margin; set leverage on the exchange side too |
| Daily loss halt | Engine protections and watchdog | 1.5% of equity | Blocks new entries until the next UTC day |
| Weekly loss halt | Watchdog | 4% | |
| Peak-to-trough lock | Watchdog | 8% | Flatten, then locked until human review |
| Slippage anomaly breaker | Gateway | Realized slippage above 3x the model over a recent window | Moves to reduce-only; indicates the cost model or venue changed |
| Stale-data breaker | Gateway | Feed older than a set number of seconds | No new entries |
| Position-without-stop timer | Watchdog | Alert at 30 s, flatten at 60 s | |
| Capital cap | Sub-account balance | Only what you can lose entirely | The one limit that holds against software bugs and stolen keys |
| Withdrawals | Exchange key permissions and withdrawal address allow-list | Disabled | |
| Live unlock | Human CLI with TOTP | Off | Not reachable through chat |

**Degraded-mode ladder.** The Supervisor and Watchdog may only move down this list. Moving back up requires a human, except NORMAL to CAUTIOUS and back, which is automatic.

| Mode | Meaning | Entered when |
|---|---|---|
| NORMAL | Full operation within limits | Default |
| CAUTIOUS | Half size, highest-confidence setups only | Elevated volatility, recent loss streak, research quality degraded |
| REDUCE_ONLY | No new entries; exits and stop management continue | Research plane down, data stale, reconciliation mismatch, slippage anomaly, daily loss halt |
| HALTED | Working entry orders cancelled; stops stay in place | Exchange problems, repeated errors, missing heartbeat from a core service |
| FLATTEN | Market-close everything and cancel all orders, then locked | Peak-to-trough lock, position without stop, suspected key compromise, human kill |

**Four-layer kill switch**

1. **Software:** a mode flag the Supervisor and CLI can set.
2. **Exchange-side:** protective stops resting on the exchange. Several venues also offer a cancel-all-after (dead-man) timer; examples include Binance Futures' countdown cancel, OKX cancel-all-after, Bybit disconnect-cancel, and Hyperliquid's scheduled cancel. **Caveat:** such a timer can cancel your protective stops along with everything else. Check per venue whether stops survive it. If they do not, do not arm it on a directional account; rely on the watchdog instead. It is well suited to market-making accounts, where pulling quotes is the goal.
3. **Independent watchdog:** a separate process on its own credentials that flattens if the main system misbehaves or goes silent.
4. **Human:** from a phone, in order: Telegram halt command, exchange app cancel and close, revoke the API key, move funds out of the sub-account.

### 5.6 Supervisor, Scheduling, and State

**Process model**

- One container per service; Docker restart policies under a systemd-managed Compose stack.
- Every service writes a heartbeat row every few seconds. The Supervisor and Watchdog read heartbeats; a missing heartbeat restarts the service and escalates the mode.
- Research cycles use a lock so runs cannot overlap, per-stage timeouts, and bounded retries. A cycle that overruns is killed and alerted, not queued.
- Keep the Supervisor deliberately dull. It restarts things and moves toward safety. It makes no trading decisions.

**Persistent state (PostgreSQL)**

Research packets, decisions (with reason code, config hash, model hash), intents, orders, fills, position snapshots, equity snapshots, mode transitions, operator actions, heartbeats. Append-only where possible. Configuration lives in version control, and the hash of the active config is recorded with every decision.

**Operations basics**

- UTC everywhere; chrony for time sync; monotonic clocks for timeouts.
- Nightly encrypted database dump plus WAL archiving, shipped off-site. **Test a restore monthly**; an untested backup is not a backup.
- Disk, memory, and log-volume alerts. A full disk is a classic silent killer.

### 5.7 Monitoring and Alerting

- **Metrics:** service heartbeats, research cycle duration and validity rate, packet-to-intent conversion, reason-code counts, order latency, slippage versus model, internal ledger versus exchange balance (reconciliation drift), open risk, drawdown, mode.
- **Alert tiers:** info (daily report), warning (degraded mode entered, repeated retries), critical (FLATTEN, position without stop, reconciliation mismatch, service down). Critical alerts repeat until acknowledged.
- **External heartbeat:** the system pings Healthchecks.io or an Uptime Kuma instance on a different host on a schedule. Silence beyond the grace period alerts your phone. This covers the case where the whole machine, network, or monitoring stack is dead and cannot report anything.
- **Daily report:** uptime, cycles run, candidates, rejections by reason, trades, P&L net of costs, reconciliation status, mode changes.
- **Weekly review:** packet validity rate, model-versus-gateway disagreement rate, slippage versus model, expectancy compared with baselines (Section 8).

### 5.8 Human Interface

- **Telegram** for alerts and `NEED_HUMAN` approvals (approve or deny, with expiry). The bot is allow-listed to your user ID and token-protected.
- **Live unlock and limit changes** only through the CLI on the VPS with TOTP, a channel no model or chat session can reach.
- **One-page runbook** on paper and in your phone: how to halt, how to flatten, how to revoke keys, how to restore from backup.

---

## 6. Failure Modes and Responses

| Failure | Detection | Automatic response | Human action |
|---|---|---|---|
| Local LLM crashes or hangs | Cycle timeout, missing heartbeat | Restart; packets expire; REDUCE_ONLY for research-driven entries | Check GPU server |
| Model returns invalid output | Schema validation fails | Discard; retry once; log | Review prompt version if it recurs |
| Model hallucinates a plausible idea | Gateway recomputation disagrees; adversarial review | REJECT or NEED_HUMAN | Review reason codes |
| Prompt injection in scraped text | No-tools design; enum outputs; per-signal size caps | Injected text cannot act; size capped | Review source, block it |
| Home GPU box or internet down | No packets arrive | Packets expire; no new research-driven entries; stops hold | Restore box |
| Exchange API down or degraded | Error rate, status feed | HALTED; stops on exchange continue | Watch; decide on manual action |
| Partial fill | Fill events | Stop size follows fill; remainder cancelled after timeout | None |
| Duplicate order after restart | Idempotent client IDs; startup reconciliation | Duplicate rejected by exchange or cancelled | Review log |
| Orphaned order or position | Scheduled reconciliation | Adopt or cancel per policy; alert | Review |
| Position without stop | Watchdog timer | Place stop; else FLATTEN | Review cause |
| Stale or wrong market data | Quality checks, cross-venue sanity checks | No entries | Check feed |
| Clock drift | chrony alerts, signature timestamp checks | Reject signed packets outside tolerance | Fix time sync |
| Flash crash or gap through stop | Slippage breaker, drawdown limits | REDUCE_ONLY or FLATTEN | Post-mortem |
| Runaway loop | Order-rate limiter, intent expiry, cycle locks | Rate limit trips; HALTED | Review |
| Disk full or DB failure | Resource alerts, write errors | HALTED | Restore |
| VPS dies | External heartbeat goes silent | Exchange-side stops hold; watchdog on separate host if used | Restore or flatten from phone |
| API key leaked | Unexpected activity; exchange alerts | Watchdog FLATTEN if configured | Revoke key; sub-account cap limits damage |
| Supply-chain compromise (malicious plugin or package) | Pinned dependencies; egress allow-list | Containment by credentials and network rules | Rotate keys |
| Power loss (home) | UPS signals; external heartbeat | Graceful shutdown of research plane only | None; trading plane is on the VPS |

---

## 7. Strategy Menu (Honest Version)

A trading platform without an edge is only infrastructure. Build the platform strategy-agnostic, and start with the simplest strategy that exercises every component.

| Strategy | LLM role | Realistic notes |
|---|---|---|
| **Trend / momentum on liquid majors, hours-to-days** | Regime filter and veto | Simplest and best-understood. Start here. Edge is modest and comes with long losing stretches |
| **Funding-rate carry / basis (delta-neutral)** | Little to none | Rules-based. Risks are exchange and counterparty risk, margin on the hedge leg, and funding flips. Good infrastructure test with a clear mechanism |
| **Market making** (Avellaneda–Stoikov) | Macro filter: pull or widen quotes around events | Deterministic and latency-sensitive. Retail usually loses to adverse selection unless on illiquid venues with rebates. Needs inventory control. Hummingbot or NautilusTrader fits |
| **News / sentiment event trading** | Central | Edge decays fast and is the most contested; the part most exposed to manipulation. Test last |
| **Prediction markets** (for example Polymarket) | Probability estimation, a natural LLM task | Thin liquidity, resolution-wording risk, and access restrictions in some jurisdictions. Treat as a research project |

**Recommendation:** run trend-following on majors with the LLM as regime filter and veto, plus a no-LLM version of the same strategy as the baseline. Funding-rate carry is a good second strategy to prove the infrastructure on something with a known mechanism.

---

## 8. Evaluation and Promotion

### 8.1 Honest Testing Rules

- **LLM backtests are contaminated.** A model trained on data covering the test period may have absorbed what happened. Valid options are (a) forward testing only, or (b) historical testing restricted to dates after the model's training cutoff. Archive every input going forward so a clean history exists.
- **Backtests of the rules** (non-LLM parts) are fine, but use walk-forward testing and purged cross-validation, and count how many variants you tried.
- **Dry-run is not proof of fillability.** Run on the exchange testnet where possible, and apply a cost and slippage model that is deliberately pessimistic.

### 8.2 Baselines and Ablation

Every evaluation compares against:

1. Holding cash.
2. A buy-and-hold basket of the whitelist.
3. **The same strategy without the LLM** (the ablation: if removing the LLM changes nothing, it was not earning its place).
4. Random entries with the same exits, sizing, and trade count (shows how much of the result is the exits and luck).

### 8.3 Pre-Registered Promotion Criteria

Write these down before the paper phase starts and do not edit them afterward.

**Operational (all required)**
- Uptime at or above a stated target over the evaluation window.
- Zero unauthorized or duplicate orders; zero positions without stops beyond the timer.
- Every chaos drill passed (Section 10).
- Reconciliation drift within tolerance throughout.

**Performance (all required)**
- Evaluation window long enough to capture more than one market regime; as a floor, 90 days and 200 or more closed trades. Thirty days and a few dozen trades cannot separate skill from luck.
- Net expectancy per trade, after fees, slippage, and funding, positive with a bootstrap confidence interval that excludes zero at a stated confidence level.
- Better than the no-LLM baseline and the random-entry baseline by a stated margin.
- If multiple variants were tried, the performance statistic adjusted for the number of tries (for example the deflated Sharpe ratio).
- Maximum drawdown within the risk budget.

**Human review**
- Review of a sample of decisions and rejections.
- Signed go-live checklist.

### 8.4 Live Canary, Then Scale

Going live is a staged experiment, not a switch.

1. **Canary:** a capped sub-account funded only with your maximum tolerable loss, smallest viable size, same gateway, same limits.
2. **Duration:** at least as long as the shortest paper evaluation window, with live slippage compared against the model.
3. **Scale** only in steps, only on criteria written beforehand.
4. **Stop criteria:** define when you shut the project down, for example no edge over baseline after six months, or any single breach of a hard limit.

---

## 9. Deployment

### 9.1 Topologies

| Option | Layout | Pros | Cons | Use for |
|---|---|---|---|---|
| **A. Single VPS** | Everything on one small server; cloud LLM or none | Simplest; static IP for API key allow-listing | Sends research prompts to a cloud model, or gives up local LLM | Early paper phase, rules-only |
| **B. Split planes (recommended)** | Trading plane on a small VPS; research plane on your home GPU box; linked by WireGuard or Tailscale | Trading stays up when home power or internet fails; local model keeps prompts private; static IP on the VPS | Two machines to maintain | Paper through live |
| **C. All at home** | Everything on the GPU box with a UPS and cellular failover | No hosting cost | Home outages, changing IP, one machine is a single point of failure | Paper only |

**Sizing hints:** the trading plane (Freqtrade, Postgres, a lightweight monitoring agent, the gateway, the watchdog) fits on a 2 vCPU / 4 GB VPS. Heavy dashboards (Grafana, Loki) can run at home and pull metrics over the private network. Put the heartbeat monitor on a third, independent host or a hosted service.

### 9.2 Hardening Basics

- SSH keys only, no passwords, non-default firewall rules, automatic security updates, fail2ban or equivalent.
- No public ports except what is needed; admin access over WireGuard or Tailscale.
- Separate Unix users and containers per component. The research container has no access to trade keys or trading config.
- Egress allow-list: the research container may reach data sources and the LLM endpoint, nothing else. The trading containers may reach the exchange and the monitoring endpoints.
- Secrets encrypted at rest with SOPS + age or Docker secrets; none in the repository, in images, in logs, or in prompts.

---

## 10. Security and Chaos Drills

### 10.1 Security

- **API keys:** trade permission only, no withdrawals, IP allow-listed to the VPS, on a dedicated sub-account funded only up to the loss cap. Enable the exchange's withdrawal address allow-list and 2FA with an authenticator app or hardware key.
- **Supply chain:** pin every dependency to exact versions with hash-checked lockfiles. Trading bot plugins, strategies, and "free signal" repositories from unknown authors are a common malware route, including wallet drainers and key stealers. Read before running; run untrusted code in a container with no keys and no network.
- **Telegram:** token stored as a secret; the bot only answers your numeric user ID; destructive commands need a confirmation step.
- **Audit:** every operator action (mode change, config change, unlock) is logged with time and source.
- **Model security:** treat all external text as hostile (Section 5.2). Log which source influenced which candidate.

### 10.2 Chaos Drills (Required Before Live)

Run each in paper mode on the testnet, then a reduced version on the live canary. Pass means the system ends in a safe, correct, reconciled state without human improvisation.

1. Kill the execution engine mid-order; restart; confirm reconciliation finds exactly the real state.
2. Kill the database; confirm HALTED and a clean restore.
3. Cut the network between planes; confirm packets expire and no new research-driven entries occur.
4. Cut the VPS's internet while a position is open; confirm the exchange-side stop remains and the external heartbeat alert fires.
5. Feed stale, then absurd, market data; confirm the data breaker trips.
6. Feed malformed and adversarial model output, including embedded instructions; confirm rejection.
7. Force a position without a stop; confirm the watchdog places one or flattens.
8. Simulate a spike in slippage; confirm REDUCE_ONLY.
9. Revoke the trade key; confirm alerts and a clean halt.
10. Pull the power on the home GPU box; confirm the trading plane does not care.
11. Restore from backup onto a fresh server using only the runbook.

---

## 11. Legal, Venue, and Tax Notes

This is a checklist of things to verify, not legal or tax advice.

- **Venue eligibility depends on where you live.** Many offshore derivatives venues and some prediction markets restrict residents of certain countries, including the US. Confirm eligibility and the venue's terms on automated trading before choosing where to build. Regulated alternatives exist (for example US-regulated crypto exchanges, Alpaca for equities and crypto, CME futures through a broker), with different product sets.
- **Sub-accounts and API features differ by venue.** Confirm sub-account support, key permission granularity, testnet availability, bracket orders, and stop behavior under cancel-all before committing.
- **Record keeping:** store every fill with fees and funding. Automated systems generate a large number of taxable events; export-ready records save pain later. Consult a tax professional.
- **Custody risk:** funds on an exchange are exposed to that exchange's failure. Keep only the capped sub-account amount there.

---

## 12. Implementation Roadmap

Each phase has a "done when." Do not start the next phase until it is met.

**Phase 0: Foundations**
Repository, Postgres, secrets handling, structured logging, external heartbeat, backups with a tested restore, Freqtrade in dry-run with a trivial rules strategy, Telegram alerts.
*Done when:* it runs unattended for 7 days, survives deliberate restarts, and alerts reach your phone, including the silence alert.

**Phase 1: Data and Research Plane**
Data collectors with point-in-time archive, quality checks, local LLM serving with schema-constrained output, signed expiring packets on a schedule, fallback chain.
*Done when:* two weeks of packets with a schema validity rate of at least 99%, no unrecovered crashes, fallback verified, and the archive replays a past cycle exactly.

**Phase 2: Decision Gateway**
All gates, sizing, reason codes, degraded-mode ladder, replay tool that reruns decisions on archived packets.
*Done when:* every gate has a unit test with passing and failing cases, and replay reproduces past decisions bit for bit.

**Phase 3: Closed Paper Loop**
Gateway to Freqtrade through the adapter and intent table, exchange testnet orders plus the cost model, watchdog, reconciliation, full monitoring, daily reports, chaos drills 1 through 11 on paper.
*Done when:* all drills pass and the loop runs two weeks without human intervention.

**Phase 4: Evaluation**
Run the pre-registered evaluation (Section 8) including baselines and the no-LLM ablation.
*Done when:* the evaluation window is complete and the criteria are scored without edits.

**Phase 5: Live Canary**
Capped sub-account, smallest viable size, all limits active, reduced chaos drills on live, live slippage tracked against the model.
*Done when:* the canary window passes with no limit breaches and slippage in line with the model.

**Phase 6: Scale or Stop**
Scale only in written steps. Apply the stop criteria. Revisit strategy mix, engine choice (for example moving to NautilusTrader), and venues.

---

## 13. Decisions Needed From You

1. Asset class and venue (crypto perps, crypto spot, equities, prediction markets), subject to eligibility where you live.
2. The maximum loss you can accept entirely; this sets the sub-account cap.
3. First strategy (recommended: trend-following with an LLM regime filter, plus a no-LLM baseline).
4. Hosting: Option B (VPS plus home GPU) or Option A to start.
5. LLM policy: local only, or local with an optional cloud fallback using minimized prompts.
6. Research cadence and which data sources to start with.
7. The promotion and stop criteria in Section 8, written down before Phase 4 starts.

---

## Appendix A: Plain-Language Glossary

- **Avellaneda–Stoikov:** a formula that tells a market maker how far from the middle price to place its buy and sell quotes. It moves quotes away from the market when the maker is holding too much of one side, and widens them when the market gets choppier.
- **Adverse selection:** the people who trade against your quotes know something you do not, so you tend to get filled right before the price moves against you.
- **Kelly criterion / fractional Kelly:** a bet-size formula that maximizes long-run growth if you knew your edge exactly. You never do, so practitioners use a fraction of it. This design uses fixed-fraction risk at the stop instead, which is simpler and safer.
- **Slippage:** the gap between the price you expected and the price you got.
- **Walk-forward testing:** fit on one stretch of history, test on the next unseen stretch, slide forward, repeat. It mimics how you would actually have used the strategy.
- **Purged cross-validation:** when examples overlap in time, remove the neighbors between the training and test sets so no information leaks across the boundary.
- **Look-ahead leakage:** the test accidentally uses information that was not available at decision time. LLMs trained on text covering the test period create this silently.
- **Deflated Sharpe ratio:** a risk-adjusted return score corrected for how many strategies you tried. Try a hundred variants and the best one looks good by luck alone; the deflation takes that into account.
- **Bootstrap confidence interval:** reshuffle your trade results thousands of times to see how much the average could swing by chance. If zero sits inside that range, you cannot claim an edge.
- **Ablation:** remove one component and see whether results get worse. If not, the component was not earning its place.
- **Dead-man's switch:** a timer that triggers a safety action (such as cancelling orders) if the system stops checking in.
- **Idempotent:** doing the same thing twice has the same effect as doing it once. Needed so a retry cannot place a second order.
- **Reconciliation:** comparing your records with the exchange's records and resolving every difference.
- **TTL (time-to-live):** an expiry stamp on data or instructions so that stale ones are ignored automatically.
- **Funding rate:** a periodic payment between long and short holders of perpetual futures that keeps the contract price near spot.
- **Basis / carry trade:** holding the spot asset and the opposite futures position at once, so price moves cancel and you collect the funding or price difference.
- **Reduce-only:** a mode in which orders may only shrink or close existing positions, never open or grow them.

---

## Appendix B: Sources

**Listed in v0.1 (could not be opened; X blocks automated access)**
- https://x.com/savipww/status/2121634572626452627
- https://x.com/bloggersarvesh/status/2032130279494853118
- https://x.com/A_Pirate_I/status/2116889572982284516

**Consulted instead**
- Market-making desk guide mirror (RohOnChain): https://youmind.com/landing/x-viral-articles/build-ai-market-making-desk
- Research-desk guide mirror (Roan): https://youmind.com/landing/x-viral-articles/grok-bot-ai-hedge-fund-research
- Engadget on Grok Bot early beta: https://www.engadget.com/2259931/spacexai-grok-bot-early-beta-how-to-try/
- OnlineMarketing.de on Grok Bot 24/7 X monitoring: https://onlinemarketing.de/technologie/grok-bot-ganz-x-durchsuchen-konkurrenzmodelle
- Bitrue on Grok trading bot limits and risks: https://www.bitrue.com/blog/can-grok-ai-trading-bot-really-make-money
- ForkLog on AI trading and bot setup: https://forklog.com/en/news/ai-trading-step-by-step-guides-bot-setup-pros-and-cons
- MQL5 community post on Grok in trading bots: https://www.mql5.com/en/blogs/post/764627
- Khaleej Times commentary on AI trading bots: https://www.khaleejtimes.com/business/commentary-ai-bots-promise-profits-while-you-sleep-but-risks-remain-wide-awake

*Tool and exchange feature descriptions in this document come from general knowledge as of mid-2026 and were not re-verified line by line. Check current documentation for each before building.*

---

*End of Design Document v0.2*
