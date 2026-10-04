"""SEO step 3: free tool pages (SSR explainer + FAQ + client-side calculators)."""

from __future__ import annotations

import html as htmllib
import json
import re
import shutil
import subprocess

import pytest
from seo_helpers import BASE, _jsonld, _meta, _sitemap, assert_seo_head

import auth_users
import seo_tools_data

TOOLS = [
    (
        "portfolio-xray",
        "Free Portfolio X-Ray: See Your Hidden Concentrations | GESH Financial",
        "Paste your tickers for a free portfolio X-ray: sector breakdown, concentration flags, "
        "and diversification score. No signup needed.",
        "Free Portfolio X-Ray",
    ),
    (
        "monte-carlo-simulator",
        "Free Monte Carlo Portfolio Simulator | GESH Financial",
        "Simulate 1,000 possible paths for your portfolio and see the worst plausible month — "
        "free, no signup. Educational illustration, not a prediction.",
        "Monte Carlo Portfolio Simulator",
    ),
    (
        "kelly-calculator",
        "Kelly Criterion Position Size Calculator | GESH Financial",
        "Size any position with the Kelly criterion — full, half, and quarter Kelly compared. "
        "Free calculator, no signup.",
        "Kelly Position Size Calculator",
    ),
]
IDS = [t[0] for t in TOOLS]


def _section(html: str, sid: str) -> str:
    m = re.search(rf'<section id="{sid}".*?</section>', html, re.S)
    assert m, sid
    return m.group(0)


def _words(fragment: str) -> int:
    return len(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


@pytest.mark.parametrize(("slug", "title", "desc", "h1"), TOOLS)
def test_tool_page_head_h1_and_logged_out_access(seo_anon, slug, title, desc, h1) -> None:
    r = seo_anon.get(f"/tools/{slug}", follow_redirects=False)
    assert r.status_code == 200
    assert "x-robots-tag" not in r.headers
    assert_seo_head(r.text, title=title, path=f"/tools/{slug}", h1=h1, description=desc)
    assert _meta(r.text, "property", "og:image") == f"{BASE}/og/{slug}.png"
    assert seo_anon.head(f"/tools/{slug}", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("slug", IDS)
def test_tool_explainer_is_300_to_500_words_with_required_headings(seo_anon, slug) -> None:
    html = seo_anon.get(f"/tools/{slug}").text
    section = _section(html, "explainer")
    assert 300 <= _words(section) <= 500, _words(section)
    heads = [htmllib.unescape(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", section)]
    assert heads[0].startswith("What is")
    assert heads[1] == "How it's calculated"
    assert heads[2] == "How to read the result"


@pytest.mark.parametrize("slug", IDS)
def test_tool_faq_jsonld_breadcrumb_and_software_application(seo_anon, slug) -> None:
    html = seo_anon.get(f"/tools/{slug}").text
    docs = _jsonld(html)
    by_type = {d["@type"]: d for d in docs}
    assert set(by_type) == {"SoftwareApplication", "BreadcrumbList", "FAQPage"}
    app = by_type["SoftwareApplication"]
    assert app["name"] == "GESH Financial" and app["operatingSystem"] == "Web"
    assert app["offers"][0]["price"] == "0"
    crumbs = by_type["BreadcrumbList"]["itemListElement"]
    assert [c["position"] for c in crumbs] == [1, 2, 3]
    assert crumbs[0]["item"] == BASE + "/" and crumbs[-1]["item"] == f"{BASE}/tools/{slug}"
    qs = [q["name"] for q in by_type["FAQPage"]["mainEntity"]]
    assert 4 <= len(qs) <= 6
    visible = htmllib.unescape(re.sub(r"<[^>]+>", " ", html))
    assert all(q in visible for q in qs)
    blob = json.dumps(docs).lower()
    assert "aggregaterating" not in blob and '"review"' not in blob


@pytest.mark.parametrize("slug", IDS)
def test_tool_page_has_label_related_links_cta_and_no_network_code(seo_anon, slug) -> None:
    html = seo_anon.get(f"/tools/{slug}").text
    assert "Educational illustration — not investment advice" in html
    assert all(f'href="/tools/{o}"' in html for o in IDS if o != slug)
    assert 'href="/pricing"' in html and 'href="/login"' in html
    assert "<noscript>" in html
    script = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert "GeshTool" in script
    for banned in (
        "fetch(",
        "XMLHttpRequest",
        "sendBeacon",
        "innerHTML",
        "eval(",
        "document.write",
    ):
        assert banned not in script, banned
    assert not re.search(r"<script[^>]+src=", html)


@pytest.mark.parametrize("slug", IDS)
def test_inputs_param_is_never_reflected_and_canonical_stays_clean(seo_anon, slug) -> None:
    payload = "<script>alert(1)</script>"
    r = seo_anon.get(f"/tools/{slug}", params={"inputs": payload})
    assert r.status_code == 200
    assert payload not in r.text and "alert(1)" not in r.text
    assert re.search(rf'<link rel="canonical" href="{re.escape(BASE)}/tools/{slug}">', r.text)


def test_tools_index_lists_all_three(seo_anon) -> None:
    r = seo_anon.get("/tools", follow_redirects=False)
    assert r.status_code == 200
    assert_seo_head(
        r.text,
        title="Free Portfolio Tools: X-Ray, Monte Carlo & Kelly Calculator | GESH Financial",
        path="/tools",
        h1="Free Portfolio Tools",
    )
    assert all(f'href="/tools/{s}"' in r.text for s in IDS)


def test_tool_paths_are_exact_public_paths_and_neighbours_stay_gated(seo_anon) -> None:
    for p in ["/tools", *(f"/tools/{s}" for s in IDS)]:
        assert auth_users.is_public_path(p) is True, p
    for p in [
        "/tools/",
        "/tools/other",
        "/tools/kelly-calculator/x",
        "/toolsx",
        "/tools/kelly-calculators",
    ]:
        assert auth_users.is_public_path(p) is False, p
    r = seo_anon.get("/tools/other", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_sitemap_lists_tools(seo_anon) -> None:
    locs = [e[0] for e in _sitemap(seo_anon)]
    for p in ["/tools", *(f"/tools/{s}" for s in IDS)]:
        assert BASE + p in locs


def test_landing_and_nav_link_to_tools(seo_anon) -> None:
    html = seo_anon.get("/").text
    assert 'href="/tools"' in html and all(f'href="/tools/{s}"' in html for s in IDS)


# ── client-side calculators, executed for real in Node ──────────────────────────

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _js(seo_anon, slug: str, expr: str):
    html = seo_anon.get(f"/tools/{slug}").text
    script = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    # json.dumps-ed twice so arbitrary script text survives node -e quoting
    code = (
        "const vm=require('vm');const c=vm.createContext({});"
        f"vm.runInContext({json.dumps(script)},c);"
        f"console.log(JSON.stringify(vm.runInContext({json.dumps(expr)},c)));"
    )
    out = subprocess.run(
        ["node", "-e", code], capture_output=True, text=True, timeout=60, check=True
    )  # noqa: S603,S607
    return json.loads(out.stdout)


@node
def test_kelly_math(seo_anon) -> None:
    r = _js(seo_anon, "kelly-calculator", "GeshTool.kelly(55, 1)")
    assert r["full"] == pytest.approx(0.10) and r["half"] == pytest.approx(0.05)
    assert r["quarter"] == pytest.approx(0.025) and r["hasEdge"] is True
    assert _js(seo_anon, "kelly-calculator", "GeshTool.kelly(60, 2)")["full"] == pytest.approx(0.4)
    none = _js(seo_anon, "kelly-calculator", "GeshTool.kelly(40, 1)")
    assert none["full"] == 0 and none["half"] == 0 and none["hasEdge"] is False
    for bad in (
        "GeshTool.kelly(0, 1)",
        "GeshTool.kelly(100, 1)",
        "GeshTool.kelly(55, 0)",
        "GeshTool.kelly(NaN, 1)",
    ):
        assert "error" in _js(seo_anon, "kelly-calculator", bad), bad


@node
def test_monte_carlo_is_deterministic_ordered_and_matches_theory(seo_anon) -> None:
    slug = "monte-carlo-simulator"
    expr = "GeshTool.simulate({mu:0,sigma:20,value:100000,days:21})"
    a, b = _js(seo_anon, slug, expr), _js(seo_anon, slug, expr)
    assert a == b and a["paths"] == 1000 and sum(a["bins"]) == 1000
    assert a["p5"] < a["p50"] < a["p95"]
    # analytic 5th percentile of 21d GBM at 20% vol is about -9.2%; 1,000 paths => wide band
    assert -0.115 < a["worstPct"] < -0.07
    flat = _js(seo_anon, slug, "GeshTool.simulate({mu:12,sigma:0,value:50000,days:21})")
    assert flat["p5"] == pytest.approx(50000 * 2.718281828459045 ** (0.12 * 21 / 252))
    hi = _js(seo_anon, slug, "GeshTool.simulate({mu:0,sigma:40,value:100000,days:21})")
    assert hi["worstPct"] < a["worstPct"]
    for bad in (
        "{mu:0,sigma:-1,value:1,days:21}",
        "{mu:0,sigma:20,value:0,days:21}",
        "{mu:0,sigma:20,value:1,days:0}",
        "{mu:0,sigma:20,value:1,days:999}",
        "{mu:NaN,sigma:20,value:1,days:21}",
    ):
        assert "error" in _js(seo_anon, slug, f"GeshTool.simulate({bad})"), bad


@node
def test_xray_parsing_and_analysis(seo_anon) -> None:
    slug = "portfolio-xray"
    p = _js(
        seo_anon,
        slug,
        "GeshTool.parsePositions('aapl 30\\nMSFT, 30\\nspy: 40%\\nAAPL 10\\nbad\\n$GOOGL 5')",
    )
    assert p["positions"] == [["AAPL", 40], ["MSFT", 30], ["SPY", 40], ["GOOGL", 5]]
    assert p["skipped"] == ["bad"]
    a = _js(seo_anon, slug, "GeshTool.analyze([['AAPL',50],['MSFT',50]])")
    assert a["sectors"] == [["Technology", 1]] and a["score"] == 50
    assert any("single position above 20%" in f for f in a["flags"])
    assert any("single sector above 40%" in f for f in a["flags"])
    big = _js(
        seo_anon,
        slug,
        "GeshTool.analyze([['AAA',1],['BBB',1],['CCC',1],['DDD',1],['EEE',1],['FFF',1],['GGG',1],['HHH',1],['III',1],['JJJ',1]])",
    )
    assert big["score"] == 90 and big["effective"] == pytest.approx(10)
    assert big["sectors"] == [["Unclassified", pytest.approx(1)]]
    assert len(big["top5"]) == 5
    assert any("unclassified" in f for f in big["flags"])
    funds = _js(seo_anon, slug, "GeshTool.analyze([['SPY',50],['VOO',50]])")
    assert any("S&P 500" in f for f in funds["flags"])
    mixed = _js(seo_anon, slug, "GeshTool.analyze([['SPY',60],['AAPL',40]])")
    assert any("also sit inside SPY" in f for f in mixed["flags"])
    broad = _js(seo_anon, slug, "GeshTool.analyze([['SPY',60],['XOM',40]])")
    assert not any(
        f.startswith("SPY is") for f in broad["flags"]
    )  # a broad fund is not a single-name bet
    assert any(f.startswith("XOM is 40%") for f in broad["flags"])
    assert "error" in _js(seo_anon, slug, "GeshTool.analyze([])")
    scaled = _js(seo_anon, slug, "GeshTool.analyze([['AAPL',2000],['XOM',2000]])")
    assert [r["w"] for r in scaled["rows"]] == [0.5, 0.5]
    # null-prototype maps: hostile tickers cannot reach Object.prototype
    evil = _js(seo_anon, slug, "GeshTool.analyze([['CONSTRUCTOR',1],['HASOWNPROPERTY',1]])")
    assert evil["sectors"] == [["Unclassified", 1]]


@node
def test_share_param_parser_is_prototype_safe(seo_anon) -> None:
    out = _js(
        seo_anon,
        "kelly-calculator",
        "(function(){var o=GeshTool.parseInputs('p:55,__proto__:x,b:2,b:9,junk');"
        "return [o.p,o.b,Object.keys(o).sort(),({}).x===undefined]})()",
    )
    assert out[0] == "55" and out[1] == "2" and out[3] is True
    assert out[2] == ["__proto__", "b", "p"] or out[2] == ["b", "p"]


def test_sector_map_is_small_static_and_unambiguous() -> None:
    assert 80 <= len(seo_tools_data.SECTORS) <= 400
    assert seo_tools_data.SECTORS["AAPL"] == "Technology"
    assert seo_tools_data.SECTORS["SPY"] == "Diversified funds"
    assert "ZZZZ" not in seo_tools_data.SECTORS


def test_layout_tool_links_match_registered_tools() -> None:
    import seo_layout
    import seo_tools

    assert [p for p, _ in seo_layout.TOOL_LINKS] == [t.path for t in seo_tools.TOOLS]
    assert set(seo_tools.PUBLIC_PATHS) <= set(auth_users.PUBLIC_EXACT_PATHS)
