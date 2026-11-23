# Design Document: 24/7 Automated Trading Desk

**Version:** 0.5 (cloud app, light, self-recovering)
**Date:** 2026-11-17
**Status:** Design only. Not production-ready. Not financial advice.
**Builds on:** v0.4, which built on v0.3 (the lighter revision you ran through Grok). The three original X posts are in v0.3's addenda and are not repeated here.
**Goal:** A small app on a cloud server that calls a cloud AI through an API, runs 24/7 with no chat session involved, and stops safely when anything goes wrong. If it does fail, it recovers itself and tells you at each step, and you can reset it with a button. No command line is ever needed.

---

## 0. Bottom Line

- The skeleton from v0.1 to v0.3 is right and is kept: the AI never places orders, stops rest on the exchange, the exchange is the source of truth, and a human owns capital and live mode.
- v0.4 makes it smaller: **one cloud server, one cloud AI API, three containers.** No home GPU, no VPN, no database server, no agent framework.
- **Rules propose trades. The AI can only allow, shrink, or block them.** This is simpler and safer than asking a model to invent trades, and it makes the "does the AI add anything?" test easy.
- The session-expiry problem you saw with Grok Bot belongs to agent platforms, not to AI. Here no chat or agent session is in the loop. The app owns all state and the AI is a stateless function called once per cycle (Section 2).
- **New in v0.5: recovery.** The system heals itself in layers, sends DOWN, RECOVERING, RECOVERED, and NEEDS YOU pushes so you always know where you stand, and gives you a Safe reset button that works even when the server is dead. No command line (Section 7).

**What v0.5 adds (recovery)**

| Gap in v0.4 and earlier | Added in v0.5 |
|---|---|
| Nothing restarted a hung app or told you | Health checks, a supervisor, ordered restarts, and DOWN / RECOVERING / RECOVERED / NEEDS YOU pushes |
| The control channel lived on the thing that might be dead | A Recovery Console outside the server, with its own Telegram bot |
| A restart meant a command line | Buttons, including Safe reset |
| One alert channel | A second channel for critical alerts |
| Restarts could be unsafe | Every start begins in PAUSED, reconcile first, crash-loop guard |
| "Recovered" could be a false comfort | RECOVERED is sent by the outside monitor after independent checks, never self-declared |

**What v0.4 cuts or changes from v0.3**

| v0.3 | v0.4 | Why |
|---|---|---|
| Home GPU research plane plus VPS plus VPN | One cloud server, AI through an API | You chose cloud; removes a machine, a VPN, and a failure path |
| Local Qwen proposes candidates | Rules propose, cloud AI vetoes or shrinks | Fewer tokens, no AI-invented trades, easy ablation test |
| Signed research packets | One atomically replaced intents file with expiry | Same server; signatures add nothing here |
| PostgreSQL | SQLite on a persistent disk plus nightly off-server backup | One writer, low volume; move to Postgres only if outgrown |
| 8 gates | 6 checks | Merged overlapping gates |
| 5 modes | 3 modes | Easier to reason about and to drill |
| 11 chaos drills | 7 | Dropped drills about the home box and the cross-plane network |
| 7 phases | 4 | Same safeguards, fewer steps |
| Tool catalogue | One short stack table | Light |
| (not covered) | AI provider backup, spend caps, update rules | Needed once the AI is a paid cloud API |

Two things v0.3 trimmed are put back, in one line each, because they affect correctness: dry-run fills are optimistic (Section 8), and "enough trades" needs a number (Section 8).

---

## 1. The Three Posts, Reviewed

| Post | What it actually is | Take | Leave |
|---|---|---|---|
| **1. savipww, "Grok Bot Trencher Setup"** | A Pump.fun memecoin dip-buying desk. Five bots on Grok Bot's own computer click Buy and Sell in the FOMO web app with a browser cursor, using an unofficial FOMO API to read and verify. Contains a referral link; the "$36 to $3,125 overnight" claim is unverified | Missing data is never a pass. The social/AI signal may nudge a score but never pass a coin (the same idea as our one-directional AI). Verify every order with three independent witnesses (screen, API, chain) and never re-click an unverified order. Stops only tighten. Nightly loss stop, ticket-size cap, trade-count cap. Paper first. The venue is truth, the local database is a copy | Browser-cursor execution. Unofficial APIs that "can break any day." Memecoin rug-pull markets. An agent platform as the runtime. Approval prompts that expire. Dependence on web-login sessions |
| **2. bloggersarvesh, "Claude + SEO"** | A local-SEO marketing playbook with an agency sales pitch. Nothing to do with trading. v0.3 flagged this correctly | Only the habit of loading context once and keeping one prompt per task | Everything else |
| **3. A_Pirate_I, "Trading Desk-in-a-Box"** | The closest match to our design: a research agent, a bounded-choice decision layer, an existing bot (Freqtrade) as the only thing that can place orders, five autonomy levels, hard limits in the bot's config, a read-only bridge | Do not rebuild the trading bot. The decision step picks from a fixed menu, not free text. "No fill, no done." Autonomy levels. Test with failures: routing accuracy, signal escapes, false closes, needless human interruptions, recovery. Automate one proven loop at a time | Per the post, an open-source Freqtrade MCP server exposes tools that place trades and reload config; never hand that to a chat agent. The paid third-party decision service is optional; a plain cloud-API call with forced JSON output does the same job |

---

## 2. Why Grok Bot Sessions Expire, and How This Design Avoids It

Post 1's own prompt shows the failure sources. Every one has a fix that comes from moving the work off the agent platform.

| What breaks on an agent platform | Evidence | v0.4 answer |
|---|---|---|
| Work lives in a vendor-run session or VM that updates, restarts, or drops installed packages | Post 1 reruns a setup script after platform updates and needs an hourly health routine | The app runs on a server you rent and control, with automatic restart. No vendor VM in the loop |
| Plan usage runs out and the agent stops | Post 1: usage is spent per routine run, the desk stops at zero, "nobody guards a LIVE night on an empty pool" | The AI is a metered API with your own hard spend cap. When the AI budget is gone, the app keeps running with no new entries |
| Approval prompts time out | Post 1: approval cards expire after 30 minutes, "a stop waiting for approval is a missed stop" | Stops rest on the exchange and never wait for a human or an AI |
| Web logins and session tokens expire (about an hour for FOMO's token) | Post 1 | Official exchange API keys. No browser, no cursor, no web login |
| Unofficial APIs break | Post 1 says so | Only official, documented exchange APIs through CCXT and Freqtrade |
| Beta product and capacity limits | Engadget (early beta); MQL5 post (Grok API capacity) | Two AI providers behind one interface. AI outage means "no new entries," never "unprotected positions" |
| State lives in a chat | General | State lives in the app's database and on the exchange. Each AI call is a fresh request carrying everything it needs |

**A session cannot expire if there is no session.**

---

## 3. Non-Negotiables

1. **The AI never places orders.** Only Freqtrade does.
2. **The AI's authority runs one way: allow, shrink, or block.** It never creates a trade, enlarges one, supplies prices, loosens a stop, widens the whitelist, or unlocks live mode.
3. **Limits live in code, in the exchange account, and in the sub-account balance.** Never in a prompt.
4. **The exchange is the truth.** Reconcile on every start and every few minutes. Missing data is never a pass.
5. **Every open position has a stop resting on the exchange.** Stops only tighten, never wait for approval, and do not depend on the AI or the app being up.
6. **Fail closed means no new entries.** It never means unprotected positions.
7. **No fill, no done.** A trade is open, closed, or failed only according to the exchange's records. Reports are built by code from exchange data, not from a model's claim.
8. **The human owns capital, strategy, and going live.** Safe-direction commands (pause, flatten, restart) are one tap. Risk-increasing ones (resume, go live, change limits) need a one-time code from your authenticator app.
9. **Every start begins in PAUSED.** The system trades again only after it reconciles with the exchange and passes its health checks.
10. **Recovery is automatic first, a button second, and always announced.** The system tries to heal itself, tells you at each step, and if it cannot, one button resets it. No command line is ever required.

---

## 4. The App

```text
ONE SMALL CLOUD SERVER (always on, static IP, every start begins in PAUSED)

  brain (container)                       freqtrade (container)
  - scheduler, one cycle per candle       - the ONLY holder of the trade key
  - rules generate candidates             - reads intents file (read-only mount)
  - calls cloud AI API: allow/shrink/block- applies its own checks + protections
  - the Gate (6 checks, sizing)           - places orders and exchange-side stops
  - writes approved intents file -------> - dry-run until promoted
  - "Desk" Telegram bot: reports, approvals
  keys: AI key + READ-ONLY exchange key   keys: trade key (no withdrawal, IP-locked)

  watchdog (container, own trade key)     supervisor (container, no exchange keys)
  - every minute: exchange vs expectations- watches health checks and heartbeats
  - position without stop: place it       - restarts failed services, in order
  - limits breached: flatten              - runs the recovery steps (Section 7.2)
                                          - polls the Recovery Console (outbound only)

OUTSIDE THE SERVER (survives the server dying)
  RECOVERY CONSOLE (small function or tiny server at a DIFFERENT provider)
  - "Recovery" Telegram bot: your buttons (Section 7.4)
  - detects silence, calls the provider's reboot / power-cycle API
  - sends DOWN / RECOVERING / RECOVERED / NEEDS YOU pushes
  Healthchecks.io: heartbeat watching
  Second alert channel (for example Pushover) for critical alerts
  Cloud AI API (primary + backup provider): stateless calls
  Exchange: stops rest here
```

### 4.1 Hosting

| Option | Verdict |
|---|---|
| **Plain VPS** (for example Hetzner, DigitalOcean, Vultr, Linode) running Docker Compose | **Recommended.** Static IP for the exchange key allow-list, never sleeps, you control restarts |
| Managed container platform (for example Fly.io, Railway, Render) | Workable only on a paid always-on tier with a persistent disk and a fixed outbound IP. Verify each of the three before committing |
| Free or eco tiers, serverless functions, dynos that sleep or restart daily | Avoid for the trading core |
| Your home PC | Paper only |
| Grok Bot or any agent platform | Not the runtime |

Sizing: 2 vCPU and 2 to 4 GB RAM is enough. Turn on automatic security updates and the provider's automated backups. Pick a region near the exchange's API servers; latency barely matters for hours-scale trading. Choose a provider whose API can reboot and power-cycle a server and whose mobile console has a reboot button. Hetzner, DigitalOcean, Vultr, and Linode offer these as far as I know; verify before committing.

### 4.2 One Cycle (default hourly, at candle close)

1. Brain pulls public market data (candles, spread, funding, scheduled events) with the read-only key.
2. The rules strategy produces zero or more candidates from the whitelist.
3. No candidates: stop here. No AI call, no cost.
4. Brain sends one compact request to the AI API: a fixed prompt, indicator summaries computed by code, optional public headlines, and the candidate list. Output is forced into a JSON schema.
5. The AI returns a global stance (normal, risky, or stand_down) and, per candidate, allow, shrink, or block with a reason code from a fixed list.
6. The Gate runs (Section 5).
7. Approved intents (ID, symbol, direction, size, levels, expiry) are written to the intents file by writing a temporary file and renaming it, so Freqtrade never reads a half-written file.
8. Freqtrade picks up unexpired intents and applies its own checks.
9. Everything is logged: prompt version, model name, config hash, reason codes.

### 4.3 What the AI Sees

| Sees | Never sees |
|---|---|
| Public market numbers, indicator summaries, public headlines, the rule-generated candidate list (symbol, direction, levels from rules) | API keys, balances, account IDs, position sizes in currency, strategy code, risk limits, exchange credentials |

This keeps strategy code and account details away from any cloud model, which fits your preference to keep code off cloud AI. Confirm each provider's current data-use terms; major providers generally state that API inputs are not used for training by default, but verify.

### 4.4 AI API Rules

- Pin the exact model name. Temperature 0 where supported.
- Forced structured output, then validate it again locally against the schema. Anything invalid is discarded.
- Timeout 30 to 60 seconds, one retry, then the backup provider. Both fail: no new entries this cycle, alert after two failed cycles.
- The model gets no tools and no web browsing. Headlines are hostile text: injected instructions cannot act, because the only possible output is a menu choice.
- Two spend limits: a monthly cap in the provider's console, and a daily token cap inside the app. Alerts at 50% and 80%.
- Cost is simple to estimate: cycles per day, times tokens per cycle, times the provider's price. With an hourly cycle and a handful of candidates it is a few thousand tokens per cycle. A mid-tier model with structured output is enough; do not pay for the largest.

---

## 5. The Gate (6 Checks, in Order)

Any failure returns REJECT with a reason code. The reason-code counts are your tuning data.

1. **Fresh and valid.** Data newer than the limit, intent not expired, symbol on the whitelist, AI output matches the schema. Missing data is a reject.
2. **Allowed to act.** Mode is RUN, AI stance is not stand_down, no event blackout window, no loss cooldown.
3. **Market is sane.** Spread, liquidity, and volatility within bands; exchange status healthy; funding not extreme.
4. **Levels come from the rules.** Entry, stop, and target from the strategy's own math. The AI never supplies prices.
5. **Worth it.** Expected edge after fees, spread, slippage, and funding must clear a minimum multiple of total cost.
6. **Within risk, then sized.** Per-trade risk at the stop, max positions, correlated exposure, gross leverage, daily and weekly loss, drawdown state. Size comes from the stop distance. An AI "shrink" halves it.

**Outputs:** REJECT (with reason), APPROVE (paper or live according to mode), or ASK_HUMAN (rare: first trade in a new symbol or an unusual size). An ASK_HUMAN request expires in 15 minutes and no answer means reject. Protective actions never use ASK_HUMAN.

---

## 6. Execution and Safety

### 6.1 Order Rules

- Freqtrade is the only thing that places, changes, or cancels orders. Its own API listens on the server's local interface only, forced entry is switched off, and the brain holds no Freqtrade credentials at all.
- Each intent has a unique ID. The exchange client order ID is derived from it, so a retry can never double-fill.
- Entries use limit or post-only orders with a timeout and a slippage cap. Market orders are for emergency exits only.
- The protective stop is placed on the exchange at fill time, using Freqtrade's on-exchange stop option where your venue supports it (verify yours). Stop size follows partial fills.
- Never blindly retry a submission. Check by client order ID first. Never re-submit an order whose status is unknown; reconcile first. Never average down.
- Respect rate limits, back off between retries, and honor exchange maintenance windows.
- Freqtrade keeps its own protections (stoploss guard, max drawdown, cooldowns) as a second line behind the Gate.
- **Exchange cancel-all-after timers:** do not arm one on a directional account unless you have confirmed your protective stops survive it. Many venues' timers cancel stops along with everything else.

### 6.2 Reconciliation (on every start, then every 5 minutes)

1. Block new entries.
2. Pull open orders, positions, and balances from the exchange.
3. Compare with the app's records.
4. Adopt or cancel unknown orders and flag every mismatch.
5. Confirm every position has a live stop. Place one if missing.
6. Resume only after all five are clean.

### 6.3 Modes

Only three. The watchdog and supervisor may move the system down this list, never up.

| Mode | Meaning | Entered when | Leaves when |
|---|---|---|---|
| **RUN** | Normal operation within limits | Default | n/a |
| **PAUSED** | No new entries. Working entry orders cancelled. Stops stay. Exits and stop management continue | Any start or restart, stale data, reconciliation mismatch, exchange trouble, AI unavailable, loss limit hit, repeated errors, your pause command | After a restart or a transient cause, automatic recovery may return to RUN once health checks pass and reconciliation is clean. Limit breaches, mismatches, and anything after FLATTEN need you: the Resume button plus an authenticator code |
| **FLATTEN** | Close everything, cancel everything, then locked | Drawdown lock, position without a stop that cannot be fixed, suspected key compromise, your flatten command | You only, after review |

### 6.4 Kill Layers

1. **Exchange-side stops.** They work when everything else is dead.
2. **Watchdog.** A separate container with its own key. Honest limit: it shares the server, so it covers app and process failures but not a dead host. For host death, layer 1 holds, and the Recovery Console (Section 7) detects the silence, reboots the server, and alerts you. Before you scale capital, move the watchdog to a second tiny server at a different provider.
3. **You, from a phone, by buttons.** Safe reset, Pause, or Flatten in the Recovery bot (Section 7.4). If those fail: exchange app cancel and close; revoke the API key; move funds out of the sub-account.

### 6.5 Starting Limits

Conservative placeholders for you to set deliberately. They are not recommendations for your capital.

| Control | Where enforced | Starting default |
|---|---|---|
| Risk per trade, measured at the stop | Gate and Freqtrade | 0.25 to 0.5% of equity |
| Max open positions | Gate and Freqtrade | 3 |
| Max gross leverage | Freqtrade and the exchange account setting | 2x or less, isolated margin |
| Daily loss | Freqtrade protections and watchdog | 1.5% of equity: PAUSED until the next UTC day |
| Weekly loss | Watchdog | 4%: PAUSED until you resume |
| Peak-to-trough lock | Watchdog | 8%: FLATTEN, then locked |
| Position without a stop | Watchdog | Alert at 30 seconds, flatten at 60 |
| Slippage anomaly | Gate | Realized slippage above 3x the model over a recent window: PAUSED |
| Capital cap | Sub-account balance | Only what you can lose entirely. The one limit that holds against bugs and stolen keys |
| Withdrawals | Exchange key permissions and withdrawal address allow-list | Disabled |
| AI spend | Provider console and app | Monthly cap plus daily token cap |

### 6.6 Who May Do What

| Level | Actions | Who | Approval |
|---|---|---|---|
| 0 Read | Prices, balances, status, trade history | Brain, watchdog | Automatic |
| 1 Prepare | Candidates, AI opinion, reports | Brain | Automatic |
| 2 Restrict and recover (safe direction) | Pause entries, block a symbol, flatten, restart app, reboot server | Recovery bot buttons, watchdog, supervisor | Automatic or one tap, with a confirm for restart, reboot, and flatten |
| 3 Trade | Open a position inside limits | Freqtrade only | Paper: automatic. Live: only after you unlock |
| 4 Money or irreversible | Raise size or leverage, loosen a stop, new key, withdraw, go live, resume after a limit breach | You only | Authenticator code typed into the bot. Withdrawals are disabled at the exchange anyway |

---

## 7. Staying Up and Recovering

**Goal:** the system should not die. If it does, it recovers itself and tells you at each step. If it cannot, you reset it with one button. No command line is ever needed.

### 7.1 Staying Up

- **Restart basics:** every container restarts automatically after a crash, Docker starts on server boot, and each container has a health check that catches hangs as well as crashes.
- **State survives:** the database lives on the server's persistent disk. A nightly encrypted copy goes to off-server storage, plus the provider's automated snapshots. **Test a restore every month.**
- **AI trouble:** primary to backup provider, then no new entries. Alert after two failed cycles. Open positions are unaffected.
- **AI budget:** alerts at 50% and 80% of the cap. At the cap the AI step turns off and the app holds with no new entries.
- **Updates:** pin every version. Never auto-update Freqtrade or the brain. Apply those updates only in PAUSED with no open positions. Operating-system security updates can be automatic, with a reboot window, because every start is safe (Section 7.2).
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

1. **Every start begins in PAUSED.** After any crash, restart, or reboot the system trades again only after it reconciles with the exchange (Section 6.2) and passes its health checks.
2. **Recovery never loosens risk.** It will not return to RUN after FLATTEN, after a loss-limit pause, after an unresolved reconciliation mismatch, or after a suspected key compromise. Those need you.
3. **Restarts happen in order:** database check, Freqtrade, brain, watchdog. The brain never starts before Freqtrade has reconciled.
4. **Crash-loop guard:** if the same service restarts more than 3 times in 30 minutes, automatic restarts stop and you are told. A loop means a bug or bad config, and restarting forever hides it.
5. **Restarting is safe with positions open.** Stops rest on the exchange, and idempotent order IDs plus reconciliation prevent duplicate orders.
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

- **RECOVERED is never self-declared.** It is sent by the Recovery Console after it independently sees steady heartbeats and the supervisor's clean reconcile result. The thing that was broken does not get to announce that it is fixed.
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
| **Safe reset** | Pause, restart the app, reconcile, report. If that fails, reboot the server, reconcile, report. Sends the same RECOVERING and RECOVERED pushes as automatic recovery | One confirm |
| **Restart app** | Restart the services in order; boots into PAUSED and reconciles | One confirm |
| **Reboot server** | Soft reboot through the provider API | One confirm |
| **Power-cycle server** | Hard power cycle, the last resort before the provider console | Confirm with a warning |
| **Pause** | Switch to PAUSED | One tap |
| **Flatten** | Close everything and lock | Confirm |
| **Resume** | Leave PAUSED | Confirm, plus a 6-digit code from your authenticator app typed into the chat |
| **Open provider console** | Link to the provider's own mobile console for a manual reboot or snapshot restore | Link |

**How the buttons reach the server**

- App-level commands travel by outbound polling: the supervisor on the server asks the Recovery Console for pending commands. The trading server needs no open inbound port for this.
- Server-level commands go through the cloud provider's API. The provider token lives only in the Recovery Console, in a provider project that contains only this server. A leaked token could harm the server but cannot move funds, since withdrawals are disabled at the exchange.
- Optional: the Recovery Console may hold a **read-only** exchange key so DOWN and NEEDS YOU messages can say whether your positions have stops even while the server is dead. Recommended.
- Optional: the same buttons on a small mobile web page served by the Recovery Console, behind a passkey or authenticator login, for anyone who does not want Telegram.

**Safety of the buttons**

- The Recovery bot answers only your Telegram user ID. Turn on Telegram's two-step verification for your account.
- Restart and reboot are safe with positions open because stops rest on the exchange and everything boots into PAUSED.
- A hijacked Telegram account could pause, reboot, or flatten, which realizes losses but does not steal funds. Resume needs the authenticator code, so a hijacker cannot restart trading. This residual risk is accepted.

**If the buttons fail too (last-resort order):** Safe reset, then Power-cycle, then the provider console (restore the latest snapshot to a new server in a few clicks; it boots into PAUSED and reconciles), then the exchange app (cancel orders and close positions), then revoke the API key, then move funds out of the sub-account. Keep the Recovery bot chat, the exchange app, and the provider console link on your phone's home screen.

---

## 8. Strategy and Evaluation

### 8.1 Strategy

Trend and momentum on three liquid pairs at an hours-scale, with the AI as veto. Do not start with memecoins, market making, sentiment-only trading, or prediction markets.

### 8.2 The Veto Test

Log every candidate the AI blocked or shrank and simulate it as if it had been taken (a "shadow trade"). Because the AI can only subtract, its trades are always a subset of what the rules alone would do. So the test is simple: **the veto earns its place only if the trades it blocked did clearly worse than the trades it allowed.** If not, remove the AI. The rest of the system stands without it, and that is a perfectly good result.

### 8.3 Baselines

Cash. Buy-and-hold of the three pairs. Rules-only (no AI). Random entries with the same exits and sizing.

### 8.4 Honest Testing

- Rules can be backtested with walk-forward testing. **AI judgments cannot be backtested reliably**, because the model may have absorbed what happened. Use forward (paper) testing, or only dates after the model's training cutoff. Archive every input and output from day one.
- Dry-run fills are optimistic (no queue position, latency, or market impact). Use the exchange's testnet where it exists and add deliberately pessimistic costs.

### 8.5 Pre-Registered Criteria

Write these down before Phase 3 and do not edit them afterward.

**Operational (all required):** uptime target met; zero duplicate or unauthorized orders; zero positions left without a stop beyond the timer; all drills passed; reconciliation clean throughout.

**Performance (all required):**
- A window long enough to include more than one market regime. As a floor, about 90 days and 200 closed trades. If the strategy cannot produce that, lengthen the window; do not lower the bar. Thirty days cannot separate skill from luck.
- Net expectancy per trade after fees, slippage, and funding is positive, with a bootstrap confidence interval that excludes zero.
- Beats cash and the random-entry baseline. The AI stays only if it passes the veto test.
- Drawdown inside the risk budget.
- If you tried several variants, demand more from the winner. The best of many tries looks good by luck.

### 8.6 Live Canary and Stop Rule

A capped sub-account funded only with your maximum tolerable loss, smallest viable size, all limits on, for a window at least as long as the shortest paper evaluation. Scale only in pre-written steps. Write the stop rule now, for example: no edge over the baselines after six months, or any breach of a hard limit.

---

## 9. Build Plan

Each phase has a "done when." Do not start the next phase until it is met.

**Phase 0: Foundation.** Cloud server, Docker Compose, Freqtrade in dry-run with a trivial rules strategy, the intents-file handoff tested with a dummy intent, the Desk Telegram bot, the supervisor, the Recovery Console with its Recovery bot buttons, Healthchecks.io, a second alert channel, backups.
*Done when:* 7 days unattended, survives a reboot, the silence alert reaches your phone on both channels, and from your phone alone you can press Safe reset and see the RECOVERING and RECOVERED pushes arrive.

**Phase 1: Brain, log-only.** Rules candidates, AI call, Gate, shadow-trade logging. No intents are sent to Freqtrade yet.
*Done when:* two weeks with at least 99% valid AI responses, AI-outage behavior verified, spend inside the cap, and a past cycle can be replayed exactly.

**Phase 2: Closed paper loop and drills.** Intents flow to Freqtrade (dry-run or testnet), watchdog on, reconciliation on, daily report, all ten drills.
*Done when:* all drills pass and the loop runs two weeks without intervention.

**Phase 3: Evaluate, then decide.** Run the pre-registered evaluation. Then live canary (optional), then scale or stop by the written rule.

**Drills (run in paper or testnet; pass means the system ends safe and reconciled with no improvisation):**

1. Kill Freqtrade mid-order, restart, and confirm reconciliation finds the exact real state.
2. Power off the server from the provider's console with a position open. Confirm the DOWN push, the automatic reboot attempt, the RECOVERING push, a clean reconcile, the RECOVERED push, and that the stop never left the exchange.
3. Block both AI providers. Confirm no new entries, an alert, and open positions untouched.
4. Feed stale data and malformed or instruction-laden AI output. Confirm both are rejected.
5. Delete a position's stop. Confirm the watchdog restores it or flattens.
6. Revoke the trade key, and separately simulate an exchange outage. Confirm a clean PAUSED and an alert.
7. Restore onto a fresh server from the latest snapshot using only the provider console and your phone, with no command line.
8. Freeze the brain without crashing it. Confirm the health check catches it, the supervisor restarts it, and you receive DOWN, RECOVERING, then RECOVERED, in that order.
9. Make recovery impossible (for example, block the restart). Confirm NEEDS YOU arrives on both channels, repeats until acknowledged, and the buttons still work.
10. Press every button in the Recovery bot, including from a phone on mobile data only. Confirm each asks for confirmation where it should, does what it says, and is logged.

---

## 10. Stack

| Need | Pick |
|---|---|
| Execution | **Freqtrade** (dry-run first, on-exchange stop, protections). Later option if you outgrow it: NautilusTrader |
| Exchange connectivity | **CCXT** (inside Freqtrade, and for the brain's read-only data) |
| Brain | Plain Python service: scheduler, rules, Pydantic validation. No agent framework needed |
| AI | One cloud AI API with forced JSON output, plus a second provider as backup |
| Storage | **SQLite** (write-ahead mode) on the persistent disk. PostgreSQL only if you outgrow it |
| Packaging | **Docker Compose** with automatic restart |
| Host | Small Linux VPS with static IP and provider backups |
| Alerts and controls | **Telegram**, two bots: Desk (on the server) and Recovery (outside it), each allow-listed to your user ID |
| Supervisor | Docker restart policies and health checks, plus a small supervisor container that restarts failed services in order and polls the Recovery Console |
| Recovery Console | A small always-on function or tiny server at a different provider: Recovery bot, silence detection, cloud-provider reboot and power-cycle API. Healthchecks.io webhooks can trigger it |
| Second alert channel | **Pushover** (emergency priority repeats until acknowledged), or ntfy, SMS, or email |
| Heartbeat | **Healthchecks.io** (hosted), or Uptime Kuma on another host. Route its alerts to the second channel as well as Telegram |
| Secrets | Env files outside the repository with tight permissions, or Docker secrets. SOPS with age if you want them encrypted in git |
| Backups | Provider snapshots plus nightly **restic** to off-server object storage |
| Reports (optional) | QuantStats |

Add nothing else until Phase 2 is green.

---

## 11. Decisions You Must Make

1. Venue and asset class, subject to eligibility where you live.
2. The maximum loss you can accept entirely. This is the sub-account cap.
3. The exact numbers in Section 6.5.
4. The three whitelist pairs and the first rules strategy.
5. The primary and backup AI providers, and the monthly spend cap.
6. The pre-registered promotion and stop criteria, written before Phase 3.
7. A cloud provider whose API can reboot and power-cycle servers.
8. A second alert channel, separate from Telegram.
9. Whether the Recovery Console holds a read-only exchange key (recommended, so alerts can say whether your positions have stops while the server is dead).

---

## 12. Known Limits, and What Not to Add

- **Recovery has limits.** It fixes crashes, hangs, and server faults. It cannot fix an exchange outage, a revoked key, a provider-wide outage, a bad strategy, or a logic bug, and it will not restart trading by itself after a limit breach, a mismatch, or FLATTEN. If both alert channels are down at once, you will not be told. Exchange-side stops are the protection that works when everything else does not.
- **No design guarantees profit.** The likely outcomes include a rules strategy with no edge after costs, and an AI veto that adds nothing. This design handles that by measuring, then simplifying or stopping.
- **More revision will not settle** whether your strategy has an edge, whether your venue is open to you, or your tax and legal position. Running Phases 0 to 2 will settle the technical questions faster than another draft.
- **This document is complete enough to build from.** The next useful artifact is code: the Freqtrade strategy that reads the intents file, and the brain service. Ask for that when you are ready.
- **Deliberately left out:** home GPU and local models, multi-agent desks, chat-agent trading tools (including any Freqtrade MCP server with order tools), market making, memecoins, browser automation, Kubernetes, and workflow engines.
- **Verify before relying:** tool and exchange behaviors here reflect general knowledge as of late 2026. Check current documentation for Freqtrade, each exchange's key permissions, testnet behavior, and stop-order semantics, and for each AI provider's structured-output support, data terms, and spend-limit controls.

---

## Appendix A: Sources

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

- **Veto:** the AI can say no or "smaller," never "more."
- **Shadow trade:** a trade the AI blocked, simulated anyway, so you can see whether the block was right.
- **Reconciliation:** comparing your records with the exchange's and fixing every difference.
- **Expiry (TTL):** a time stamp after which an instruction is ignored automatically.
- **Slippage:** the gap between the price you expected and the price you got.
- **Bootstrap confidence interval:** reshuffling your trade results thousands of times to see how much the average could swing by luck. If zero sits inside the range, you cannot claim an edge.
- **Cancel-all-after (dead-man switch):** an exchange timer that cancels your open orders if you stop checking in. It can cancel protective stops too.
- **Heartbeat:** a small "I'm alive" signal sent on a schedule. Silence is the alarm.
- **Supervisor:** a small program whose only job is to notice failed services and restart them in the right order.
- **Crash loop:** a service that keeps dying and restarting. Automatic restarts stop after a few tries so the problem is not hidden.
- **Idempotent:** doing the same thing twice has the same effect as doing it once, so a retry cannot place a second order.
- **Ablation:** removing one part to see whether results get worse. If not, the part was not earning its place.

---

*End of Design Document v0.5*
