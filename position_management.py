# position_management.py - Position management utilities
"""
Position management module.
Contains shared functions for managing positions across leg1 and leg2.
Uses executor_nubra_bridge for square-off (Nubra backend).
"""

import threading
from datetime import datetime


def _mark_closed(state: dict, reason: str) -> dict:
    if "position_open" in state:
        state["position_open"] = False
        state["lots"] = 0
        state["lots_total"] = 0
        if "fills" in state:
            state["fills"]["lots_filled"] = 0
            state["fills"]["qty_filled"] = 0
    else:
        state["armed"] = False
        state["completed"] = True
    state.setdefault("exit_reason", reason)
    return state


def _force_exit_generic(state: dict) -> dict:
    try:
        symbol = state.get("symbol")
        if not symbol:
            return _mark_closed(state, "USER_SQUAREOFF")

        is_long = bool(state.get("is_long"))
        exit_side = "SELL" if is_long else "BUY"

        lot_size = int(state.get("lot_size") or 0)
        fills = state.get("fills") or {}
        lots_total = int(state.get("lots") or state.get("lots_total") or 0)
        lots_filled = int(fills.get("lots_filled", 0) or 0)
        lots_open = lots_filled

        if "net_lots" in state and state["net_lots"] > 0:
            lots_open = state["net_lots"]
            print(f"[EXECUTOR] Using net_lots for square off: {lots_open} lots")
        else:
            print(f"[EXECUTOR] Using lots_filled for square off: {lots_open} lots")

        if lot_size <= 0:
            print(f"[EXECUTOR] Skip exit for {symbol}: no execution profile (not a traded leg)")
            return _mark_closed(state, "USER_SQUAREOFF")

        if lots_open <= 0:
            print(f"[EXECUTOR] Skip exit for {symbol}: state shows 0 open lots")
            return _mark_closed(state, "USER_SQUAREOFF")

        if state.get("_exit_in_progress"):
            print(f"[EXECUTOR] Exit already in progress for {symbol}; ignoring duplicate request.")
            return state

        splice_lots = int(state.get("splice_lots", 5))
        modify_interval_sec = float(state.get("modify_time", 5.0))
        aggressive_after_sec = None

        from executor_nubra_bridge import place_order_splices_mid_ltq

        on_progress = state.get("_on_progress") if callable(state.get("_on_progress")) else None

        def _on_exit_lot(evt: dict):
            try:
                prev_filled = int((state.get("fills") or {}).get("lots_filled", 0) or 0)
                incoming_lots_cum = int(evt.get("lots_filled") or evt.get("lot_index") or prev_filled)
                new_lots_total_filled = max(prev_filled, incoming_lots_cum)
                delta = new_lots_total_filled - prev_filled
                if delta > 0:
                    state.setdefault("fills", {})
                    state["fills"]["lots_filled"] = prev_filled + delta
                    state["fills"]["qty_filled"] = int(state["fills"]["lots_filled"]) * lot_size
                    if state["fills"]["entry_time"] is None:
                        state["fills"]["entry_time"] = datetime.now().isoformat(timespec="seconds")
                    if state["fills"]["lots_filled"] >= lots_total:
                        state["position_open"] = False
                if callable(on_progress):
                    try:
                        on_progress({
                            "type": "lot_fill",
                            "phase": "exit",
                            "symbol": evt.get("symbol"),
                            "side": evt.get("side"),
                            "lot_index": int(evt.get("lot_index") or new_lots_total_filled),
                            "lots_done": int(evt.get("lots_filled") or new_lots_total_filled),
                            "lots_total": int(evt.get("lots_total") or lots_total),
                            "lot_size": int(evt.get("lot_size") or lot_size),
                            "avg_px_hint": evt.get("avg_px"),
                        })
                    except Exception:
                        pass
            except Exception as ex:
                print(f"[EXECUTOR] _on_exit_lot error: {ex}")

        state["exit_reason"] = state.get("exit_reason") or "USER_SQUAREOFF"
        state["_exit_in_progress"] = True

        def _exit_worker():
            try:
                place_order_splices_mid_ltq(
                    key_name=symbol,
                    side=exit_side,
                    lots=int(lots_open),
                    lot_size=lot_size,
                    splice_lots=splice_lots,
                    modify_interval_sec=modify_interval_sec,
                    poll_interval_sec=0.10,
                    aggressive_after_sec=aggressive_after_sec,
                    phase="exit",
                    on_lot_filled=_on_exit_lot,
                )
            except Exception as e:
                print(f"[EXECUTOR] Exit error for {symbol}: {e}")
                state["_exit_in_progress"] = False
                _mark_closed(state, "USER_SQUAREOFF")
                return
            try:
                final_filled = int((state.get("fills") or {}).get("lots_filled", 0) or 0)
                if final_filled >= lots_total:
                    state["position_open"] = False
                    state["exit_reason"] = state.get("exit_reason") or "USER_SQUAREOFF"
                else:
                    state["position_open"] = (final_filled < lots_total)
            except Exception:
                pass
            finally:
                state["_exit_in_progress"] = False

        t = threading.Thread(target=_exit_worker, daemon=True)
        state["_exit_thread"] = t
        t.start()

        return state

    except Exception as e:
        print(f"[EXECUTOR] Exit error for {state.get('symbol')}: {e}")
        return _mark_closed(state, "USER_SQUAREOFF")


def check_sl_hit(state: dict, ltp: float, spot_ltp: float, SLtype: str) -> tuple[bool, float]:
    is_long = state.get("is_long", False)
    option_type = state.get("option_type", "C")
    sl = state.get("sl")

    if sl is None:
        return False, None

    hit = False
    ref_price = None

    if SLtype == "POINTS":
        ref_price = ltp
        hit = (is_long and ref_price <= sl) or (not is_long and ref_price >= sl)
    else:
        ref_price = spot_ltp
        if option_type == "C":
            hit = (is_long and ref_price <= sl) or (not is_long and ref_price >= sl)
        elif option_type == "P":
            hit = (is_long and ref_price >= sl) or (not is_long and ref_price <= sl)

    return hit, ref_price
