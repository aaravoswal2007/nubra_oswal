#!/usr/bin/env python3
"""
Subscribe to orderbook data for whole ref_ids list; update in-memory market_data
in Oswal key_name format and field structure (compatible with marketdata_store / executor_live).
Per Nubra docs: data is received through on_market_data; orderbook subscription filters it.
"""

import os
import csv
import certifi
import json
import time
from pathlib import Path

# Fix SSL certificate issues on macOS
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from nubra_python_sdk.ticker import websocketdata
from nubra_client import ensure_nubra

# ----- In-memory market_data (Oswal-compatible structure) -----
# Same key_name and field layout as Oswal_trading_frontend/.../marketdata_store.market_data
market_data = {}

# ref_id (int) -> key_name (str), e.g. 1069800 -> "ADANIGREEN_920_CE"
ref_id_to_key_name = {}

# Target key_name for optional console print (parity with existing app logging)
TARGET_KEY_PRINT = "ADANIGREEN_920_CE"

_message_count = 0
_connect_count = 0
_tick_count = 0


def _load_ref_id_to_key_name(csv_path: str) -> dict:
    """Build ref_id -> key_name from option_ref_ids_feb_filtered_by_strikes.csv.
    key_name = f'{asset}_{strike_price//100}_{option_type}' (e.g. ADANIENT_2000_CE).
    """
    mapping = {}
    path = Path(__file__).resolve().parent / csv_path
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ref_id = int(row["ref_id"])
                asset = (row.get("asset") or "").strip()
                strike_price = int(row.get("strike_price") or 0)
                option_type = (row.get("option_type") or "").strip()
                key_name = f"{asset}_{strike_price // 100}_{option_type}"
                mapping[ref_id] = key_name
            except (ValueError, KeyError):
                continue
    return mapping


def _orderbook_to_oswal_tick(msg) -> dict | None:
    """Convert Nubra OrderBookWrapper to one Oswal-style market_data entry (value dict)."""
    ref_id = getattr(msg, "ref_id", None)
    if ref_id is None:
        return None
    ref_id = int(ref_id)
    key_name = ref_id_to_key_name.get(ref_id)
    if not key_name:
        return None

    bids = getattr(msg, "bids", [])
    asks = getattr(msg, "asks", [])

    def _level_px_sz(levels, i):
        if i >= len(levels):
            return "N/A"
        level = levels[i]
        price = getattr(level, "price", None)
        size = getattr(level, "size", getattr(level, "quantity", getattr(level, "qty", 0)))
        if price is None:
            return "N/A"
        px = round(price / 100, 2) if price is not None else 0
        return f"{px}|{size}"

    best_bid_1 = _level_px_sz(bids, 0)
    best_bid_2 = _level_px_sz(bids, 1)
    best_ask_1 = _level_px_sz(asks, 0)
    best_ask_2 = _level_px_sz(asks, 1)

    ltp = getattr(msg, "last_traded_price", getattr(msg, "ltp", None))
    if ltp is not None and isinstance(ltp, (int, float)):
        ltp = round(ltp / 100, 2) if ltp else "N/A"
    ltp = ltp if ltp is not None else "N/A"

    ltq = getattr(msg, "last_traded_quantity", getattr(msg, "ltq", getattr(msg, "last_traded_qty", 0)))
    cum = getattr(msg, "total_traded_quantity", getattr(msg, "cum_vol", None))
    seq = getattr(msg, "sequence_number", getattr(msg, "seq", None))
    ts = getattr(msg, "exchange_timestamp", getattr(msg, "timestamp", None))
    ltt = getattr(msg, "last_traded_time", None)

    return {
        "ltp": ltp,
        "ltq": ltq,
        "cum_vol": cum,
        "seq": seq,
        "Best bid 1": best_bid_1,
        "Best bid 2": best_bid_2,
        "Best ask 1": best_ask_1,
        "Best ask 2": best_ask_2,
        "timestamp": ts,
        "last_trade_time": ltt,
        "inst_id": ref_id,
    }


def on_orderbook_data(msg):
    """Process every OrderBookWrapper: resolve key_name, write Oswal-format tick to market_data."""
    global _tick_count
    incoming_ref_id = getattr(msg, "ref_id", None)
    if incoming_ref_id is None:
        return
    # DEBUG: see all orderbook ref_ids coming in
    print(f"[DEBUG] on_orderbook_data: ref_id={incoming_ref_id}", flush=True)

    entry = _orderbook_to_oswal_tick(msg)
    if entry is None:
        # DEBUG: ref_id not in mapping or could not convert
        print(f"[DEBUG] skipped ref_id={incoming_ref_id} (no mapping or bad tick)", flush=True)
        return
    key_name = ref_id_to_key_name.get(int(incoming_ref_id))
    if not key_name:
        # DEBUG: mapping missing for this ref_id
        print(f"[DEBUG] no key_name for ref_id={incoming_ref_id}", flush=True)
        return
    market_data[key_name] = entry
    _tick_count += 1

    # DEBUG: occasionally dump one sample entry
    if _tick_count % 50 == 0:
        try:
            sample_key = next(iter(market_data))
            print(f"[DEBUG] market_data size={len(market_data)} sample_key={sample_key}", flush=True)
            print(f"[DEBUG] market_data[{sample_key}]={market_data[sample_key]}", flush=True)
        except StopIteration:
            pass

    # Optional: print for TARGET_KEY_PRINT (parity with existing app)
    if key_name == TARGET_KEY_PRINT:
        def _parse_price(px_sz_str):
            if px_sz_str == "N/A":
                return "N/A"
            try:
                return px_sz_str.split("|")[0]
            except Exception:
                return px_sz_str
        bb1 = _parse_price(entry["Best bid 1"])
        bb2 = _parse_price(entry["Best bid 2"])
        ba1 = _parse_price(entry["Best ask 1"])
        ba2 = _parse_price(entry["Best ask 2"])
        print(f"[{TARGET_KEY_PRINT}] bb1={bb1} bb2={bb2} ba1={ba1} ba2={ba2}", flush=True)

def on_connect(msg):
    """Connection callback - doc: print('[status]', msg). De-duplicate repeated connects (reconnects)."""
    global _connect_count
    _connect_count += 1
    if _connect_count == 1:
        print(f"[Status] ✅ Connected: {msg}", flush=True)
        print("[Status] WebSocket ready. You can subscribe to data streams.", flush=True)
    else:
        print(f"[Status] Reconnected (#{_connect_count}): {msg}", flush=True)

def on_close(reason):
    print(f"[Status] Closed: {reason}", flush=True)

def on_market_data(msg):
    """
    Primary data receiver per Nubra docs:
    'Orderbook data is received through on_market_data, filtered by orderbook subscription.'
    """
    global _message_count
    _message_count += 1
    # Orderbook: has bids and asks (OrderBookWrapper) -> print full response structure
    if hasattr(msg, 'bids') and hasattr(msg, 'asks'):
        on_orderbook_data(msg)

def on_error(err):
    error_str = str(err)
    print(f"\n[Error] ❌ {err}", flush=True)
    if "subscription" in error_str.lower():
        print(f"[Error] ⚠️  Subscription-related error detected!", flush=True)
    if "SSL" in error_str or "certificate" in error_str.lower():
        print("\n⚠️  SSL Certificate Error Detected!", flush=True)
        print("Try running:", flush=True)
        print("  export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')", flush=True)
        print("  export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE", flush=True)
        print("Then run this script again.", flush=True)
        print("See SSL_FIX.md for more details.\n", flush=True)

def main():
    # Load ref_id -> key_name mapping from CSV
    global ref_id_to_key_name
    ref_id_to_key_name = _load_ref_id_to_key_name("option_ref_ids_feb_filtered_by_strikes.csv")
    print(f"Loaded ref_id->key_name mapping: {len(ref_id_to_key_name)} instruments")

    # Load ref_ids from JSON
    ref_ids_path = Path(__file__).resolve().parent / "ref_ids_list.json"
    with open(ref_ids_path, "r") as f:
        ref_ids = json.load(f)

    # Convert to strings for subscription
    ref_ids_str = [str(ref_id) for ref_id in ref_ids]

    print(f"Loaded {len(ref_ids_str)} ref_ids from ref_ids_list.json")
    print(f"Subscribing to whole list; printing ticks for key: {TARGET_KEY_PRINT}")
    print("-" * 80)
    
    # Initialize SDK (UAT vs PROD from .env NUBRA_ENV)
    nubra = ensure_nubra()
    
    # Initialize WebSocket
    socket = websocketdata.NubraDataSocket(
        client=nubra,
        on_orderbook_data=on_orderbook_data,
        on_market_data=on_market_data,  # Also catch all market data
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error
    )
    
    # Connect
    socket.connect()
    
    # Wait a moment for connection to be fully established
    print("Waiting for connection to stabilize...", flush=True)
    time.sleep(2)
    
    # Check connection status before subscribing
    print(f"\n[Subscription Check] WebSocket connected: {socket.connected}", flush=True)
    if not socket.connected:
        print("[ERROR] WebSocket is not connected! Cannot subscribe.", flush=True)
        return
    
    # Subscribe to orderbook data for whole list
    # Note: subscribe expects a list of strings
    print(f"\n[Subscription] Subscribing to whole list ({len(ref_ids_str)} ref_ids)...", flush=True)
    print(f"[Subscription] Target key (will print): {TARGET_KEY_PRINT}", flush=True)
    
    try:
        print(f"\n[Subscription] Calling socket.subscribe()...", flush=True)
        print(f"[Subscription] Parameters:", flush=True)
        print(f"  - symbols: {len(ref_ids_str)} ref_ids (from ref_ids_list.json)", flush=True)
        print(f"  - data_type: 'orderbook'", flush=True)
        print(f"  - socket.connected: {socket.connected}", flush=True)
        
        # Call subscribe with whole list
        result = socket.subscribe(ref_ids_str, data_type="orderbook")
        print(f"[Subscription] socket.subscribe() returned: {result}", flush=True)
        print(f"[Subscription] Subscription method completed without exception", flush=True)
        
        # Wait a moment for subscription to be processed
        time.sleep(1)
        
        # Check connection status after subscription
        print(f"[Subscription] Post-subscription check:", flush=True)
        print(f"  - socket.connected: {socket.connected}", flush=True)
        
        # Try to check subscriptions_batch (may not be accessible)
        try:
            subs_count = len(socket.subscriptions_batch) if hasattr(socket, 'subscriptions_batch') else "N/A"
            print(f"  - Active subscriptions count: {subs_count}", flush=True)
            if hasattr(socket, 'subscriptions_batch') and socket.subscriptions_batch:
                print(f"  - Subscription keys: {list(socket.subscriptions_batch)[:3]}...", flush=True)
        except Exception as e:
            print(f"  - Could not access subscriptions_batch: {e}", flush=True)
        
        print(f"\n[Subscription] ✅ Subscription request sent successfully!", flush=True)
        print(f"[Subscription] Waiting for orderbook data updates...", flush=True)
        print(f"[Subscription] If no data appears, the instruments may not be actively trading.\n", flush=True)
        
    except Exception as e:
        print(f"\n[Subscription] ❌ ERROR during subscription: {e}", flush=True)
        import traceback
        print(f"[Subscription] Traceback:", flush=True)
        traceback.print_exc()

    print(f"\nSubscribed! Updating market_data for all instruments; printing ticks for {TARGET_KEY_PRINT}.")
    print("Press Ctrl+C to stop")
    
    # Keep running - this blocks and processes incoming messages
    try:
        socket.keep_running()
    except KeyboardInterrupt:
        print("\n\nStopping...")
        socket.close()

if __name__ == "__main__":
    main()
