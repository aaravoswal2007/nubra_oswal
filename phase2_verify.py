#!/usr/bin/env python3
"""
Phase 2 acceptance: place_order_nubra and place_order_nubra_by_key (no real order unless you pass --live).
Run from nubra_oswal:
  python phase2_verify.py                    # dry run: show params
  python phase2_verify.py --live             # place one real order (ref_id=1895206, qty=600, price=6000)
"""

import sys
from nubra_order import place_order_nubra

def main():
    ref_id = 1895206
    qty = 600
    price_rupees = 5900
    side = "BUY"
    print(f"[Phase 2] ref_id={ref_id}, qty={qty}, price_rupees={price_rupees}")
    if "--live" in sys.argv:
        print(f"[Phase 2] Placing LIVE order: {side} {qty} @ {price_rupees} (ref_id={ref_id})...")
        r = place_order_nubra(ref_id, side, qty, price_rupees)
        print("[Phase 2] Result:", r)
    else:
        print(f"[Phase 2] Dry run. Would call place_order_nubra(ref_id={ref_id}, side={side}, qty={qty}, price_rupees={price_rupees})")
        print("Run with --live to place one real order (use with caution).")

if __name__ == "__main__":
    main()
