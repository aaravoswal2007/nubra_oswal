# leg2.py - Leg 2 strategy logic (Nubra: executor_nubra_bridge, configloader.get_stock_steps)
"""
Leg 2 strategy module.
Handles Leg 2 (real trade) logic including entry execution, TSL processing, SL checks, and time-based square-off.
"""

import logging
from datetime import datetime
from marketdata_store import market_data

from strategy_helpers import (
    get_symbol, _calc_sl_level, _ltp_from_tick, _tsl_prefix,
    _squareoff_deadline
)
from tsl_processor import process_tsl_logic
from tsl_manager import (
    initialize_tsl_state, set_tsl_baselines, get_tsl_config, log_tsl_configuration
)
from position_management import check_sl_hit

log = logging.getLogger("strategy")


def _get_stock_steps():
    from configloader import get_stock_steps
    return get_stock_steps()


def leg2_start(
    stock, strike, option_type, trade_direction,
    SLval, SLtype, lots=1, modify_time=5, market_order=75,
    TSLarm=None, TSLmove=None, TSLtype=None,
    squareoff_time=None, squareoff_day_offset=None,
    squareoff_time_next_day="10:30:00",
    continuous_profiling=False,
    splice_lots: int = 5,
    on_progress=None,
    pair_name: str | None = None,
):
    option_type = option_type.upper()
    is_long = trade_direction.upper() == "BUY"

    symbol = get_symbol(stock, strike, "CE" if option_type == "C" else "PE")
    log_tsl_configuration(symbol, TSLarm, TSLmove, TSLtype, option_type, is_long, pair_name, "Leg2")

    tick = market_data.get(symbol)
    spot_tick = market_data.get(stock.upper())
    ltp = _ltp_from_tick(tick)
    spot_ltp = _ltp_from_tick(spot_tick)

    so_time = squareoff_time if squareoff_time else squareoff_time_next_day
    so_offset = 1 if (squareoff_day_offset is None) else int(squareoff_day_offset)

    if ltp is None or spot_ltp is None:
        return None

    entry = float(ltp)
    spot_entry = float(spot_ltp)
    sl = _calc_sl_level(entry, spot_entry, SLval, SLtype, is_long, option_type)

    stock_steps = _get_stock_steps()
    lot_size = stock_steps.get(stock.upper(), {}).get("lot_size", 600)
    qty = int(lot_size) * int(lots)

    state = {
        "symbol": symbol,
        "stock": stock.upper(),
        "option_type": option_type,
        "pair_name": pair_name,

        "is_long": is_long,
        "entry_side": "BUY" if is_long else "SELL",
        "exit_side": "SELL" if is_long else "BUY",

        "entry": entry,
        "spot_entry": spot_entry,
        "sl": sl,
        "SLtype": SLtype,
        "TSLarm": TSLarm,
        "TSLmove": TSLmove,
        "TSLtype": TSLtype or SLtype,

        "position_open": True,
        "exit_reason": None,
        "opened_at": datetime.now().isoformat(),
        "deadline": _squareoff_deadline(so_time, so_offset).isoformat(),
        "continuous_profiling": continuous_profiling,

        "lots": int(lots),
        "qty": int(qty),
        "lot_size": int(lot_size),

        "splice_lots": int(splice_lots),
        "modify_time": float(modify_time),
        "market_order": float(market_order),

        "latest_opt_ltp": float(entry),
        "latest_spot_ltp": float(spot_entry),

        "lots_total": int(lots),
        "fills": {
            "lots_filled": 0,
            "qty_filled": 0,
            "avg_entry": None,
            "entry_time": None,
        },

        "_exit_in_progress": False,
        "_exit_thread": None,
        "_on_progress": on_progress,
    }

    initialize_tsl_state(state, TSLarm, TSLmove, TSLtype)
    set_tsl_baselines(state, entry, spot_entry)

    try:
        from executor_nubra_bridge import place_order_splices_mid_ltq
        print("[STRATEGY] Using Nubra executor (executor_nubra_bridge.place_order_splices_mid_ltq)")

        side = "BUY" if is_long else "SELL"

        def _emit_progress_from_exec(e: dict):
            if not on_progress:
                return
            try:
                progress_data = {
                    "type": "lot_fill",
                    "phase": "entry",
                    "symbol": e.get("symbol"),
                    "side": e.get("side"),
                    "lot_index": int(e.get("lot_index") or e.get("lots_filled") or 0),
                    "lots_done": int(e.get("lots_filled") or 0),
                    "lots_total": int(e.get("lots_total") or lots),
                    "lot_size": int(e.get("lot_size") or lot_size),
                    "avg_px_hint": e.get("avg_px"),
                }
                on_progress(progress_data)
            except Exception as ex:
                print(f"[Main_strategy] _emit_progress_from_exec error: {ex}")

        try:
            place_order_splices_mid_ltq(
                key_name=symbol,
                side=side,
                lots=int(lots),
                lot_size=int(lot_size),
                splice_lots=int(splice_lots),
                modify_interval_sec=float(modify_time),
                poll_interval_sec=0.10,
                aggressive_after_sec=None,
                product_type="NRML",
                phase="entry",
                on_lot_filled=_emit_progress_from_exec,
            )
        except TypeError:
            try:
                place_order_splices_mid_ltq(
                    key_name=symbol,
                    side=side,
                    lots=int(lots),
                    lot_size=int(lot_size),
                    splice_lots=int(splice_lots),
                    modify_interval_sec=float(modify_time),
                    poll_interval_sec=0.10,
                    aggressive_after_sec=None,
                    on_progress=on_progress,
                )
            except TypeError:
                place_order_splices_mid_ltq(
                    key_name=symbol,
                    side=side,
                    lots=int(lots),
                    lot_size=int(lot_size),
                    splice_lots=int(splice_lots),
                    modify_interval_sec=float(modify_time),
                    poll_interval_sec=0.10,
                    aggressive_after_sec=None,
                )

    except ImportError as e:
        raise RuntimeError("executor_nubra_bridge not found") from e

    log.info(f"LEG2 ARMED: {symbol} entry={entry} qty={qty} lot_size={lot_size} spot={spot_entry} sl={sl} deadline={state['deadline']}")
    return state


def leg2_step(state, paused=False):
    events = []
    if not state.get("position_open", False):
        return state, events

    if paused:
        sym = state["symbol"]
        stk = state["stock"]
        tick = market_data.get(sym)
        spot_tick = market_data.get(stk)
        ltp = _ltp_from_tick(tick)
        spot_ltp = _ltp_from_tick(spot_tick)
        if ltp is not None and spot_ltp is not None:
            state["latest_opt_ltp"] = float(ltp)
            state["latest_spot_ltp"] = float(spot_ltp)
            _update_mtm(state, ltp)
        return state, events

    sym = state["symbol"]
    stk = state["stock"]
    tick = market_data.get(sym)
    spot_tick = market_data.get(stk)
    ltp = _ltp_from_tick(tick)
    spot_ltp = _ltp_from_tick(spot_tick)
    if ltp is None or spot_ltp is None:
        return state, events

    state["latest_opt_ltp"] = float(ltp)
    state["latest_spot_ltp"] = float(spot_ltp)
    _update_mtm(state, ltp)

    is_long = state["is_long"]
    option_type = state["option_type"]
    SLtype = state.get("SLtype")
    TSLarm, TSLmove, TSLtype = get_tsl_config(state, None, None, None)
    pair_name = state.get("pair_name")

    process_tsl_logic(
        state, ltp, spot_ltp, is_long, option_type,
        TSLarm, TSLmove, TSLtype, SLtype, pair_name, "Leg2"
    )

    hit, ref_price = check_sl_hit(state, ltp, spot_ltp, SLtype)

    if hit:
        state["position_open"] = False
        state["exit_reason"] = "SL_HIT"
        events.append({
            "type": "leg2_sl_hit", "symbol": sym,
            "ref_price": ref_price, "sl_level": state["sl"],
            "entry": state["entry"], "qty": state["qty"]
        })
        return state, events

    if datetime.now() >= datetime.fromisoformat(state["deadline"]):
        state["position_open"] = False
        state["exit_reason"] = "TIME_SQUAREOFF"
        events.append({
            "type": "leg2_time_squareoff", "symbol": sym,
            "deadline": state["deadline"],
            "qty": state["qty"]
        })
        return state, events

    return state, events


def _update_mtm(state: dict, ltp: float):
    try:
        basis = (state.get("fills") or {}).get("avg_entry") or state["entry"]
        open_lots = int((state.get("fills") or {}).get("lots_filled") or 0)
        lot_sz = int(state.get("lot_size", 0) or 0)
        if lot_sz and open_lots:
            if state["is_long"]:
                state["mtm"] = float(ltp - basis) * lot_sz * open_lots
            else:
                state["mtm"] = float(basis - ltp) * lot_sz * open_lots
        else:
            state["mtm"] = 0.0
    except Exception:
        state["mtm"] = 0.0


def leg2_force_exit(state: dict) -> dict:
    from position_management import _force_exit_generic
    return _force_exit_generic(state)
