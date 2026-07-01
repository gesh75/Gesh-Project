import traceback
from market_data import screen_market
from ai_analyzer import analyze_screener
try:
    print("Screening market...")
    candidates = screen_market(top_n=40)
    print("Done screening. Length:", len(candidates))
    print("Analyzing screener...")
    res = analyze_screener(candidates)
    print("Success")
except Exception as e:
    traceback.print_exc()
