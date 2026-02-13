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

# Same as Oswal executor_live: 0.05 rupees = 5 paise tick
PRICE_TICK = 0.05


def _round_tick(x: float) -> float:
    """Round price to nearest tick (0.05 rupees). Same as Oswal _round_tick."""
    return round(round(x / PRICE_TICK) * PRICE_TICK, 2)


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

    # Round price to nearest 0.05 rupees (same as Oswal); Nubra API expects order_price as integer (paise)
    if price_type_val == "LIMIT":
        order_price_rupees = _round_tick(float(price_rupees))
        order_price = int(round(order_price_rupees * 100))  # paise
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


def modify_order_nubra(
    order_id: int | str,
    price_rupees: float,
    qty: int,
    *,
    exchange: str = "NSE",
) -> dict[str, Any]:
    """
    Modify an existing Nubra order's price (and qty). Uses same lock as place_order.
    Uses modify_order_v2 with request dict per Nubra docs (order_price in paise, order_qty, exchange, order_type).
    """
    order_price_rupees = _round_tick(float(price_rupees))
    order_price_paise = int(round(order_price_rupees * 100))

    # Per Nubra docs: modify_order_v2(order_id=..., request={...}); ORDER_TYPE_REGULAR requires order_price, order_qty, exchange, order_type
    # Doc example uses string values; we send int for order_price/order_qty (paise) - if API rejects, try str()
    request: dict[str, Any] = {
        "order_price": order_price_paise,
        "order_qty": int(qty),
        "exchange": (exchange or "NSE").strip().upper(),
        "order_type": "ORDER_TYPE_REGULAR",
    }

    nubra = ensure_nubra()
    from nubra_python_sdk.trading.trading_data import NubraTrader

    trade = NubraTrader(nubra, version="V2")
    with _NUBRA_ORDER_LOCK:
        result = trade.modify_order_v2(order_id=int(order_id), request=request)

    # Wait for next order_update for this order_id via WebSocket (consider order modified when socket confirms)
    from order_updates_nubra import wait_for_order_update, get_order_state
    socket_confirmed = wait_for_order_update(int(order_id), timeout_sec=3.0)

    # If socket confirmed, compare the order_price in the update with what we sent.
    price_match = False
    state_px = None
    if socket_confirmed:
        st = get_order_state(int(order_id))
        if st is not None:
            state_px = st.get("order_px")
            try:
                if state_px is not None and abs(float(state_px) - float(order_price_rupees)) < 1e-6:
                    price_match = True
            except (TypeError, ValueError):
                price_match = False

    def _get(name: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    # Derive a simple status for callers:
    # - "ok"            : socket confirmed and broker price matches what we sent
    # - "timeout"       : no order_update seen within timeout (socket slow / disconnect)
    # - "price_mismatch": socket confirmed but broker price != what we sent
    if socket_confirmed and price_match:
        status = "ok"
    elif not socket_confirmed:
        status = "timeout"
    else:
        status = "price_mismatch"

    return {
        "order_id": _get("order_id", order_id),
        "message": _get("message"),
        "result": result,
        "status": status,
        "socket_confirmed": socket_confirmed,
        "price_match": price_match,
        "state_px": state_px,
        "sent_px": order_price_rupees,
    }


def cancel_order_nubra(order_id: int | str) -> dict[str, Any]:
    """
    Cancel a single Nubra order by ID (REST: DELETE orders/{order_id}).
    Uses the same lock as place/modify so all order API calls are serialized.
    Returns dict with "message" (e.g. "delete request pushed") or "error" on failure.
    """
    order_id = int(order_id)
    nubra = ensure_nubra()
    from nubra_python_sdk.trading.trading_data import NubraTrader

    trade = NubraTrader(nubra, version="V2")
    with _NUBRA_ORDER_LOCK:
        # SDK: cancel_order_by_id(order_id) → REST DELETE orders/{order_id}
        result = trade.cancel_order_by_id(order_id)
    # Normalize response to dict
    if hasattr(result, "message"):
        msg = getattr(result, "message", None)
    elif isinstance(result, dict):
        msg = result.get("message")
    else:
        msg = str(result) if result is not None else "delete request pushed"
    return {"order_id": order_id, "message": msg, "result": result}


if __name__ == "__main__":
    # CLI test harness intentionally left without logging to avoid noisy output in production environments.
    import sys
    if len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "cancel":
        if len(sys.argv) < 3:
            sys.exit(1)
        order_id = int(sys.argv[2])
        cancel_order_nubra(order_id)
    elif len(sys.argv) >= 5:
        ref_id = int(sys.argv[1])
        side = sys.argv[2]
        qty = int(sys.argv[3])
        price_rupees = float(sys.argv[4])
        place_order_nubra(ref_id, side, qty, price_rupees)
    else:
        # No-op when called without enough arguments.
        pass
