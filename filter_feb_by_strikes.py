#!/usr/bin/env python3
"""
Filter option_ref_ids_feb_expiry.csv by strike ranges and save to a new CSV.
"""

import pandas as pd

# Strike range (min, max) inclusive for CE and PE per underlying
STRIKE_RANGES = {
    "RELIANCE": (1300, 1500),
    "ADANIENT": (2000, 2400),
    "ADANIGREEN": (850, 950),
    "KOTAKBANK": (350, 450),
    "HDFCBANK": (850, 950),
}

def main():
    # Read the February expiry CSV
    df = pd.read_csv("option_ref_ids_feb_expiry.csv")
    
    print(f"Total rows in February expiry CSV: {len(df)}")
    
    # Convert strike_price to numeric (it's stored as integer like 88000, need to divide by 100)
    # Or check if it's already in the right format
    df["strike_numeric"] = pd.to_numeric(df["strike_price"], errors="coerce")
    # If strike_price is stored as 88000 (meaning 880.00), divide by 100
    # If it's already 880, keep as is
    # Let's check: from the sample, strike_price is 88000 for 880 strike, so divide by 100
    df["strike_numeric"] = df["strike_numeric"] / 100
    
    # Normalize asset names
    df["asset_upper"] = df["asset"].astype(str).str.strip().str.upper()
    
    # Filter by strike ranges per asset
    mask_range = pd.Series(False, index=df.index)
    for asset, (low, high) in STRIKE_RANGES.items():
        asset_mask = df["asset_upper"] == asset
        strike_mask = (df["strike_numeric"] >= low) & (df["strike_numeric"] <= high)
        mask_range = mask_range | (asset_mask & strike_mask)
    
    df_filtered = df.loc[mask_range].drop(columns=["strike_numeric", "asset_upper"]).reset_index(drop=True)
    
    print(f"Filtered rows (by strike ranges): {len(df_filtered)}")
    print("\nStrike ranges applied:")
    for asset, (low, high) in STRIKE_RANGES.items():
        print(f"  {asset}: {low}–{high}")
    
    # Save to new CSV
    output_filename = "option_ref_ids_feb_filtered_by_strikes.csv"
    df_filtered.to_csv(output_filename, index=False)
    print(f"\n✅ Saved filtered February expiry options to: {output_filename}")
    
    # Show sample
    print("\nSample rows:")
    print(df_filtered.head(15).to_string())

if __name__ == "__main__":
    main()
