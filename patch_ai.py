import re
import shutil

with open("ai_analyzer.py", "r") as f:
    code = f.read()

# Back up before any in-place rewrite so a bad/partial patch is recoverable.
shutil.copy2("ai_analyzer.py", "ai_analyzer.py.bak")


def _sub_checked(pattern, repl, text, label, flags=0):
    """re.sub that fails loudly if the pattern did not match — prevents silently
    writing an unchanged file when the source has drifted from the patterns."""
    new, n = re.subn(pattern, repl, text, flags=flags)
    assert n > 0, f"patch_ai: pattern did not match — {label}"
    return new

# 1. Deflate DEEP_SYSTEM_PROMPT
old_deep_prompt_start = r'DEEP_SYSTEM_PROMPT = """You are an elite quantitative financial analyst combining the depth of Simply Wall St, Bloomberg, and Goldman Sachs research.\n\nAnalyze the portfolio and return ONLY valid JSON — no markdown, no code blocks, no explanation. Start the response with \{ directly.\n\nCRITICAL JSON RULES.*?'
old_deep_prompt_end = r'- dividend: 0=no dividend, 60=moderate 2-4% yield, 100=high yield >5% safe payout\n\nProvide 2-4 risks per stock and 1-3 rewards. Be specific — reference actual business metrics."""'

new_deep_prompt = '''DEEP_SYSTEM_PROMPT = """You are an elite quantitative financial analyst combining the depth of Simply Wall St, Bloomberg, and Goldman Sachs research.

SCORING GUIDE (be realistic — avoid clustering all at 50):
- valuation: 0=massively overvalued, 50=fair, 100=deeply undervalued
- growth: 0=declining revenues, 50=5-10% growth, 100=>50% revenue growth
- health: 0=<3 months cash runway, 50=average balance sheet, 100=net cash fortress
- momentum: 0=down >50% with deteriorating fundamentals, 50=sideways, 100=strong uptrend
- dividend: 0=no dividend, 60=moderate 2-4% yield, 100=high yield >5% safe payout

Provide 2-4 risks per stock and 1-3 rewards. Be specific — reference actual business metrics."""'''

code = _sub_checked(old_deep_prompt_start + r'.*?' + old_deep_prompt_end, new_deep_prompt, code, "deep_prompt", flags=re.DOTALL)


# 2. Deflate SCREENER_SYSTEM_PROMPT
old_screener_prompt_start = r'SCREENER_SYSTEM_PROMPT = """You are an elite quantitative stock screener combining Goldman Sachs research depth with Simply Wall St clarity.\n\nGiven a list of stock candidates.*?'
old_screener_prompt_end = r'Order picks by composite_score descending."""'

new_screener_prompt = '''SCREENER_SYSTEM_PROMPT = """You are an elite quantitative stock screener combining Goldman Sachs research depth with Simply Wall St clarity.

Given a list of stock candidates with 1-week data and fundamentals, select exactly 20 stocks that represent the BEST investment opportunities for this week.

Selection criteria — use a BALANCED MIXTURE (not pure momentum):
- MOMENTUM (25%): Strong 1W and 1M price action, volume confirmation
- VALUE (20%): P/E vs growth rate (PEG concept), P/B not excessive, analyst upside
- GROWTH (25%): Revenue + earnings growth trajectory, forward guidance
- ANALYST CONSENSUS (20%): Strong buy/buy ratings, meaningful price target upside
- QUALITY (10%): Business model durability, balance sheet, sector tailwinds

Rules:
- Include a mix: 2-3 large-cap stability picks + 5-7 mid-cap growth + 2-3 value plays + 2-3 contrarian/turnaround
- Avoid ETFs (SPY, QQQ, IWM) as final picks
- 1-2 picks can be WATCH (not quite BUY but worth monitoring)
- Be realistic about scores — spread them meaningfully (not all 75-85)

Order picks by composite_score descending."""'''

code = _sub_checked(old_screener_prompt_start + r'.*?' + old_screener_prompt_end, new_screener_prompt, code, "screener_prompt", flags=re.DOTALL)


# Delete JSON helpers
code = re.sub(r'# ─── JSON Repair Helpers ─────────────────────────────────────────────────────────────\n\ndef _sanitise_response.*?def _extract_and_parse.*?(?=# ───|\ndef deep_analyze_portfolio)', '', code, flags=re.DOTALL)


with open("ai_analyzer.py", "w") as f:
    f.write(code)

print("Prompts stripped.")
