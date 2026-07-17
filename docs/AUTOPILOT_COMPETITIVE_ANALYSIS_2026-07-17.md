# Autopilot (joinautopilot.com) — competitive analysis + the legal extract

**Date:** 2026-07-17 · Research scope A (full) + C (regulatory). Implements the B extract
(acquisition mechanic GESH can legally adopt) as the **Congress-Watch** feature in this PR.

> **Framing that matters more than any feature:** GESH's constraint is **demand, not features**
> (1 paying subscriber, working product). Autopilot's *core* value — copy-trading a proven track
> record — is exactly what GESH (a) legally can't do without becoming an RIA and (b) doesn't have
> (engine expectancy −0.51%, OOS 0.0). So the extract is **not** the product; it's Autopilot's
> **distribution engine**, which is legal and buildable.

## Part A — What Autopilot is and why it grew (verified)

Autopilot is a **copy-trading** app, not a research dashboard. It connects to the user's *existing*
brokerage account and directs it to place trades that mirror a chosen "portfolio"/"pilot" — so the
user's money stays in their own account while Autopilot has trading authorization.

| Fact | Value | Source |
|---|---|---|
| Model | Connects your brokerage; auto-mirrors chosen portfolios' trades | College Investor, WSZ |
| Viral hook | **Pelosi / Congress Tracker** — built from public STOCK Act disclosures | Forbes, Autopilot |
| Pricing | **$29/quarter or $100/year, per portfolio**; **$500 min per portfolio** | College Investor |
| Third-party "Pilots" | extra **$99–$299/yr** each | College Investor |
| Traction | Launched **2023** by the viral Pelosi Tracker team; **100k+ users, $2B+ traded** | Forbes/WSZ |

**Why it works (the growth engine — this is the transferable part):**
1. **A viral, newsworthy hook built on free public data.** "Trade like Congress" is a *story* that
   spreads on TikTok/X for free. The data (STOCK Act 45-day disclosures) costs nothing.
2. **Named pilots with visible track records.** You follow a *person/portfolio you can see*, not a
   spreadsheet — social proof is the product.
3. **Zero-effort automation.** "Set it and forget it."
4. **Inherently shareable performance.** Growth compounded through screenshots and the tracker's own
   social account — distribution was built in, not bolted on.

## Part C — The regulatory wall (concrete)

**Autopilot's core requires registration GESH deliberately avoids.** Placing trades with discretion
in a customer's account is **investment-advisory + discretionary trading** under the Investment
Advisers Act: it needs **RIA registration (Form ADV)**, fiduciary duty, ADV Part 2 delivery, and a
custody/execution partner (the user's assets stay at their broker; Autopilot holds limited trading
authority). That is a different legal, insurance, and compliance animal from a research tool.

GESH's own rules exist precisely to stay out of this lane: `PAPER_MODE=true`, "not financial
advice," no personalized-investment-advice monetization before counsel. **So the copy-trading loop
is off-limits without counsel + registration — and it's the one thing you'd need real signal edge to
justify, which the engine doesn't have.**

**But the DATA and the NARRATIVE are not advice.** Publishing *general, impersonal, regularly-issued*
commentary on **public** disclosures is the **"publisher's exclusion"** (Lowe v. SEC) — a financial
newsletter, not an adviser. A Congress-trade *tracker*, framed as information with no execution and
no tailored recommendation, sits squarely in that protected lane. That is the extractable value.

## The extract — ranked

| Element | Verdict | Why |
|---|---|---|
| **Congress/notable-investor trade tracker (informational)** | ✅ **Adopt now** | Their viral hook, on free public data, in the publisher lane. GESH already has the agent + public-page patterns. **Shipped in this PR.** |
| **Shareable performance / signal cards** (OG images, "share this") | ✅ Adopt now | Distribution built-in; GESH already has `/track-record`. |
| **Named "model portfolios" you can *watch*** (Buffett 13F, sector themes) — as analysis, not execution | ✅ Adopt (informational only) | 13F filings are public; frame as research, never "copy/execute." |
| Referral / invite loop | ✅ Adopt now | Pure growth mechanic, no regulatory surface. |
| **Copy-trading / auto-execution in a customer account** | 🚫 **Counsel + RIA only** | Discretionary advisory activity. Do not build without registration. |
| Third-party paid "pilots" marketplace | 🚫 Counsel only | Compensated advice → adviser status for the pilots and/or platform. |
| Charging for "trade like [politician]" *execution* | 🚫 Can't | Same wall + optics; keep it informational. |

## What this PR ships — Congress-Watch (the adopt-now extract)

`congress_agent.py` + three routes, all **informational, public-data, offline-safe, no execution**:
- `GET /congress-watch` — a **public, shareable** page (in `PUBLIC_PREFIXES`): "what is Congress
  buying" leaderboard + latest disclosed transactions. The acquisition magnet, server-rendered, all
  strings escaped (XSS-safe), carrying the 45-day-lag + not-affiliated + not-advice disclaimers, with
  a CTA into `/pricing` and `/track-record`.
- `GET /api/congress` — leaderboard + recent (public JSON).
- `GET /api/congress/{symbol}` — per-ticker disclosures, to attach to the research card.
- Data source is pluggable via `CONGRESS_DATA_URL`; defaults to the public house/senate stock-watcher
  datasets; **degrades to an honest empty state** on any failure.
- Tests: `tests/test_congress_agent.py` (6, offline) — normalization, buy-only leaderboard,
  per-symbol filter, offline/network-error empty states, and public-page XSS escaping.

### Honest flags
- **Verify the data source live.** The default stock-watcher URLs were *not* re-confirmed live during
  this build (web-search classifier was down). The code is defensive (graceful empty state), but
  before promoting the page, point `CONGRESS_DATA_URL` at a confirmed source (stock-watcher S3,
  Finnhub `/stock/congressional-trading` — GESH already has the key — or Quiver) and check the page
  renders real rows.
- **This solves distribution, not edge.** It gives GESH a free, viral, legal top-of-funnel it lacks.
  It does **not** create signal accuracy — pair it with the honest `/track-record` and the research
  tooling that already works.
- **Turn Autopilot's weakness into the pitch.** Their #1 substantive criticism is that copied
  congressional trades are *stale* (45-day lag). GESH's honest framing — "here's the public data,
  and here's real-time AI analysis on those tickers" — is the differentiated, compliant angle.

*Sources: thecollegeinvestor.com/59796/autopilot-review, forbes.com "What Is Autopilot" (Jul 2026),
wallstreetzen.com autopilot review, joinautopilot.com. Regulatory analysis is general information,
not legal advice — confirm with counsel before any execution/marketplace feature.*
