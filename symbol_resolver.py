#!/usr/bin/env python3
"""
Resolve option symbol from stock, strike label, and option type.
Same logic as Oswal strategy_helpers.get_symbol: ATM, ITMx, OTMx → numeric strike using spot LTP and step.
"""

from __future__ import annotations

from typing import Optional


def _parse_ltp(ltp) -> Optional[float]:
    """Parse LTP from market_data entry (float or '123.45|qty' string)."""
    if ltp is None:
        return None
    if isinstance(ltp, (int, float)):
        return float(ltp)
    s = str(ltp).split("|")[0].strip()
    if not s or s == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_symbol(stock: str, strike: str, option_type: str) -> str:
    """
    Resolve option symbol from stock, strike, and option type.

    Args:
        stock: Stock name (e.g. "RELIANCE")
        strike: "ATM", "ITM3", "OTM2", or absolute integer strike
        option_type: "CE" or "PE" (or "C"/"E" normalized to CE/PE)

    Returns:
        key_name e.g. "RELIANCE_1500_CE"
    """
    stock = stock.upper().strip()
    opt = (option_type or "C").strip().upper()
    if opt == "C":
        opt = "CE"
    elif opt == "P":
        opt = "PE"
    if opt not in ("CE", "PE"):
        opt = "CE"

    # Lazy imports so Bridge can load without pulling full subscribe_orderbook at import time
    try:
        from subscribe_orderbook import market_data
    except Exception:
        from marketdata_store import market_data
    from configloader import get_stock_steps

    stock_steps = get_stock_steps()
    if stock not in stock_steps:
        raise ValueError(f"Stock {stock} not in config stock_steps; add it to config.ini [stocks] stock_steps")
    step = int(stock_steps[stock]["step"])

    spot_data = market_data.get(stock)
    if not spot_data:
        raise ValueError(f"No spot data found for stock: {stock}. Ensure market data is subscribed and live.")
    spot_ltp = _parse_ltp(spot_data.get("ltp"))
    if spot_ltp is None or spot_ltp <= 0:
        raise ValueError(f"Invalid or missing spot LTP for {stock}. Ensure market data is live.")
    atm_strike = int((spot_ltp + step / 2) // step) * step

    strike_str = (strike or "").strip().upper()
    if strike_str == "ATM":
        offset = 0
    elif strike_str.startswith("ITM"):
        try:
            n = int(strike_str[3:])
        except ValueError:
            raise ValueError(f"Strike {strike!r} must be ITM followed by a number (e.g. ITM3)")
        offset = -n if opt == "CE" else n
    elif strike_str.startswith("OTM"):
        try:
            n = int(strike_str[3:])
        except ValueError:
            raise ValueError(f"Strike {strike!r} must be OTM followed by a number (e.g. OTM2)")
        offset = n if opt == "CE" else -n
    else:
        try:
            abs_strike = int(float(strike_str))
            return f"{stock}_{abs_strike}_{opt}"
        except (ValueError, TypeError):
            raise ValueError(
                f"Strike must be ATM, ITMx, OTMx, or a numeric strike; got {strike!r}"
            )

    final_strike = atm_strike + (offset * step)
    return f"{stock}_{final_strike}_{opt}"
