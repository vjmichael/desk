# Design Document: 24/7 Automated Trading Desk

**Version:** 0.1 (Paper-First)  
**Date:** 2026-11-17  
**Status:** Design only — not production-ready  
**Goal:** Reliable, always-on research → decision → execution system that does **not** depend on a live chat session.

---

## 1. Design Principles

1. **AI never places orders.**  
   Large language models research, score, and recommend. A dedicated trading bot is the only component allowed to submit orders.

2. **Paper trading first.**  
   Live trading is locked until the system has run cleanly in dry-run for a defined period with explicit human approval.

3. **Hard risk limits live in the execution layer**, not in prompts.

4. **Every action is logged and auditable.**

5. **Fail closed.**  
   If research, decision, or connectivity fails → do nothing.

6. **Human remains the authority** for capital, strategy changes, and going live.

---

## 2. High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                     Supervisor / Orchestrator                │
│              (systemd / Docker / simple Python loop)         │
└────────────────────────────┬────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌─────────────────┐   ┌──────────────────┐
│ Research      │   │ Decision Layer  │   │ Execution Bot    │
│ Service       │   │                 │   │ (Freqtrade or    │
│               │   │ - Rules         │   │  equivalent)     │
│ - LLM API or  │   │ - Scoring       │   │                  │
│   local model │   │ - Filters       │   │ - Paper / Live   │
│ - News / X    │   │ - Risk checks   │   │ - Position mgmt  │
│ - Market data │   │                 │   │ - Order routing  │
└───────┬───────┘   └────────┬────────┘   └────────┬─────────┘
        │                    │                     │
        └────────────────────┼─────────────────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ Monitoring & Alerts │
                  │ (Telegram / logs)   │
                  └─────────────────────┘
```

---

## 3. Component Details

### 3.1 Research Service

**Responsibility:** Gather information and produce structured research output. Never decides to trade.

**Inputs:**
- Market data (price, volume, funding, open interest)
- News / social signals (optional)
- Existing open positions and recent trades
- Whitelist of tradable pairs

**Outputs (structured JSON):**
- Market regime summary
- Candidate setups with entry / stop / target ideas
- Supporting evidence and confidence notes
- Explicit list of rejected ideas and reasons

**Implementation options:**
- Scheduled calls to Grok / Claude / other LLM API
- Local model (Qwen, Llama, etc.) for lower cost and privacy
- Combination of deterministic data pulls + LLM summarization

**Schedule:**  
Configurable (e.g., every 15–60 minutes, or at specific session opens).

### 3.2 Decision Layer

**Responsibility:** Decide whether a research candidate is allowed to become an order intent.

**Allowed outputs only:**
- `REJECT`
- `PAPER_APPROVE` (send to paper bot)
- `NEED_HUMAN`
- `LIVE_APPROVE` (only after human has unlocked live mode)

**Decision inputs:**
- Research output
- Current account state (balance, open risk, daily P&L)
- Hard rules (max positions, max risk per trade, max daily loss, allowed pairs, time filters, volatility filters, etc.)

**Implementation:**
- Prefer deterministic rules + scoring first
- Optional small decision model later
- No free-form “AI decides to buy”

### 3.3 Execution Bot

**Responsibility:** The only component that can place, modify, or cancel orders.

**Recommended starting point:** Freqtrade (mature, good dry-run mode, active community) or equivalent.

**Requirements:**
- Dry-run / paper mode by default
- Strict position sizing
- Hard stop-loss and max daily loss
- Exchange API keys with **withdrawals disabled**
- No ability for the research or decision layers to change its core risk config

**Interface:**  
Decision layer sends a clean, validated order intent. Bot accepts or rejects based on its own internal checks.

### 3.4 Supervisor / Orchestrator

**Responsibility:** Keep everything alive, restart failed services, enforce schedule, and prevent runaway loops.

**Functions:**
- Start/stop/restart services
- Run research on schedule
- Pass research → decision → execution
- Heartbeat monitoring
- Emergency stop (kill switch)

**Implementation:**  
systemd services, Docker Compose, or a simple supervised Python process.

### 3.5 Monitoring & Alerting

- Structured logs for every research cycle, decision, and order attempt
- Telegram (or similar) alerts for:
  - New paper/live trade
  - Daily loss limit hit
  - Service down
  - Need-human decisions
- Daily / weekly summary report

---

## 4. Data & State

**Persistent state required:**
- Research history
- Decision log (with reasons)
- Trade log (entry, exit, fees, P&L)
- Configuration (pairs, risk limits, schedules)
- Mode flag: `PAPER` | `LIVE` (default `PAPER`)

**Storage:**  
SQLite or Postgres + file-based config. All state must survive restarts.

---

## 5. Risk Controls (Non-Negotiable)

| Control                    | Location              | Notes                          |
|---------------------------|-----------------------|--------------------------------|
| Max risk per trade        | Execution bot         | Hard coded                     |
| Max open positions        | Execution bot         | Hard coded                     |
| Max daily loss            | Execution bot         | Stops new entries              |
| Allowed pairs whitelist   | Decision + Execution  | Both layers                    |
| Withdrawals               | Exchange API key      | Disabled                       |
| Live mode unlock          | Human only            | Explicit action                |
| Kill switch               | Supervisor            | Immediate halt                 |

AI prompts must never be able to override these.

---

## 6. Operating Modes

1. **Paper Mode** (default)  
   Full pipeline runs. Orders go only to dry-run. Used for validation.

2. **Live Mode**  
   Requires explicit human unlock after paper performance criteria are met. Still subject to all hard risk limits.

3. **Research-Only Mode**  
   Research + decision run, but no order intents are sent.

4. **Emergency Stop**  
   All new entries blocked. Open positions can be managed manually or by bot’s existing exit logic.

---

## 7. Promotion Criteria (Paper → Live)

Suggested minimum before considering live:

- Minimum number of paper trading days (e.g. 30+)
- Defined number of closed paper trades
- No critical system failures
- Risk limits never breached in unexpected ways
- Human review of decision quality
- Written go-live checklist signed off by the operator

These criteria should be defined in advance and not changed after the fact to fit desired results.

---

## 8. Deployment Target

**Recommended for 24/7 operation:**
- Small VPS (Linux) or always-on local machine
- Docker Compose for service isolation
- Automatic restarts via systemd or Docker restart policies
- Off-site log shipping or at least daily backups of state

Local always-on PC is acceptable for paper trading. VPS is cleaner for long-term live use.

---

## 9. What This Design Deliberately Avoids

- Letting an LLM click around a broker website with a cursor
- Giving any AI component withdrawal or account-modification rights
- Single monolithic “AI that does everything”
- Hiding risk limits inside prompts
- Going live with small capital and high leverage hoping for fast growth

---

## 10. Implementation Phases

**Phase 0 – Skeleton**  
Supervisor + logging + paper execution bot in dry-run with manual research input.

**Phase 1 – Research Automation**  
Scheduled research service producing structured output.

**Phase 2 – Decision Layer**  
Rules-based filtering and approval of research candidates.

**Phase 3 – Closed Paper Loop**  
Full automatic paper trading with monitoring and daily reports.

**Phase 4 – Evaluation**  
Measure signal quality, decision accuracy, system reliability.

**Phase 5 – Live (Optional)**  
Only after Phase 4 criteria are met and human explicitly unlocks.

---

## 11. Success Metrics (Paper Stage)

- System uptime
- Research cycles completed without error
- Percentage of candidates rejected by decision layer (and why)
- Paper trade expectancy and drawdown
- Number of times human intervention was required
- Zero unauthorized live orders

---

## 12. Final Notes

This design turns the “Grok Bot keeps trading while I sleep” idea into a proper system:

- Research is AI-assisted
- Decisions are constrained and auditable
- Execution is handled by purpose-built software
- The system can actually run 24/7
- Risk is controlled by code and exchange permissions, not by hoping a prompt is followed

It will not magically produce consistent profits. It will give you a controllable, inspectable, always-on pipeline that you can improve or shut down cleanly.

---

## Next Steps

1. Choose the execution bot (Freqtrade recommended as starting point)
2. Define your exact risk limits and whitelist
3. Specify the research schedule and data sources
4. Decide paper-trading success criteria before any live discussion

---

*End of Design Document*

Source:
https://x.com/savipww/status/2121634572626452627
https://x.com/bloggersarvesh/status/2032130279494853118
https://x.com/A_Pirate_I/status/2116889572982284516