# GESH — Founder Outreach (ready to send)

_Drafted 2026-07-03. **HOLD until the Codex re-review passes** (per Georgi:
polish everything before any customer tests). Positioning per Codex: sell as
**research / observability**, not performance — the track record is honest but
early (19% wins, negative Sharpe, no SPY benchmark yet)._

## The comp workflow (how a reply becomes access)

1. Someone replies with the email they want access under.
2. Run: `python3 scripts/comp_user.py grant their@email.com`
   → creates an observer (comp, no billing sub) + emails the magic link.
3. `python3 scripts/comp_user.py list` to see comp accounts;
   `revoke their@email.com` to cut access anytime.
4. Comp accounts are structurally ignored by the webhook revoke path + nightly
   reconcile (no `billing_sub_id`), so they persist until you revoke.

They log in via magic link → see the masked **$100K model-book** dashboard
(their money is never involved; they read *your* research).

---

## Draft A — LinkedIn DM (trading friends / colleagues)

> Hey [Name] — built something I'd like your eyes on.
>
> Over the last few months I built **GESH**: an AI market-intelligence
> dashboard. An 11-signal consensus engine, market-regime detection, Kelly-based
> sizing, risk analytics — running daily, with a fully transparent paper-trading
> track record (every trade timestamped, losses included).
>
> It went live this week at $49/mo, but I'm **comping founding users** — I want
> 5 sharp people using it and telling me what's wrong before I scale.
>
> Interested? Send me the email you want access under and you'll have a login
> link in 2 minutes. It's a **research tool, not financial advice** — you keep
> trading in your own account.

---

## Draft B — Email (warmer contacts)

> **Subject: I launched something — want a founding seat?**
>
> [Name],
>
> Short version: I built an AI trading-**research** platform and it went live
> this week.
>
> What it does, concretely:
> - Runs an 11-node consensus engine (momentum, trend, fundamentals, news
>   sentiment, Kelly, cycle analysis) on any US ticker — daily, automatically
> - Detects market regime (ADX/Bollinger/RSI + live Fed data) and adjusts
>   signal weights
> - Publishes a fully transparent paper-trading track record — timestamped
>   entries/exits, **losses shown**, and I'm adding an SPY benchmark
>
> It's $49/mo, but I'm comping the first founding users for honest feedback. No
> card, no trial games — reply with the email you want and I'll provision access
> today. If it's not useful, tell me why; that's the price.
>
> One clear line: it's a **research/observability tool**. It analyzes; you decide
> and trade in your own account. Early days on the track record — I'd rather you
> judge the *reasoning*, not the returns yet.
>
> — Georgi

---

## Honesty guardrails (keep these true in every message)

- Say "research / observability," never "beats the market" or "signals that make
  money" — the track record is early (negative Sharpe, ~19% wins).
- "Not financial advice; you trade your own account" — every message.
- Don't promise a specific win rate. The `/track-record` page shows the real
  numbers (wins, losses, breakevens, profit factor) — let it speak.

## Target list (fill in)

- 3–5 named humans who know you as the network-engineer-who-builds. Trading
  friends, LinkedIn network, colleagues. Quality > quantity for alpha feedback.
