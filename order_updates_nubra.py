#!/usr/bin/env python3
"""
Phase 4: Nubra realtime order/trade updates → order_state (socket_state equivalent).
WebSocket runs in a daemon thread; order_state is updated from on_order_update and on_trade_update.
Compatible with executor: get_socket_state(), update_socket_state(), get_order_state().
"""

import threading
import time
from typing import Any, Optional

# Same shape as Oswal socket_state: order_id (int) -> { filled, status, avg_px, ts }
order_state: dict[int, dict] = {}
_order_lock = threading.Lock()

_socket_connected = False
_socket_started = False
_socket_instance = None
_start_lock = threading.Lock()


def _get_attr(msg: Any, name: str, default: Any = None) -> Any:
    """Get attribute from wrapper object or dict."""
    if msg is None:
        return default
    if isinstance(msg, dict):
        return msg.get(name, default)
    return getattr(msg, name, default)


def _normalize_status(status: Any) -> str:
    """Map Nubra OrderStatusEnum to short uppercase status (PENDING, FILLED, etc.)."""
    if status is None:
        return "PENDING"
    s = str(status).strip().upper()
    # Strip enum prefix if present, e.g. ORDER_STATUS_FILLED -> FILLED
    if s.startswith("ORDER_STATUS_"):
        s = s.replace("ORDER_STATUS_", "", 1)
    return s if s else "PENDING"


def _apply_update(msg: Any) -> None:
    """Update order_state from an OrderInfoWrapper or AckInfoWrapper (order/trade update)."""
    oid = _get_attr(msg, "order_id")
    if oid is None:
        return
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        return
    filled_qty = _get_attr(msg, "filled_qty")
    status = _get_attr(msg, "order_status")
    avg_price = _get_attr(msg, "avg_price")  # May be in paise
    if filled_qty is not None:
        try:
            filled_qty = int(filled_qty)
        except (TypeError, ValueError):
            filled_qty = None
    if avg_price is not None:
        try:
            avg_price = float(avg_price)
            # If price looks like paise (e.g. 9550), convert to rupees for consistency
            if avg_price > 0 and avg_price < 1e7 and avg_price == int(avg_price):
                avg_price = round(avg_price / 100.0, 2)
        except (TypeError, ValueError):
            avg_price = None
    status_str = _normalize_status(status)
    update_socket_state(oid, filled_qty or 0, status_str, avg_px=avg_price)


def update_socket_state(
    app_order_id: int,
    filled_qty: int,
    status: str,
    avg_px: Optional[float] = None,
) -> None:
    """
    Update order_state for an order (thread-safe).
    Used to seed just-placed orders (e.g. PENDING_LOCAL) and by WebSocket callbacks.
    Filled quantity is always max(old, new) to handle out-of-order events.
    """
    try:
        oid = int(app_order_id)
    except (TypeError, ValueError):
        return
    with _order_lock:
        cur = order_state.get(oid) or {"filled": 0, "status": "PENDING", "avg_px": None, "ts": 0.0}
        prev_filled = int(cur.get("filled") or 0)
        new_filled = int(filled_qty or 0)
        cur["filled"] = max(prev_filled, max(0, new_filled))
        cur["status"] = str(status or cur.get("status") or "PENDING").strip().upper()
        if avg_px is not None:
            try:
                cur["avg_px"] = float(avg_px)
            except (TypeError, ValueError):
                pass
        cur["ts"] = time.time()
        order_state[oid] = cur


def get_order_state(order_id: int) -> Optional[dict]:
    """Return copy of state for one order, or None if not found."""
    with _order_lock:
        st = order_state.get(int(order_id))
        return dict(st) if st else None


def get_socket_state() -> dict[int, dict]:
    """Return copy of full order_state (same API as Oswal get_socket_state)."""
    with _order_lock:
        return {k: dict(v) for k, v in order_state.items()}


def is_socket_connected() -> bool:
    """True if order-updates WebSocket is connected."""
    return _socket_connected


def _on_order_update(msg: Any) -> None:
    _apply_update(msg)


def _on_trade_update(msg: Any) -> None:
    _apply_update(msg)


def _on_connect(msg: Any) -> None:
    global _socket_connected
    _socket_connected = True
    print(f"[order_updates_nubra] Connected: {msg}", flush=True)


def _on_close(reason: Any) -> None:
    global _socket_connected
    _socket_connected = False
    print(f"[order_updates_nubra] Closed: {reason}", flush=True)


def _on_error(err: Any) -> None:
    print(f"[order_updates_nubra] Error: {err}", flush=True)


def start_order_updates_socket() -> None:
    """
    Start Nubra order/trade updates WebSocket in a daemon thread.
    Idempotent: does nothing if already started.
    """
    global _socket_started, _socket_instance
    with _start_lock:
        if _socket_started:
            return
        _socket_started = True

    from nubra_client import ensure_nubra
    from nubra_python_sdk.ticker import orderupdate

    nubra = ensure_nubra()
    _socket_instance = orderupdate.OrderUpdate(
        client=nubra,
        on_order_update=_on_order_update,
        on_trade_update=_on_trade_update,
        on_connect=_on_connect,
        on_close=_on_close,
        on_error=_on_error,
    )

    def _run() -> None:
        try:
            _socket_instance.connect()
            _socket_instance.keep_running()
        except Exception as e:
            print(f"[order_updates_nubra] Thread error: {e}", flush=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print("[order_updates_nubra] WebSocket thread started (order/trade updates).", flush=True)


def close_order_updates_socket() -> None:
    """Close the order-updates WebSocket if running."""
    global _socket_connected, _socket_instance
    if _socket_instance is not None and hasattr(_socket_instance, "close"):
        try:
            _socket_instance.close()
        except Exception:
            pass
        _socket_instance = None
    _socket_connected = False


if __name__ == "__main__":
    start_order_updates_socket()
    print("Order updates socket started. get_socket_state() / get_order_state(order_id) available.")
    try:
        while True:
            time.sleep(5)
            with _order_lock:
                n = len(order_state)
            print(f"[order_updates_nubra] order_state size={n} connected={_socket_connected}", flush=True)
    except KeyboardInterrupt:
        close_order_updates_socket()
        print("Stopped.")
