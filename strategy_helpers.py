# strategy_helpers.py - Same API as Oswal for bridge/leg1/leg2 compatibility.
# Uses nubra_oswal marketdata_store and configloader.get_stock_steps.
from datetime import datetime, timedelta

from marketdata_store import market_data


def _get_stock_steps():
    """Lazy load to avoid pulling heavy instruments_to_sub at import."""
    from configloader import get_stock_steps
    return get_stock_steps()


# ===== Symbol Resolver =====
def get_symbol(stock: str, strike: str, option_type: str) -> str:
    stock = stock.upper()
    option_type = option_type.upper()
    stock_steps = _get_stock_steps()
    spot_data = market_data.get(stock)
    if not spot_data:
        raise ValueError(f"No spot data found for stock: {stock}")
    spot_ltp = spot_data.get("ltp")
    if spot_ltp is None:
        raise ValueError(f"No spot LTP for stock: {stock}")
    if isinstance(spot_ltp, str) and "|" in spot_ltp:
        spot_ltp = float(spot_ltp.split("|")[0].strip())
    else:
        spot_ltp = float(spot_ltp)
    step = stock_steps.get(stock, {}).get("step", 100)
    atm_strike = int((spot_ltp + step / 2) // step) * step

    if strike == "ATM":
        offset = 0
    elif strike.startswith("ITM"):
        n = int(strike[3:])
        offset = -n if option_type == "CE" else n
    elif strike.startswith("OTM"):
        n = int(strike[3:])
        offset = n if option_type == "CE" else -n
    else:
        try:
            abs_strike = int(strike)
            return f"{stock}_{abs_strike}_{option_type}"
        except Exception:
            raise ValueError("Strike must be ATM, ITMx, OTMx, or an absolute integer strike")

    final_strike = atm_strike + (offset * step)
    return f"{stock}_{final_strike}_{option_type}"


# ===== Wait until spot data is ready =====
def wait_for_spot(stock: str):
    import time
    s = stock.upper()
    while market_data.get(s) is None:
        print(f"[Main_strategy] Waiting for {s} spot data...")
        time.sleep(0.05)
    print(f"[Main_strategy] {s} spot data ready:", market_data.get(s))


# ===== SL Calculation =====
def _calc_sl_level(entry_price, spot_entry, val, sl_type, is_long, option_type):
    option_type = option_type.upper()
    entry_price = float(entry_price or 0.0)
    spot_entry = float(spot_entry or 0.0)
    val = float(val or 0.0)

    if sl_type == "POINTS":
        return entry_price - val if is_long else entry_price + val
    elif sl_type == "UNDERLYING_POINTS" and option_type == "C":
        return spot_entry - val if is_long else spot_entry + val
    elif sl_type == "UNDERLYING_POINTS" and option_type == "P":
        return spot_entry + val if is_long else spot_entry - val
    elif sl_type == "UNDERLYING_PERCENT" and option_type == "C":
        return spot_entry * (1 - val / 100.0) if is_long else spot_entry * (1 + val / 100.0)
    elif sl_type == "UNDERLYING_PERCENT" and option_type == "P":
        return spot_entry * (1 + val / 100.0) if is_long else spot_entry * (1 - val / 100.0)
    else:
        raise ValueError(f"Unknown SLtype: {sl_type}")


# ===== Market Data Helpers =====
def _ltp_from_tick(tick):
    return tick.get("ltp") if tick else None


# ===== TSL Prefix Helper =====
def _tsl_prefix(pair_name: str | None) -> str:
    return f"[{pair_name}] " if pair_name else ""


# ===== Deadline Helpers =====
def _squareoff_deadline(squareoff_time: str, day_offset: int = 1):
    hh, mm, *rest = [int(x) for x in squareoff_time.split(":")]
    ss = rest[0] if rest else 0
    now = datetime.now()
    base = datetime(now.year, now.month, now.day, hh, mm, ss)
    return base + timedelta(days=int(day_offset))


# ===== Time Parsing Helper =====
def _parse_start_time(start_time_str: str):
    parts = [int(x) for x in start_time_str.split(":")]
    if len(parts) == 2:
        parts.append(0)
    return tuple(parts)
