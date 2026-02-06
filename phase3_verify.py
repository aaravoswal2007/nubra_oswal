#!/usr/bin/env python3
"""
Phase 3 acceptance: market_data helpers (_touch/_mid).
Run from nubra_oswal: python phase3_verify.py
- Tests _touch and _mid with sample market_data.
- If subscribe_orderbook.py is running, can test with real market_data.
"""

from market_data_helpers import _touch, _mid, get_mid_price

def main():
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
    
    print("[Phase 3] Testing _touch/_mid with sample market_data:")
    bb, ba, ltp = _touch(sample_market_data, "ADANIGREEN_920_CE")
    print(f"  _touch: bb={bb}, ba={ba}, ltp={ltp}")
    
    mid = _mid(bb, ba, ltp)
    print(f"  _mid: {mid}")
    
    mid2 = get_mid_price(sample_market_data, "ADANIGREEN_920_CE")
    print(f"  get_mid_price: {mid2}")
    
    # Test with current_price (should use next level if matches)
    print("\n[Phase 3] Testing with current_price=95.50 (matches best bid):")
    bb2, ba2, ltp2 = _touch(sample_market_data, "ADANIGREEN_920_CE", current_price=95.50)
    print(f"  _touch(current_price=95.50): bb={bb2}, ba={ba2}")
    mid3 = _mid(bb2, ba2, ltp2, current_price=95.50)
    print(f"  _mid: {mid3} (should use bb2=95.45, so mid should be lower)")
    
    # Try to use real market_data if subscribe_orderbook is running
    try:
        from subscribe_orderbook import market_data
        if market_data:
            print(f"\n[Phase 3] Found {len(market_data)} instruments in subscribe_orderbook.market_data")
            sample_key = next(iter(market_data))
            bb3, ba3, ltp3 = _touch(market_data, sample_key)
            mid4 = get_mid_price(market_data, sample_key)
            print(f"  Sample key '{sample_key}': bb={bb3}, ba={ba3}, ltp={ltp3}, mid={mid4}")
    except ImportError:
        print("\n[Phase 3] subscribe_orderbook.market_data not available (run subscribe_orderbook.py to test with real data)")

if __name__ == "__main__":
    main()
