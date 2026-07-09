# GESH Customer Surface — External Review Brief (main@821f172)

*Prepared for an external reviewer (Grok) with no repository access. Every claim is quoted verbatim from the repository at `main` = `821f172`, cited by `file:line`. Two files carry almost the entire surface: `server.py` (2,772 lines) and `auth_users.py`; the paid LLM path lives in `ai_analyzer.py`.*

---

## 1. What to review & why

GESH just shipped a **multi-tenant customer surface** on a service (`:9000`) that was built single-tenant. That surface simultaneously handles (a) the **owner's real brokerage dollars** (which must never reach a customer raw), (b) **customer financial PII** — each customer's own entered positions and analyses (customer A must never see B), and (c) **paid Claude LLM spend** (which must never be anonymous or uncapped, and must be per-customer quota-limited). The three things worth your skepticism are therefore: **isolation** (can identity be spoofed / can one tenant read another?), **money-masking** (does any owner-dollar field escape unmasked?), and **spend gates** (can the quota or auth gate be bypassed to burn the owner's API budget?). This doc gives you the exact functions to attack.

---

## 2. The product in 30 seconds

**Before:** one Robinhood account (the owner's book), analyzed by an owner-only dashboard. Data came from shared files (`live_portfolio_snapshot.json`, `engine_results.json`) and a single-tenant SQLite DB (`gesh_financial.db`). No customer concept.

**After (the NEW surface):** paying customers log in behind an auth wall and get two features:
1. **Analyze ANY ticker** — a rich single-ticker AI research card (VAL/GRO/HLT/MOM/DIV scores, fair-value pill, 3M/12M forecast, risks/rewards, AI verdict). One Claude Sonnet call per uncached ticker.
2. **Analyze MY portfolio** — the customer enters their *own* holdings; GESH runs the full owner engine suite (deep AI + Kelly + Monte-Carlo) on the customer's book.

Customer data lives in a **separate** DB (`gesh_auth.db`), every row scoped by the customer's email, where email is derived **only** from a server-signed HMAC session cookie. Owner endpoints a customer can still reach have their dollars rewritten to a **$100k model book**.

---

## 3. Security invariants (these MUST hold)

1. **Owner dollars never reach a customer raw.** Every non-owner `/api/*` JSON body is either scaled to the $100k model book by `_scale_money`, or the request is refused `403` (owner-only paths). The only unmasked customer surfaces are on a mask-exempt allow-list that is provably free of owner data.
2. **Masking fails closed, never open.** Unknown real total → scale factor `0.0` (dollars zero out). Any exception in the masking middleware → `500`, never the raw body. HMAC secret refuses an insecure fallback when the wall is on.
3. **No anonymous or uncapped LLM spend.** Both customer LLM endpoints require a valid session (`401` for anonymous) **and** a per-customer rolling-24h quota. Owner-budget/mutation endpoints require the owner cookie or `GESH_API_KEY` (`403` for customers).
4. **Per-customer isolation is identity-scoped, not filter-scoped.** Every customer read/write is keyed on the authenticated session email. There is no route that binds an email/user-id from path/query/body — so there is no cross-customer query surface to bypass.
5. **The mask-exempt list carries no owner data — and must stay that way.** `/api/analyze-ticker` (public per-ticker research) and `/api/mybook` (the customer's own positions + public quotes). Adding any owner-touching path to this tuple silently defeats invariant #1.
6. **Revocation is immediate.** Session auth re-checks `is_active` every request; typed tokens (`session` vs single-use `magic`) stop a leaked magic URL from becoming a durable session.
7. **Auth-off must never silently collapse tenants into the owner.** With `GESH_AUTH_ENABLED` falsy there is no customer concept; identity resolution returns the **owner** bucket and masking is bypassed. Multi-tenant production *requires* the flag on plus a real `GESH_AUTH_SECRET`. (This is the highest-impact deploy footgun — see §7/§8.)

---

## 4. Architecture & key code

### 4a. Identity is derived from the signed cookie ONLY

Every per-customer endpoint resolves identity through one function (`server.py:556`):

```python
def _session_email(request: Request) -> str | None:
    """The requesting user's identity for per-user data. In dev/solo mode (auth off)
    everything is the owner bucket; in production it's the session."""
    if not auth_users.auth_enabled():
        return os.getenv("GESH_OWNER_EMAIL", "georgy75@gmail.com").strip().lower()
    return auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
```

The only inputs are the `GESH_AUTH_ENABLED` env flag and the `gesh_session` cookie. `authenticate_session` (`auth_users.py:468`) accepts an identity only if the HMAC signature verifies, the token is unexpired, its `typ` is `session`, **and** the user still exists and is active (so revocation is immediate):

```python
def authenticate_session(token: str | None) -> str | None:
    sub = verify_token(token, expected_typ="session")
    if not sub:
        return None
    u = get_user(sub)
    if not u or not u["is_active"]:
        return None
    return sub
```

`verify_token` (`auth_users.py:137`) — the subject (email) comes out of the **signed** payload; a client cannot assert an arbitrary email without forging HMAC over the server secret, and a `typ='magic'` URL token cannot be replayed as a session:

```python
    payload_b64, sig = token.split(".")
    expected = _b64u(hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    data = json.loads(_b64u_dec(payload_b64))
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    if expected_typ is not None and data.get("typ", "session") != expected_typ:
        return None        # wrong purpose (e.g. a magic token presented as a session)
    return data.get("sub")
```

The signing secret **fails closed** when the wall is on (`auth_users.py:63`) — if `GESH_AUTH_SECRET` is unset and the `.secret` file is unreadable it raises rather than fall back to a guessable key:

```python
        if auth_enabled():
            raise RuntimeError(
                "GESH_AUTH_SECRET unset and .secret file unavailable while auth is "
                f"enabled — refusing insecure fallback key: {e}")
        return secrets.token_bytes(32)
```

Owner vs customer is a pure email compare (`auth_users.py:434`): `is_owner(email)` is true only for `GESH_OWNER_EMAIL` (default `georgy75@gmail.com`); everyone else is a customer.

### 4b. Gate #1 — `require_api_key` (OWNER-ONLY; `server.py:38`)

Guards mutating / owner-budget-spending endpoints. **An authenticated customer is explicitly `403`'d.** With the wall on, the browser uses the owner *cookie* (no API key in the browser → closes an XSS→key-theft vector); headless cron uses the header. Fails closed (`503`) if unconfigured:

```python
    if auth_users.auth_enabled():
        email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
        if email and auth_users.is_owner(email):
            return True
        if email:
            raise HTTPException(status_code=403, detail="Owner-only action")
    expected = os.getenv("GESH_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True
```

### 4c. Gate #2 — `require_customer_or_owner` (CUSTOMER-OR-OWNER, never anonymous; `server.py:68`)

Guards exactly the two customer LLM-spend endpoints. Admits any authenticated session but **rejects anonymous** — never an open, uncapped LLM faucet:

```python
    if auth_users.auth_enabled():
        email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
        if email:
            return True
        raise HTTPException(status_code=401, detail="Authentication required")
    expected = os.getenv("GESH_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True
```

### 4d. Money-masking middleware (`server.py:426`)

Every `/api/*` JSON response to a **non-owner** session is rewritten. Owner-only views are `403`'d before any body is read; mask-exempt prefixes serve raw; everything else is scaled to the model book. **Fails closed** on any exception:

```python
@app.middleware("http")
async def _customer_view_middleware(request: Request, call_next):
    if not auth_users.auth_enabled():
        return await call_next(request)
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email or auth_users.is_owner(email):
        return await call_next(request)
    path = request.url.path
    if path == "/report" or any(path == p or path.startswith(p + "/") for p in _OWNER_ONLY_PATHS):
        return JSONResponse(status_code=403, content={"detail": "Owner-only view"})
    if any(path == p or path.startswith(p + "/") for p in _MASK_EXEMPT_PREFIXES):
        return await call_next(request)
    resp = await call_next(request)
    if not path.startswith("/api/") or "application/json" not in resp.headers.get("content-type", ""):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    try:
        masked = _scale_money(_json.loads(body), _customer_scale_factor())
        if isinstance(masked, dict):
            masked["view"] = "model_book"
        return JSONResponse(status_code=resp.status_code, content=masked)
    except Exception:
        # Fail CLOSED for customers: never fall through to the raw-dollar body.
        return JSONResponse(status_code=500, content={"detail": "masking error"})
```

Supporting constants:
- `_OWNER_ONLY_PATHS` (`server.py:292`) = `("/report", "/invest", "/progress", "/watch", "/usage")` — these embed raw owner dollars in HTML and are never scrubbable, so they are blocked outright.
- `_MASK_EXEMPT_PREFIXES` (`server.py:298`) = `("/api/analyze-ticker", "/api/mybook")` — the two provably-owner-data-free customer surfaces.
- The scale factor **fails closed to zero** (`server.py:373`): `_MODEL_BOOK_USD / total if total > 0 else 0.0`, and `0.0` on any exception.

`_scale_money` (`server.py:384`) is non-mutating and masks by **semantic dict key** (not value shape). It scales `_MONEY_KEYS`/`*_usd`/`*_dollars` numerics by the factor, scales `_QTY_KEYS` share counts, scales bare-number arrays under `_MONEY_LIST_KEYS` (the ECG equity_history / sparkline P0 that had no dict key), keeps `_KEEP_STRING_KEYS` per-share forecasts raw, and **redacts** any other string containing `$` or "share":

```python
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[-–—]\s?\$?\d[\d,]*(?:\.\d+)?)?")
_SHARES_RE = re.compile(r"\b\d+(?:\.\d+)?(?:\s?[-–—]\s?\d+(?:\.\d+)?)?\s+shares?\b", re.I)
def _redact_prose(text: str) -> str:
    text = _MONEY_RE.sub("$•••", text)          # owner $-amounts / ranges
    text = _SHARES_RE.sub("a position", text)   # share counts / ranges
    return text
```

### 4e. Per-customer isolation — the DDL and every statement `WHERE email=?`

All per-customer tables live in `gesh_auth.db` (chmod `0o600`) and key on email (`auth_users.py:162`):

```sql
CREATE TABLE IF NOT EXISTS customer_positions (
    email TEXT NOT NULL, symbol TEXT NOT NULL, shares REAL NOT NULL,
    avg_cost REAL, updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (email, symbol) );
CREATE TABLE IF NOT EXISTS customer_analyses (
    email TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS usage_events (
    email TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL );
```

Reads/writes are all bound-param email-scoped (`auth_users.py:599`+), e.g.:

```python
def get_positions(email) -> list[dict]:
    rows = con.execute(
        "SELECT symbol, shares, avg_cost FROM customer_positions "
        "WHERE email=? ORDER BY updated_at, symbol", (em,)).fetchall()
```

The 30-position cap uses a **single-statement** `INSERT…SELECT` (a count-then-insert pair could exceed the cap under concurrent adds — `auth_users.py:621`):

```python
    "INSERT OR IGNORE INTO customer_positions (email, symbol, shares, avg_cost, updated_at) "
    "SELECT ?,?,?,?,? WHERE (SELECT COUNT(*) FROM customer_positions WHERE email=?) < ?"
```

The `customer_analyses` prune is email-fenced on **both** the outer DELETE and inner SELECT (`auth_users.py:665`), so one customer's save can never evict another's history. The only cross-customer read is `usage_summary` (`GROUP BY email`, no filter) — it backs owner-only `/usage`.

The customer's book analysis is a **closed loop**: `customer_positions (WHERE email=?)` → in-memory payload (`_customer_book_payload`, built from the customer's rows + **public** quotes, hardcoding `cash/buying_power/day_change = 0.0`) → engines → back to `customer_analyses (email)`. Grep confirms the customer path never calls the single-tenant writers `log_analysis`/`log_equity_snapshot` (owner-only, `server.py:957`, `1086-1087`) and never writes `live_portfolio_snapshot.json` / `engine_results.json`.

### 4f. The per-customer rolling-24h quota (`server.py:1705`)

```python
_QUOTA_DEFAULTS = {"ticker_card": 25, "book_analysis": 10}
_TIER_QUOTA_MULT = {"observer": 1, "trader": 2, "desk": 5}

def _check_customer_quota(email: str | None, event: str) -> None:
    """Raise 429 when the user's rolling-24h count of `event` has hit their
    tier-scaled cap. Fail-open on metering errors — quota must never take the
    product down."""
    if not email or auth_users.is_owner(email):
        return
    try:
        base = int(os.getenv(f"QUOTA_DAILY_{event.upper()}", _QUOTA_DEFAULTS.get(event, 0)))
        if base <= 0:
            return
        limit = base * _TIER_QUOTA_MULT.get(auth_users.get_tier(email), 1)
        if auth_users.usage_count_24h(email, event) >= limit:
            raise HTTPException(status_code=429, detail=...)
    except HTTPException:
        raise
    except Exception:
        return   # FAIL-OPEN
```

Owner-exempt; tier-scaled (observer ×1 / trader ×2 / desk ×5, tier set by the Lemon Squeezy webhook); env-overridable; the denominator `usage_count_24h` counts **live** events only (cached hits log a separate `*_cached` name that never consumes quota). In **both** spend endpoints the quota gate sits **after** the cache re-check and **before** any spend, inside a single-flight lock — so a cached hit and a capped user both cost `$0` (not even the price quote). The paid call is `deep_analyze_ticker` (max_tokens 2000) or `deep_analyze_portfolio`; a global opt-in dollar breaker `_budget_guard` (`ai_analyzer.py:101`, off by default via `LLM_DAILY_BUDGET_USD`) is the only aggregate backstop.

---

## 5. The change surface (what shipped)

`git diff --stat 71fb99c..821f172` = **14 files, +2,408 / −81**, over 8 merged PRs (#18–#25, Jul 7–8 2026). Pre-existing (do NOT re-review as new): owner dashboard, consensus/Kelly/MC/regime engines, the login wall, the billing webhook, and the `_scale_money` machinery — the new work *reuses* masking + owner engines unchanged and adds routes, tables, gates, and two LLM-spend surfaces in front.

| PR | Merge | What shipped |
|----|-------|--------------|
| **#18** | `f2e5805` | `analyze-ticker` v1: shared `_HOLDING_ANALYSIS_ITEM` schema so owner card and customer card can't drift; `deep_analyze_ticker`; `POST /api/analyze-ticker`; new `require_customer_or_owner` gate; heavy-rate + mask-exempt registration. Feature commit `4362196` states it *"Addresses all 7 findings from the adversarial review of the v1 diff."* |
| **#19** | `ba0dc14` | Hardening on #18: intraday chart trims to last N **trading** sessions; per-ticker GET 5-min cache + heavy rate bucket; `add_watch` switched to concurrency-safe single-statement cap; **per-symbol single-flight + eviction** on the AI card; dropped a double-`daEsc` XSS bug; conftest clears shared caches/rate ledger/asyncio locks between tests. |
| **#20** | `a34b412` | `mybook` v2: `customer_positions` table + CRUD (30-position cap); `GET/POST/DELETE /api/mybook` + `POST /api/mybook/analyze` running the owner pipeline on the customer's book; `/api/mybook` added to mask-exempt. |
| **#21** | `8ea6e82` | Verdict-aware Kelly: `_run_book_engines` runs deep analysis **before** Kelly and feeds per-holding verdicts in (parity with owner; previously sized verdict-blind). |
| **#22** | `6ea61df` | `import-watchlist`; print-friendly `GET /mybook/report` (every dynamic string escaped server-side, no LLM spend); `customer_analyses` + `usage_events` tables; per-customer usage metering; owner-only `/api/usage/summary`. |
| **#23** | `7c47066` | `usage_count_24h` + `_check_customer_quota`, placed after cache re-check, before spend; 429 UI states. |
| **#24** | `c5c3b9d` | Billing-tier quota scaling (`get_tier` + `_TIER_QUOTA_MULT`); owner `/usage` cockpit (owner-only path + defense-in-depth handler check). |
| **#25** | `821f172` | Live smoke script `smoke_customer_surface.py`; parallel close-cache warming for cold books; docs (`docs/CUSTOMER_SURFACE.md`). |

---

## 6. Endpoint inventory (routes a logged-in customer can reach)

| Route | Method | Auth gate | Masked for customer? | LLM-spend? |
|---|---|---|---|---|
| `/api/analyze-ticker` (`:1761`) | POST | **`require_customer_or_owner`** (never anon) | **Exempt** — public single-name research, no owner `$` | **YES** — Claude; quota `ticker_card` (25×tier) |
| `/api/mybook/analyze` (`:1970`) | POST | **`require_customer_or_owner`** | **Exempt** — customer's own `$` | **YES** — Claude+Kelly+MC; quota `book_analysis` (10×tier) |
| `/api/mybook` (`:1869`/`:1886`/`:1905`) | GET/POST/DELETE | wall + `_session_email` | Exempt — customer's own book | No |
| `/api/mybook/import-watchlist` (`:2024`) | POST | wall + `_session_email` | Exempt | No |
| `/mybook/report` (`:2128`) | GET (HTML) | wall + `_session_email` | Not `/api/` → not masked; carries only customer's data, escaped server-side | No |
| `/api/watchlist` (`:564`/`:573`/`:587`) | GET/POST/DELETE | wall + `_session_email` | N/A (symbols) | No |
| `/api/me` (`:543`) | GET | wall + inline session-check | N/A (role only, no PII) | No |
| `/api/portfolio` (`:1019`) | GET | wall (any session) | **YES** — owner book scaled to model book | No |
| `/api/regime/{sym}` (`:1586`), `/fundamentals/{sym}` (`:1607`), `/analyst/{sym}` (`:1632`), `/news/{sym}` (`:1656`), `/chart/{sym}` (`:1362`) | GET | wall (any session) | Masked (no-op — no owner `$` keys collide) | No |
| `/usage` (`:2146`), `/api/usage/summary` (`:2187`) | GET | wall + `_OWNER_ONLY_PATHS`/inline `is_owner` | **403 to customer** (owner cockpit) | — |

**Everything else** is owner-only: every other `/api/*` **POST** carries `Depends(require_api_key)` → **403 to a customer** (`/api/deep-analysis :1062`, `/api/screener :1307`, `/api/kelly :1326`, `/api/consensus :1421`, `/api/monte-carlo :1108`, all `/api/agents/* :1235-1307`, all `/api/gemini/* :2232-2293`, all `/api/trading/* :2533-2631`, `/api/config :2339`, all trade/paper mutations). Every other `/api/*` **GET** is customer-reachable through the wall but **masked** by `_scale_money`.

---

## 7. Known issues / open questions (honest)

1. **`GESH_COOKIE_SECURE=0` tradeoff.** The session cookie's `Secure` flag defaults to on (`server.py:601`, `_cookie_secure` returns True unless the env is `0/false/no/off`). It exists so local plain-HTTP dev works; if a production TLS deploy ever sets `GESH_COOKIE_SECURE=0`, the session cookie ships over plaintext. The default is safe; the risk is a misconfigured deploy. No runtime assertion binds "auth enabled + TLS" to "Secure on".
2. **RH session is dead → `/api/portfolio` serves a cached MCP snapshot.** The live `robin_stocks` session is not revivable (`~/.tokens/robinhood.pickle` is 0 bytes; RH changed their auth flow). `data_fetcher.load_snapshot_payload()` maps the MCP-built `live_portfolio_snapshot.json` into the payload shape and `/api/portfolio` serves it marked `offline=True` + snapshot age when `robin_stocks` raises (commit `c3142d1`). **Consequence for masking:** `_customer_scale_factor` reads `equity_summary.total` from that same snapshot file — so a **stale** owner total drives the customer scale factor. It never *leaks* (it fails closed to `0.0` if the file is missing), but the model-book normalization is only as fresh as the last snapshot refresh (the 10:30 ET chain).
3. **One flaky-test class, mitigated in conftest.** All `TestClient`s share the `testclient` client IP → a leaked per-IP rate ledger produced spurious `429`s, and leaked `asyncio.Lock`s (bound to a dead event loop) `500`'d the next test. `tests/conftest.py` (PR #19/#23) now clears the response caches, the rate ledger, and `_ticker_card_cache`/`_ticker_card_flight` between tests and points `auth_users` at a per-test throwaway DB. Worth re-verifying the autouse fixtures actually cover every shared global (a newly-added module-level cache could reintroduce cross-test bleed). **[UPDATE (`feat/spend-gate-hardening`): the specific `test_concurrent_same_symbol_spends_one_llm_call` flake — its fake request lacked `.cookies`, which the cached-path `_session_email` usage-log reads — is now fixed.]**
4. **Smoke harness needs provisioned users / keys; LLM steps are opt-in.** `smoke_customer_surface.py` prints `PASS/FAIL/SKIP` and only **SKIPs** (not fails) when the wall/keys aren't configured — the real customer LLM steps require `--with-llm` (real API cost) and the auth steps require `--email/--password` for a live test user. So a green smoke run does **not** by itself prove the paid path end-to-end unless it was run `--with-llm` against a provisioned account; the LLM assertions are effectively disabled by default.

---

## 8. Review questions for Grok, ranked by risk

1. **Auth-off tenant collapse (highest impact).** With `GESH_AUTH_ENABLED` falsy, `_session_email` returns the **owner** email for *every* caller (`server.py:559-560`) and the masking middleware early-returns raw (`server.py:428`). Is there any code path or deploy config in which the customer-facing deployment could run with the flag unset — and is there a reason there is **no runtime assertion** tying "customers/billing exist" to `auth_enabled()`? If the flag is ever off in prod, every "customer" becomes the owner (reads/writes the owner bucket AND sees unmasked owner dollars).
2. **IDOR / cookie forgery.** The whole model reduces to: can a client make `_session_email` return an email that isn't theirs? Audit `verify_token`'s HMAC compare (`auth_users.py:147`), the `typ='session'` binding (`:153`), and confirm `sub` is never attacker-controlled. Is there truly **no** route that binds `email`/`user_id` from path/query/body? (Grep says the only `email`-parameter functions are internal helpers `_check_customer_quota` and `_render_book_report`.)
3. **Does mask-exempt `/api/analyze-ticker` ever include owner data?** It's served raw. Confirm `deep_analyze_ticker`'s output is purely public per-ticker research (per-share fair value, forecasts) grounded on `_latest_close`, with no positions/portfolio/owner-dollar field that would then be shared into the process-wide, **symbol-keyed** `_ticker_card_cache` (`server.py:1735`) and read back by another customer.
4. **Does mask-exempt `/api/mybook*` ever surface owner or cross-customer data?** `_customer_book_payload` hardcodes `cash/buying_power/day_change = 0.0` and values only `get_positions(email)` × public quotes. Verify a bug could not pull owner cash in, and that the per-email `_mybook_cache` / `_mybook_flight` / `save_book_analysis`/`get_latest_book_analysis` are all keyed on the *same* email — a normalization mismatch (`_session_email` raw vs `_norm(email)` in DB writes) could serve A the cached path of B.
5. **Fail-open quota.** `_check_customer_quota` swallows any non-HTTP exception and returns (no cap; `server.py:1727`). If `usage_events` reads fail (DB locked/corrupt), every customer becomes uncapped while still serving paid Claude calls, and the only backstop (`LLM_DAILY_BUDGET_USD`) is **off by default**. Is fail-*open* the right trade for a spend gate, and should there be a mandatory global floor? **[ADDRESSED in `feat/spend-gate-hardening`: `_check_customer_quota` now FAILS CLOSED (503) on a metering read error — please verify the trade-off, and weigh in on whether a mandatory global `LLM_DAILY_BUDGET_USD` floor is still warranted.]**
6. **Tier spoofing → 5× spend.** `get_tier` trusts `users.tier` set by the Lemon Squeezy webhook (`auth_users.py:711`), applied with no re-verification against live billing. If the webhook can be induced to set `tier='desk'` (replay, HMAC weakness, variant-name substring match), the attacker gets ×5. Also: revoke clears `is_active` but the code notes it does **not** clear `tier` — can a churned desk user retain ×5 until reconciled? **[PARTIALLY ADDRESSED in `feat/spend-gate-hardening`: `get_tier` now returns `None` for INACTIVE users, so a churned/revoked account can no longer scale quota via a stale tier. The webhook-trust / tier-spoofing vector on ACTIVE users remains open — please review `billing.py`'s tier-set path.]**
7. **Per-symbol spend ceiling.** `_safe_symbol` validates format only (`server.py:122`). An authed customer can cycle thousands of distinct valid tickers, each a cache-miss → paid Claude call, bounded only by their 25/50/125 count quota and 30/min heavy rate limit. Model the max: 125 desk ticker cards/day × N desk customers — within budget expectations?
8. **Rate limiter is per-process, per-IP, in-memory.** `_RATE_HITS` is a process-local dict (`server.py:133`), keyed on `request.client.host` (set to `"unknown"` when `request.client` is None → all such callers share one bucket). Behind multiple workers or a load balancer the 30/min heavy cap multiplies per worker and is proxy-rotatable. Confirm single-process deployment and a trustworthy client-IP header.
9. **Middleware ordering / body draining.** The masking middleware buffers and re-serializes `resp.body_iterator` and only acts when content-type is `application/json` and path starts `/api/` (`server.py:444`). Enumerate any customer-reachable `/api/*` route that streams or sets a non-`application/json` content type — it would bypass masking. Also confirm the auth-gate/masking/rate-limit middleware registration order composes as intended.
10. **Book input is count-capped but is it size/$-capped?** Per customer, calls are capped (25/50/125) but each `deep_analyze_portfolio` input scales with the customer's position count (≤30). Is the book-path prompt input length bounded, or can 30 positions with long fields inflate token cost per call?
11. **Owner-only defense-in-depth.** `/usage` and `/api/usage/summary` are gated both at `_OWNER_ONLY_PATHS` (403 in middleware) and by an inline `is_owner` check. Confirm both layers are present on *every* endpoint that renders other customers' emails/counts (they render PII across tenants), and that `usage_summary` (the one unfiltered cross-customer aggregate) is never reachable from a customer route.
12. **Stale masking denominator.** `_customer_scale_factor` divides the model book by `live_portfolio_snapshot.json`'s `equity_summary.total` (§7 item 2). It fails closed to `0.0` if unreadable, but if the snapshot is stale/refreshed mid-flight, could the factor momentarily be wrong (e.g. mixing a fresh percentage with a stale scale)? Is there any window where a partially-written snapshot yields a nonzero-but-wrong total?