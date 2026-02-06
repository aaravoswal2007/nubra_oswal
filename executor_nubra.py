#!/usr/bin/env python3
"""
Phase 5: Single-splice Nubra executor.

High-level behaviour (simplified vs XTS executor_live):
- Input: key_name, side, lots, lot_size (same shape as Oswal).
- Resolve key_name -> ref_id via instrument_dict_nubra.
- Get MID price from Nubra market_data (subscribe_orderbook.market_data + market_data_helpers).
- Place ONE child order for the full quantity via nubra_order.place_order_nubra_by_key.
- Seed order_updates_nubra.order_state and poll get_order_state(order_id) until filled.
- Emit on_lot_filled ONCE when the WHOLE parent lots are filled.

No splicing, no modify/cancel, no REST reconciliation yet.
"""

from __future__ import annotations

import time
from typing import Optional, Callable, Dict, Any

from instrument_dict_nubra import ensure_instrument_dict
from market_data_helpers import get_mid_price
from nubra_order import place_order_nubra_by_key
from order_updates_nubra import (
    start_order_updates_socket,
    update_socket_state,
    get_order_state,
    is_socket_connected,
)

# Type alias for on_lot_filled callback (same idea as Oswal)
OnLotFilled = Optional[Callable[[Dict[str, Any]], None]]


def place_order_splices_mid_ltq_nubra(
    *,
    key_name: str,
    side: str,
    lots: int,
    lot_size: int,
    poll_interval_sec: float = 0.25,
    product_type: str = "NRML",
    phase: str = "entry",  # "entry" or "exit"
    on_lot_filled: OnLotFilled = None,
) -> None:
    """
    Single-splice Nubra executor:
    - Place ONE order for lots * lot_size at MID price.
    - Wait for it to be filled using order_updates_nubra.get_order_state().
    - When fully filled, call on_lot_filled once with Oswal-style payload.
    """
    side_upper = (side or "").strip().upper()
    if side_upper not in ("BUY", "SELL"):
        raise ValueError(f"Invalid side: {side} (must be BUY or SELL)")

    if lots <= 0 or lot_size <= 0:
        print(f"[executor_nubra] Invalid parameters: lots={lots} lot_size={lot_size}")
        return

    inst = ensure_instrument_dict()
    if key_name not in inst:
        raise ValueError(f"{key_name} not in instrument_dict; generate instruments/instrument_dict_nubra first.")

    total_qty = lots * lot_size

    # Import market_data lazily to avoid circulars; subscribe_orderbook defines module-level market_data
    try:
        from subscribe_orderbook import market_data  # type: ignore
    except ImportError:
        print("[executor_nubra] ERROR: Could not import market_data from subscribe_orderbook. "
              "Run subscribe_orderbook.py in the same process to populate market_data.")
        return

    print(f"[executor_nubra] Starting {key_name} {side_upper} {lots} lots (qty={total_qty}) phase={phase}")

    # Wait for MID price
    work_px: Optional[float] = None
    while work_px is None:
        try:
            work_px = get_mid_price(market_data, key_name)
        except Exception as e:
            print(f"[executor_nubra] get_mid_price error for {key_name}: {e}")
            work_px = None
        if work_px is None:
            time.sleep(max(0.10, poll_interval_sec))

    print(f"[executor_nubra] Using MID price {work_px:.2f} for parent order.")

    # Ensure order-updates WebSocket is running
    start_order_updates_socket()

    # Place one Nubra order for the full quantity
    print(f"[executor_nubra] Placing order: {key_name} {side_upper} qty={total_qty} @ {work_px:.2f}")
    resp = place_order_nubra_by_key(
        key_name,
        side_upper,
        total_qty,
        work_px,
        product_type=product_type,
        validity="DAY",
        price_type="LIMIT",
    )

    order_id = resp.get("order_id")
    if order_id is None:
        print(f"[executor_nubra] ERROR: place_order_nubra_by_key did not return order_id: {resp}")
        return

    # Seed local order_state as PENDING_LOCAL
    update_socket_state(order_id, 0, "PENDING_LOCAL", avg_px=None)
    print(f"[executor_nubra] Order placed: order_id={order_id} {key_name} {side_upper} qty={total_qty} @ {work_px:.2f}")

    # Poll order state until filled (or terminal status)
    lots_emitted = 0
    parent_start = time.monotonic()

    while True:
        st = get_order_state(order_id)
        if not st:
            # If socket isn't connected yet, just keep waiting
            if not is_socket_connected():
                time.sleep(max(0.25, poll_interval_sec))
                continue
            # Socket connected but no state yet - keep waiting
            time.sleep(max(0.25, poll_interval_sec))
            continue

        status = str(st.get("status") or "").upper()
        filled = int(st.get("filled") or 0)
        avg_px = st.get("avg_px")

        print(f"[executor_nubra] order_id={order_id} status={status} filled={filled}/{total_qty} avg_px={avg_px}")

        # Terminal statuses
        if filled >= total_qty or status in ("FILLED", "ORDER_STATUS_FILLED"):
            # Emit on_lot_filled once (whole parent lots complete)
            if lots_emitted < lots and on_lot_filled:
                try:
                    lot_data = {
                        "symbol": key_name,
                        "side": side_upper,
                        "phase": phase,
                        "lot_index": lots,        # single splice → all lots at once
                        "lots_filled": lots,
                        "lots_total": lots,
                        "lot_size": lot_size,
                        "avg_px": avg_px,
                    }
                    on_lot_filled(lot_data)
                except Exception as e:
                    print(f"[executor_nubra] on_lot_filled error: {e}")
            print(f"[executor_nubra] Parent order completed: {key_name} {side_upper} {lots} lots.")
            break

        if status in ("CANCELLED", "REJECTED", "EXPIRED"):
            print(f"[executor_nubra] Parent order ended with status={status}, filled={filled}/{total_qty}.")
            break

        # Still working - sleep and poll again
        time.sleep(max(0.25, poll_interval_sec))


if __name__ == "__main__":
    # Minimal manual test (requires subscribe_orderbook + order_updates_nubra running):
    # Adjust key_name/lots/lot_size for your environment.
    def _print_lot(info: Dict[str, Any]) -> None:
        print(f"[TEST] on_lot_filled: {info}")

    place_order_splices_mid_ltq_nubra(
        key_name="ADANIGREEN_920_CE",
        side="BUY",
        lots=1,
        lot_size=600,
        on_lot_filled=_print_lot,
    )

