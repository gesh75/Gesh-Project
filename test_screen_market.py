import traceback
from market_data import screen_market
try:
    print("Screening market...")
    candidates = screen_market(top_n=5)
    print("Done screening. Length:", len(candidates))
except Exception as e:
    traceback.print_exc()
