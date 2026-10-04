"""Public marketing landing for logged-out `/`.

All figures on this page are STATIC and ILLUSTRATIVE (labelled as such). It never
reads holdings, cash, account, broker, LLM or billing state.
"""

from __future__ import annotations

import seo_layout as L
import seo_pages

TITLE = "GESH Financial | Portfolio Tracking, AI Scores & Risk Analytics"
DESCRIPTION = (
    "Track positions, AI scores, Monte Carlo risk and Kelly sizing in one daily portfolio "
    "report. Educational tool for self-directed investors — not advice."
)
H1 = "See what's in your book."
SUBHEAD = (
    "GESH Financial is portfolio observability: positions, risk, and sizing measured every "
    "day — educational model output, not advice."
)
FEATURES = "Daily portfolio report, AI scores, Monte Carlo risk simulation, Kelly position sizing"

FAQ: list[tuple[str, str]] = [
    (
        "Is this financial advice?",
        "No. GESH Financial is an educational research and observability tool. It is not a "
        "registered investment adviser, gives no personalized investment advice and owes no "
        "fiduciary duty. Every decision stays with you.",
    ),
    (
        "Does GESH trade or hold my money?",
        "No. GESH does not manage money, hold funds or place trades for customers. It measures "
        "the positions you tell it about and reports what it finds.",
    ),
    (
        "What does the daily report measure?",
        "Your positions, sector mix, diversification, a Monte Carlo risk read, Kelly-style "
        "sizing context and AI-generated scores. Where a quote or input is missing the report "
        "says it is not available instead of guessing.",
    ),
    (
        "Are the track-record results real trading?",
        "No. The public track record shows hypothetical, simulated paper trades, including "
        "losers, with methodology and limitations disclosed. Past performance, including "
        "simulated performance, does not guarantee future results.",
    ),
    (
        "Can AI scores be wrong?",
        "Yes. Scores are model output built from public data and can be incomplete, late or "
        "mistaken. Treat them as one input to your own research, never as an instruction.",
    ),
    (
        "How much does it cost?",
        "See the pricing page for the current plan. Browsing the track record and the public "
        "pages needs no account.",
    ),
]


def _card(tag: str, heading: str, body: str) -> str:
    return (
        f'<div class="card"><span class="tag">{L.esc(tag)}</span>'
        f"<h3>{L.esc(heading)}</h3>{body}</div>"
    )


def _how_it_works() -> str:
    return (
        '<section aria-labelledby="how"><h2 id="how">How it works</h2>'
        '<ol class="steps">'
        "<li><b>Add your positions.</b> Enter the tickers and share counts you want measured "
        "in My Book.</li>"
        "<li><b>Get the daily read.</b> GESH measures exposure, risk and sizing against public "
        "market data and writes one report.</li>"
        "<li><b>Review and decide.</b> The output is educational. You decide what, if "
        "anything, to do.</li></ol></section>"
    )


def _live_look() -> str:
    verdicts = _card(
        "Illustrative",
        "Book verdicts",
        '<p class="num">Example A &nbsp;·&nbsp; within target weight</p>'
        '<p class="num">Example B &nbsp;·&nbsp; overweight vs. cap</p>'
        '<p class="num">Example C &nbsp;·&nbsp; unpriced — not scored</p>'
        '<p class="muted">Sample labels on a made-up book, not a real portfolio.</p>',
    )
    mc = _card(
        "Illustrative",
        "Monte Carlo risk card",
        '<p class="num">1,000 simulated paths · 21 trading days</p>'
        '<p class="num">5th-percentile outcome: −8.4% (example)</p>'
        '<p class="muted">Sample numbers showing the format. A simulation is not a forecast.</p>',
    )
    kelly = _card(
        "Illustrative",
        "Kelly sizing",
        '<p class="num">Win probability 55% · payoff 1:1</p>'
        '<p class="num">Full 10.0% · Half 5.0% · Quarter 2.5%</p>'
        '<p class="muted">Worked example of the formula, not a recommendation.</p>',
    )
    return (
        '<section aria-labelledby="look"><h2 id="look">A live look at the report</h2>'
        '<p class="muted">Illustrative examples with static sample data — never real '
        "holdings.</p>"
        f'<div class="grid">{verdicts}{mc}{kelly}</div></section>'
    )


def _measures() -> str:
    items = [
        ("Positions table", "Every holding with weight, cost basis context and data freshness."),
        ("Sector mix", "How much of the book sits in each sector, so a hidden tilt is visible."),
        (
            "Diversification radar",
            "Groups of related exposure compared against one another, not a single score.",
        ),
        (
            "Funding swaps",
            "When an idea needs cash, which existing position would shrink the book's "
            "concentration least.",
        ),
    ]
    cards = "".join(
        f'<div class="card"><h3>{L.esc(n)}</h3><p>{L.esc(d)}</p></div>' for n, d in items
    )
    return (
        '<section aria-labelledby="measures"><h2 id="measures">What the report measures</h2>'
        f'<div class="grid">{cards}</div></section>'
    )


def _proof_and_pricing() -> str:
    return (
        '<section aria-labelledby="proof"><h2 id="proof">Show the losers too</h2>'
        '<p>The public <a href="/track-record">track record</a> lists logged closes, '
        "expectancy and profit factor for hypothetical, simulated paper trades — "
        "methodology and limitations included. It is not live trading, and past "
        "performance does not guarantee future results.</p>"
        '<p><a class="btn alt" href="/track-record">Read the track record</a></p></section>'
        '<section aria-labelledby="start"><h2 id="start">Start measuring your book</h2>'
        "<p>Compare the plan and start with the daily report.</p>"
        '<p><a class="btn" href="/pricing">See pricing</a> '
        '<a class="btn alt" href="/login">Sign in</a></p></section>'
    )


def render_landing() -> str:
    body = (
        f'<h1>{L.esc(H1)}</h1><p class="sub">{L.esc(SUBHEAD)}</p>'
        f'<p class="muted">{L.esc(FEATURES)}</p>'
        '<p><a class="btn" href="/pricing">Get started</a> '
        '<a class="btn alt" href="/track-record">See the track record</a></p>'
        f'<div class="note"><b>Disclaimer.</b> {L.esc(L.DISCLAIMER)}</div>'
        + _how_it_works()
        + _live_look()
        + _measures()
        + _proof_and_pricing()
        + L.faq_html(FAQ)
    )
    head = seo_pages.head_tags(
        title=TITLE,
        description=DESCRIPTION,
        path="/",
        jsonld=[
            seo_pages.software_application_ld([L.free_offer()]),
            L.faq_ld(FAQ),
        ],
    )
    return L.page(head, body)
