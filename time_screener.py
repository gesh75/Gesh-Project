import time
from server import run_screener

start = time.time()
print("Starting...")
run_screener()
print(f"Finished in {time.time() - start:.2f} seconds")
