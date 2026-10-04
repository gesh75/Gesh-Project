"""Free public tools: Portfolio X-Ray, Monte Carlo simulator, Kelly calculator.

Server-side rendered shell + explainer + FAQ; calculations run client-side in the
visitor's browser (see seo_tools_js). These routes never read owner holdings, cash,
broker, LLM or billing state, and never echo query parameters into the page.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import seo_layout as L
import seo_pages
import seo_tools_js

router = APIRouter()
LABEL = "Educational illustration — not investment advice"


@dataclass(frozen=True)
class Tool:
    slug: str
    title: str
    description: str
    h1: str
    intro: str
    explainer: tuple[tuple[str, tuple[str, ...]], ...]
    faq: tuple[tuple[str, str], ...]
    form: str
    script: str
    noscript: str

    @property
    def path(self) -> str:
        return f"/tools/{self.slug}"


XRAY_FORM = (
    '<label for="x-in">One position per line: ticker, then weight or amount</label>'
    '<textarea id="x-in" name="positions" spellcheck="false" '
    'placeholder="AAPL 30&#10;MSFT 25&#10;SPY 45">AAPL 30\nMSFT 25\nSPY 45</textarea>'
    '<p><button class="btn" type="submit">X-ray my portfolio</button></p>'
)
MC_FORM = (
    '<div class="grid">'
    '<div><label for="m-mu">Expected annual return (%)</label>'
    '<input id="m-mu" name="mu" inputmode="decimal" value="8"></div>'
    '<div><label for="m-sigma">Annual volatility (%)</label>'
    '<input id="m-sigma" name="sigma" inputmode="decimal" value="18"></div>'
    '<div><label for="m-value">Portfolio value ($)</label>'
    '<input id="m-value" name="value" inputmode="decimal" value="100000"></div>'
    '<div><label for="m-days">Horizon (trading days)</label>'
    '<input id="m-days" name="days" inputmode="numeric" value="21"></div></div>'
    '<p><button class="btn" type="submit">Run 1,000 simulations</button></p>'
)
KELLY_FORM = (
    '<div class="grid">'
    '<div><label for="k-p">Win probability (%)</label>'
    '<input id="k-p" name="p" inputmode="decimal" value="55"></div>'
    '<div><label for="k-b">Payoff odds (win per $1 risked)</label>'
    '<input id="k-b" name="b" inputmode="decimal" value="1"></div>'
    '<div><label for="k-bank">Bankroll in $ (optional)</label>'
    '<input id="k-bank" name="bank" inputmode="decimal" placeholder="10000"></div></div>'
    '<p><button class="btn" type="submit">Calculate</button></p>'
)

TOOLS: tuple[Tool, ...] = (
    Tool(
        slug="portfolio-xray",
        title="Free Portfolio X-Ray: See Your Hidden Concentrations | GESH Financial",
        description=(
            "Paste your tickers for a free portfolio X-ray: sector breakdown, concentration "
            "flags, and diversification score. No signup needed."
        ),
        h1="Free Portfolio X-Ray",
        intro="Paste your tickers and weights to see sector mix, your biggest positions and overlap.",
        explainer=(
            (
                "What is a portfolio X-ray?",
                (
                    "A portfolio X-ray looks through the tickers you hold and shows what the book "
                    "is really made of. Instead of a list of symbols, you see how much sits in "
                    "each sector, which few positions dominate, and where two holdings quietly "
                    "overlap. It is the fastest way to spot a hidden concentration, such as a "
                    "portfolio of ten different stocks that is seventy percent technology.",
                    "It works on any list of tickers: a handful of holdings, a retirement-account "
                    "mix, or a model portfolio you are still considering.",
                ),
            ),
            (
                "How it's calculated",
                (
                    "Paste one position per line as a ticker and a weight or amount. The tool "
                    "converts everything to percentages that add up to 100. Each ticker is "
                    "matched to a sector using a small built-in list of common US stocks and "
                    "ETFs. Anything not on the list is shown as Unclassified rather than guessed.",
                    "Concentration is measured two ways: the five largest positions by weight, "
                    "and a diversification score of 100 × (1 − HHI), where HHI is the sum of the "
                    "squared weights. One holding scores 0 and ten equal holdings score 90. "
                    "Overlap flags appear when you hold several funds that track the same index, "
                    "or a broad index fund plus the mega-cap stocks it is built around. The flags "
                    "show that overlap exists; they do not estimate how large it is.",
                ),
            ),
            (
                "How to read the result",
                (
                    "Start with the sector table. A single sector above 40 percent, or a single "
                    "position above 20 percent, is flagged as worth a second look. Those "
                    "thresholds are rules of thumb, not recommendations. Next check the "
                    "unclassified share: when it is large, the sector table understates the "
                    "picture. Finally read the overlap notes. Two S&P 500 funds are close to one "
                    "position counted twice. None of this says what to buy or sell; it shows "
                    "what you already own. The calculation runs in your browser and nothing you "
                    "paste is sent to a server.",
                ),
            ),
        ),
        faq=(
            (
                "How do I enter my portfolio?",
                "Type one position per line: a ticker followed by a weight, a percentage or a "
                "dollar amount, such as AAPL 30 or MSFT $12,000. Weights are rescaled to 100%.",
            ),
            (
                "Why is a ticker shown as Unclassified?",
                "The sector list is small and static. Tickers that are not on it are labelled "
                "Unclassified instead of being guessed, and the tool flags when that share is large.",
            ),
            (
                "Is my portfolio stored or sent anywhere?",
                "No. The calculation runs in your browser. The share link stores your inputs in "
                "the web address only if you choose to copy it.",
            ),
            (
                "What is the diversification score?",
                "It is 100 × (1 − HHI), where HHI is the sum of squared position weights. It "
                "measures how evenly the weights are spread, not how risky the holdings are.",
            ),
            (
                "Is this investment advice?",
                "No. It is an educational illustration of the data you enter, not a "
                "recommendation to buy, sell or hold anything.",
            ),
        ),
        form=XRAY_FORM,
        script=seo_tools_js.xray_script(),
        noscript="The X-ray needs JavaScript to run in your browser.",
    ),
    Tool(
        slug="monte-carlo-simulator",
        title="Free Monte Carlo Portfolio Simulator | GESH Financial",
        description=(
            "Simulate 1,000 possible paths for your portfolio and see the worst plausible "
            "month — free, no signup. Educational illustration, not a prediction."
        ),
        h1="Monte Carlo Portfolio Simulator",
        intro="Simulate 1,000 possible paths and see the worst plausible stretch for your portfolio.",
        explainer=(
            (
                "What is a Monte Carlo portfolio simulation?",
                (
                    "A Monte Carlo simulation answers a simple question: if markets keep behaving "
                    "roughly the way your assumptions say, how widely could this portfolio's "
                    "value spread over the next few weeks? Instead of predicting one outcome, it "
                    "generates many possible paths, here 1,000, and looks at the whole "
                    "distribution, including the unlucky end.",
                    "Risk teams use the same idea because a single average return hides the "
                    "spread that decides whether a drawdown is tolerable. Looking at 1,000 paths "
                    "makes that spread visible, so you can ask whether you could realistically "
                    "hold through the bad ones before you ever face them.",
                ),
            ),
            (
                "How it's calculated",
                (
                    "You enter an expected annual return, an annual volatility, a portfolio "
                    "value and a horizon in trading days. The default is 21 days, about one "
                    "month. Each simulated day draws a random shock from a normal distribution "
                    "scaled to daily volatility, which is annual volatility divided by the square "
                    "root of 252, plus a drift equal to the expected return minus half the "
                    "variance. Compounding those days gives one path's ending value, and "
                    "repeating the process 1,000 times gives the distribution.",
                    "The random numbers are seeded from your inputs, so the same inputs always "
                    "produce the same chart and a shared link shows exactly what you saw. The "
                    "tool reports the 5th percentile, the level that 95 of 100 simulated paths "
                    "finished above, as the worst plausible stretch, together with the median and "
                    "the 95th percentile.",
                ),
            ),
            (
                "How to read the result",
                (
                    "The histogram shows where the 1,000 endings landed. The left tail is the "
                    "part to study. A 5th-percentile result of minus nine percent means that, "
                    "under your assumptions, roughly one path in twenty lost at least that much. "
                    "It does not mean losses cannot be larger. Real returns have fatter tails and "
                    "volatility that clusters, which a simple normal model understates. Treat the "
                    "answer as a sensitivity check: raise the volatility and watch the tail "
                    "widen. It is an educational illustration, not a prediction or a guarantee.",
                ),
            ),
        ),
        faq=(
            (
                "What does the 5th percentile mean?",
                "Of the 1,000 simulated paths, 50 ended at or below that value. It is a "
                "plausible bad outcome under your assumptions, not a floor.",
            ),
            (
                "What volatility should I use?",
                "Use an annualised standard deviation of returns for your portfolio. Broad US "
                "equity indexes have often sat in the high teens, but your own holdings may differ.",
            ),
            (
                "Why do I get the same chart every time?",
                "The random draws are seeded from your inputs so results are reproducible and "
                "shareable. Change any input and the paths change.",
            ),
            (
                "Does this predict my returns?",
                "No. It propagates the assumptions you enter through a simple model. If the "
                "assumptions are wrong the output is wrong.",
            ),
            (
                "Is this investment advice?",
                "No. It is an educational illustration, not a forecast or a recommendation.",
            ),
        ),
        form=MC_FORM,
        script=seo_tools_js.monte_carlo_script(),
        noscript="The simulator needs JavaScript to run in your browser.",
    ),
    Tool(
        slug="kelly-calculator",
        title="Kelly Criterion Position Size Calculator | GESH Financial",
        description=(
            "Size any position with the Kelly criterion — full, half, and quarter Kelly "
            "compared. Free calculator, no signup."
        ),
        h1="Kelly Position Size Calculator",
        intro="Enter a win probability and payoff to compare full, half and quarter Kelly sizing.",
        explainer=(
            (
                "What is the Kelly criterion?",
                (
                    "The Kelly criterion is a formula for the fraction of a bankroll to risk on "
                    "a bet or trade when you have an estimated edge. Developed by John Kelly at "
                    "Bell Labs in 1956, it picks the size that maximises the long-run growth rate "
                    "of capital. Risk too little and you leave growth on the table; risk too much "
                    "and volatility erodes it.",
                    "It appears in gambling theory, quantitative investing and position-sizing "
                    "discussions because it ties size to two numbers you can state: how often you "
                    "expect to win, and how much you win relative to what you lose. The answer "
                    "is a fraction of capital rather than a dollar figure, so it scales with "
                    "whatever bankroll you have.",
                ),
            ),
            (
                "How it's calculated",
                (
                    "Enter your estimated probability of winning, called p, and the payoff odds, "
                    "called b: how much you win for each dollar risked. Full Kelly is "
                    "f* = p − (1 − p) / b. With a 55 percent chance of winning and a 1 to 1 "
                    "payoff, f* = 0.55 − 0.45 = 10 percent of the bankroll.",
                    "Half Kelly and quarter Kelly scale that number by 0.5 and 0.25. If the "
                    "result is zero or negative the formula sees no positive edge and the "
                    "suggested size is none. You can add a bankroll to see the same fractions as "
                    "dollar amounts.",
                ),
            ),
            (
                "How to read the result",
                (
                    "Full Kelly assumes your probability and payoff estimates are exact, and they "
                    "never are. Estimation error pushes you toward over-betting, which is why many "
                    "practitioners prefer half or quarter Kelly: they give up some theoretical "
                    "growth in exchange for much smaller drawdowns. Treat the full Kelly figure as "
                    "an upper bound, not a target. Sensitivity matters too. Change p by two points "
                    "and watch how quickly the answer moves. This is arithmetic on numbers you "
                    "supply, not a recommendation to take any position.",
                ),
            ),
        ),
        faq=(
            (
                "What is a payoff of 1 versus 2?",
                "A payoff of 1 means you win $1 for every $1 risked. A payoff of 2 means you win "
                "$2 for every $1 risked. Losses are assumed to be the full amount risked.",
            ),
            (
                "Why use half or quarter Kelly?",
                "Because the inputs are estimates. Smaller fractions reduce drawdowns and the "
                "damage from an overestimated edge.",
            ),
            (
                "What if the result is 0%?",
                "The formula found no positive edge at those inputs, so it suggests risking "
                "nothing on that bet.",
            ),
            (
                "Where do the win probability and payoff come from?",
                "From you. The calculator does not look anything up; a poor estimate gives a "
                "poor answer.",
            ),
            (
                "Is this investment advice?",
                "No. It is an educational calculator, not a recommendation to take any position.",
            ),
        ),
        form=KELLY_FORM,
        script=seo_tools_js.kelly_script(),
        noscript="The calculator needs JavaScript to run in your browser.",
    ),
)
BY_SLUG = {t.slug: t for t in TOOLS}

INDEX_PATH = "/tools"
INDEX_TITLE = "Free Portfolio Tools: X-Ray, Monte Carlo & Kelly Calculator | GESH Financial"
INDEX_DESCRIPTION = (
    "Free portfolio tools with no signup: a portfolio X-ray, a Monte Carlo simulator and a "
    "Kelly position-size calculator. Educational, not advice."
)
PUBLIC_PATHS: tuple[str, ...] = (INDEX_PATH, *(t.path for t in TOOLS))


def _explainer(tool: Tool) -> str:
    parts = []
    for heading, paragraphs in tool.explainer:
        body = "".join(f"<p>{L.esc(p)}</p>" for p in paragraphs)
        parts.append(f"<h2>{L.esc(heading)}</h2>{body}")
    return '<section id="explainer">' + "".join(parts) + "</section>"


def _related(tool: Tool) -> str:
    links = "".join(
        f'<li><a href="{t.path}">{L.esc(t.h1)}</a></li>' for t in TOOLS if t.slug != tool.slug
    )
    return (
        '<section id="related"><h2>Related tools</h2><ul>'
        f'{links}<li><a href="/tools">All free tools</a></li>'
        '<li><a href="/track-record">Public track record</a></li></ul></section>'
    )


def _cta() -> str:
    return (
        '<section id="cta" class="card"><h2>Save to GESH / unlock the full dashboard</h2>'
        "<p>Track your real positions, risk and sizing in one daily report.</p>"
        '<p><a class="btn" href="/pricing">See plans</a> '
        '<a class="btn alt" href="/login">Sign in</a></p></section>'
    )


def render_tool(tool: Tool) -> str:
    body = (
        f"<h1>{L.esc(tool.h1)}</h1>"
        f'<p class="sub">{L.esc(tool.intro)}</p>'
        f'<div class="note"><b>{L.esc(LABEL)}.</b> Hypothetical or simulated results do not '
        "guarantee future results.</div>"
        '<section id="tool" class="card"><form id="tool-form" novalidate>'
        f"{tool.form}</form>"
        '<p><button class="btn alt" id="share-btn" type="button">Copy share link</button> '
        '<span class="muted" id="share-status" role="status"></span></p>'
        '<div id="tool-out" aria-live="polite"></div>'
        f'<noscript><p class="err">{L.esc(tool.noscript)}</p></noscript></section>'
        + _explainer(tool)
        + L.faq_html(list(tool.faq))
        + _related(tool)
        + _cta()
    )
    head = seo_pages.head_tags(
        title=tool.title,
        description=tool.description,
        path=tool.path,
        og_image=tool.slug,
        jsonld=[
            seo_pages.software_application_ld([L.free_offer()]),
            L.breadcrumb_ld([("Home", "/"), ("Free tools", INDEX_PATH), (tool.h1, tool.path)]),
            L.faq_ld(list(tool.faq)),
        ],
    )
    return L.page(head, body, extra_head="").replace(
        "</body>", f"<script>{tool.script}</script></body>", 1
    )


def render_index() -> str:
    cards = "".join(
        f'<div class="card"><h3><a href="{t.path}">{L.esc(t.h1)}</a></h3>'
        f"<p>{L.esc(t.intro)}</p></div>"
        for t in TOOLS
    )
    body = (
        "<h1>Free Portfolio Tools</h1>"
        '<p class="sub">Three calculators that run in your browser. No signup, no data sent.</p>'
        f'<div class="note"><b>{L.esc(LABEL)}.</b></div>'
        f'<div class="grid">{cards}</div>' + _cta()
    )
    head = seo_pages.head_tags(
        title=INDEX_TITLE,
        description=INDEX_DESCRIPTION,
        path=INDEX_PATH,
        jsonld=[
            L.breadcrumb_ld([("Home", "/"), ("Free tools", INDEX_PATH)]),
        ],
    )
    return L.page(head, body)


@router.api_route(INDEX_PATH, methods=["GET", "HEAD"], include_in_schema=False)
def tools_index() -> HTMLResponse:
    return HTMLResponse(render_index())


def _register(tool: Tool) -> None:
    def view() -> HTMLResponse:
        return HTMLResponse(render_tool(tool))

    router.add_api_route(tool.path, view, methods=["GET", "HEAD"], include_in_schema=False)


for _tool in TOOLS:
    _register(_tool)
