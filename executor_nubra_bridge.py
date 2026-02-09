#!/usr/bin/env python3
"""
Phase 7: Bridge adapter for Nubra executor.

Exposes place_order_splices_mid_ltq with the same signature as executor_live.place_order_splices_mid_ltq_real
so the frontend (Bridge, leg2, position_management) can use the Nubra executor without code changes.
- modify_interval_sec: used for periodic modify to new MID (default 3s). aggressive_after_sec still ignored.
- on_progress: legacy param; used as on_lot_filled if on_lot_filled is not provided.
"""

from __future__ import annotations

from typing import Optional, Callable, Dict, Any

from executor_nubra import place_order_splices_mid_ltq_nubra


def place_order_splices_mid_ltq(
    *,
    key_name: str,
    side: str,
    lots: int,
    lot_size: int,
    splice_lots: int = 5,
    modify_interval_sec: float = 5.0,   # ignored
    poll_interval_sec: float = 0.25,
    aggressive_after_sec: Optional[float] = None,  # ignored
    product_type: str = "NRML",
    phase: str = "entry",
    on_lot_filled: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,  # legacy; used if on_lot_filled is None
) -> None:
    """
    Drop-in for executor_live.place_order_splices_mid_ltq_real.
    Delegates to place_order_splices_mid_ltq_nubra; extra kwargs (modify_interval_sec, aggressive_after_sec) are ignored.
    """
    callback = on_lot_filled if on_lot_filled is not None else on_progress
    place_order_splices_mid_ltq_nubra(
        key_name=key_name,
        side=side,
        lots=lots,
        lot_size=lot_size,
        splice_lots=splice_lots,
        modify_interval_sec=modify_interval_sec,
        poll_interval_sec=poll_interval_sec,
        product_type=product_type,
        phase=phase,
        on_lot_filled=callback,
    )
