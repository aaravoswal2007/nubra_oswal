#!/usr/bin/env python3
"""
Filter option_ref_ids_all.csv for February expiry options and save to a new CSV.
Filters by stock_name containing "FEB" and/or expiry date in February 2026.
"""

import pandas as pd

def main():
    # Read the CSV
    df = pd.read_csv("option_ref_ids_all.csv")
    
    print(f"Total rows in original CSV: {len(df)}")
    
    # Filter for February expiry: check stock_name contains "FEB" (case-insensitive)
    df["stock_name_upper"] = df["stock_name"].astype(str).str.upper()
    mask_feb = df["stock_name_upper"].str.contains("FEB", na=False)
    
    # Also filter by expiry date if needed (February 2026: 20260201 to 20260229)
    # Convert expiry to string and check if it starts with "202602"
    expiry_str = df["expiry"].astype(str)
    mask_expiry_feb = expiry_str.str.startswith("202602")
    
    # Combine both filters (OR logic - either stock_name has FEB or expiry is Feb 2026)
    df_feb = df.loc[mask_feb | mask_expiry_feb].drop(columns=["stock_name_upper"]).reset_index(drop=True)
    
    print(f"February expiry rows: {len(df_feb)}")
    
    # Save to new CSV
    output_filename = "option_ref_ids_feb_expiry.csv"
    df_feb.to_csv(output_filename, index=False)
    print(f"✅ Saved February expiry options to: {output_filename}")
    
    # Show sample
    print("\nSample rows:")
    print(df_feb.head(10).to_string())

if __name__ == "__main__":
    main()
