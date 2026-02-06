#!/usr/bin/env python3
"""
Fetch option instruments for Reliance, Adani Ent, Adani Green, HDFC Bank, Kotak Bank.
Print full option list and a second DataFrame filtered by strike ranges (CE and PE).
"""

import pandas as pd
from nubra_python_sdk.refdata.instruments import InstrumentData
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

# Underlying symbols (NSE)
OPTION_UNDERLYINGS = ["RELIANCE", "ADANIENT", "ADANIGREEN", "HDFCBANK", "KOTAKBANK"]

# Strike range (min, max) inclusive for CE and PE per underlying
STRIKE_RANGES = {
    "RELIANCE": (1300, 1500),
    "ADANIENT": (2000, 2400),
    "ADANIGREEN": (850, 950),
    "KOTAKBANK": (350, 450),
    "HDFCBANK": (850, 950),
}

def main():
    # Initialize SDK. Use env_creds=True if you have PHONE_NO and MPIN in .env
    nubra = InitNubraSdk(NubraEnv.PROD)  # or NubraEnv.UAT for testing

    instruments = InstrumentData(nubra)
    df_all = instruments.get_instruments_dataframe()
    if df_all is None or df_all.empty:
        print("No instrument data available.")
        return

    df_all = df_all.copy()
    df_all["asset_upper"] = df_all["asset"].astype(str).str.strip().str.upper()
    df_all["deriv_upper"] = df_all["derivative_type"].astype(str).str.strip().str.upper()

    mask_opt = df_all["deriv_upper"] == "OPT"
    mask_asset = df_all["asset_upper"].isin(OPTION_UNDERLYINGS)
    df_options = df_all.loc[mask_opt & mask_asset].drop(columns=["asset_upper", "deriv_upper"])

    cols = ["ref_id", "asset", "stock_name", "strike_price", "option_type", "expiry", "exchange", "lot_size", "tick_size"]
    cols = [c for c in cols if c in df_options.columns]
    sort_cols = [c for c in ["asset", "expiry", "strike_price", "option_type"] if c in df_options.columns]

    # DataFrame 1: all options for these underlyings
    df_all_options = df_options[cols].sort_values(sort_cols or ["ref_id"]).reset_index(drop=True)
    print(f"Option instruments for: {', '.join(OPTION_UNDERLYINGS)} (all strikes)")
    print(f"Total rows: {len(df_all_options)}\n")
    print(df_all_options.to_string())
    print("\n" + "=" * 80 + "\n")

    # DataFrame 2: only rows where strike is in the defined range for that asset (CE and PE)
    strike_series = pd.to_numeric(df_options["strike_price"], errors="coerce")
    mask_range = pd.Series(False, index=df_options.index)
    for asset, (low, high) in STRIKE_RANGES.items():
        asset_mask = df_options["asset"].astype(str).str.strip().str.upper() == asset
        strike_mask = (strike_series >= low) & (strike_series <= high)
        mask_range = mask_range | (asset_mask & strike_mask)

    df_filtered = df_options.loc[mask_range][cols].sort_values(sort_cols or ["ref_id"]).reset_index(drop=True)

    print("Filtered by strike range (CE and PE):")
    for asset, (low, high) in STRIKE_RANGES.items():
        print(f"  {asset}: {low}–{high}")
    print(f"Total rows: {len(df_filtered)}\n")
    print(df_filtered.to_string())

    # Save full DataFrame (all options for all 5 stocks, no strike filtering) to CSV
    csv_filename = "option_ref_ids_all.csv"
    df_all_options.to_csv(csv_filename, index=False)
    print(f"\n✅ Saved full DataFrame (all strikes) to: {csv_filename}")

    # Also save filtered DataFrame to CSV (optional)
    csv_filtered_filename = "option_ref_ids_filtered.csv"
    df_filtered.to_csv(csv_filtered_filename, index=False)
    print(f"✅ Saved filtered DataFrame (by strike ranges) to: {csv_filtered_filename}")

if __name__ == "__main__":
    main()
