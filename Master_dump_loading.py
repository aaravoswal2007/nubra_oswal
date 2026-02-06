# Master_dump_loading.py
import pandas as pd
import os
from datetime import datetime
from nubra_python_sdk.refdata.instruments import InstrumentData


def load_master_dump(nubra, max_retries=2):
    """
    Load instruments from Nubra SDK (equivalent to XTS master dump).
    
    Args:
        nubra: Initialized Nubra SDK client
        max_retries: Maximum number of retry attempts (default: 2)
    
    Returns:
        pandas.DataFrame: Master dump DataFrame with XTS-like structure, or None if loading failed
    """
    instruments = InstrumentData(nubra)
    
    for attempt in range(max_retries):
        try:
            df_all = instruments.get_instruments_dataframe()
            
            if df_all is None or df_all.empty:
                print(f"Attempt {attempt + 1}/{max_retries}: No instrument data available")
                if attempt < max_retries - 1:
                    print("Retrying...")
                    continue
                else:
                    print("Error: Failed to load instruments after retries")
                    return None
            
            # Filter for options only
            # Convert derivative_type to uppercase for case-insensitive comparison
            df_options = df_all[
                df_all["derivative_type"].astype(str).str.strip().str.upper() == "OPT"
            ].copy()
            
            if df_options.empty:
                print(f"Attempt {attempt + 1}/{max_retries}: No option instruments found")
                if attempt < max_retries - 1:
                    print("Retrying...")
                    continue
                else:
                    print("Error: No option instruments available")
                    return None
            
            # Map Nubra columns to XTS-like structure
            # Create a DataFrame with columns matching XTS master dump structure
            masterdf = pd.DataFrame()
            
            # Map columns
            masterdf['ExchangeInstrumentID'] = df_options['ref_id'].astype(int)  # Store ref_id but name it ExchangeInstrumentID
            masterdf['Name'] = df_options['asset'].astype(str).str.strip().str.upper()
            # Nubra stores strike prices in paise (e.g., 62000 for 620.00), convert to rupees by dividing by 100
            strike_price_paise = pd.to_numeric(df_options['strike_price'], errors='coerce')
            masterdf['StrikePrice'] = strike_price_paise / 100.0  # Convert from paise to rupees
            masterdf['ContractExpiration'] = pd.to_datetime(df_options['expiry'], errors='coerce').dt.date
            
            # Map option_type: "CE" -> 3, "PE" -> 4 (matching XTS format)
            option_type_map = {"CE": 3, "PE": 4}
            masterdf['OptionType'] = df_options['option_type'].astype(str).str.strip().str.upper().map(option_type_map)
            
            # Add other useful columns if available
            if 'lot_size' in df_options.columns:
                masterdf['LotSize'] = pd.to_numeric(df_options['lot_size'], errors='coerce')
            if 'tick_size' in df_options.columns:
                masterdf['TickSize'] = pd.to_numeric(df_options['tick_size'], errors='coerce')
            if 'stock_name' in df_options.columns:
                masterdf['StockName'] = df_options['stock_name'].astype(str)
            
            # Remove rows with missing critical data
            masterdf = masterdf.dropna(subset=['ExchangeInstrumentID', 'Name', 'StrikePrice', 'ContractExpiration', 'OptionType'])
            
            print(f"Master dump loaded successfully: {len(masterdf)} option instruments")
            
            # ===== Save Master Dump (Optional) =====
            today_str = datetime.now().strftime('%Y-%m-%d')
            filename_today = f"option_ids_{today_str}.xlsx"
            current_folder = os.path.dirname(os.path.abspath(__file__))
            filepath_today = os.path.join(current_folder, filename_today)
            
            maindf = masterdf[['ExchangeInstrumentID', 'Name', 'StrikePrice', 'ContractExpiration', 'OptionType']]
            if 'LotSize' in masterdf.columns:
                maindf['LotSize'] = masterdf['LotSize']
            
            if not os.path.exists(filepath_today):
                maindf.to_excel(filepath_today, index=False, engine='openpyxl')
                print(f"New file created: {filename_today}")
            
            return masterdf
            
        except Exception as e:
            print(f"Attempt {attempt + 1}/{max_retries}: Error loading master dump: {e}")
            if attempt < max_retries - 1:
                print("Retrying...")
                continue
            else:
                print("Error: Failed to load master dump after all retries")
                return None
    
    return None
