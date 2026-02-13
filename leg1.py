# leg1.py - Leg 1 strategy logic (Nubra: uses marketdata_store, strategy_helpers, tsl_*, position_management)
"""
Leg 1 strategy module.
Handles Leg 1 (notional entry) logic including arming, TSL processing, and SL checks.
"""

import logging
from datetime import datetime
from marketdata_store import market_data

from strategy_helpers import (
    get_symbol, _calc_sl_level, _ltp_from_tick, _tsl_prefix, _parse_start_time
)
from tsl_processor import process_tsl_logic
from tsl_manager import (
    initialize_tsl_state, set_tsl_baselines, reset_tsl_state, get_tsl_config
)
from position_management import check_sl_hit

log = logging.getLogger("strategy")


def leg1_step_start_time(
    stock, strike, option_type,
    trade_direction='SELL', start_time_str="09:20:00",
    SLval=0.0, SLtype="POINTS",
    TSLarm=None, TSLmove=None, TSLtype=None,
    state=None, continuous_profiling=False,
    resume_emit: bool = False,
    pair_name: str | None = None,
):
    is_long = trade_direction.upper() == "BUY"
    option_type = option_type.upper()
    _p = _tsl_prefix(pair_name)

    if state is None:
        state = {
            "armed": False, "completed": False,
            "entry": None, "spot_entry": None,
            "sl": None, "symbol": None,
        }
        initialize_tsl_state(state, TSLarm, TSLmove, TSLtype)

    if resume_emit and state.get("armed") and not state.get("completed") and not state.get("_resume_banner_done", False):
        events = [{
            "type": "leg1_armed",
            "symbol": state["symbol"],
            "entry_price": state["entry"],
            "spot_entry": state["spot_entry"],
            "sl_level": state["sl"],
            "sltype": SLtype,
            "tsltype": TSLtype
        }]
        state["_resume_banner_done"] = True
        return state, events

    if state.get("completed", False):
        return state, []

    if not state["armed"]:
        state["symbol"] = get_symbol(stock, strike, "CE" if option_type == "C" else "PE")

    hh, mm, ss = _parse_start_time(start_time_str)
    now = datetime.now().time()
    past_start = (now.hour, now.minute, now.second) >= (hh, mm, ss)
    events = []

    ltp = None
    spot_ltp = None

    if (not state["armed"]) and past_start:
        tick = market_data.get(state["symbol"])
        spot_tick = market_data.get(stock.upper())
        ltp = _ltp_from_tick(tick)
        spot_ltp = _ltp_from_tick(spot_tick)
        if ltp is not None and spot_ltp is not None:
            state["armed"] = True
            state["entry"] = float(ltp)
            state["spot_entry"] = float(spot_ltp)
            set_tsl_baselines(state, state["entry"], state["spot_entry"])
            state["sl"] = _calc_sl_level(state["entry"], state["spot_entry"], SLval, SLtype, is_long, option_type)
            state["is_long"] = is_long
            state["option_type"] = option_type
            state["entry_side"] = "BUY" if is_long else "SELL"
            state["exit_side"] = "SELL" if is_long else "BUY"
            state["SLtype"] = SLtype
            events.append({
                "type": "leg1_armed", "symbol": state["symbol"],
                "entry_price": state["entry"], "spot_entry": state["spot_entry"],
                "sl_level": state["sl"], "sltype": SLtype, "tsltype": TSLtype
            })
        return state, events

    if state["armed"]:
        tick = market_data.get(state["symbol"])
        spot_tick = market_data.get(stock.upper())
        ltp = _ltp_from_tick(tick)
        spot_ltp = None
        try:
            spot_ltp = _ltp_from_tick(spot_tick)
        except Exception:
            spot_ltp = None
        if ltp is None or spot_ltp is None:
            return state, events

        if ltp == "N/A" or spot_ltp == "N/A":
            return state, events

        try:
            ltp = float(ltp)
            spot_ltp = float(spot_ltp)
        except (ValueError, TypeError):
            return state, events

        TSLarm, TSLmove, TSLtype = get_tsl_config(state, TSLarm, TSLmove, TSLtype)
        SLtype = state.get("SLtype") or SLtype
        is_long = state.get("is_long", is_long)
        option_type = state.get("option_type", option_type)

        process_tsl_logic(
            state, ltp, spot_ltp, is_long, option_type,
            TSLarm, TSLmove, TSLtype, SLtype, pair_name, "Leg1"
        )

        hit, ref_price = check_sl_hit(state, ltp, spot_ltp, SLtype)

        if hit:
            events.append({
                "type": "leg1_sl_hit", "symbol": state["symbol"],
                "ref_price": ref_price, "sl_level": state["sl"],
                "entry_price": state["entry"], "peak": state.get("peak", 0)
            })
            state.update({
                "armed": False, "completed": True,
                "entry": None, "spot_entry": None,
                "sl": None, "symbol": None
            })
            reset_tsl_state(state)

    return state, events


def leg1_force_exit(state: dict) -> dict:
    from position_management import _force_exit_generic
    return _force_exit_generic(state)
