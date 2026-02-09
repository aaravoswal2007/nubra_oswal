#!/usr/bin/env python3
"""
Phase 7 acceptance: test Nubra executor via the Bridge adapter (no frontend).

This calls the same entry point the frontend would use — executor_nubra_bridge.place_order_splices_mid_ltq
— with frontend-style kwargs (modify_interval_sec, aggressive_after_sec, etc.). Verifies the adapter
forwards to the Nubra executor and that extra params are ignored.

Usage (from nubra_oswal):
  1. Terminal 1:  python subscribe_orderbook.py
  2. Terminal 2:  python phase7_verify.py

Optional env overrides:
  PHASE7_KEY_NAME=ADANIGREEN_920_CE
  PHASE7_LOTS=1
  PHASE7_LOT_SIZE=600
  PHASE7_SPLICE_LOTS=1
  PHASE7_SIDE=BUY
"""

import os
from typing import Dict, Any

# Use the bridge adapter (same API the frontend uses)
from executor_nubra_bridge import place_order_splices_mid_ltq


def _on_lot_filled(info: Dict[str, Any]) -> None:
    print(f"[Phase 7] on_lot_filled: {info}")


def main() -> None:
    key_name = os.environ.get("PHASE7_KEY_NAME", "RELIANCE_1500_CE")
    lots = int(os.environ.get("PHASE7_LOTS", "50"))
    lot_size = int(os.environ.get("PHASE7_LOT_SIZE", "600"))
    splice_lots = int(os.environ.get("PHASE7_SPLICE_LOTS", "10"))
    side = os.environ.get("PHASE7_SIDE", "BUY").strip().upper()

    print(f"[Phase 7] Calling bridge adapter place_order_splices_mid_ltq (frontend-style kwargs)")
    print(f"[Phase 7] key_name={key_name} side={side} lots={lots} lot_size={lot_size} splice_lots={splice_lots}")
    print(f"[Phase 7] modify_interval_sec / aggressive_after_sec are passed but ignored by Nubra executor.\n")

    place_order_splices_mid_ltq(
        key_name=key_name,
        side=side,
        lots=lots,
        lot_size=lot_size,
        splice_lots=splice_lots,
        modify_interval_sec=5.0,   # ignored
        poll_interval_sec=0.25,
        aggressive_after_sec=None,  # ignored
        product_type="NRML",
        phase="entry",
        on_lot_filled=_on_lot_filled,
    )

    print("\n[Phase 7] Done.")


if __name__ == "__main__":
    main()
