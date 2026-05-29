# Gesh Dashboard Redesign — Design Spec

**Date:** 2026-05-29
**Status:** Draft for review
**Scope:** Full visual + UX redesign of `templates/dashboard.html` across all 9 tabs, single-file vanilla (no build step), served by FastAPI on port 9000.

---

## 1. Goal

Elevate the dark "observability" identity from a flat green-on-black terminal look into a refined, hierarchy-rich dashboard, while keeping the no-build vanilla-JS architecture. Fix the four core problems identified in review:

1. **Hollow first impression** — half the tabs are identical "click to run" placeholders.
2. **Readability** — `21px` monospace on `body` makes prose (Bull/Bear debate) a fixed-width wall.
3. **Weak hierarchy** — tables and cards have uniform weight; color only signals green/red.
4. **Navigation** — emoji tab strip overflows horizontally with no scroll affordance.

## 2. Constraints & Non-Goals

- **Keep:** single file `templates/dashboard.html`, vanilla JS, Chart.js via CDN, FastAPI static-template serving, all existing API contracts.
- **No build step**, no npm, no bundler. Fonts via CDN (`Inter`), icons as inline SVG (Lucide paths) — no icon-font dependency.
- **Non-goals:** rewriting the Python engines, changing data semantics, React migration, mobile-first responsive (keep desktop-primary but avoid horizontal overflow).
- All existing endpoints stay backward-compatible. New endpoints are additive only.

---

## 3. Design Tokens (CSS `:root`)

Replace the current token block. Accent stays **electric cyan**.

```
/* Surfaces */
--bg:#0A0E14;          /* near-black base */
--card:#121821;        /* elevated surface */
--card2:#161D28;       /* nested surface */
--hover:#1A2230;       /* row/hover */
--border:#1E2733;      /* 1px hairline */
--border2:#2A3645;     /* stronger divider */

/* Accent (single signature) */
--accent:#00D4FF;
--accent-dim:rgba(0,212,255,.10);
--accent-glow:0 0 0 1px rgba(0,212,255,.25), 0 0 20px rgba(0,212,255,.12);

/* Semantic — DATA ONLY */
--green:#00E676; --gdim:rgba(0,230,118,.12);
--red:#FF5370;   --rdim:rgba(255,83,112,.12);
--amber:#FFB020; --admdim:rgba(255,176,32,.12);
--violet:#A78BFA; /* reserved for secondary tags */

/* Text */
--text:#E2E8F0;     /* primary prose */
--text-dim:#94A3B8; /* secondary */
--muted:#5B6878;    /* tertiary/labels */

/* Typography */
--font-sans:'Inter','SF Pro Text',system-ui,sans-serif;  /* prose, labels, UI */
--font-mono:'SF Mono','Fira Code','JetBrains Mono',monospace; /* numbers, tickers, tabular */

/* Radii & elevation */
--r-sm:8px; --r-md:12px; --r-lg:16px;
--shadow:0 1px 3px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.25);

/* Motion */
--ease:cubic-bezier(.4,0,.2,1);
--t-fast:120ms; --t-base:180ms;
```

**Typography rule (critical fix):**
- `body` → `--font-sans`, base `15px` (down from 21px), `line-height:1.55`.
- `--font-mono` applied *only* to: `.num`, `.sym`/tickers, all `td`/`th` numeric cells, KPI values, score numbers, code-like config.
- Prose blocks (analysis text, Bull/Bear arguments, descriptions) use sans, `15–16px`, `line-height:1.6`, `max-width:72ch`.

---

## 4. App Shell

### 4.1 Layout
Switch from top tab-bar to a **fixed left sidebar (220px) + main content** grid.

```
┌──────────┬─────────────────────────────────────────┐
│ SIDEBAR  │ HEADER (sticky): brand · live dot · clock │
│ (nav)    ├─────────────────────────────────────────┤
│          │ KPI STAT ROW (4 cards w/ sparklines)     │
│          ├─────────────────────────────────────────┤
│          │ ACTIVE TAB CONTENT                       │
└──────────┴─────────────────────────────────────────┘
```
On narrow widths (<1100px) the sidebar collapses to a 56px icon rail (labels hidden, hover tooltip). No horizontal overflow ever.

### 4.2 Sidebar navigation
- Inline Lucide-style SVG line icons (no emoji), short labels, grouped:
  - **MONITOR** — Overview, Risk, Scanner
  - **INTELLIGENCE** — AI Analysis, Intelligence, Radar, Daily
  - **EXECUTION** — Signals, Paper Trades
- Active item: cyan left accent bar (3px) + cyan text + `--card2` background.
- **Per-section status badge** (right of label): small dot —
  - green dot = fresh cached result available,
  - amber clock = stale (>24h),
  - hollow = never run.
  Driven by a `sectionStatus` map populated from the cached-result GET endpoints (§7).

### 4.3 Header
- Brand left.
- **Pulsing live dot** (reuse existing `@keyframes pulse`) + "LIVE" replacing the plain pill.
- **One global clock** ("Updated HH:MM:SS") — single source of truth; per-section timestamps removed in favor of this + the sidebar badges. A `lastUpdated` state updates on each successful portfolio poll.
- Keep existing macro-regime banner + cash-warning banner, restyled to new tokens.

### 4.4 KPI stat row
Four cards: Portfolio Value, Day Change, Cash, Buying Power.
- Colored **left accent bar** (3px) per card.
- **Micro-sparkline** behind/below the value — 1-day series from `/api/portfolio` history (or `/api/chart` of a portfolio proxy if no series; fall back to flat line gracefully).
- Up/down **arrow glyph** + colored delta.
- Value in `--font-mono`, label in sans uppercase `--muted`.

---

## 5. Reusable Components

A small component layer (CSS classes + JS render helpers) so the whole app reads as one system.

### 5.1 Engine card (`renderEngineCard`)
Standardizes Consensus, Kelly, Monte Carlo, Deep Analysis, Daily, agent cards:
```
┌─ HEADER: title + tag chips ──────────── [footer action lives here] ┐
│ BODY: result | skeleton | empty-preview                            │
└─ FOOTER: "last run 4h ago" · ▶ Run / ↻ Re-run · est-runtime chip ──┘
```
Single CSS block `.ecard / .ecard-head / .ecard-body / .ecard-foot`.

### 5.2 Skeleton loader (`skeleton(layout)`)
Replaces the centered-spinner placeholders. Dimmed shimmer blocks matching the result's real layout (rows, rings, grids). Shown during runs and as the "preview" of empty run-tabs.

### 5.3 Toast (`toast(msg, type)`)
Bottom-right, auto-dismiss 4s, types: success/error/info. Fired on run completion/failure. Replaces silent `innerHTML` error swaps (errors also still render inline).

### 5.4 Command palette (`Cmd/Ctrl+K`)
Overlay modal: fuzzy list of (a) all tabs, (b) all chart symbols/holdings, (c) run actions ("Run Consensus", "Run Daily Analysis"…). Enter executes: switch tab / load symbol / trigger run. Pure vanilla, keyboard-navigable, Esc closes.

### 5.5 Tables
- Sticky `th` (sans, uppercase, `--muted`), rows separated by hairline (no zebra).
- Right-aligned `--font-mono` numbers, `font-variant-numeric:tabular-nums`.
- Inline **allocation bar** in the ALLOC% column (thin cyan fill behind the % text).
- Row hover `--hover`.

### 5.6 Status tile (`renderTile`)
For ECG health cards and Regime cards: compact tile, colored 3px status strip on top, label + value, optional tiny trend sparkline.

---

## 6. The "Never Empty" Pattern

For every run-based tab (AI Analysis, Signals/Consensus+Kelly, Risk/MonteCarlo, Intelligence, Radar, Daily, Paper):

1. **On tab open**, immediately fetch the cached last result (§7 GET endpoints).
2. If cached result exists → render it + footer "last run {relative time} · ↻ Re-run".
3. If none → render a **dimmed skeleton preview** of the result layout (not a generic icon+paragraph) with a prominent ▶ Run button and an **est-runtime chip** (e.g. "~30s").
4. During a run → skeleton shimmer in place, button shows spinner, runtime chip counts up.
5. On completion → results animate in (existing `fadeIn`), toast fires, sidebar badge → green, global clock unaffected (section badge carries freshness).

Result: no two run-tabs look identical-and-empty; each previews its own unique result shape.

---

## 7. Backend Additions (additive, `server.py`)

The compute POST handlers currently return but rely on `engine_results.json` being written elsewhere (`run_engines2.py`). To support cached-by-default:

- **Persist on POST:** after a successful `/api/deep-analysis`, `/api/consensus`, `/api/kelly`, `/api/monte-carlo`, write the result into `engine_results.json` under its existing key (`ai_result`, `consensus`, `kelly`, `monte_carlo`) plus a `*_ran_at` ISO timestamp.
- **New GET endpoints** (read-only, return cached or `{cached:false}`):
  - `GET /api/deep-analysis/cached`
  - `GET /api/consensus/cached`
  - `GET /api/kelly/cached`
  - `GET /api/monte-carlo/cached`
  - `GET /api/sections/status` → `{overview:{...}, consensus:{ran_at,fresh}, ...}` single call to populate sidebar badges + footers.
- Intelligence and Daily already have history/cached endpoints — reuse as-is.
- All new endpoints read the same `engine_results.json` Gesh already maintains; no new storage, no schema migration. Missing file/key → graceful `{cached:false}`.

**Immutability:** cache writes build a new dict merged onto the loaded JSON, then atomic-write (temp file + rename) — never in-place mutate the on-disk file mid-write.

---

## 8. Per-Tab Treatment

| Tab | Change |
|-----|--------|
| **Overview** | Keep structure (chart + positions + allocation donut + watchlist). Apply new tokens, table style w/ inline alloc bar, sparkline KPIs. Strongest tab — lightest touch. |
| **AI Analysis** | Engine card; cached deep-analysis shown by default; skeleton preview of snowflake/score-ring layout when empty. |
| **Signals** | Two engine cards (Consensus, Kelly) standardized; both cached-by-default; skeleton previews. |
| **Risk** | Monte Carlo as engine card (cached); ECG health → status tiles grid; Regime → status tiles w/ ADX/RSI readouts + sparkline. Most data-dense — mainly restyle. |
| **Intelligence** | Standardize agent output cards; already loads cached history — wire to sidebar badge. |
| **Radar** | Engine card; cached screener result; skeleton preview of results table. |
| **Scanner** | Keep config form (restyled inputs); results in standardized table; two explainer cards become compact info tiles. |
| **Paper Trades** | KPI strip restyled; trade log table w/ new style (REJECTED rows get amber/red status strip); Gate Check 8-checks as status tiles; **Bull/Bear debate → §9 two-column component**. |
| **Daily Analysis** | Engine card; keep "load past run" dropdown; restyle stat cards + action plan + holdings. |

---

## 9. Bull/Bear Debate Component

Replace the single fixed-width text wall with a two-column layout:

```
┌─ 🐂 BULL (green head) ──┐  ┌─ 🐻 BEAR (red head) ──┐
│ Conviction ▓▓▓▓▓░ 72%   │  │ Conviction ▓▓▓░░ 48%  │
│ ▸ Argument 1 (collapse) │  │ ▸ Argument 1          │
│ ▸ Argument 2            │  │ ▸ Argument 2          │
└─────────────────────────┘  └────────────────────────┘
         ─ VERDICT bar (winner + margin) ─
```
- Colored column headers (green/red), conviction as horizontal bar meters.
- Each argument a collapsible `<details>`-style block; prose in sans.
- Parses existing debate response shape (bull/bear text + scores). If response is unstructured text, split heuristically into bullet arguments; degrade to single readable sans column if parsing fails.

---

## 10. Implementation Strategy (single file)

Edit `templates/dashboard.html` in ordered passes to keep diffs reviewable:

1. **Tokens + typography** (`:root`, `body`, mono-scoping) — global visual shift.
2. **Shell** — sidebar nav + header + KPI row markup/CSS/JS; remove tab-bar.
3. **Component layer** — engine card, skeleton, toast, table styles, status tile, command palette (CSS + JS helpers).
4. **Backend** — `server.py` cache-persist + GET endpoints + `/api/sections/status`.
5. **Wire run-tabs** to cached-by-default + skeleton previews + sidebar badges.
6. **Bull/Bear component**.
7. **Per-tab restyle sweep** (Overview → Daily).

Each pass independently loadable in the browser (no build).

## 11. Testing & Verification

- Manual: load each of the 9 tabs via Playwright, screenshot before/after, confirm: no horizontal overflow, prose is sans, numbers are mono, run-tabs show cached/skeleton (never blank), Cmd+K opens, toast fires.
- Backend: `curl` each new GET endpoint with and without `engine_results.json` present (graceful `{cached:false}`).
- Regression: existing POST endpoints unchanged response shape; existing JS render functions still receive the same data.
- No console errors on load or tab switch.

## 12. Risks

- **Sparkline data:** `/api/portfolio` may not expose intraday series. Mitigation: use available series; flat-line fallback, never error.
- **Bull/Bear parsing:** response may be free text. Mitigation: heuristic split + graceful single-column fallback.
- **Single-file size:** already 167KB; additions push it larger. Acceptable (no build), but keep CSS deduplicated and JS helpers shared.
- **Font CDN offline:** Inter via CDN; `system-ui` fallback in the stack so offline still renders.
