#!/usr/bin/env python3
"""
Phase 4 acceptance: order updates WebSocket + order_state.
Run from nubra_oswal:
  python phase4_verify.py   # start WebSocket, print order_state every 5s; Ctrl+C to stop
"""

import time
from order_updates_nubra import (
    start_order_updates_socket,
    get_socket_state,
    get_order_state,
    update_socket_state,
    is_socket_connected,
)

def main():
    print("[Phase 4] Starting order updates WebSocket...")
    start_order_updates_socket()
    # Optional: seed a fake order to see state
    # update_socket_state(99999, 0, "PENDING_LOCAL", avg_px=None)
    print("[Phase 4] WebSocket started. Waiting for connection and order/trade updates.")
    print("[Phase 4] Place an order (e.g. phase2_verify.py --live) to see updates here.")
    print("Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(5)
            connected = is_socket_connected()
            state = get_socket_state()
            print(f"[Phase 4] connected={connected} order_state keys={len(state)} {list(state.keys())[:10]}{'...' if len(state) > 10 else ''}", flush=True)
            for oid, st in list(state.items())[:3]:
                print(f"  order_id={oid} status={st.get('status')} filled={st.get('filled')} avg_px={st.get('avg_px')}", flush=True)
    except KeyboardInterrupt:
        print("\n[Phase 4] Stopped.")

if __name__ == "__main__":
    main()
