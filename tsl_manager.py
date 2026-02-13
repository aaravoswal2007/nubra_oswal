# tsl_manager.py - TSL state management and initialization
"""
Trailing Stop Loss (TSL) manager module.
Handles TSL state initialization, configuration, and state management.
"""

import logging

log = logging.getLogger("strategy")


def initialize_tsl_state(state: dict, TSLarm: float = None, TSLmove: float = None, TSLtype: str = None):
    if "baseline_premium" not in state:
        state["baseline_premium"] = None
    if "baseline_spot" not in state:
        state["baseline_spot"] = None
    if "peak" not in state:
        state["peak"] = 0.0
    if "trail_history" not in state:
        state["trail_history"] = []
    if "TSLarm" not in state:
        state["TSLarm"] = TSLarm
    if "TSLmove" not in state:
        state["TSLmove"] = TSLmove
    if "TSLtype" not in state:
        state["TSLtype"] = TSLtype
    if "next_tsl_trigger" not in state:
        state["next_tsl_trigger"] = None


def set_tsl_baselines(state: dict, entry: float, spot_entry: float):
    state["baseline_premium"] = float(entry)
    state["baseline_spot"] = float(spot_entry)


def reset_tsl_state(state: dict):
    state["baseline_premium"] = None
    state["baseline_spot"] = None
    state["peak"] = 0.0
    state["trail_history"] = []
    state.pop("_tsl_armed", None)
    state.pop("_tsl_trigger_level", None)
    state.pop("_tsl_debug_logged", None)
    state.pop("next_tsl_trigger", None)


def get_tsl_config(state: dict, TSLarm: float = None, TSLmove: float = None, TSLtype: str = None):
    tsl_arm = float(state.get("TSLarm") or 0) if state.get("TSLarm") is not None else TSLarm
    tsl_move = float(state.get("TSLmove") or 0) if state.get("TSLmove") is not None else TSLmove
    tsl_type = state.get("TSLtype") or TSLtype
    return tsl_arm, tsl_move, tsl_type


def log_tsl_configuration(symbol: str, TSLarm: float = None, TSLmove: float = None, TSLtype: str = None,
                          option_type: str = None, is_long: bool = None, pair_name: str = None, leg_name: str = "Leg"):
    from strategy_helpers import _tsl_prefix

    _p = _tsl_prefix(pair_name)
    if TSLarm is not None and TSLmove is not None and TSLtype is not None:
        direction = "LONG" if is_long else "SHORT"
        log.info(f"{leg_name.upper()} TSL configured: {symbol} {TSLtype} | Arm={TSLarm:.2f} | Move={TSLmove:.2f} | Type={option_type} | Direction={direction}")
        print(f"{_p}[TSL] {leg_name} {symbol} TSL configured: {TSLtype} | Arm={TSLarm:.2f} | Move={TSLmove:.2f} | Type={option_type} | Direction={direction}")
    else:
        log.info(f"{leg_name.upper()} TSL disabled: {symbol} TSLarm={TSLarm}, TSLmove={TSLmove}, TSLtype={TSLtype}")
        print(f"{_p}[TSL] {leg_name} {symbol} TSL disabled: TSLarm={TSLarm}, TSLmove={TSLmove}, TSLtype={TSLtype}")
