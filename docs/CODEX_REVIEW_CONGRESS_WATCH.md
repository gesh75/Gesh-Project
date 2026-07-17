# Codex review request — PR #93 (Congress-Watch / the Autopilot extract)

**Reviewer:** Codex · **Author:** Claude (Opus 4.8) · **Date:** 2026-07-17
**PR:** https://github.com/gesh75/Gesh-Project/pull/93 · branch `feat/congress-watch` · head `9ec26e4`
**Status:** OPEN, CI green (`test` ✅ + `customer-isolation` ✅), **merge held** for review.

## What this is
Competitive research on **Autopilot (joinautopilot.com)** → an implementation of the one piece GESH
can legally adopt. Autopilot's growth engine is the viral **Congress/Pelosi Tracker** (public STOCK
Act data) → then *copy-traded* into the user's brokerage. The copy-trading needs **RIA registration**
GESH avoids, and edge the engine doesn't have. This PR extracts the **distribution** half — a public,
shareable **Congress-Watch** page — framed as *information*, not execution.

**Diff (6 files, +443):**
| File | What |
|---|---|
| `congress_agent.py` (+208) | tracker module: pluggable source (FMP free-tier / `CONGRESS_DATA_URL`), 1h cache, offline-safe, `render_watch_html()` |
| `server.py` (+30) | `GET /congress-watch` (public HTML) + `GET /api/congress` + `GET /api/congress/{symbol}` |
| `auth_users.py` (+3) | `/congress-watch` + `/api/congress` added to `PUBLIC_PREFIXES` |
| `tests/test_congress_agent.py` (+97) | 7 offline tests |
| `tests/test_customer_leak_sweep.py` (+5) | categorized the 2 new API routes as `EXCLUDED` |
| `docs/AUTOPILOT_COMPETITIVE_ANALYSIS_2026-07-17.md` (+100) | the A+C research + ranked extract |

## What I want back
Per area below: a verdict (**APPROVE / CHANGES / BLOCK**) and findings ranked
**CRITICAL/HIGH/MEDIUM/LOW**, each with a concrete failure scenario (input → bad outcome). This PR
puts **untrusted third-party data on a PUBLIC, unauthenticated page** and makes a **legal claim** —
please be adversarial on areas 1 and 2 specifically.

```bash
git fetch origin && git diff origin/main...origin/feat/congress-watch    # branch-only diff
git checkout origin/feat/congress-watch
# venv: /Users/georgigaydarov/Projects/gesh-project/venv/bin/python3.14 -m pytest tests/test_congress_agent.py tests/test_customer_leak_sweep.py -q
```

---

## Area 1 — Compliance / regulatory (HIGHEST STAKES — this is a legal claim in shipped code)
The doc asserts the tracker sits in the Advisers-Act **"publisher's exclusion" (Lowe v. SEC)** —
general, impersonal, regularly-published commentary on public data is a *newsletter*, not an adviser,
so no RIA registration. The page carries "informational / not advice / not affiliated / 45-day lag"
disclaimers and does **not** execute or recommend.

**Challenge it:**
1. Is the publisher's-exclusion framing actually sound for a *tracker that ranks "what to buy"*
   (`leaderboard()` → "Most-bought tickers by Congress")? Does a ranked buy-list edge toward
   implied recommendation vs. neutral information? Where's the line?
2. The page CTA is "Analyze these tickers with GESH → /pricing". Does pairing a buy-leaderboard with
   a paid-product CTA change the analysis (touting / promotion)?
3. Anything beyond the Advisers Act — state law, "not affiliated with any government official"
   sufficiency, use of members' names/likeness, or FINRA/marketing rules if GESH ever markets it?
4. **Flag honestly:** this is my analysis, not legal advice. Should merging be gated on counsel
   sign-off, or is shipping-dark (no data source configured) low-risk enough to merge now?

## Area 2 — Public-page XSS (`/congress-watch` renders untrusted third-party strings, unauthenticated)
`render_watch_html()` builds HTML from disclosure rows (member names, tickers, amounts) that come
from an external feed. I escape every dynamic value with `html.escape` and interpolate into text and
one attribute (`style='color:...'` driven by a boolean, not user data). Example row:
```python
f"<td style='padding:6px 14px;color:{'#00e87a' if r.get('is_buy') else '#ff6b6b'}'>"
f"{e(str(r.get('action','')).title())}</td>"
```
**Break it:**
1. Is `html.escape` (default `quote=True`) sufficient for every context here, including the
   `style='...'` single-quoted attribute? Any place a value lands in an attribute or URL context
   *without* `e()`? (Check the leaderboard rows, the recent rows, and the `<meta og:*>` tags.)
2. Is there any unescaped interpolation of `symbol`/`politician`/`amount` anywhere in the module?
3. The page has no CSP header of its own (unlike the dashboard). Does the app-level CSP/middleware
   cover this route, and should this public page set its own headers?
4. The `test_public_page_escapes_untrusted_strings` test asserts `<script>` is escaped — is that
   coverage broad enough (attribute-context payloads, `javascript:` in a would-be URL)?

## Area 3 — FMP source correctness (wired but UNVERIFIED — no key to test)
`_fetch_raw()` tries FMP (`senate-trading-rss-feed` / `senate-disclosure-rss-feed`) when `FMP_API_KEY`
is set, else `CONGRESS_DATA_URL`, else empty. `_norm()` maps FMP camelCase
(`transactionDate`/`disclosureDate`/`firstName`+`lastName`/`office`).
**Verify:** are those the correct current FMP endpoints/fields (I could not test — no key)? Is the
house side missing (both endpoints look senate-oriented)? Does the graceful-degrade path truly never
raise on an unexpected shape? Is the 1h cache keyed correctly (`_CACHE["_raw"]`, monotonic TTL)?

## Area 4 — Leak-sweep categorization: EXCLUDED vs COVERED (challenge my call)
CI forced categorizing `/api/congress` + `/api/congress/{symbol}`. I put them in **EXCLUDED**
("public STOCK Act data, identical for every viewer, no owner/customer dollars", same class as
`/api/long-term-macro`). **Argue the other side:** these ARE customer-reachable GETs — should they be
**COVERED** (an actual request asserted free of owner sentinels) rather than reasoned-excluded? Is
"reasoned exclusion" acceptable for a route that echoes an *external* feed (supply-chain: could the
upstream ever inject an owner-looking string)?

## Area 5 — Data licensing / ToS (a risk I did NOT fully resolve)
The page **re-publishes** third-party data (FMP, or whatever `CONGRESS_DATA_URL` points at) on a
public site. Does FMP's (or the chosen source's) ToS permit redistribution/display? This is separate
from the securities-law question and could independently block a *public* page. Please flag whether
this needs checking before promotion.

## Area 6 — Offline-safe / "ships dark" + business framing
Verify: with no `FMP_API_KEY` and no `CONGRESS_DATA_URL`, the page renders a clean empty state and
never errors (I tested this). And sanity-check the doc's honest framing — "this fixes distribution,
not signal edge" — is that the right characterization, or does a public buy-leaderboard risk
implying GESH endorses those tickers?

---

## Related (also held, if you want to sweep them)
Independent PRs from the same run, each isolated: **#81** cookie-Secure per-request · **#83** Kelly
201 invariant tests · **#84** signal-expectancy diagnosis. Not part of this review unless useful.

**Output:** per-area verdict + one ranked findings table; call out anything that should block merge —
especially in areas 1, 2, and 5.
