# instruments_to_sub.py
import pandas as pd
import os
import json
from datetime import datetime
import logging
from pathlib import Path
from fno_strikes import get_fno_strikes_around_spot
from instrument_file_manager import save_instrument_dict_to_file
from Master_dump_loading import load_master_dump
from configloader import get_cash_ids, get_stock_steps
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

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
    filename=daily_log("instruments"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("instruments")

# ===== Stock Configuration =====
CASH_IDS = get_cash_ids()
stock_steps = get_stock_steps()

# ===== Initialize Nubra SDK with Manual Login =====
# Using UAT environment per Nubra docs: https://nubra.io/products/api/docs/python-sdk/index.html
# Manual OTP authentication - you will be prompted for phone, OTP, and MPIN
log.info("Initializing Nubra SDK with manual OTP authentication")
print("\n" + "=" * 60)
print("Nubra SDK Login (UAT Environment)")
print("=" * 60)
print("You will be prompted for:")
print("  1. Phone number")
print("  2. OTP (sent via SMS)")
print("  3. MPIN")
print("=" * 60 + "\n")

try:
    nubra = InitNubraSdk(NubraEnv.UAT)
except (TypeError, KeyError) as e:
    if "subscriptable" in str(e).lower() or "user info" in str(e).lower() or "fetching user" in str(e).lower():
        log.warning("Known Nubra UAT issue: userinfo response can omit version_info; SDK raises during init. See README.")
        print("[Nubra] Known UAT issue: 'Exception while fetching user info' — userinfo can omit version_info. Auth may still have succeeded; see nubra_oswal/README.md.", flush=True)
    raise
log.info("Initialized Nubra SDK with UAT environment")
print("\n✅ Authentication successful!")

# ===== Load Master Dump =====
max_retries = 2
masterdf = load_master_dump(nubra, max_retries)

if masterdf is None:
    log.error("Cannot proceed without master dump. Exiting.")
    print("Error: Cannot proceed without master dump. Exiting.")
    exit(1)

# ===== Build F&O Subscription List =====
instruments_to_subscribe, instrument_dict = get_fno_strikes_around_spot(
    stock_steps, 
    CASH_IDS, 
    nubra, 
    masterdf
)
current_folder = os.path.dirname(os.path.abspath(__file__))

save_instrument_dict_to_file(instrument_dict, folder_path=current_folder)
log.info(f"Total instruments to subscribe: {len(instruments_to_subscribe)}")
print(f"Total instruments to subscribe: {len(instruments_to_subscribe)}")

# Export for use by other modules (same as XTS version)
# instruments_to_subscribe: List of ref_ids (as strings) for Nubra subscription
# instrument_dict: {key_name: ref_id} mapping where ref_id values are stored but named ExchangeInstrumentID for compatibility
__all__ = ['instruments_to_subscribe', 'instrument_dict', 'stock_steps']
