#!/usr/bin/env python3
"""
Subscribe to orderbook data for whole ref_ids list; update in-memory market_data
in Oswal key_name format and field structure (compatible with marketdata_store / executor_live).
Per Nubra docs: data is received through on_market_data; orderbook subscription filters it.
"""

import os
import certifi
import time
import logging
from pathlib import Path
from datetime import datetime

# Fix SSL certificate issues on macOS
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from nubra_python_sdk.ticker import websocketdata
from nubra_client import ensure_nubra

# ===== Import local marketdata_store =====
import marketdata_store

# ===== Import instruments from instruments_to_sub =====
from instruments_to_sub import instruments_to_subscribe, instrument_dict

# ===== Setup logging =====
# Create logs directory if it doesn't exist
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Daily log file (similar to XTS structure)
def daily_log(prefix):
    """Generate daily log file path."""
    today = datetime.now().strftime("%Y-%m-%d")
    return str(LOG_DIR / f"{prefix}_{today}.log")

logging.basicConfig(
    filename=daily_log("marketdata_nubra"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("marketdata_nubra")

# ===== Use marketdata_store.market_data =====
market_data = marketdata_store.market_data
log.info("Using marketdata_store.market_data")

# ref_id (int) -> key_name (str), e.g. 1069800 -> "ADANIGREEN_920_CE"
# Built from instrument_dict (reverse mapping)
ref_id_to_key_name = {}

# Target key_name for optional console print (parity with existing app logging)
# Will be set to first instrument or can be configured
TARGET_KEY_PRINT = None

# Real-time market data log: set LOG_MARKET_DATA_KEY=ADANIGREEN_920_CE to log that symbol to console (and log file)
LOG_MARKET_DATA_KEY = os.environ.get("LOG_MARKET_DATA_KEY", "").strip() or None  # e.g. "ADANIGREEN_920_CE"
# Throttle: log at most every N seconds for the key (0 = every tick)
LOG_MARKET_DATA_INTERVAL_SEC = float(os.environ.get("LOG_MARKET_DATA_INTERVAL_SEC", "1.0"))

_message_count = 0
_connect_count = 0
_tick_count = 0
_last_log_time = 0.0

# For re-subscribe on reconnect: set in main() before connect
_ref_ids_to_subscribe = None
_data_socket = None


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
    global _tick_count, _last_log_time
    try:
        incoming_ref_id = getattr(msg, "ref_id", None)
        if incoming_ref_id is None:
            return

        entry = _orderbook_to_oswal_tick(msg)
        if entry is None:
            # ref_id not in mapping or could not convert
            log.debug(f"Skipped ref_id={incoming_ref_id} (no mapping or bad tick)")
            return
        
        key_name = ref_id_to_key_name.get(int(incoming_ref_id))
        if not key_name:
            # mapping missing for this ref_id
            log.debug(f"No key_name for ref_id={incoming_ref_id}")
            return
        
        # Update shared market_data (same as XTS implementation)
        market_data[key_name] = entry
        _tick_count += 1

        # Optional: real-time log for a specific symbol (e.g. LOG_MARKET_DATA_KEY=ADANIGREEN_920_CE)
        if LOG_MARKET_DATA_KEY and key_name == LOG_MARKET_DATA_KEY:
            now = time.time()
            if LOG_MARKET_DATA_INTERVAL_SEC <= 0 or (now - _last_log_time) >= LOG_MARKET_DATA_INTERVAL_SEC:
                _last_log_time = now
                bb1 = entry.get("Best bid 1", "N/A")
                ba1 = entry.get("Best ask 1", "N/A")
                ltp = entry.get("ltp", "N/A")
                try:
                    if bb1 != "N/A" and ba1 != "N/A":
                        pb, _ = bb1.split("|", 1) if "|" in str(bb1) else (bb1, 0)
                        pa, _ = ba1.split("|", 1) if "|" in str(ba1) else (ba1, 0)
                        mid = (float(pb) + float(pa)) / 2.0
                        mid = round(mid / 0.05) * 0.05
                    else:
                        mid = "N/A"
                except Exception:
                    mid = "N/A"
                line = f"[MD] {key_name}  bid1={bb1}  ask1={ba1}  mid={mid}  ltp={ltp}"
                print(line, flush=True)
                log.info(line)

    except Exception as e:
        log.error(f"Error updating market data: {e}")

def on_connect(msg):
    """Connection callback - log connection status and (re-)subscribe to orderbook."""
    global _connect_count
    _connect_count += 1
    if _connect_count == 1:
        log.info(f"✅ Connected: {msg}")
        log.info("WebSocket ready. You can subscribe to data streams.")
        print(f"[Status] ✅ Connected: {msg}", flush=True)
    else:
        log.info(f"Reconnected (#{_connect_count}): {msg}")
        print(f"[Status] Reconnected (#{_connect_count}): {msg}", flush=True)

    # (Re-)subscribe to orderbook so data flows after every connect/reconnect
    if _ref_ids_to_subscribe and _data_socket:
        try:
            result = _data_socket.subscribe(_ref_ids_to_subscribe, data_type="orderbook")
            log.info(f"Subscribed to orderbook ({len(_ref_ids_to_subscribe)} ref_ids) result={result}")
            print(f"[Status] Subscribed to orderbook ({len(_ref_ids_to_subscribe)} instruments)", flush=True)
        except Exception as e:
            log.error(f"Subscribe failed: {e}", exc_info=True)
            print(f"[Status] Subscribe failed: {e}", flush=True)

def on_close(reason):
    log.info(f"Connection closed: {reason}")
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
    log.error(f"❌ {err}")
    print(f"\n[Error] ❌ {err}", flush=True)
    if "subscription" in error_str.lower():
        log.warning("⚠️  Subscription-related error detected!")
        print(f"[Error] ⚠️  Subscription-related error detected!", flush=True)
    if "SSL" in error_str or "certificate" in error_str.lower():
        log.error("⚠️  SSL Certificate Error Detected!")
        print("\n⚠️  SSL Certificate Error Detected!", flush=True)
        print("Try running:", flush=True)
        print("  export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')", flush=True)
        print("  export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE", flush=True)
        print("Then run this script again.", flush=True)
        print("See SSL_FIX.md for more details.\n", flush=True)

def main():
    log.info("Starting Nubra market data subscription")
    
    # Build ref_id -> key_name mapping from instrument_dict (reverse mapping)
    global ref_id_to_key_name, TARGET_KEY_PRINT
    # instrument_dict values are ref_ids (may be int or str), convert to int for mapping
    ref_id_to_key_name = {int(ref_id): key_name for key_name, ref_id in instrument_dict.items()}
    
    # Check: subscription list should cover all instruments in instrument_dict
    dict_ref_ids = {str(ref_id) for ref_id in instrument_dict.values()}
    sub_ref_ids = set(instruments_to_subscribe)
    if dict_ref_ids != sub_ref_ids:
        missing_in_sub = dict_ref_ids - sub_ref_ids
        extra_in_sub = sub_ref_ids - dict_ref_ids
        if missing_in_sub:
            log.warning(f"instrument_dict has ref_ids not in instruments_to_subscribe: {len(missing_in_sub)}")
            print(f"[Check] WARNING: {len(missing_in_sub)} ref_ids in instrument_dict are NOT in subscription list", flush=True)
        if extra_in_sub:
            log.warning(f"instruments_to_subscribe has ref_ids not in instrument_dict: {len(extra_in_sub)}")
            print(f"[Check] WARNING: {len(extra_in_sub)} ref_ids in subscription list are NOT in instrument_dict", flush=True)
    else:
        log.info("Subscription list matches instrument_dict (all instruments covered)")
        print(f"[Check] OK: Subscription list matches instrument_dict ({len(dict_ref_ids)} instruments)", flush=True)
    
    # Set TARGET_KEY_PRINT to first instrument if not set
    if TARGET_KEY_PRINT is None and instrument_dict:
        TARGET_KEY_PRINT = list(instrument_dict.keys())[0]
    
    log.info(f"Loaded ref_id->key_name mapping: {len(ref_id_to_key_name)} instruments from instrument_dict")
    print(f"Loaded {len(ref_id_to_key_name)} instruments from instrument_dict")
    print(f"Total instruments to subscribe: {len(instruments_to_subscribe)}")
    
    # instruments_to_subscribe is already a list of strings (ref_ids)
    ref_ids_str = instruments_to_subscribe

    global _ref_ids_to_subscribe, _data_socket
    _ref_ids_to_subscribe = ref_ids_str

    print(f"Subscribing to {len(ref_ids_str)} instruments (and on every reconnect)")
    print("-" * 80)
    
    # Initialize SDK (UAT vs PROD from .env NUBRA_ENV)
    nubra = ensure_nubra()
    
    # Initialize WebSocket — use on_orderbook_data only for orderbook ticks
    socket = websocketdata.NubraDataSocket(
        client=nubra,
        on_orderbook_data=on_orderbook_data,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error
    )
    _data_socket = socket
    
    # Connect; on_connect will (re-)subscribe to orderbook for this and every reconnect
    socket.connect()
    
    print(f"\n✅ Updating market_data for all {len(ref_ids_str)} instruments")
    if LOG_MARKET_DATA_KEY:
        print(f"[MD] Real-time market data logging: {LOG_MARKET_DATA_KEY} (every {LOG_MARKET_DATA_INTERVAL_SEC}s)", flush=True)
    print("Press Ctrl+C to stop")
    
    # Keep running - this blocks and processes incoming messages
    try:
        socket.keep_running()
    except KeyboardInterrupt:
        print("\n\nStopping...")
        socket.close()

if __name__ == "__main__":
    main()
