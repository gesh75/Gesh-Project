#!/usr/bin/env python3
"""Regenerate the static 1200x630 Open Graph images in static/og/.

Offline, stdlib-only (macOS: headless Chrome + sips): renders small HTML cards. Run it by hand
when copy changes, then commit the PNGs (the server only ever serves the committed
files, so production needs neither Chrome nor Pillow).

    python3 scripts/gen_og_images.py            # all images
    CHROME=/path/to/chrome python3 scripts/gen_og_images.py
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "og"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FOOTER = "Hypothetical illustration — not advice"

CARDS: dict[str, tuple[str, str, list[int]]] = {
    "gesh-financial": (
        "See what's in your book.",
        "Portfolio tracking, AI scores & risk analytics",
        [30, 55, 42, 78, 64, 90, 70],
    ),
    "portfolio-xray": (
        "Free Portfolio X-Ray",
        "Sector mix, concentration flags & diversification score",
        [90, 70, 52, 38, 26, 18, 10],
    ),
    "monte-carlo-simulator": (
        "Monte Carlo Portfolio Simulator",
        "1,000 simulated paths · the worst plausible month",
        [6, 14, 28, 50, 82, 100, 82, 50, 28, 14, 6],
    ),
    "kelly-calculator": (
        "Kelly Position Size Calculator",
        "Full, half & quarter Kelly compared",
        [100, 50, 25],
    ),
}


def card_html(headline: str, sub: str, bars: list[int]) -> str:
    n = len(bars)
    gap, width = 14, (440 - 14 * (n - 1)) / n
    rects = "".join(
        f'<rect x="{i * (width + gap):.1f}" y="{240 - b * 2.4:.1f}" width="{width:.1f}" '
        f'height="{b * 2.4:.1f}" rx="6" fill="#00d4ff" opacity="0.85"/>'
        for i, b in enumerate(bars)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1200px;height:630px;overflow:hidden}}
body{{background:#0a121d;color:#e6edf3;
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;position:relative}}
.brand{{position:absolute;left:72px;top:56px;font-size:30px;font-weight:700;color:#00d4ff}}
h1{{position:absolute;left:72px;top:170px;width:620px;margin:0;font-size:68px;line-height:1.1}}
.sub{{position:absolute;left:72px;top:420px;width:600px;font-size:30px;color:#aeb9c7}}
svg{{position:absolute;right:72px;top:190px}}
.foot{{position:absolute;left:72px;bottom:48px;font-size:24px;color:#f0b429}}
</style></head><body><div class="brand">GESH Financial</div>
<h1>{html.escape(headline)}</h1><div class="sub">{html.escape(sub)}</div>
<svg width="440" height="250" viewBox="0 0 440 250">{rects}</svg>
<div class="foot">{html.escape(FOOTER)}</div></body></html>"""


def render(name: str, spec: tuple[str, str, list[int]]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{name}.png"
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / f"{name}.html"
        page.write_text(card_html(*spec), encoding="utf-8")
        subprocess.run(  # noqa: S603  (fixed argv, local file, no shell)
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                # headless Chrome reserves 87px of the window for (invisible) browser UI, so the
                # viewport is 630px tall only with a 717px window; the strip is cropped below.
                "--window-size=1200,717",
                f"--screenshot={target}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(  # noqa: S603,S607  (macOS sips: crop the top 1200x630, offset 0,0)
            ["sips", "--cropToHeightWidth", "630", "1200", "--cropOffset", "0", "0", str(target)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    return target


def main() -> int:
    if not Path(CHROME).exists():
        print(f"Chrome not found at {CHROME}; set CHROME=...", file=sys.stderr)
        return 1
    for name, spec in CARDS.items():
        print("wrote", render(name, spec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
