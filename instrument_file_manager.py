# instrument_file_manager.py
import os
import pandas as pd
from datetime import datetime


def save_instrument_dict_to_file(instrument_dict, folder_path="."):
    """
    Save instrument dictionary to Excel file.
    
    Args:
        instrument_dict: Dictionary mapping instrument names (key_name) to ExchangeInstrumentID (ref_id values)
        folder_path: Path to folder where file should be saved
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    filename = f"instrument_dict_{today_str}.xlsx"
    filepath = os.path.join(folder_path, filename)

    records = []
    for name, eid in instrument_dict.items():
        parts = name.split("_")
        stock = parts[0]
        # defaults
        strike = ""
        opttype = ""

        if len(parts) == 3:
            # e.g. RELIANCE_1280_CE
            strike, opttype = parts[1], parts[2]
        elif len(parts) == 2:
            # e.g. RELIANCE_FUT or any 2-part key
            strike = parts[1]
            # leave opttype blank (or set to "FUT" if that's your convention)
        else:
            # len(parts) == 1 → spot symbol like RELIANCE
            opttype = "SPOT"

        records.append({
            "Stock": stock,
            "StrikePrice": strike,
            "OptionType": opttype,
            "ExchangeInstrumentID": eid  # Contains ref_id value, but named ExchangeInstrumentID for compatibility
        })

    df = pd.DataFrame(records)
    df.to_excel(filepath, index=False, engine="openpyxl")
    print(f"Instrument dict saved to: {filepath}")
