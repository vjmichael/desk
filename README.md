# Maj Vaj / Desk

**Maj Vaj** is a 24/7 paper-trading desk: Freqtrade + FreqAI places the (paper) orders, Grok checks news and X
sentiment and issues a verdict, and your money rules cap everything. **Desk** is its front door: keys, web page,
Telegram reports. Install or update on a Debian server with `deploy/majvaj-install.sh` (as root).

## Maj Vaj safety rules (enforced inside the bot's sizing)
- Only the authorized bankroll is ever at risk; the rest of the account is never used.
- Winning trades' net profit is protected and never traded again; losses reduce the active bankroll.
- Daily loss limit; optional daily goal: stop (default), keep trading, or opportunity mode (careful extra trades in
  chosen sessions, one at a time, smaller, stronger signal, NORMAL verdict, never giving back more than you allow).
- Grok verdict: stand down blocks new entries, caution halves size, an expired or missing verdict blocks.
- Any fault in these checks refuses the trade (fail closed). Open trades are always managed.
- Watchdog every 2 minutes: restarts stuck services (max 3 an hour), alerts via the Recovery bot, optional outside
  heartbeat; Recovery bot commands /status /pause /resume /restart. Nothing can move money or change limits.

## Desk (original key screen and legacy paper engine)

Enter your keys, test every connection, and hold the loosened paper-mode Gate.
Nothing here places an order. Live trading is not enabled in this build.

## Run it
Needs Python 3.10 or newer. No packages to install.

    python -m desk

It prints an address like `http://127.0.0.1:8765/?t=...` and opens it. That address only works on your own computer and changes every run. On Windows use `py -m desk` if `python` is not found.

1. Paste a key, press **Save**, then press that card's **Test** button.
2. Telegram: send your bot any message first, press **Find chat ID**, then **Test bot**.
3. Kraken: create the key with **Query funds** only and **no withdrawals**. Paper trading needs no trading permission.

Keys are saved in `data/secrets.json` (owner-only on Mac/Linux; on Windows it relies on your user folder permissions). `data/` is excluded by `.gitignore`: never commit it.

## What is verified
| Part | Status |
|---|---|
| Gate rules, sizing, decision log | Verified here (tests, 40 total) |
| Kraken request signing | Verified here against Kraken's published example |
| Key screen, local server security | Verified here (token, host check, no key echoed) |
| Kraken key test, Grok key test | **Confirmed on the real services** (by you, on Windows) |
| Telegram tests | Mocked only. First run failed with a certificate error on Windows; a verified-TLS fallback was added. **Needs your re-run** |

## The Gate, in paper mode
Rejects only: not a long spot idea, expired, symbol not allowed, desk paused,
daily loss limit, already holding it, max positions, stale or wide-spread prices,
stale entry (more than 1% from market), nonsensical stop/target, net reward after
fees below 1.2x the risk, or a position under the minimum order size.
Not required in paper mode: approved strategy family, prevalidated edge model,
evidence list, AI confidence. An AI stand-down halves the size instead of blocking.
Every decision lists each check and its reason; `log_decision` appends them to a file.

## Run the tests
    python -m unittest discover -s tests

## Certificate errors (Telegram on Windows)
Desk always verifies sites. If your computer's certificate store cannot verify one,
Desk retries with the bundled Mozilla root list in `desk/certs/`. If you still see a
certificate message, it is almost always antivirus "HTTPS scanning", a VPN or proxy, or a
wrong system clock. Pause that feature briefly and test again. Never turn verification off:
the Telegram bot token travels in the connection.

## Paper trading (v0.2.0)

Open the keys page, then the "Paper trading" link. Press Start. Every 5 minutes the desk reads Kraken prices and manages open paper positions. Every 60 minutes (and when you press Scout now) it asks Grok for up to 3 long ideas, runs them through the Gate, and opens paper trades for those that pass. The Desk Telegram bot reports; the Recovery bot alerts on repeated failures.

Status: tested here only against fakes. The first real run against Kraken, Grok and Telegram is yours.
