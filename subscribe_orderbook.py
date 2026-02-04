#!/usr/bin/env python3
"""
Subscribe to orderbook data for whole ref_ids list; print only target ref_id.
Per Nubra docs: data is received through on_market_data; orderbook subscription filters it.
"""

import os
import certifi
import json
import time

# Fix SSL certificate issues on macOS
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from nubra_python_sdk.ticker import websocketdata
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

# Target ref_id: only this one will be printed (OrderBookWrapper)
TARGET_REF_ID = 1069800

_message_count = 0
_connect_count = 0
_tick_count = 0

def _print_orderbook_wrapper(msg):
    """Print only best bid 1 & 2 and best ask 1 & 2 prices (divided by 100)."""
    global _tick_count
    _tick_count += 1
    bids = getattr(msg, 'bids', [])
    asks = getattr(msg, 'asks', [])
    def p(x):
        return round(x / 100, 2) if x is not None else None
    print("\n[Tick #{}] [Orderbook] ref_id={}".format(_tick_count, getattr(msg, 'ref_id', None)), flush=True)
    print("  best_bid_1: {}".format(p(bids[0].price) if len(bids) > 0 else None), flush=True)
    print("  best_bid_2: {}".format(p(bids[1].price) if len(bids) > 1 else None), flush=True)
    print("  best_ask_1: {}".format(p(asks[0].price) if len(asks) > 0 else None), flush=True)
    print("  best_ask_2: {}".format(p(asks[1].price) if len(asks) > 1 else None), flush=True)
    print("-" * 50, flush=True)

def on_orderbook_data(msg):
    """Callback for orderbook data - print OrderBookWrapper only for TARGET_REF_ID."""
    incoming_ref_id = getattr(msg, 'ref_id', None)
    if incoming_ref_id is None:
        return
    if int(incoming_ref_id) != TARGET_REF_ID:
        return
    _print_orderbook_wrapper(msg)

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
    # Load ref_ids from JSON
    with open("ref_ids_list.json", "r") as f:
        ref_ids = json.load(f)
    
    # Convert to strings for subscription
    ref_ids_str = [str(ref_id) for ref_id in ref_ids]
    
    print(f"Loaded {len(ref_ids_str)} ref_ids from ref_ids_list.json")
    print(f"Subscribing to whole list; printing only ref_id: {TARGET_REF_ID}")
    print("-" * 80)
    
    # Initialize SDK
    nubra = InitNubraSdk(NubraEnv.PROD)  # or NubraEnv.PROD, env_creds=True
    
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
    print(f"[Subscription] Target ref_id (will print): {TARGET_REF_ID}", flush=True)
    
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

    print(f"\nSubscribed! Waiting for orderbook ticks for ref_id {TARGET_REF_ID}...")
    print("Press Ctrl+C to stop")
    print("[Response: OrderBookWrapper for ref_id {} only]\n".format(TARGET_REF_ID))
    
    # Keep running - this blocks and processes incoming messages
    try:
        socket.keep_running()
    except KeyboardInterrupt:
        print("\n\nStopping...")
        socket.close()

if __name__ == "__main__":
    main()
