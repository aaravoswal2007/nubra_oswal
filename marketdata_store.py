# marketdata_store.py
# Self-contained market data storage for nubra_oswal

import shelve
import dbm.dumb
import threading
import time
import os
from pathlib import Path

# ====== Global in-memory market data ======
market_data = {}

# ====== Set DB path to current working directory ======
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR / "data")
DATA_DIR.mkdir(exist_ok=True)
db_path = str(DATA_DIR / "market_data.db")

# ====== Load from shelve on startup ======
def load_from_shelve():
    if os.path.exists(db_path):
        with shelve.open(db_path) as shelf:
            market_data.update(shelf)
            print(f"[Startup] Loaded {len(market_data)} instruments from disk.")
    else:
        print("[Startup] No existing market_data.db found. Starting fresh.")

load_from_shelve()

# ====== Save to shelve ======
def save_to_shelve():
    try:
        with shelve.open(db_path, writeback=True) as shelf:
            for key, value in market_data.items():
                shelf[str(key)] = value
        
    except Exception as e:
        print("[Save] Error saving market data:", e)

# ====== Autosave thread ======
def autosave(interval=10):
    def _run():
        while True:
            time.sleep(interval)
            save_to_shelve()
    threading.Thread(target=_run, daemon=True).start()

# Start autosave
autosave()
