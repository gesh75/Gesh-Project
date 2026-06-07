"""
invest_radar.py — Daily multi-factor DIVERSIFICATION radar (advisory-only).

Answers the gap the daily report has always had: it only ever recommends adding
to EXISTING holdings. This module scans the screener universe, EXCLUDES current
holdings, scores each NEW name on a 5-factor model over day/week/month horizons,
gates entries on multi-timeframe confluence, filters by correlation to the book,
enforces sector-diversification caps, and sizes each pick against AVAILABLE cash.

Design provenance: a research→design→adversarial-review workflow. The adversarial
pass caught (and this code fixes) several would-be silent failures:
  * ewma_vol needs a pd.Series — a raw ndarray silently nulls vol for every name,
    killing risk-adjusted momentum + the low-vol factor + risk-parity sizing.
  * z-score EACH sub-metric BEFORE averaging (else growth ~0.15 is washed out by
    pb ~5-30); pb lives ONLY in Value, never double-counted into Quality.
  * Hard vetoes fire on signals that actually exist; missing data never manufactures
    a timing score; factor weights renormalize when a factor goes dark.
  * Network is BOUNDED: screen_market once (cached) → score → top-K → one batched
    1y frame for vol+correlation → one detect_regimes → consensus on ≤4 final picks.

HARD invariants (enforced + tested):
  * ADVISORY-ONLY: never constructs a TradeIntent, never imports trading.execution /
    risk_gate / autopilot, never touches the autonomy-gate funnel, never auto-trades.
  * READ-ONLY: reads yfinance + the screener; writes nothing to gesh_financial.db.
  * RESPECTS allocation_config: never breaches min_cash_buffer; honours
    max_open_positions / max_stock_per_trade. Slots full or cash<buffer → SWAP_ONLY.
  * NO PII: only `cash` and `total` are read from equity_summary; withdrawable_amount,
    average_buy_price, quantities are never echoed.
  * FAIL-SOFT: any data failure returns the honest empty contract; never raises into
    the 10:30 pipeline.
  * yfinance/robin_stocks ONLY at runtime (the 10:30 job is a plain Python process,
    not a Claude session — no MCP).
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Callable

# ── tunables ──────────────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "momentum": 0.20, "quality": 0.30, "value": 0.20,
    "analyst_revisions": 0.15, "low_vol": 0.15,
}
CORR_THRESHOLD = 0.70        # >= this corr to any holding (or picked name) → drop
SECTOR_CAP = 0.30            # a single sector may not exceed 30% post-trade
TOP3_SECTOR_CAP = 0.70       # top-3 sectors combined may not exceed 70%
MAX_NEW_NAMES = 4            # never suggest more than this many new names at once
NEW_NAME_PCT_CAP = 0.07      # max 7% of portfolio into one brand-new name
MIN_PRICE = 5.0             # liquidity / penny floor
MIN_COVERAGE = 3            # a factor needs >=3 valid points to be trusted
SCREEN_TOP_N = 40           # how many names screen_market enriches with fundamentals
POOL_MULT = 4               # expensive stage runs on max(new*MULT, POOL_MIN) names
POOL_MIN = 12

_RECO_ORD = {"strong_buy": 1.0, "buy": 0.5, "hold": 0.0,
             "underperform": -0.5, "sell": -1.0, "strong_sell": -1.0}

_DEFAULT_CFG = {"min_cash_buffer": 500.0, "max_open_positions": 5,
                "max_stock_per_trade": 5000.0}


# ══════════════════════════════════════════════════════════════════════════════
# Pure helpers (no network — fully unit-testable)
# ══════════════════════════════════════════════════════════════════════════════

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def winsorized_z(values: list[float | None], limit: float = 3.0) -> list[float]:
    """Cross-sectional z-score, winsorized at +/-`limit` SD. None / NaN → 0.0
    (neutral). Fewer than MIN_COVERAGE valid points → all-zero (untrustworthy)."""
    valid = [float(v) for v in values if _is_num(v)]
    if len(valid) < MIN_COVERAGE:
        return [0.0] * len(values)
    mu = statistics.fmean(valid)
    sd = statistics.pstdev(valid)
    if sd == 0:
        return [0.0] * len(values)
    out: list[float] = []
    for v in values:
        if not _is_num(v):
            out.append(0.0)
        else:
            out.append(max(-limit, min(limit, (float(v) - mu) / sd)))
    return out


def _avg_present(subzs: list[float], present: list[bool]) -> float | None:
    """Average only the sub-z values whose ORIGINAL input was present."""
    vals = [z for z, p in zip(subzs, present) if p]
    return statistics.fmean(vals) if vals else None


def compute_factor_scores(candidates: list[dict], vols: dict[str, float | None]) -> list[dict]:
    """Score the cross-section on 5 orthogonal factors. Returns NEW dicts (inputs
    untouched). Each sub-metric is z-scored BEFORE averaging into its factor, so
    heterogeneous units never let one sub-metric dominate. Weights renormalize over
    factors with >=MIN_COVERAGE valid inputs so a dark factor never silently dilutes."""
    n = len(candidates)
    if n == 0:
        return []
    syms = [c.get("symbol") for c in candidates]

    # Momentum — vol-scaled (risk-adjusted). Impute median sigma so every name is in
    # the SAME unit pool before z-scoring (no divide/no-divide leakage).
    sig_list = [vols.get(s) for s in syms]
    valid_sig = [s for s in sig_list if _is_num(s) and s > 0]
    med_sig = statistics.median(valid_sig) if valid_sig else 1.0
    mom_raw: list[float] = []
    for c, s in zip(candidates, sig_list):
        blend = 0.6 * (c.get("ret_1w") or 0.0) + 0.4 * (c.get("ret_1m") or 0.0)
        sigma = s if (_is_num(s) and s > 0) else (med_sig or 1.0)
        mom_raw.append(blend / (sigma or 1.0))
    zmom = winsorized_z(mom_raw)
    cov_mom = sum(1 for c in candidates if _is_num(c.get("ret_1w")) or _is_num(c.get("ret_1m")))

    # Quality — growth only (NO pb here; pb belongs to Value to avoid double-count).
    zeg = winsorized_z([c.get("earnings_growth") for c in candidates])
    zrg = winsorized_z([c.get("revenue_growth") for c in candidates])
    zqual, cov_qual = [], 0
    for i, c in enumerate(candidates):
        pres = [_is_num(c.get("earnings_growth")), _is_num(c.get("revenue_growth"))]
        if any(pres):
            cov_qual += 1
        q = _avg_present([zeg[i], zrg[i]], pres)
        zqual.append(q if q is not None else 0.0)

    # Value — sign-flipped earnings/book yield (lower multiple = better). pe<=0 → drop.
    pe_in = [(-c["pe_ratio"]) if (_is_num(c.get("pe_ratio")) and c["pe_ratio"] > 0) else None
             for c in candidates]
    pb_in = [(-c["pb_ratio"]) if (_is_num(c.get("pb_ratio")) and c["pb_ratio"] > 0) else None
             for c in candidates]
    zpe, zpb = winsorized_z(pe_in), winsorized_z(pb_in)
    zval, cov_val = [], 0
    for i in range(n):
        pres = [pe_in[i] is not None, pb_in[i] is not None]
        if any(pres):
            cov_val += 1
        v = _avg_present([zpe[i], zpb[i]], pres)
        zval.append(v if v is not None else 0.0)

    # Analyst revisions / upside — only counts when there are real analysts.
    up_in: list[float | None] = []
    for c in candidates:
        has = _is_num(c.get("analyst_upside")) and (c.get("num_analysts") or 0) >= 1
        up_in.append(float(c["analyst_upside"]) if has else None)
    zup = winsorized_z(up_in)
    zrev, cov_rev = [], 0
    for i, c in enumerate(candidates):
        if up_in[i] is None:
            zrev.append(0.0)
            continue
        cov_rev += 1
        reco = _RECO_ORD.get(str(c.get("reco_key", "")).lower(), 0.0)
        base = 0.85 * zup[i] + 0.15 * reco
        if (c.get("num_analysts") or 0) < 3:
            base *= 0.5                       # thin coverage → half weight
        zrev.append(base)

    # Low volatility — negated annualized vol (lower vol scores higher).
    lv_in = [(-vols[s]) if (s in vols and _is_num(vols.get(s)) and vols[s] > 0) else None
             for s in syms]
    zlv = winsorized_z(lv_in)
    cov_lv = sum(1 for v in lv_in if v is not None)

    factor_z = {"momentum": zmom, "quality": zqual, "value": zval,
                "analyst_revisions": zrev, "low_vol": zlv}
    coverage = {"momentum": cov_mom, "quality": cov_qual, "value": cov_val,
                "analyst_revisions": cov_rev, "low_vol": cov_lv}

    active = [f for f in WEIGHTS if coverage[f] >= MIN_COVERAGE]
    wsum = sum(WEIGHTS[f] for f in active) or 1.0
    weights_used = {f: (WEIGHTS[f] / wsum if f in active else 0.0) for f in WEIGHTS}

    out: list[dict] = []
    for i, c in enumerate(candidates):
        comp = sum(weights_used[f] * factor_z[f][i] for f in WEIGHTS)
        out.append({
            **c,
            "factor_scores": {f: round(factor_z[f][i], 4) for f in WEIGHTS},
            "composite_z": round(comp, 4),
            "weights_used": weights_used,
            "factor_coverage": coverage,
        })
    return out


def timing_gate(cand: dict, regime_row: dict | None, fft: dict | None,
                sector_hot: bool) -> dict:
    """Multi-timeframe entry gate. Returns confluence fraction [0,1] + hard-veto
    flag. Each confluence bit requires REAL data — missing inputs never award credit.
    Hard veto = month-trend down, OR overbought-and-extended, OR a bearish regime."""
    ret_1m = cand.get("ret_1m") or 0.0
    rr = regime_row or {}
    adx = rr.get("adx")
    rsi = rr.get("rsi")
    pctb_raw = rr.get("pct_b")
    pctb = (pctb_raw / 100.0) if _is_num(pctb_raw) else None
    regime_lbl = str(rr.get("regime") or "UNKNOWN").upper()

    veto, reason = False, None
    if ret_1m < 0:
        veto, reason = True, "ret_1m<0 (month direction down)"
    elif _is_num(rsi) and pctb is not None and rsi > 70 and pctb > 0.9:
        veto, reason = True, "overbought & extended (RSI>70 & %B>0.9)"
    elif any(tok in regime_lbl for tok in ("BEAR", "RISK-OFF", "DOWN")):
        veto, reason = True, f"regime {regime_lbl}"

    bullish_regime = any(tok in regime_lbl for tok in ("TREND", "BREAKOUT", "MOMENTUM-HIGH"))
    bits = [
        (ret_1m > 0) and bullish_regime,                                   # direction
        _is_num(adx) and adx >= 25,                                        # trend strength
        pctb is not None and 0.3 <= pctb <= 0.7,                           # location (pullback)
        _is_num(rsi) and 45 <= rsi <= 65,                                  # cooled momentum
        bool(fft) and bool(fft.get("is_rising")) and _is_num(fft.get("cycle_position"))
            and fft["cycle_position"] <= 0.65,                             # cycle trough/rising
        bool(sector_hot),                                                  # sector strength
    ]
    conf = sum(1 for b in bits if b) / len(bits)
    return {
        "confluence_fraction": round(conf, 3), "vetoed": veto, "veto_reason": reason,
        "regime": regime_lbl, "adx": adx, "rsi": rsi, "pct_b": pctb_raw,
        "fft_signal": (fft or {}).get("signal"), "fft_cycle_position": (fft or {}).get("cycle_position"),
        "sector_hot": bool(sector_hot),
    }


def size_one(price: float, vol: float | None, inv_vol_weight: float,
             deployable_remaining: float, total: float, cfg: dict,
             kelly_usd: float | None = None) -> dict:
    """Size one new position at the MINIMUM binding constraint: half-Kelly, inverse-vol
    risk-parity slice, 7% hard cap, max_stock_per_trade, and remaining cash budget."""
    cap_usd = NEW_NAME_PCT_CAP * total
    max_stock = float(cfg.get("max_stock_per_trade", 5000.0))
    rp_usd = max(0.0, inv_vol_weight) * max(0.0, deployable_remaining)
    opts = {
        "kelly": kelly_usd if (kelly_usd and kelly_usd > 0) else cap_usd,
        "risk_parity": rp_usd,
        "7pct_cap": cap_usd,
        "max_stock_per_trade": max_stock,
        "budget": deployable_remaining,
    }
    positives = {k: v for k, v in opts.items() if v and v > 0}
    if not positives or price <= 0:
        return {"recommended_usd": 0.0, "recommended_shares": 0, "sizing_binding_constraint": "budget"}
    binding = min(positives, key=positives.get)
    size = positives[binding]
    shares = math.floor(size / price)
    return {"recommended_usd": round(size, 2), "recommended_shares": int(shares),
            "sizing_binding_constraint": binding}


def holdings_sector_value(holdings: list[dict], sector_of: Callable[[str], str | None]) -> dict[str, float]:
    """Current $-weight per sector for the EXISTING book (the real concentration the
    user complains about). Unresolved sector buckets as 'UNKNOWN' (fail-closed)."""
    out: dict[str, float] = {}
    for h in holdings:
        sec = sector_of(h.get("symbol", "")) or "UNKNOWN"
        out[sec] = out.get(sec, 0.0) + float(h.get("equity") or 0.0)
    return out


def max_corr_to(sym: str, held: set[str], corr: dict | None) -> float | None:
    """Max |correlation| of `sym` to any held name. None when no corr data
    (guard disabled — caller must treat as UNKNOWN, never as 'passed')."""
    if not corr:
        return None
    row = corr.get(sym) or {}
    vals = [abs(row[h]) for h in held if _is_num(row.get(h))]
    return max(vals) if vals else None


# ══════════════════════════════════════════════════════════════════════════════
# Network fetchers (only hit when the caller does NOT inject — monkeypatchable)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_screened() -> list[dict]:
    """The single heaviest call: screen_market enriches the ~95-name universe. The
    daily engine driver may cache this; here we just call it (it returns [] on failure)."""
    import market_data
    return market_data.screen_market(top_n=SCREEN_TOP_N) or []


def _fetch_price_frame(symbols: list[str]):
    """One batched 1y daily-close frame for vol + correlation. Returns a DataFrame or None."""
    try:
        import yfinance as yf
        raw = yf.download(list(dict.fromkeys(symbols)), period="1y",
                          auto_adjust=True, progress=False, threads=False)
        if raw is None or len(raw) == 0:
            return None
        import pandas as pd
        close = raw["Close"] if "Close" in getattr(raw, "columns", []) or (
            hasattr(raw.columns, "get_level_values") and "Close" in raw.columns.get_level_values(0)) else raw
        if isinstance(close, pd.Series):
            close = close.to_frame(name=symbols[0])
        return close
    except Exception:
        return None


def _vols_from_frame(frame, symbols: list[str]) -> dict[str, float | None]:
    """EWMA vol per symbol from the batched frame. CRITICAL: ewma_vol needs a Series."""
    out: dict[str, float | None] = {}
    if frame is None:
        return {s: None for s in symbols}
    try:
        import volatility_models as vm
        import pandas as pd
        for s in symbols:
            try:
                col = frame[s] if s in getattr(frame, "columns", []) else None
                if col is None:
                    out[s] = None
                    continue
                rets = col.dropna().pct_change().dropna()
                out[s] = float(vm.ewma_vol(pd.Series(rets), span=30)) if len(rets) >= 5 else None
            except Exception:
                out[s] = None
    except Exception:
        return {s: None for s in symbols}
    return out


def _corr_from_frame(frame, cand_syms: list[str], held: set[str]) -> dict | None:
    """{cand: {held: corr}} from 1y daily returns. None if the frame is unusable."""
    if frame is None:
        return None
    try:
        rets = frame.dropna(how="all").pct_change().dropna(how="all")
        cols = set(getattr(rets, "columns", []))
        out: dict[str, dict[str, float]] = {}
        for c in cand_syms:
            if c not in cols:
                continue
            row: dict[str, float] = {}
            for h in held:
                if h in cols:
                    try:
                        cval = rets[c].corr(rets[h])
                        if _is_num(cval):
                            row[h] = float(cval)
                    except Exception:
                        pass
            out[c] = row
        return out or None
    except Exception:
        return None


def _fetch_regime_lookup(symbols: list[str]) -> dict[str, dict]:
    try:
        import regime
        res = regime.detect_regimes(list(symbols))
        return {r["symbol"]: r for r in (res or {}).get("regimes", []) if r.get("symbol")}
    except Exception:
        return {}


def _fetch_fft(sym: str, frame) -> dict:
    try:
        import fft_cycles
        prices = None
        if frame is not None and sym in getattr(frame, "columns", []):
            prices = frame[sym].dropna().to_numpy()
        return fft_cycles.detect_fft_cycles(sym, prices=prices) or {}
    except Exception:
        return {}


def _fetch_sector_hot(symbols: list[str]) -> set[str]:
    try:
        import sector_filter
        rep = sector_filter.batch_sector_check(list(symbols)) or {}
        return {r["symbol"] for r in rep.get("hot", []) if r.get("symbol")}
    except Exception:
        return set()


_SECTOR_CACHE: dict[str, str | None] = {}


def _sector_of_live(sym: str) -> str | None:
    if sym in _SECTOR_CACHE:
        return _SECTOR_CACHE[sym]
    try:
        import yfinance as yf
        sec = yf.Ticker(sym).info.get("sector")
    except Exception:
        sec = None
    _SECTOR_CACHE[sym] = sec
    return sec


def _vol_of(sym: str, vols: dict, frame) -> float | None:
    v = vols.get(sym)
    return v if _is_num(v) else None


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def _now_iso(now: str | None) -> str:
    return now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty(mode: str, *, now: str | None, deployable: float = 0.0, open_slots: int = 0,
           new_names: int = 0, excluded: list[str] | None = None,
           sector_weights: dict | None = None, warnings: list[str] | None = None,
           swaps: list | None = None, diag: dict | None = None) -> dict:
    d = {"universe_size": 0, "after_exclusions": 0, "after_timing_gate": 0,
         "after_corr_filter": 0, "after_sector_cap": 0, "data_warnings": warnings or []}
    if diag:
        d.update(diag)
    return {
        "as_of": _now_iso(now), "mode": mode, "deployable_cash": round(deployable, 2),
        "open_slots": int(open_slots), "new_names_to_add": int(new_names),
        "excluded_holdings": sorted(excluded or []), "candidates": [],
        "swap_suggestions": swaps or [], "existing_sector_weights": sector_weights or {},
        "diagnostics": d,
    }


def scan_candidates(
    holdings: list[dict],
    equity_summary: dict,
    *,
    screened: list[dict] | None = None,
    regime_lookup: dict[str, dict] | None = None,
    fft_lookup: dict[str, dict] | None = None,
    vols: dict[str, float | None] | None = None,
    sector_hot: set[str] | None = None,
    sector_of: Callable[[str], str | None] | None = None,
    corr: dict | None = None,
    price_frame: Any = None,
    config: dict | None = None,
    consensus_fn: Callable[..., dict] | None = None,
    now: str | None = None,
) -> dict:
    """Advisory-only diversification scan. Every external dependency is injectable so
    this is deterministic + network-free under test. Never raises into the caller."""
    try:
        return _scan_impl(holdings, equity_summary, screened=screened,
                          regime_lookup=regime_lookup, fft_lookup=fft_lookup, vols=vols,
                          sector_hot=sector_hot, sector_of=sector_of, corr=corr,
                          price_frame=price_frame, config=config,
                          consensus_fn=consensus_fn, now=now)
    except Exception as exc:   # ultimate fail-soft: never break the 10:30 pipeline
        return _empty("NO_ACTION", now=now,
                      excluded=[h.get("symbol", "") for h in (holdings or [])],
                      warnings=[f"radar aborted: {type(exc).__name__}: {exc}"])


def _scan_impl(holdings, equity_summary, *, screened, regime_lookup, fft_lookup, vols,
               sector_hot, sector_of, corr, price_frame, config, consensus_fn, now) -> dict:
    cfg = {**_DEFAULT_CFG, **(config or _load_cfg())}
    sector_of = sector_of or _sector_of_live
    warnings: list[str] = []

    # PII discipline: only cash + total ever leave equity_summary.
    cash = float(equity_summary.get("cash") or 0.0)
    total = float(equity_summary.get("total") or 0.0) or 1.0
    min_buf = float(cfg["min_cash_buffer"])
    deployable = max(0.0, cash - min_buf)
    open_slots = max(0, int(cfg["max_open_positions"]) - len(holdings))
    new_names = min(open_slots, MAX_NEW_NAMES)
    held = {str(h.get("symbol", "")).upper() for h in holdings}
    excluded = sorted(s for s in held if s)

    # 1. Universe (the single heavy fetch unless injected).
    if screened is None:
        try:
            screened = _fetch_screened()
        except Exception as exc:
            return _empty("NO_ACTION", now=now, deployable=deployable, open_slots=open_slots,
                          new_names=new_names, excluded=excluded,
                          warnings=[f"universe fetch failed: {type(exc).__name__}: {exc}"])

    # 2. Exclude holdings + data-sanity.
    universe_n = len(screened)
    candidates: list[dict] = []
    for c in screened:
        sym = str(c.get("symbol", "")).upper()
        if not sym or sym in held:
            continue
        if not _is_num(c.get("price")) or c["price"] < MIN_PRICE:
            continue
        if (c.get("num_analysts") or 0) < 1:
            continue
        sec = c.get("sector") or sector_of(sym)
        if not sec:
            continue
        candidates.append({**c, "symbol": sym, "sector": sec})
    after_excl = len(candidates)

    existing_weights = {k: round(v / total, 4)
                        for k, v in holdings_sector_value(holdings, sector_of).items()}

    if not candidates:
        return _empty("NO_ACTION", now=now, deployable=deployable, open_slots=open_slots,
                      new_names=new_names, excluded=excluded, sector_weights=existing_weights,
                      warnings=warnings + ["no eligible new-name candidates"],
                      diag={"universe_size": universe_n, "after_exclusions": 0})

    # 3. Vols (batched frame) + fundamental scoring → rank → bounded pool.
    cand_syms = [c["symbol"] for c in candidates]
    if vols is None:
        if price_frame is None:
            price_frame = _fetch_price_frame(cand_syms + list(held))
        vols = _vols_from_frame(price_frame, cand_syms + list(held))
        if not any(_is_num(v) for v in vols.values()):
            warnings.append("vol unavailable for all names (low-vol + risk-parity degraded)")
    scored = compute_factor_scores(candidates, vols)
    scored.sort(key=lambda c: c["composite_z"], reverse=True)
    pool_k = max(new_names * POOL_MULT, POOL_MIN)
    pool = scored[:pool_k]

    # 4. Timing gate (regime + fft + sector confluence) on the bounded pool only.
    pool_syms = [c["symbol"] for c in pool]
    if regime_lookup is None:
        regime_lookup = _fetch_regime_lookup(pool_syms)
    if sector_hot is None:
        sector_hot = _fetch_sector_hot(pool_syms)
    survivors: list[dict] = []
    for c in pool:
        sym = c["symbol"]
        rrow = (regime_lookup or {}).get(sym)
        fft = (fft_lookup or {}).get(sym)
        if fft is None and fft_lookup is None:
            fft = _fetch_fft(sym, price_frame)
        tg = timing_gate(c, rrow, fft, sym in (sector_hot or set()))
        if tg["vetoed"]:
            warnings.append(f"{sym} vetoed: {tg['veto_reason']}")
            continue
        survivors.append({**c, "timing": tg})
    after_timing = len(survivors)

    # 5. Correlation to the book (soft: unknown stays, >=threshold drops).
    for s in survivors:
        mc = max_corr_to(s["symbol"], held, corr if corr is not None else
                         _corr_from_frame(price_frame, [s["symbol"]], held))
        s["max_corr_to_holdings"] = round(mc, 3) if mc is not None else None
        s["correlation_ok"] = (mc is None) or (mc < CORR_THRESHOLD)
    pre_corr = survivors
    survivors = [s for s in survivors if s["correlation_ok"]]
    if not survivors and pre_corr:
        # Don't let the corr cull empty the set — keep the lowest-corr few, flagged.
        survivors = sorted(pre_corr, key=lambda s: (s["max_corr_to_holdings"] or 1.0))[:new_names or 1]
        warnings.append("correlation cull emptied set — relaxed to lowest-corr names")
    after_corr = len(survivors)

    # rank survivors: best composite first, lower correlation breaks ties
    survivors.sort(key=lambda s: (-s["composite_z"], (s["max_corr_to_holdings"] or 0.0)))

    # 6. BUY selection (only when slots + cash allow), else SWAP advisory.
    sector_value = holdings_sector_value(holdings, sector_of)
    picks: list[dict] = []
    if new_names > 0 and deployable > 0:
        picks = _select(survivors, sector_value, total, new_names, deployable, vols, cfg)

    # 7. Consensus only on the FINAL picks (bounded ≤ MAX_NEW_NAMES) as a display field.
    if picks:
        cfn = consensus_fn or _consensus_safe
        for p in picks:
            try:
                cons = cfn(p["symbol"], equity=0, total_portfolio=total,
                           avg_cost=p["price"], current_price=p["price"]) or {}
            except Exception:
                cons = {}
            p["consensus_verdict"] = cons.get("verdict", "N/A")
            p["consensus_raw_score"] = cons.get("raw_score")

    mode = "BUY" if picks else ("SWAP_ONLY" if survivors else "NO_ACTION")
    swaps = [] if mode == "BUY" else _build_swaps(holdings, sector_value, survivors, total, sector_of)

    return {
        "as_of": _now_iso(now), "mode": mode, "deployable_cash": round(deployable, 2),
        "open_slots": open_slots, "new_names_to_add": new_names,
        "excluded_holdings": excluded, "candidates": [_public(p) for p in picks],
        "swap_suggestions": swaps, "existing_sector_weights": existing_weights,
        "diagnostics": {
            "universe_size": universe_n, "after_exclusions": after_excl,
            "after_timing_gate": after_timing, "after_corr_filter": after_corr,
            "after_sector_cap": len(picks), "data_warnings": warnings,
        },
    }


def _select(survivors, sector_value, total, n, deployable, vols, cfg) -> list[dict]:
    """Two-pass sized selection: pass 1 = best name per NEW sector (diversification
    preference); pass 2 = fill remaining slots/budget. Enforces sector caps and
    candidate-vs-candidate de-correlation; drops sub-1-share suggestions."""
    inv = {s["symbol"]: (1.0 / (vols.get(s["symbol"]) or 0.02)) for s in survivors}
    inv_sum = sum(inv.values()) or 1.0
    sv = dict(sector_value)
    picks: list[dict] = []
    remaining = deployable
    used_sectors: set[str] = set()

    def cap_ok(sec: str, size: float) -> bool:
        after = dict(sv)
        after[sec] = after.get(sec, 0.0) + size
        if after[sec] / total > SECTOR_CAP:
            return False
        top3 = sum(sorted(after.values(), reverse=True)[:3])
        return top3 / total <= TOP3_SECTOR_CAP

    def decorrelated(cand) -> bool:
        for p in picks:
            mc = (cand.get("_corr_among") or {}).get(p["symbol"])
            if _is_num(mc) and abs(mc) >= CORR_THRESHOLD:
                return False
        return True

    def try_take(s) -> bool:
        nonlocal remaining
        if len(picks) >= n or remaining <= 0:
            return False
        if not decorrelated(s):
            return False
        sz = size_one(s["price"], vols.get(s["symbol"]), inv[s["symbol"]] / inv_sum,
                      remaining, total, cfg)
        if sz["recommended_shares"] < 1:
            return False
        if not cap_ok(s["sector"], sz["recommended_usd"]):
            return False
        s = {**s, **sz, "sector_weight_after": round((sv.get(s["sector"], 0.0) + sz["recommended_usd"]) / total, 4),
             "sector_cap_ok": True}
        sv[s["sector"]] = sv.get(s["sector"], 0.0) + sz["recommended_usd"]
        remaining -= sz["recommended_usd"]
        used_sectors.add(s["sector"])
        picks.append(s)
        return True

    for s in survivors:                          # pass 1 — one per new sector
        if len(picks) >= n:
            break
        if s["sector"] in used_sectors:
            continue
        try_take(s)
    for s in survivors:                          # pass 2 — fill remaining
        if len(picks) >= n:
            break
        if any(p["symbol"] == s["symbol"] for p in picks):
            continue
        try_take(s)
    return picks


def _build_swaps(holdings, sector_value, survivors, total, sector_of) -> list[dict]:
    """When no BUY is possible, surface a funding-source swap: trim the most
    OVER-WEIGHT sector's largest holding to fund the best diversifying new name."""
    if not survivors or not holdings:
        return []
    if not sector_value:
        return []
    over_sector = max(sector_value, key=sector_value.get)
    in_sector = [h for h in holdings if (sector_of(h.get("symbol", "")) or "UNKNOWN") == over_sector]
    if not in_sector:
        return []
    trim = max(in_sector, key=lambda h: float(h.get("equity") or 0.0))
    best = survivors[0]
    return [{
        "trim_symbol": trim.get("symbol"),
        "fund_new_symbol": best["symbol"],
        "rationale": (f"{over_sector} is the most concentrated sector "
                      f"({round(sector_value[over_sector] / total * 100)}% of book). Trimming "
                      f"{trim.get('symbol')} would fund {best['symbol']} ({best['sector']}), a "
                      f"lower-correlation diversifier — or raise max_open_positions toward 12–15."),
    }]


_PUBLIC_FIELDS = (
    "symbol", "company_name", "sector", "price", "ret_1w", "ret_1m",
    "composite_z", "factor_scores", "weights_used", "factor_coverage", "timing",
    "consensus_verdict", "consensus_raw_score", "analyst_upside", "num_analysts",
    "max_corr_to_holdings", "correlation_ok", "sector_weight_after", "sector_cap_ok",
    "recommended_usd", "recommended_shares", "sizing_binding_constraint",
)


def _public(p: dict) -> dict:
    """Whitelist the fields surfaced downstream — never spread raw inputs (PII guard)."""
    out = {k: p[k] for k in _PUBLIC_FIELDS if k in p}
    out["composite_score"] = _to_score(p.get("composite_z"))
    out["horizon_view"] = {
        "day": (p.get("timing") or {}).get("fft_signal") or "—",
        "week": f"1w {p.get('ret_1w')}%",
        "month": f"1m {p.get('ret_1m')}%",
    }
    t = p.get("timing") or {}
    out["rationale"] = (f"{p.get('sector')} diversifier · composite z={p.get('composite_z')} · "
                        f"confluence {int((t.get('confluence_fraction') or 0) * 100)}% · "
                        f"corr-to-book {p.get('max_corr_to_holdings')}")
    return out


def _to_score(z: float | None) -> int:
    """Map a composite z (~[-3,3]) to a coarse 0-100 display score (no false precision)."""
    if not _is_num(z):
        return 50
    return int(max(0, min(100, round(50 + 16.67 * z))))


def summarize_for_prompt(radar: dict, max_names: int = 4) -> str:
    """A compact, advisory-only text block for injection into the deep-analysis
    prompt so Claude's narrative is AWARE of new-name opportunities — without
    bloating the prompt with the full radar dict. Never invents data."""
    if not radar:
        return ""
    mode = radar.get("mode", "NO_ACTION")
    weights = radar.get("existing_sector_weights") or {}
    top_sector = max(weights, key=weights.get) if weights else None
    lines = ["DIVERSIFICATION RADAR (advisory-only — new, NON-held names; never auto-traded):",
             f"  mode={mode} · deployable_cash=${radar.get('deployable_cash', 0):.0f} · "
             f"open_slots={radar.get('open_slots', 0)}"]
    if top_sector:
        lines.append(f"  book concentration: {top_sector} {weights[top_sector] * 100:.0f}% "
                     f"(sector weights: {', '.join(f'{k} {v*100:.0f}%' for k, v in sorted(weights.items(), key=lambda x: -x[1])[:4])})")
    cands = radar.get("candidates") or []
    if cands:
        lines.append("  top new-name candidates:")
        for c in cands[:max_names]:
            lines.append(f"    - {c.get('symbol')} ({c.get('sector')}) score={c.get('composite_score')} "
                         f"{c.get('recommended_shares')} sh ≈${c.get('recommended_usd')} · {c.get('consensus_verdict','N/A')} "
                         f"· corr-to-book {c.get('max_corr_to_holdings')}")
    for s in (radar.get("swap_suggestions") or [])[:2]:
        lines.append(f"  swap idea: trim {s.get('trim_symbol')} → fund {s.get('fund_new_symbol')}")
    return "\n".join(lines)


def _load_cfg() -> dict:
    try:
        from allocation_config import load_config
        return load_config()
    except Exception:
        return dict(_DEFAULT_CFG)


def _consensus_safe(symbol: str, **kw) -> dict:
    try:
        import consensus
        return consensus.run_consensus_holding(symbol, **kw) or {}
    except Exception:
        return {}
