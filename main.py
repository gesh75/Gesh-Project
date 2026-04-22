from __future__ import annotations
import sys, textwrap
from datetime import datetime
from auth import login, logout
from data_fetcher import build_data_payload, DEFAULT_WATCHLIST
from ai_analyzer import analyze_portfolio

RESET="\033[0m"; BOLD="\033[1m"; CYAN="\033[96m"; GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; DIM="\033[2m"; LINE="─"*70

def _color_pct(v):
    return f"{GREEN if v>=0 else RED}{'+' if v>=0 else ''}{v:.2f}%{RESET}"

def _section(t):
    print(f"\n{BOLD}{CYAN}{LINE}{RESET}\n{BOLD}{CYAN}  {t}{RESET}\n{BOLD}{CYAN}{LINE}{RESET}")

def main():
    print(f"\n{BOLD}{'═'*70}\n   GESH FINANCIAL OBSERVABILITY TOOL  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'═'*70}{RESET}")
    print(f"\n{YELLOW}[1/3] Authenticating with Robinhood...{RESET}")
    try:
        login(); print(f"{GREEN}      ✓ Session established.{RESET}")
    except Exception as e:
        print(f"{RED}      ✗ {e}{RESET}", file=sys.stderr); sys.exit(1)
    print(f"\n{YELLOW}[2/3] Fetching live portfolio data...{RESET}")
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST); print(f"{GREEN}      ✓ Data fetched.{RESET}")
    except Exception as e:
        print(f"{RED}      ✗ {e}{RESET}", file=sys.stderr); logout(); sys.exit(1)
    print(f"\n{YELLOW}[3/3] Running AI risk analysis...{RESET}")
    try:
        analysis = analyze_portfolio(payload); print(f"{GREEN}      ✓ Analysis complete.{RESET}")
    except Exception as e:
        print(f"{RED}      ✗ {e}{RESET}", file=sys.stderr); logout(); sys.exit(1)

    eq = payload["equity_summary"]
    _section("ACCOUNT SNAPSHOT")
    for k,v in [("Portfolio Equity",eq["portfolio_equity"]),("Extended-Hours Equity",eq["extended_hours_equity"]),("Cash",eq["cash"]),("Buying Power",eq["buying_power"])]:
        print(f"  {k:<32} ${v:>12,.2f}")

    _section("OPEN POSITIONS")
    holdings = payload["holdings"]
    if holdings:
        print(f"{DIM}  {'SYMBOL':<8} {'QTY':>8} {'AVG COST':>10} {'EQUITY':>12} {'ALLOC%':>8} {'CHNG%':>9}{RESET}\n  {'─'*65}")
        for h in holdings:
            print(f"  {h['symbol']:<8} {h['quantity']:>8.4f} {h['average_buy_price']:>10.2f} {h['equity']:>12,.2f} {h['portfolio_percentage']:>7.1f}% {_color_pct(h.get('percent_change',0)):>18}")

    _section("WATCHLIST")
    wl = payload["watchlist"]
    if wl:
        print(f"{DIM}  {'SYMBOL':<8} {'LAST':>10} {'BID':>10} {'ASK':>10} {'PREV':>12} {'CHG%':>9}{RESET}\n  {'─'*65}")
        for w in wl:
            print(f"  {w['symbol']:<8} {w['last_trade_price']:>10.2f} {w['bid_price']:>10.2f} {w['ask_price']:>10.2f} {w['previous_close']:>12.2f} {_color_pct(w.get('change_pct',0)):>18}")

    _section("AI RISK ANALYSIS  (claude-3-5-sonnet)")
    for line in analysis.splitlines():
        print(f"  {textwrap.fill(line, width=68, subsequent_indent='    ')}")
    print(f"\n{BOLD}{CYAN}{LINE}{RESET}\n")
    logout()

if __name__ == "__main__":
    main()
