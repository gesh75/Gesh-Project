"""tax_lots_enrich.py — lot-level enrichment for the tax-harvest report.

Why this module exists (and why it is NOT what was originally proposed)
-----------------------------------------------------------------------
The plan was "per-lot data unlocks more harvestable loss than the blended
average cost basis shows". **Measured on the real book 2026-08-21, that is false
at this scale**: position-level harvestable loss summed to $407, lot-level to
$376 — lot selection found *less*, because on this book the lots mostly lose
together. The one case where it genuinely helped was NVDA (blended −$11 vs −$19
if only the two losing lots are sold), and $8 is not a feature.

The real gap the lot data closes is **correctness, not optimisation**:

`gesh_core.tax_harvester.get_wash_status()` reads only the `wash_sale_log`
table, and that table is written only by `record_harvest()`. So it detects a
wash window opened by a *prior harvest sale we made*, and is **structurally
blind to purchases**. IRS wash-sale rules trigger on acquiring substantially
identical stock within 30 days BEFORE or AFTER the loss sale — so an ordinary
buy (the KO and ZS adds executed 2026-08-21, say) creates real wash-sale
exposure the harvester cannot currently see, and would happily report such a
position as ELIGIBLE.

`get_equity_tax_lots` supplies the missing input: a real `open_date` per lot.

Design constraints
------------------
* **Additive only.** `enrich()` returns a NEW dict and never edits, removes or
  recomputes an existing key. `total_harvestable_loss_usd`, `eligible`,
  `summary` etc. keep the exact meaning `gen_report2.py` and `server.py`
  already read, so no downstream reader changes.
* **No gesh-core edit.** `tax_harvester.py` here is a gesh-core shim and rule
  #46 says the shim body does nothing — shared logic belongs in gesh-core, and
  bumping that pinned tag is a deliberate separate act. This layer therefore
  lives in the product repo and wraps the shim's OUTPUT.
* **Unknown is never safe.** If the MCP is unavailable the lots come back empty,
  and an empty lot list must render as `lot_data: "unavailable"` — never as
  "no recent purchases, no wash risk". Same rule-#59 theme that made a missing
  cache entry read as "never" instead of a 9999-minute age.

Nothing here places, cancels or modifies an order; it only reads.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

WASH_SALE_DAYS = 30          # IRS window, mirrors gesh_core.tax_harvester

UNAVAILABLE = "unavailable"
OK = "ok"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def recent_purchases(lots: list[dict], *, within_days: int = WASH_SALE_DAYS,
                     today: date | None = None) -> list[dict]:
    """Lots acquired within the wash-sale window (the 'before' leg of the rule)."""
    today = today or _today()
    out = []
    for lot in lots or []:
        d = _parse(lot.get("open_date"))
        if d is None:
            continue
        age = (today - d).days
        if 0 <= age <= within_days:
            out.append({**lot, "days_ago": age})
    return sorted(out, key=lambda x: x["days_ago"])


def harvest_plan(lots: list[dict]) -> dict:
    """Which specific lots to sell to realise the largest loss.

    Only losing, broker-`selectable` lots are included. Robinhood's order tools
    accept a `tax_lots` array, so `lot_ids` is directly usable when/if order
    placement is ever authorised — but this function places nothing.
    """
    sellable = [x for x in lots or []
                if x.get("selectable") and (x.get("unrealized_usd") or 0) < 0]
    sellable.sort(key=lambda x: x["unrealized_usd"])          # biggest loss first
    loss = sum(x["unrealized_usd"] for x in sellable)
    st = sum(x["unrealized_usd"] for x in sellable if x.get("term") == "st")
    lt = sum(x["unrealized_usd"] for x in sellable if x.get("term") == "lt")
    return {
        "lot_ids": [x["lot_id"] for x in sellable],
        "shares": round(sum(x["quantity"] for x in sellable), 6),
        "loss_usd": round(loss, 2),
        "short_term_loss_usd": round(st, 2),
        "long_term_loss_usd": round(lt, 2),
        "lot_count": len(sellable),
    }


def symbol_lot_view(symbol: str, price: float, *, loader=None,
                    today: date | None = None) -> dict:
    """Per-symbol lot analysis. `loader` defaults to rh_mcp_data.unrealized_by_lot.

    Returns ``{"lot_data": "unavailable"}`` when lots cannot be read — callers
    must not read that as an absence of wash-sale risk.
    """
    if loader is None:
        try:
            from rh_mcp_data import unrealized_by_lot as loader     # noqa: N813
        except Exception as e:                                       # pragma: no cover
            logger.warning("tax_lots_enrich: rh_mcp_data unavailable (%s)", e)
            return {"lot_data": UNAVAILABLE, "reason": str(e)}

    try:
        lots = loader(symbol, price)
    except Exception as e:
        logger.warning("tax_lots_enrich: lot fetch failed for %s (%s)", symbol, e)
        return {"lot_data": UNAVAILABLE, "reason": str(e)}

    if not lots:
        return {"lot_data": UNAVAILABLE, "reason": "no lots returned"}

    recent = recent_purchases(lots, today=today)
    plan = harvest_plan(lots)
    return {
        "lot_data": OK,
        "lot_count": len(lots),
        "lots": lots,
        "harvest_plan": plan,
        "recent_purchases": recent,
        "wash_risk_from_purchases": bool(recent),
        "wash_risk_days_remaining": (
            max(WASH_SALE_DAYS - recent[0]["days_ago"], 0) if recent else 0
        ),
    }


def enrich(report: dict, holdings: list[dict], *, loader=None,
           today: date | None = None) -> dict:
    """Layer lot detail onto a `get_harvest_summary()` report. Never mutates it.

    Adds:
      ``lots_by_symbol``            per-symbol view (see symbol_lot_view)
      ``purchase_wash_warnings``    positions the base scan called ELIGIBLE that
                                    actually sit inside a purchase-side wash window
      ``lot_coverage``              {checked, available, unavailable} — reported as
                                    a ratio so a total MCP outage cannot read as
                                    "no warnings found"
    """
    if not isinstance(report, dict):
        return report
    out = dict(report)
    price_by_sym = {}
    for h in holdings or []:
        sym = (h.get("symbol") or "").upper()
        px = h.get("price") or h.get("current_price")
        if sym and px:
            price_by_sym[sym] = float(px)

    # Symbol set = the base scan's rows UNION every holding currently at a loss.
    #
    # The union is load-bearing, not belt-and-braces: get_harvest_summary()
    # exposes only `eligible` / `in_wash_window` / `no_cost_basis` and DISCARDS
    # every BELOW_THRESHOLD candidate, so with MIN_LOSS_USD at $500 a book whose
    # largest single loss is ~$100 produces a report with zero losing rows in it.
    # Deriving only from the report would then check nothing at all — and report
    # "no wash warnings", which is the exact false-clear this module exists to
    # prevent. A position under the harvest threshold still carries wash-sale
    # exposure the moment it IS sold.
    symbols: list[str] = []

    def _add(t: str) -> None:
        t = (t or "").upper()
        if t and t in price_by_sym and t not in symbols:
            symbols.append(t)

    for key in ("eligible", "in_wash_window", "below_threshold", "candidates"):
        for r in report.get(key) or []:
            _add(r.get("ticker") or r.get("symbol") or "")

    for h in holdings or []:
        sym = (h.get("symbol") or "").upper()
        cb = h.get("average_buy_price") or h.get("avg_cost") or h.get("cost_basis")
        px = price_by_sym.get(sym)
        try:
            if sym and px and cb and float(px) < float(cb):
                _add(sym)
        except (TypeError, ValueError):
            continue

    views, warnings = {}, []
    for sym in symbols:
        v = symbol_lot_view(sym, price_by_sym[sym], loader=loader, today=today)
        views[sym] = v
        if v.get("wash_risk_from_purchases"):
            first = v["recent_purchases"][0]
            warnings.append({
                "ticker": sym,
                "bought_days_ago": first["days_ago"],
                "open_date": first.get("open_date"),
                "shares": first.get("quantity"),
                "days_until_safe": v["wash_risk_days_remaining"],
                "note": (
                    f"{sym} was bought {first['days_ago']}d ago — selling at a loss "
                    f"now is a wash sale under the 30-day purchase-side rule. "
                    f"The base scan cannot see this (it tracks only prior harvest "
                    f"sales, not purchases)."
                ),
            })

    available = sum(1 for v in views.values() if v.get("lot_data") == OK)
    out["lots_by_symbol"] = views
    out["purchase_wash_warnings"] = warnings
    out["lot_coverage"] = {
        "checked": len(views),
        "available": available,
        "unavailable": len(views) - available,
    }
    return out


if __name__ == "__main__":                                       # pragma: no cover
    import json
    eng = json.load(open("engine_results.json"))
    rep = enrich(eng.get("tax_report") or {}, eng.get("holdings") or [])
    cov = rep["lot_coverage"]
    print(f"lot coverage: {cov['available']}/{cov['checked']} symbols")
    for w in rep["purchase_wash_warnings"]:
        print(f"  ⚠ {w['ticker']}: bought {w['bought_days_ago']}d ago, "
              f"safe in {w['days_until_safe']}d")
    for sym, v in rep["lots_by_symbol"].items():
        if v.get("lot_data") != OK:
            print(f"  {sym}: {v['lot_data']} ({v.get('reason')})")
            continue
        p = v["harvest_plan"]
        print(f"  {sym}: {v['lot_count']} lots · sell {p['lot_count']} "
              f"({p['shares']} sh) → {p['loss_usd']:+.2f} "
              f"(st {p['short_term_loss_usd']:+.0f} / lt {p['long_term_loss_usd']:+.0f})")
