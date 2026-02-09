#!/usr/bin/env python3
"""
Run Phase 7 and market data in the same process so the executor gets live MID.

Starts subscribe_orderbook in a background thread (so market_data is updated),
waits for connection + data, then runs phase7_verify in the main thread.
Use this when you want place/modify orders to use live mid from the orderbook.

Usage (from nubra_oswal):
  python run_phase7_with_market_data.py

You may be prompted for Nubra login (MPIN) when the market-data thread starts.
Optional env: PHASE7_KEY_NAME, PHASE7_LOTS, PHASE7_LOT_SIZE, PHASE7_SPLICE_LOTS, PHASE7_SIDE.
"""

import threading
import time

# Start market data WebSocket in a background thread so market_data is live in this process
def _run_subscribe_orderbook() -> None:
    import subscribe_orderbook
    subscribe_orderbook.main()


def main() -> None:
    print("[run_phase7] Starting market data WebSocket in background thread...")
    print("[run_phase7] (If you see a login/MPIN prompt, enter it now.)")
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
