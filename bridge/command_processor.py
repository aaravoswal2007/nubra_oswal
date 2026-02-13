"""
Command processing for Bridge.
Handles executor line parsing and print hook interception.
"""
import re
import builtins
from bridge.shared_state import _TL, _progress_seen
from bridge.state_wrappers import _get_json_positions
from bridge.progress_handler import _handle_progress
from marketdata_store import market_data

# Regex patterns for parsing executor output
_re_real_fill_child = re.compile(r"\[RealExec\].*FILL.*child\d+:\s*\+(\d+)(?:\s*@\s*([0-9.]+))?", re.IGNORECASE)
_re_real_fill_splice = re.compile(r"\[RealExec\].*FILL.*SPLICE\s+\d+:\s*\+(\d+)(?:\s*@\s*([0-9.]+))?", re.IGNORECASE)
_re_real_done_child = re.compile(r"\[RealExec\].*DONE\s+child\d+.*\+(\d+)(?:\s*@\s*([0-9.]+))?", re.IGNORECASE)
_re_real_fill_generic = re.compile(r"\[RealExec\].*FILL.*\+(\d+)(?:\s*@\s*([0-9.]+))?", re.IGNORECASE)


def _process_exec_line(line: str):
    """
    Fallback parser for executor prints.
    Keeps your legacy behavior intact. Disabled when _TL.phase is falsy.
    """
    pair = getattr(_TL, "pair", None)
    phase = getattr(_TL, "phase", None)   # "entry" | "exit"
    if not pair or not phase:
        return

    snap = _get_json_positions().get(pair)
    if not snap:
        return

    lot_size = int((snap.get("lot_size") or 1))

    def _fallback_px():
        # Try to get executed price from order book or socket state
        try:
            # First try to get from order book if available
            from InteractiveSocketExample import get_socket_state
            socket_state = get_socket_state()
            
            # Look for any filled orders for this symbol
            for order_id, order_data in socket_state.items():
                if order_data.get("status") == "FILLED" and order_data.get("avg_px"):
                    return float(order_data.get("avg_px"))
            
            # If no executed price available, fall back to LTP as last resort
            sym = snap.get("symbol")
            if sym:
                md = market_data.get(sym) or {}
                v = md.get("ltp")
                return float(v) if v is not None else 0.0
        except Exception:
            pass
        return 0.0

    def _apply_fill(qty_units: int, px: float | None):
        if px is None:
            px = _fallback_px()

        lots = int(round(float(qty_units) / float(lot_size))) if lot_size else 0
        # Reuse the new progress updater to keep logic in one place
        _handle_progress(pair, {
            "type": "lot_fill",
            "phase": phase,
            "symbol": snap.get("symbol"),
            "side": snap.get("side"),
            "lot_index": ( # convert to a cumulative lot index for de-dupe
                _progress_seen.get(pair, {}).get(phase, 0) + max(lots, 0)
            ),
            "lots_total": int(snap.get("lots_total") or 0),
            "lot_size": lot_size,
            "avg_px_hint": px,  # This will be used as executed price in the VWAP calculation
        })

    m = _re_real_fill_child.search(line)
    if m:
        qty = int(m.group(1)); px = float(m.group(2)) if m.group(2) else None
        _apply_fill(qty, px); return

    m = _re_real_fill_splice.search(line)
    if m:
        qty = int(m.group(1)); px = float(m.group(2)) if m.group(2) else None
        _apply_fill(qty, px); return

    m = _re_real_done_child.search(line)
    if m:
        qty = int(m.group(1)); px = float(m.group(2)) if m.group(2) else None
        _apply_fill(qty, px); return

    if "[RealExec]" in line:
        m = _re_real_fill_generic.search(line)
        if m:
            qty = int(m.group(1)); px = float(m.group(2)) if m.group(2) else None
            _apply_fill(qty, px); return


# Intercept ALL prints → forward to UI + parse fills (fallback)
_orig_print = builtins.print
def _print_hook(*args, **kwargs):
    try:
        msg = " ".join(str(a) for a in args)
        
        # Filter out packet queue messages
        if any(phrase in msg.lower() for phrase in [
            "packet queue is empty, aborting",
            "packet queue is empty",
            "aborting",
            "queue is empty"
        ]):
            return  # Don't print or process these messages
        
        _process_exec_line(msg)
    except Exception:
        pass
    return _orig_print(*args, **kwargs)


def setup_print_hook():
    """Setup the print hook to intercept all prints"""
    builtins.print = _print_hook

