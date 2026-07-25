"""Real Chromium proof that the customer runtime emits no owner-domain traffic."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

import server

FORBIDDEN_PATHS = (
    "/api/portfolio",
    "/api/trades",
    "/api/trades/stats",
    "/api/ecg",
    "/api/regime",
    "/api/portfolio/history",
    "/api/sections/status",
)
_CHART_TAG = (
    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js" '
    'integrity="sha384-e6nUZLBkQ86NJ6TVVKAeSaK8jWa3NhkYWZFomE39AvDbQWeie9PlQqM3pmYW5d1g" '
    'crossorigin="anonymous"></script>'
)
_CHART_STUB = (
    "<script>window.Chart=class Chart{constructor(){this.data={datasets:[]};}"
    "destroy(){} update(){}};</script>"
)


def _customer_html() -> str:
    html = server._render_dashboard("customer")
    assert _CHART_TAG in html
    html = html.replace(_CHART_TAG, _CHART_STUB, 1)
    html = html.replace(
        "<head>",
        '<head><base href="https://gesh.test/">',
        1,
    )
    html = html.replace(
        "<body",
        '<body data-initial-role="customer"',
        1,
    )
    return html.replace("{{ model }}", "test-model")


def _json_response(route: Route, data: dict | list) -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(data),
    )


def test_customer_browser_emits_zero_owner_requests_for_two_refresh_intervals():
    requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    def handle(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        requests.append(path)

        if parsed.netloc in {"fonts.googleapis.com", "fonts.gstatic.com"}:
            route.fulfill(status=200, content_type="text/css", body="")
            return
        if path == "/api/me":
            _json_response(route, {"role": "customer"})
            return
        if path == "/api/watchlist":
            _json_response(route, {"status": "ok", "data": ["NVDA"]})
            return
        if path == "/api/mybook":
            _json_response(
                route,
                {
                    "status": "ok",
                    "data": {
                        "positions": [],
                        "unpriced": [],
                        "signals": {},
                        "kelly_deltas": {},
                        "risky_symbols": [],
                    },
                },
            )
            return
        if path == "/api/mybook/summary":
            _json_response(
                route,
                {
                    "status": "ok",
                    "data": {
                        "positions_count": 0,
                        "watch_count": 1,
                        "top_changes": [],
                        "last_verdict": None,
                        "next_action": {
                            "target": "book",
                            "label": "Add your first position",
                        },
                    },
                },
            )
            return
        if path == "/api/preferences":
            _json_response(
                route,
                {
                    "status": "ok",
                    "data": {
                        "alerts_opt_out": False,
                        "digest_opt_out": False,
                    },
                },
            )
            return

        # Owner-domain requests are deliberately fulfilled with a sentinel.  The
        # assertion below still fails if one occurs, while the page can continue
        # long enough to expose retries and both scheduled refresh intervals.
        if path in FORBIDDEN_PATHS:
            _json_response(
                route,
                {
                    "status": "ok",
                    "data": {
                        "source": "robinhood_mcp",
                        "holdings": [],
                        "equity_summary": {},
                    },
                },
            )
            return
        _json_response(route, {"status": "ok", "data": {}})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", handle)
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.set_content(_customer_html(), wait_until="load")
        page.wait_for_timeout(65_000)

        assert page.locator("#mybook-nav").is_visible()
        assert page.locator("#tab-home").is_visible()
        page.keyboard.press("Control+k")
        palette_text = page.locator("#cmdk-list").inner_text()
        assert "Go to Home" in palette_text
        assert "Go to Analyze" in palette_text
        assert "Overview" not in palette_text
        assert "Risk" not in palette_text
        assert "Paper Trades" not in palette_text
        browser.close()

    forbidden = [
        path
        for path in requests
        if path in FORBIDDEN_PATHS
        or path.startswith("/api/portfolio?")
        or path.startswith("/api/trades?")
    ]
    assert not forbidden, f"customer browser emitted owner-domain requests: {forbidden}"
    assert "/api/mybook" in requests
    assert "/api/mybook/summary" in requests
    assert not console_errors
    assert not page_errors
