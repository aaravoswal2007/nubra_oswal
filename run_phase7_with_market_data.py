#!/usr/bin/env python3
"""
Run Phase 7 and market data in the same process so the executor gets live MID.

Parent test file that:
1. Calls ensure_nubra() once at start (TOTP/.env auto-login if configured)
2. Starts subscribe_orderbook in a background thread (so market_data is updated)
3. Waits for connection + data, then runs phase7_verify in the main thread

Use this when you want place/modify orders to use live mid from the orderbook.

Usage (from nubra_oswal):
  python run_phase7_with_market_data.py

Optional env: PHASE7_KEY_NAME, PHASE7_LOTS, PHASE7_LOT_SIZE, PHASE7_SPLICE_LOTS, PHASE7_SIDE.
"""

import threading
import time

# Ensure Nubra SDK client is initialized once at process start
from nubra_client import ensure_nubra

# Start market data WebSocket in a background thread so market_data is live in this process
def _run_subscribe_orderbook() -> None:
    import subscribe_orderbook
    subscribe_orderbook.main()


def main() -> None:
    # Initialize Nubra SDK client once (TOTP/.env auto-login if configured)
    print("[run_phase7] Initializing Nubra SDK client (TOTP/.env auto-login)...")
    ensure_nubra()
    print("[run_phase7] ✅ Nubra client ready\n")

    print("[run_phase7] Starting market data WebSocket in background thread...")
    thread = threading.Thread(target=_run_subscribe_orderbook, daemon=True)
    thread.start()

    print("[run_phase7] Waiting 15s for connection and orderbook data...")
    time.sleep(15)

    print("[run_phase7] Running Phase 7 executor (same process = live market_data).\n")
    import phase7_verify
    phase7_verify.main()

    print("\n[run_phase7] Done. (Market data thread stops when this process exits.)")


if __name__ == "__main__":
    main()
