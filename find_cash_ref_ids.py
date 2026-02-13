#!/usr/bin/env python3
"""
Find cash/equity ref_ids for stocks from Nubra instruments.
Step 1: Load master dump and save to CSV/Excel
Step 2: Find cash ref_ids manually from the dump
"""

import pandas as pd
import os
from datetime import datetime
from nubra_python_sdk.refdata.instruments import InstrumentData
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

# Stocks we need cash ref_ids for
STOCKS = ["RELIANCE", "ADANIENT", "HDFCBANK", "KOTAKBANK", "ADANIGREEN"]

def main():
    print("=" * 60)
    print("Step 1: Loading Master Dump from Nubra")
    print("=" * 60)
    print("\n[Login] You will be prompted for OTP authentication...")
    
    # Initialize SDK - manual login
    try:
        nubra = InitNubraSdk(NubraEnv.UAT)
    except (TypeError, KeyError) as e:
        if "subscriptable" in str(e).lower() or "user info" in str(e).lower() or "fetching user" in str(e).lower():
            print("[Nubra] Known UAT issue: 'Exception while fetching user info' — userinfo can omit version_info. Auth may still have succeeded; see nubra_oswal/README.md.", flush=True)
        raise
    print("\n✅ Authentication successful!")
    
    instruments = InstrumentData(nubra)
    df_all = instruments.get_instruments_dataframe()
    
    if df_all is None or df_all.empty:
        print("❌ No instrument data available.")
        return
    
    print(f"\n📊 Loaded {len(df_all)} total instruments")
    
    # Show available columns
    print(f"\n📋 Available columns ({len(df_all.columns)}):")
    for i, col in enumerate(df_all.columns, 1):
        print(f"  {i}. {col}")
    
    # Save master dump to CSV and Excel for manual inspection
    today_str = datetime.now().strftime('%Y-%m-%d')
    csv_file = f"master_dump_all_{today_str}.csv"
    excel_file = f"master_dump_all_{today_str}.xlsx"
    
    print(f"\n💾 Saving master dump to files...")
    df_all.to_csv(csv_file, index=False)
    print(f"  ✅ CSV: {csv_file}")
    
    # Save to Excel (only key columns to keep file size manageable)
    key_cols = ["ref_id", "asset", "stock_name", "derivative_type", "strike_price", "option_type", "expiry", "exchange"]
    excel_cols = [col for col in key_cols if col in df_all.columns]
    if excel_cols:
        try:
            df_all[excel_cols].to_excel(excel_file, index=False, engine='openpyxl')
            print(f"  ✅ Excel: {excel_file} (key columns only)")
        except ImportError:
            print(f"  ⚠️  Excel export skipped (openpyxl not installed). CSV file available: {csv_file}")
        except Exception as e:
            print(f"  ⚠️  Excel export failed: {e}. CSV file available: {csv_file}")
    
    print(f"\n📊 Master dump saved! You can now inspect the files to find cash ref_ids.")
    print(f"   Total instruments: {len(df_all)}")
    
    # Show sample data
    print(f"\n📋 Sample data (first 5 rows):")
    print(df_all.head().to_string())
    
    # Show derivative_type distribution
    if "derivative_type" in df_all.columns:
        print(f"\n📊 Derivative type distribution:")
        print(df_all["derivative_type"].value_counts().head(20))
    
    print("\n" + "=" * 60)
    print("Step 2: Finding Cash Ref IDs for Each Stock")
    print("=" * 60)
    
    # Prepare for searching
    df_all = df_all.copy()
    df_all["asset_upper"] = df_all["asset"].astype(str).str.strip().str.upper()
    
    # Find cash ref_ids for our stocks
    cash_ref_ids = {}
    
    print("\n🔍 Searching for cash instruments for each stock...")
    print("   (Cash instruments typically have no strike_price and derivative_type != 'OPT')")
    print()
    
    for stock in STOCKS:
        print(f"\n📊 {stock}:")
        print("-" * 60)
        
        # Find all instruments for this stock
        stock_mask = df_all["asset_upper"] == stock.upper()
        stock_instruments = df_all[stock_mask]
        
        if stock_instruments.empty:
            print(f"❌ NOT FOUND in any instruments")
            cash_ref_ids[stock] = 0
            continue
        
        print(f"   Found {len(stock_instruments)} total instruments for {stock}")
        
        # Try to identify cash instruments
        # Cash instruments: no strike_price (or strike_price is 0/NaN), and not OPT derivative
        cash_candidates = stock_instruments.copy()
        
        if "strike_price" in cash_candidates.columns:
            # Filter out instruments with strike_price (those are options)
            mask_no_strike = cash_candidates["strike_price"].isna() | (cash_candidates["strike_price"] == 0)
            cash_candidates = cash_candidates[mask_no_strike]
            print(f"   {len(cash_candidates)} instruments without strike_price")
        
        if "derivative_type" in cash_candidates.columns:
            # Filter out OPT (options)
            mask_not_opt = cash_candidates["derivative_type"].astype(str).str.strip().str.upper() != "OPT"
            cash_candidates = cash_candidates[mask_not_opt]
            print(f"   {len(cash_candidates)} instruments after filtering out OPT")
        
        if len(cash_candidates) > 0:
            # Show all candidates
            print(f"\n   💡 Cash instrument candidates:")
            display_cols = ["ref_id", "asset", "stock_name", "derivative_type", "exchange"]
            display_cols = [col for col in display_cols if col in cash_candidates.columns]
            
            for idx, row in cash_candidates.head(10).iterrows():
                ref_id = row.get("ref_id", "N/A")
                stock_name = row.get("stock_name", "N/A")
                deriv_type = row.get("derivative_type", "N/A")
                exchange = row.get("exchange", "N/A")
                print(f"      ref_id: {ref_id:8} | Name: {stock_name:20} | Type: {deriv_type:10} | Exchange: {exchange}")
            
            if len(cash_candidates) > 10:
                print(f"      ... and {len(cash_candidates) - 10} more")
            
            # Take the first one as the cash instrument
            ref_id = int(cash_candidates.iloc[0]["ref_id"])
            stock_name = cash_candidates.iloc[0].get("stock_name", "N/A")
            deriv_type = cash_candidates.iloc[0].get("derivative_type", "N/A")
            exchange = cash_candidates.iloc[0].get("exchange", "N/A")
            
            print(f"\n   ✅ Selected: ref_id = {ref_id} (Name: {stock_name}, Type: {deriv_type}, Exchange: {exchange})")
            cash_ref_ids[stock] = ref_id
        else:
            # No clear cash instrument found, show all instruments for manual selection
            print(f"\n   ⚠️  No clear cash instrument found. Showing all instruments:")
            display_cols = ["ref_id", "asset", "stock_name", "derivative_type", "strike_price", "exchange"]
            display_cols = [col for col in display_cols if col in stock_instruments.columns]
            
            for idx, row in stock_instruments.head(20).iterrows():
                ref_id = row.get("ref_id", "N/A")
                stock_name = row.get("stock_name", "N/A")
                deriv_type = row.get("derivative_type", "N/A")
                strike = row.get("strike_price", "N/A")
                exchange = row.get("exchange", "N/A")
                print(f"      ref_id: {ref_id:8} | Name: {stock_name:20} | Type: {deriv_type:10} | Strike: {strike:8} | Exchange: {exchange}")
            
            if len(stock_instruments) > 20:
                print(f"      ... and {len(stock_instruments) - 20} more")
            
            print(f"\n   ⚠️  Please manually select the cash ref_id from the list above")
            cash_ref_ids[stock] = 0
    
    print("\n" + "=" * 60)
    print("Config.ini Update:")
    print("=" * 60)
    print("\nUpdate your config.ini with:")
    print(f'cash_ids = {cash_ref_ids}')
    print("\nOr copy this JSON:")
    import json
    print(json.dumps(cash_ref_ids, indent=2))
    
    return cash_ref_ids

if __name__ == "__main__":
    main()
