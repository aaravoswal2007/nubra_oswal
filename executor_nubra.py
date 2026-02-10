#!/usr/bin/env python3
"""
Phase 5+6: Nubra executor (single- or multi-splice).

- Phase 5: One order for full qty; poll until filled; emit on_lot_filled once.
- Phase 6: Splice loop — place multiple child orders (splice_lots at a time), track
  cumulative fills, emit on_lot_filled at whole-lot boundaries (_maybe_emit).
"""

from __future__ import annotations

import math
import time
from typing import Optional, Callable, Dict, Any

from instrument_dict_nubra import ensure_instrument_dict
from market_data_helpers import get_mid_price
from nubra_order import place_order_nubra_by_key, modify_order_nubra, cancel_order_nubra
from order_updates_nubra import (
    start_order_updates_socket,
    update_socket_state,
    get_order_state,
    wait_for_order_update,
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
    splice_lots: int = 5,
    modify_interval_sec: float = 3.0,
    modify_min_move_rupees: float = 0.05,  # only modify when mid moves more than this (5 paise)
    poll_interval_sec: float = 0.25,
    product_type: str = "NRML",
    phase: str = "entry",  # "entry" or "exit"
    on_lot_filled: OnLotFilled = None,
) -> None:
    """
    Nubra executor (mid-peg spliced).
    - Places one child (splice) at a time at MID price.
    - Event-driven: waits for order updates (or timeout) instead of polling; handles
      REJECTED (abort parent), CANCELLED/EXPIRED (continue parent), FILLED, and missing mode (15s).
    - Emits on_lot_filled at whole-lot boundaries (cumulative across splices).
    """
    side_upper = (side or "").strip().upper()
    if side_upper not in ("BUY", "SELL"):
        raise ValueError(f"Invalid side: {side} (must be BUY or SELL)")

    if lots <= 0 or lot_size <= 0 or splice_lots <= 0:
        print(f"[executor_nubra] Invalid parameters: lots={lots} lot_size={lot_size} splice_lots={splice_lots}")
        return

    inst = ensure_instrument_dict()
    if key_name not in inst:
        raise ValueError(f"{key_name} not in instrument_dict; generate instruments/instrument_dict_nubra first.")

    total_qty = lots * lot_size
    if splice_lots > lots:
        splice_lots = lots
    per_splice_qty = min(splice_lots * lot_size, total_qty)
    expected_splices = int(math.ceil(total_qty / per_splice_qty)) + 2  # small buffer

    # Import market_data lazily
    try:
        from subscribe_orderbook import market_data  # type: ignore
    except ImportError:
        print("[executor_nubra] ERROR: Could not import market_data from subscribe_orderbook. "
              "Run subscribe_orderbook.py in the same process to populate market_data.")
        return

    print(f"[executor_nubra] Starting {key_name} {side_upper} {lots} lots, splice_lots={splice_lots} phase={phase}")

    start_order_updates_socket()

    remaining_total = total_qty
    cum_filled_parent_qty = 0
    lots_emitted = 0
    placed_orders: set = set()  # order_id (int or str from API)
    splice_idx = 0

    def _norm_oid(oid: Any) -> Any:
        if oid is None:
            return None
        try:
            return int(oid)
        except (TypeError, ValueError):
            return oid

    def _maybe_emit(new_cum_qty: int, avg_px: Optional[float] = None) -> None:
        nonlocal cum_filled_parent_qty, lots_emitted
        if new_cum_qty <= cum_filled_parent_qty:
            return
        if new_cum_qty > total_qty:
            new_cum_qty = total_qty
        cum_filled_parent_qty = new_cum_qty
        new_lots = cum_filled_parent_qty // lot_size
        while lots_emitted < new_lots and lots_emitted < lots:
            lots_emitted += 1
            if on_lot_filled:
                try:
                    lot_data = {
                        "symbol": key_name,
                        "side": side_upper,
                        "phase": phase,
                        "lot_index": lots_emitted,
                        "lots_filled": lots_emitted,
                        "lots_total": lots,
                        "lot_size": lot_size,
                        "avg_px": avg_px,
                    }
                    on_lot_filled(lot_data)
                except Exception as e:
                    print(f"[executor_nubra] on_lot_filled error: {e}")

    while remaining_total > 0:
        splice_idx += 1
        if splice_idx > expected_splices:
            print(f"[executor_nubra] FAILSAFE: splice_idx {splice_idx} > expected_splices {expected_splices}. Aborting.")
            break

        splice_qty = min(per_splice_qty, remaining_total)
        if splice_qty <= 0:
            break

        # Get MID price
        work_px: Optional[float] = None
        while work_px is None:
            try:
                work_px = get_mid_price(market_data, key_name)
            except Exception as e:
                print(f"[executor_nubra] get_mid_price error for {key_name}: {e}")
                work_px = None
            if work_px is None:
                time.sleep(max(0.10, poll_interval_sec))
        if work_px <= 0:
            print(f"[executor_nubra] FAILSAFE: invalid price {work_px}. Aborting.")
            break

        print(f"[executor_nubra] Splice {splice_idx}: {side_upper} {splice_qty} @ {work_px:.2f}")
        resp = place_order_nubra_by_key(
            key_name,
            side_upper,
            splice_qty,
            work_px,
            product_type=product_type,
            validity="DAY",
            price_type="LIMIT",
        )
        order_id = resp.get("order_id")
        if order_id is None:
            print(f"[executor_nubra] ERROR: place_order_nubra_by_key did not return order_id: {resp}")
            break
        oid_key = _norm_oid(order_id)
        if oid_key is not None and oid_key in placed_orders:
            print(f"[executor_nubra] ERROR: Duplicate order_id {order_id}. Aborting.")
            break
        if oid_key is not None:
            placed_orders.add(oid_key)

        update_socket_state(order_id, 0, "PENDING_LOCAL", avg_px=None)
        print(f"[executor_nubra] Order placed: order_id={order_id} {key_name} {side_upper} qty={splice_qty} @ {work_px:.2f}")

        filled_this_child = 0
        next_modify_at = time.monotonic() + modify_interval_sec
        last_modify_price_rupees: Optional[float] = work_px  # only modify when mid moves > modify_min_move_rupees
        missing_started_at: Optional[float] = None  # when order_state had no entry for this order_id

        while True:
            now = time.monotonic()
            # Event-driven: block until next order update or timeout (for modify + missing check)
            wait_timeout = min(2.0, max(0.25, next_modify_at - now))
            wait_for_order_update(order_id, timeout_sec=wait_timeout)

            st = get_order_state(order_id)

            if not st:
                # Missing mode: order not in order_state
                if missing_started_at is None:
                    missing_started_at = now
                    print(f"[executor_nubra] [WARN] Order {order_id} state missing — entering missing mode")
                missing_for = now - missing_started_at
                if missing_for >= 15.0:
                    print(f"[executor_nubra] [FAILSAFE] Order {order_id} missing for {missing_for:.1f}s → cancelling then continuing parent")
                    try:
                        cancel_order_nubra(order_id)
                    except Exception as e:
                        print(f"[executor_nubra] cancel_order_nubra({order_id}) error: {e}", flush=True)
                    break
                continue

            missing_started_at = None  # clear missing mode when we have state

            status = str(st.get("status") or "").upper()
            filled_resolved = int(st.get("filled") or 0)
            filled_resolved = max(0, min(filled_resolved, splice_qty))
            avg_px = st.get("avg_px")

            # Fill delta
            if filled_resolved > filled_this_child:
                inc = filled_resolved - filled_this_child
                filled_this_child = filled_resolved
                remaining_total -= inc
                _maybe_emit(cum_filled_parent_qty + inc, avg_px=avg_px)

            # Terminal: REJECTED → abort parent
            if status in ("REJECT", "REJECTED"):
                print(f"[executor_nubra] [TERMINAL] Order {order_id} REJECTED (filled {filled_this_child}/{splice_qty}) → aborting parent")
                remaining_total = 0
                break

            # Terminal: CANCEL/CANCELLED/EXPIRED → continue parent
            if status in ("CANCEL", "CANCELLED", "EXPIRED"):
                print(f"[executor_nubra] [TERMINAL] Order {order_id} {status} (filled {filled_this_child}/{splice_qty}) → continuing parent")
                break

            # Terminal: FILLED or fully filled
            if status == "FILLED" or filled_this_child >= splice_qty:
                print(f"[executor_nubra] Splice {splice_idx} complete: order_id={order_id} filled={filled_this_child}/{splice_qty}")
                break

            # Periodic modify to new MID (only when mid moved > modify_min_move_rupees)
            if modify_interval_sec > 0 and now >= next_modify_at:
                try:
                    new_mid = get_mid_price(market_data, key_name)
                    if new_mid is not None and new_mid > 0 and last_modify_price_rupees is not None:
                        if abs(new_mid - last_modify_price_rupees) > modify_min_move_rupees:
                            modify_order_nubra(order_id, new_mid, splice_qty)
                            last_modify_price_rupees = new_mid
                except Exception as e:
                    print(f"[executor_nubra] modify_order error: {e}", flush=True)
                next_modify_at = now + modify_interval_sec

        if remaining_total <= 0:
            break

    print(f"[executor_nubra] Parent order done: {key_name} {side_upper} lots_emitted={lots_emitted}/{lots}")


if __name__ == "__main__":
    def _print_lot(info: Dict[str, Any]) -> None:
        print(f"[TEST] on_lot_filled: {info}")

    place_order_splices_mid_ltq_nubra(
        key_name="ADANIGREEN_920_CE",
        side="BUY",
        lots=2,
        lot_size=600,
        splice_lots=1,
        on_lot_filled=_print_lot,
    )

