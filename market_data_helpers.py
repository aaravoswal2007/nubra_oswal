#!/usr/bin/env python3
"""
Phase 3: Market data helpers (_touch/_mid) for Nubra.
Same logic as Oswal executor_live.py, works with market_data from subscribe_orderbook.py.
"""

from typing import Optional, Tuple

# Price tick for rounding (0.05 rupees = 5 paise)
PRICE_TICK = 0.05


def _round_tick(x: float) -> float:
    """Round price to nearest tick (0.05)."""
    return round(round(x / PRICE_TICK) * PRICE_TICK, 2)


def _parse_px_sz(pair: Optional[str]) -> Tuple[Optional[float], Optional[int]]:
    """Parse "Price|Size" string to (price, size). Returns (None, None) if invalid."""
    if not pair or pair == "N/A":
        return None, None
    try:
        p, s = str(pair).split("|", 1)
        return float(p), int(float(s))
    except Exception:
        return None, None


def _touch(market_data: dict, symbol_key: str, current_price: Optional[float] = None) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Get best bid/ask from market_data dict.
    If current_price is provided and matches best bid/ask, returns next best level instead.
    Returns: (bb, ba, ltp)
    """
    # Safely access market_data with error handling
    try:
        t = market_data.get(symbol_key) or {}
    except Exception as e:
        print(f"[market_data_helpers] [ERROR] Failed to get market_data for {symbol_key}: {e}")
        t = {}
    
    # Parse best bid/ask levels
    bb1, _ = _parse_px_sz(t.get("Best bid 1")) or (None, None)
    ba1, _ = _parse_px_sz(t.get("Best ask 1")) or (None, None)
    bb2, _ = _parse_px_sz(t.get("Best bid 2")) or (None, None)
    ba2, _ = _parse_px_sz(t.get("Best ask 2")) or (None, None)

    def _to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    # Fallback to alternative field names if "Best bid 1"/"Best ask 1" not available
    if bb1 is None:
        try:
            for k in ("best_bid", "bid", "BestBidPrice"):
                bb1 = _to_float(t.get(k))
                if bb1 is not None:
                    break
        except Exception as e:
            print(f"[market_data_helpers] [ERROR] Failed to parse fallback bid fields for {symbol_key}: {e}")
        
        if bb1 is None:
            print(f"[market_data_helpers] [WARN] No bid price found for {symbol_key}")

    if ba1 is None:
        try:
            for k in ("best_ask", "ask", "BestAskPrice"):
                ba1 = _to_float(t.get(k))
                if ba1 is not None:
                    break
        except Exception as e:
            print(f"[market_data_helpers] [ERROR] Failed to parse fallback ask fields for {symbol_key}: {e}")
        
        if ba1 is None:
            print(f"[market_data_helpers] [WARN] No ask price found for {symbol_key}")

    # If we have a current price, check if it matches best bid/ask and use next level if so
    if current_price is not None:
        # bb = best bid 1 if best bid 1 != our bid, else best bid 2
        if bb1 is not None and abs(bb1 - current_price) < 0.01:
            bb = bb2  # Use best bid 2 if our order is at best bid 1
        else:
            bb = bb1
        
        # ba = best ask 1 if best ask 1 != our ask, else best ask 2
        if ba1 is not None and abs(ba1 - current_price) < 0.01:
            ba = ba2  # Use best ask 2 if our order is at best ask 1
        else:
            ba = ba1
    else:
        bb = bb1
        ba = ba1

    ltp = None
    for k in ("ltp", "LastTradedPrice", "LTP"):
        if k in t:
            try:
                ltp = float(t.get(k)) if t.get(k) is not None else None
            except Exception:
                ltp = None
            break

    # CRITICAL: If we can't get both bid and ask prices, raise error
    # We need at least bid OR ask to calculate mid price (LTP alone is not sufficient)
    if bb is None and ba is None:
        error_msg = f"[market_data_helpers] [FATAL] No bid/ask prices available for {symbol_key}: bid={bb}, ask={ba}, ltp={ltp}."
        print(error_msg)
        raise RuntimeError(f"No bid/ask prices available for {symbol_key} - cannot calculate mid price")

    return bb, ba, ltp


def _mid(bb: Optional[float], ba: Optional[float], ltp: Optional[float], current_price: Optional[float] = None) -> Optional[float]:
    """
    Calculate mid-price while ignoring orders at our current price level.
    When our order is the best bid/ask, we use the next best level instead.
    This prevents price chasing when our own order becomes the best bid/ask.
    
    Note: _touch() handles ignoring our own bid/ask by using next best level,
    so bb/ba passed here are already adjusted if current_price matches best bid/ask.
    """
    # Normal mid calculation
    if bb is not None and ba is not None:
        return _round_tick((bb + ba) / 2.0)
    elif bb is not None:
        return _round_tick(bb)
    elif ba is not None:
        return _round_tick(ba)
    elif current_price is not None:
        return current_price
    elif ltp is not None:
        return _round_tick(ltp)
    return None


def get_mid_price(market_data: dict, key_name: str, current_price: Optional[float] = None) -> Optional[float]:
    """
    Convenience function: get mid price for a key_name.
    Calls _touch() then _mid().
    """
    bb, ba, ltp = _touch(market_data, key_name, current_price)
    return _mid(bb, ba, ltp, current_price)


if __name__ == "__main__":
    # Test with sample market_data
    sample_market_data = {
        "ADANIGREEN_920_CE": {
            "Best bid 1": "95.50|100",
            "Best bid 2": "95.45|200",
            "Best ask 1": "95.55|150",
            "Best ask 2": "95.60|300",
            "ltp": "95.52",
        }
    }
    bb, ba, ltp = _touch(sample_market_data, "ADANIGREEN_920_CE")
    mid = _mid(bb, ba, ltp)
    print(f"Test: bb={bb}, ba={ba}, ltp={ltp}, mid={mid}")
    print(f"get_mid_price: {get_mid_price(sample_market_data, 'ADANIGREEN_920_CE')}")
