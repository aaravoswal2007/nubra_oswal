# tsl_processor.py - Core TSL processing logic
"""
Trailing Stop Loss (TSL) processor module.
Contains the core logic for calculating runup and processing TSL triggers.
"""

import logging

log = logging.getLogger("strategy")


# ===== TSL Trail Calculation =====
def _trail_sl(current_price, spot_price, move_up, tsl_type, is_long, option_type):
    """
    Calculate trailing stop loss adjustment amount.
    """
    option_type = option_type.upper()

    if tsl_type == "POINTS":
        return move_up if is_long else -move_up

    elif tsl_type == "UNDERLYING_POINTS":
        if option_type == "C":
            return move_up if is_long else -move_up
        else:
            return -move_up if is_long else move_up

    elif tsl_type == "UNDERLYING_PERCENT":
        if option_type == "C":
            return spot_price * (1 + move_up / 100.0) if is_long else spot_price * (1 - move_up / 100.0)
        else:
            return spot_price * (1 - move_up / 100.0) if is_long else spot_price * (1 + move_up / 100.0)

    else:
        raise ValueError(f"Unknown TSLtype: {tsl_type}")


def calculate_runup(state: dict, ltp: float, spot_ltp: float, is_long: bool, option_type: str, TSLtype: str = None, SLtype: str = None) -> float:
    effective_tsl_type = TSLtype if TSLtype is not None else SLtype

    if effective_tsl_type in ["UNDERLYING_POINTS", "UNDERLYING_PERCENT"]:
        baseline_ul = state.get("baseline_spot")
        if baseline_ul is None:
            baseline_ul = state.get("spot_entry")
            if baseline_ul is None:
                baseline_ul = spot_ltp
            state["baseline_spot"] = baseline_ul
        underlying_movement = spot_ltp - baseline_ul
        if option_type == "C":
            runup = underlying_movement if is_long else -underlying_movement
        else:
            runup = -underlying_movement if is_long else underlying_movement
    else:
        baseline_prem = state.get("baseline_premium")
        if baseline_prem is None:
            baseline_prem = state.get("entry")
            if baseline_prem is None:
                baseline_prem = ltp
            state["baseline_premium"] = baseline_prem
        runup = (ltp - baseline_prem) if is_long else (baseline_prem - ltp)

    return runup


def process_tsl_logic(
    state: dict,
    ltp: float,
    spot_ltp: float,
    is_long: bool,
    option_type: str,
    TSLarm: float = None,
    TSLmove: float = None,
    TSLtype: str = None,
    SLtype: str = None,
    pair_name: str = None,
    leg_name: str = "Leg"
) -> bool:
    from strategy_helpers import _tsl_prefix

    _p = _tsl_prefix(pair_name)
    runup = calculate_runup(state, ltp, spot_ltp, is_long, option_type, TSLtype, SLtype)

    if runup > 0 and runup > state.get("peak", 0):
        state["peak"] = runup

    if (TSLarm is not None and TSLmove is not None and TSLarm > 0 and TSLmove > 0 and
        state.get("peak", 0) >= TSLarm):

        should_trigger_tsl = False

        if not state.get('_tsl_armed'):
            state['_tsl_armed'] = True
            effective_tsl_type = TSLtype if TSLtype is not None else SLtype
            if effective_tsl_type in ["UNDERLYING_POINTS", "UNDERLYING_PERCENT"]:
                state['_tsl_trigger_level'] = spot_ltp
            else:
                state['_tsl_trigger_level'] = ltp
            log.info(f"{leg_name.upper()} TSL ARMED: {state.get('symbol', 'UNKNOWN')} peak={state['peak']:.2f} trigger_level={state['_tsl_trigger_level']:.2f} TSLtype={TSLtype} SLtype={SLtype}")
            print(f"{_p}[TSL] {leg_name} {state.get('symbol', 'UNKNOWN')} TSL ARMED at peak {state['peak']:.2f}")
            if state.get("peak", 0) >= TSLarm:
                should_trigger_tsl = True
                log.info(f"{leg_name.upper()} TSL IMMEDIATE TRIGGER: {state.get('symbol', 'UNKNOWN')} peak={state['peak']:.2f} >= TSLarm={TSLarm:.2f}")

        if not should_trigger_tsl:
            should_trigger_tsl = _check_tsl_trigger_condition(
                state, ltp, spot_ltp, is_long, option_type, TSLarm, TSLtype, SLtype
            )

        if should_trigger_tsl:
            return _execute_tsl_trigger(
                state, ltp, spot_ltp, is_long, option_type, TSLarm, TSLmove, TSLtype, SLtype, pair_name, leg_name
            )

    return False


def _check_tsl_trigger_condition(
    state: dict,
    ltp: float,
    spot_ltp: float,
    is_long: bool,
    option_type: str,
    TSLarm: float,
    TSLtype: str = None,
    SLtype: str = None
) -> bool:
    effective_tsl_type = TSLtype if TSLtype is not None else SLtype

    if effective_tsl_type == "UNDERLYING_POINTS":
        if state.get('_tsl_trigger_level') is None:
            state['_tsl_trigger_level'] = spot_ltp
        underlying_movement = spot_ltp - state['_tsl_trigger_level']
        if option_type == "C":
            favorable_movement = underlying_movement if is_long else -underlying_movement
        else:
            favorable_movement = -underlying_movement if is_long else underlying_movement
        if not state.get('_tsl_debug_logged') or (state.get("peak", 0) > 0 and state.get("peak", 0) % 5 == 0):
            state['_tsl_debug_logged'] = True
        if favorable_movement >= TSLarm:
            return True

    elif effective_tsl_type == "UNDERLYING_PERCENT":
        if state.get('_tsl_trigger_level') is None:
            state['_tsl_trigger_level'] = spot_ltp
        underlying_movement = spot_ltp - state['_tsl_trigger_level']
        if option_type == "C":
            favorable_movement = underlying_movement if is_long else -underlying_movement
        else:
            favorable_movement = -underlying_movement if is_long else underlying_movement
        if state['_tsl_trigger_level'] != 0:
            favorable_movement_pct = (favorable_movement / state['_tsl_trigger_level']) * 100.0
        else:
            favorable_movement_pct = 0
        if favorable_movement_pct >= TSLarm:
            return True

    else:
        current_premium = ltp
        premium_movement = current_premium - state.get("_tsl_trigger_level", current_premium)
        favorable_movement = premium_movement if is_long else -premium_movement
        if favorable_movement >= TSLarm:
            return True

    return False


def _execute_tsl_trigger(
    state: dict,
    ltp: float,
    spot_ltp: float,
    is_long: bool,
    option_type: str,
    TSLarm: float,
    TSLmove: float,
    TSLtype: str = None,
    SLtype: str = None,
    pair_name: str = None,
    leg_name: str = "Leg"
) -> bool:
    from strategy_helpers import _tsl_prefix

    _p = _tsl_prefix(pair_name)
    old_sl = state["sl"]
    effective_type = TSLtype if TSLtype is not None else SLtype

    if effective_type == "POINTS":
        if is_long:
            new_sl = state["sl"] + TSLmove
        else:
            new_sl = state["sl"] - TSLmove
    elif effective_type == "UNDERLYING_POINTS":
        if option_type == "C":
            if is_long:
                new_sl = state["sl"] + TSLmove
            else:
                new_sl = state["sl"] - TSLmove
        else:
            if is_long:
                new_sl = state["sl"] - TSLmove
            else:
                new_sl = state["sl"] + TSLmove
    else:
        trail_amount = _trail_sl(ltp, spot_ltp, TSLmove, effective_type, is_long, option_type)
        new_sl = state["sl"] + trail_amount

    if is_long and option_type == "C":
        new_sl = max(state["sl"], new_sl)
    elif is_long and option_type == "P":
        new_sl = min(state["sl"], new_sl)
    elif not is_long and option_type == "C":
        new_sl = min(state["sl"], new_sl)
    elif not is_long and option_type == "P":
        new_sl = max(state["sl"], new_sl)

    if new_sl != old_sl:
        state["sl"] = new_sl
        old_peak = state.get("peak", 0)
        effective_tsl_type = TSLtype if TSLtype is not None else SLtype
        _update_baseline_after_trigger(state, spot_ltp, ltp, is_long, option_type, TSLarm, effective_tsl_type, pair_name, leg_name)
        state["peak"] = 0
        if effective_tsl_type in ["UNDERLYING_POINTS", "UNDERLYING_PERCENT"]:
            state['_tsl_trigger_level'] = spot_ltp
        else:
            state['_tsl_trigger_level'] = ltp
        log.info(f"{leg_name.upper()} TSL HIT: {state.get('symbol', 'UNKNOWN')} old_sl={old_sl:.2f} new_sl={new_sl:.2f} peak={old_peak:.2f} -> reset to 0 (baseline adjusted, trigger level reset) ltp={ltp:.2f}")
        print(f"{_p}[TSL] {leg_name} {state.get('symbol', 'UNKNOWN')} TSL HIT | old SL {old_sl:.2f} -> new SL {new_sl:.2f} | peak {old_peak:.2f} -> reset (baseline adjusted)")
        return True

    return False


def _update_baseline_after_trigger(
    state: dict,
    spot_ltp: float,
    ltp: float,
    is_long: bool,
    option_type: str,
    TSLarm: float,
    effective_tsl_type: str,
    pair_name: str = None,
    leg_name: str = "Leg"
):
    from strategy_helpers import _tsl_prefix

    _p = _tsl_prefix(pair_name)
    symbol = state.get('symbol', 'UNKNOWN')

    if effective_tsl_type in ["UNDERLYING_POINTS", "UNDERLYING_PERCENT"]:
        baseline_spot = state.get("baseline_spot")
        if baseline_spot is None:
            baseline_spot = state.get("spot_entry")
            if baseline_spot is None:
                baseline_spot = spot_ltp
        if option_type == "C":
            if is_long:
                state["baseline_spot"] = baseline_spot + TSLarm
            else:
                state["baseline_spot"] = baseline_spot - TSLarm
        else:
            if is_long:
                state["baseline_spot"] = baseline_spot - TSLarm
            else:
                state["baseline_spot"] = baseline_spot + TSLarm
        log.info(f"{leg_name.upper()} TSL Baseline Adjusted: {symbol} new_baseline_spot={state['baseline_spot']:.2f}")
        print(f"{_p}[TSL] {leg_name} {symbol} Baseline Spot: {state['baseline_spot']:.2f}")
    else:
        baseline_prem = state.get("baseline_premium")
        if baseline_prem is None:
            baseline_prem = state.get("entry")
            if baseline_prem is None:
                baseline_prem = ltp
        if is_long:
            state["baseline_premium"] = baseline_prem + TSLarm
        else:
            state["baseline_premium"] = baseline_prem - TSLarm
        log.info(f"{leg_name.upper()} TSL Baseline Adjusted: {symbol} new_baseline_premium={state['baseline_premium']:.2f}")
        print(f"{_p}[TSL] {leg_name} {symbol} Baseline Premium: {state['baseline_premium']:.2f}")
