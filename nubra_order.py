#!/usr/bin/env python3
"""
Phase 2: Single-order placement (Nubra API only).
One order per call; no splice loop. All Nubra order API calls go through a single lock.
"""

import threading
from typing import Any

from nubra_client import ensure_nubra

# Serialize all Nubra order API calls (place/modify/cancel) to avoid concurrent-request issues.
_NUBRA_ORDER_LOCK = threading.Lock()


def place_order_nubra(
    ref_id: int,
    side: str,
    qty: int,
    price_rupees: float,
    *,
    product_type: str = "NRML",
    validity: str = "DAY",
    exchange: str = "NSE",
    price_type: str = "LIMIT",
    tag: str | None = None,
) -> dict[str, Any]:
    """
    Place a single Nubra order (limit or market).

    - ref_id: instrument ref_id from instrument_dict (or Instrument API).
    - side: "BUY" or "SELL".
    - qty: order quantity (contracts/shares).
    - price_rupees: limit price in rupees (ignored if price_type="MARKET"). Automatically rounded to tick_size (from CSV).
    - product_type: "NRML" (CNC) or "MIS" (intraday).
    - validity: "DAY" or "IOC".
    - exchange: "NSE" or "BSE".
    - price_type: "LIMIT" or "MARKET".
    - tag: optional order tag.

    Returns dict with order_id, exchange_order_id, ref_id, order_status, and other response fields.
    """
    side_upper = (side or "").strip().upper()
    if side_upper not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    order_side = "ORDER_SIDE_BUY" if side_upper == "BUY" else "ORDER_SIDE_SELL"

    product_upper = (product_type or "NRML").strip().upper()
    if product_upper == "MIS":
        order_delivery_type = "ORDER_DELIVERY_TYPE_IDAY"
    else:
        order_delivery_type = "ORDER_DELIVERY_TYPE_CNC"

    validity_upper = (validity or "DAY").strip().upper()
    validity_type = "IOC" if validity_upper == "IOC" else "DAY"

    price_type_upper = (price_type or "LIMIT").strip().upper()
    price_type_val = "MARKET" if price_type_upper == "MARKET" else "LIMIT"

    # Round price to tick_size (tick_size from CSV is in paise, convert to rupees for rounding)
    if price_type_val == "LIMIT":
        order_price = float(price_rupees)
        # Round to nearest tick_size (tick_size from CSV is in paise, convert to rupees)
        from instrument_dict_nubra import load_tick_size_map
        tick_size_map = load_tick_size_map()
        tick_size_paise = tick_size_map.get(int(ref_id), 5)  # default 5 paise
        # tick_size_rupees = tick_size_paise / 100  # convert to rupees
        order_price = round(order_price / tick_size_paise) * tick_size_paise
    else:
        order_price = 0

    exchange_upper = (exchange or "NSE").strip().upper()
    exchange_val = "BSE" if exchange_upper == "BSE" else "NSE"

    payload: dict[str, Any] = {
        "ref_id": int(ref_id),
        "order_type": "ORDER_TYPE_REGULAR",
        "order_qty": int(qty),
        "order_side": order_side,
        "order_delivery_type": order_delivery_type,
        "validity_type": validity_type,
        "price_type": price_type_val,
        "exchange": exchange_val,
    }
    if price_type_val == "LIMIT":
        payload["order_price"] = order_price
    if tag is not None:
        payload["tag"] = str(tag)

    nubra = ensure_nubra()
    from nubra_python_sdk.trading.trading_data import NubraTrader

    trade = NubraTrader(nubra, version="V2")
    print(f"[nubra_order] create_order payload: {payload}", flush=True)
    with _NUBRA_ORDER_LOCK:
        result = trade.create_order(payload)

    # Build a simple dict for callers (executor, tests); support both object and dict response
    def _get(name: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    out: dict[str, Any] = {
        "order_id": _get("order_id"),
        "exchange_order_id": _get("exchange_order_id"),
        "ref_id": _get("ref_id", ref_id),
        "order_status": _get("order_status"),
    }
    if _get("filled_qty") is not None:
        out["filled_qty"] = _get("filled_qty")
    if _get("avg_filled_price") is not None:
        out["avg_filled_price"] = _get("avg_filled_price")
    return out


def place_order_nubra_by_key(
    key_name: str,
    side: str,
    qty: int,
    price_rupees: float,
    *,
    instrument_dict: dict[str, int] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Place a single order by key_name (e.g. ADANIGREEN_920_CE).
    Resolves key_name -> ref_id via instrument_dict, then calls place_order_nubra.
    """
    if instrument_dict is None:
        from instrument_dict_nubra import ensure_instrument_dict
        instrument_dict = ensure_instrument_dict()
    ref_id = instrument_dict.get(key_name)
    if ref_id is None:
        raise ValueError(f"key_name {key_name!r} not in instrument_dict")
    return place_order_nubra(ref_id, side, qty, price_rupees, **kwargs)


if __name__ == "__main__":
    # Minimal test: require ref_id and price (no real order unless you pass real values).
    import sys
    if len(sys.argv) >= 5:
        ref_id = int(sys.argv[1])
        side = sys.argv[2]
        qty = int(sys.argv[3])
        price_rupees = float(sys.argv[4])
        print(f"Placing {side} {qty} @ {price_rupees} (ref_id={ref_id})...")
        r = place_order_nubra(ref_id, side, qty, price_rupees)
        print("Result:", r)
    else:
        print("Usage: python nubra_order.py <ref_id> <BUY|SELL> <qty> <price_rupees>")
        print("Example: python nubra_order.py 1069800 BUY 600 95.50")
