# fno_strikes.py
from datetime import datetime
import pandas as pd
# Note: MarketData import may need adjustment based on actual Nubra SDK structure
# Refer to Nubra API docs: https://nubra.io/products/api/docs/python-sdk/index.html
try:
    from nubra_python_sdk.marketdata.market_data import MarketData
except ImportError:
    # Fallback if import path is different
    try:
        from nubra_python_sdk.market_data import MarketData
    except ImportError:
        MarketData = None


def get_fno_strikes_around_spot(stock_step_map, cash_ids, nubra, masterdf, default_depth=12):
    """
    Build F&O subscription list around spot price for given stocks.
    
    Args:
        stock_step_map: Dictionary with stock configuration (step, depth, lot_size)
        cash_ids: Dictionary mapping stock names to cash ref_ids
        nubra: Initialized Nubra SDK client
        masterdf: DataFrame with master dump data (XTS-like structure)
        default_depth: Default depth if not specified in stock_step_map
    
    Returns:
        tuple: (instrument_list, instrument_dict)
        - instrument_list: List of ref_ids (as strings) for Nubra subscription
        - instrument_dict: {key_name: ref_id} mapping (ref_id stored as value, named ExchangeInstrumentID for compatibility)
    """
    instrument_list = []
    instrument_dict = {}
    
    # Initialize market data client
    # Note: MarketData API usage may need adjustment based on actual Nubra SDK
    if MarketData is None:
        raise ImportError("MarketData class not found. Check Nubra SDK import path.")
    market_data = MarketData(nubra)

    for stock, cfg in stock_step_map.items():
        strike_step = cfg["step"]
        depth = cfg.get("depth", default_depth)  # Use individual depth or default
        try:
            print(f"Processing {stock} with depth={depth}...")
            
            # 1️⃣ Add spot instrument first
            if stock not in cash_ids:
                print(f"Error processing {stock}: missing CASH_IDS mapping")
                continue
            spot_ref_id = cash_ids[stock]
            if spot_ref_id == 0:
                print(f"Warning: {stock} cash_ref_id is 0 (not configured). Skipping spot instrument.")
            else:
                instrument_dict[stock] = spot_ref_id  # key_name = stock name for spot
                instrument_list.append(str(spot_ref_id))  # Nubra expects strings
                print(f"Added {stock} spot to subscription list (ref_id: {spot_ref_id})")

            # 2️⃣ Get current spot LTP from Nubra
            if spot_ref_id == 0:
                print(f"Error processing {stock}: cannot get spot quote without valid cash_ref_id")
                continue
                
            try:
                # Get current market price using Nubra Current Price API
                # Reference: https://nubra.io/products/api/docs/python-sdk/market-data/current-price.html
                # The API expects symbol name (e.g., "RELIANCE", "NIFTY"), not ref_id
                current_price_obj = market_data.current_price(stock.upper())
                
                if current_price_obj is None:
                    print(f"Error processing {stock}: current_price returned None")
                    continue
                
                # Extract price from CurrentPrice object
                # Prices are returned in paise (exchange-native units), so divide by 100
                ltp = current_price_obj.price
                
                if ltp is None:
                    print(f"Error processing {stock}: price is None in response")
                    continue
                
                # Convert from paise to rupees (divide by 100)
                # Reference: https://nubra.io/products/api/docs/python-sdk/market-data/current-price.html#notes
                ltp = float(ltp) / 100.0
                
                print(f"Got spot LTP for {stock}: {ltp} (from {current_price_obj.price} paise)")
                    
            except Exception as e:
                print(f"Error processing {stock}: current_price failed: {e}")
                continue
            
            rounded_strike = round(ltp / strike_step) * strike_step
            # Filter masterdf for this stock once (reused for expiry and strike selection)
            stock_filter = masterdf['Name'] == stock
            filt = masterdf[stock_filter]
            if filt.empty:
                print(f"Error processing {stock}: no instruments in master dump")
                continue
            
            # Get all unique expiry dates sorted
            expiry_dates = sorted(filt['ContractExpiration'].unique())
            today = datetime.now().date()
            # Check if closest expiry is less than 4 days away
            closest_expiry = expiry_dates[0]
            days_to_expiry = (closest_expiry - today).days
            if days_to_expiry <= 4 and len(expiry_dates) > 1:
                # Use next expiry if closest is within 4 days
                expiry = expiry_dates[1]
                print(f" Closest expiry ({closest_expiry}) is only {days_to_expiry} days away, using next expiry ({expiry})")
            else:
                expiry = expiry_dates[0]

            # 3️⃣ Add F&O strikes
            option_type_map = {'CE': 3, 'PE': 4}
            stock_instruments_count = 0
            for option_key, option_code in option_type_map.items():
                subset = masterdf[
                    (masterdf['Name'] == stock) &
                    (masterdf['OptionType'] == option_code) &
                    (masterdf['ContractExpiration'] == expiry)
                ].copy()
                subset['StrikePriceNum'] = pd.to_numeric(subset['StrikePrice'], errors='coerce')
                
                strikes = (
                    subset['StrikePriceNum']
                    .dropna()
                    .sort_values()
                    .unique()
                )
                if len(strikes) == 0:
                    print(f"    Warning: No strikes for {stock} {option_key}")
                    continue

                def is_step_aligned(value: float) -> bool:
                    ratio = (value - rounded_strike) / strike_step
                    return abs(ratio - round(ratio)) < 1e-8

                aligned_strikes = [strike for strike in strikes if is_step_aligned(strike)]
                if not aligned_strikes:
                    print(f"    Warning: No strikes aligned to step {strike_step} for {stock} {option_key}")
                    continue

                # Find closest aligned strike to our rounded_strike
                closest_index = min(range(len(aligned_strikes)), key=lambda idx: abs(aligned_strikes[idx] - rounded_strike))
                start_index = max(0, closest_index - depth)
                end_index = min(len(aligned_strikes) - 1, closest_index + depth)

                for strike_val in aligned_strikes[start_index:end_index + 1]:
                    match = subset[subset['StrikePriceNum'] == strike_val]
                    if match.empty:
                        continue
                    ref_id = int(match.iloc[0]['ExchangeInstrumentID'])  # Contains ref_id value
                    display_strike = int(strike_val) if float(strike_val).is_integer() else strike_val
                    # Create key_name: {STOCK}_{STRIKE}_{OPTION_TYPE} (full strike, not divided by 100)
                    key_name = f"{stock.upper()}_{display_strike}_{option_key}"
                    if key_name in instrument_dict:
                        continue
                    instrument_dict[key_name] = ref_id  # Store ref_id but think of it as ExchangeInstrumentID
                    instrument_list.append(str(ref_id))  # Nubra expects strings for subscription
                    stock_instruments_count += 1
            
            print(f"   {stock}: Added {stock_instruments_count} F&O instruments (depth={depth})")

        except Exception as e:
            print(f"Error processing {stock}: {e}")
            import traceback
            traceback.print_exc()

    return instrument_list, instrument_dict
