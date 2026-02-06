# configloader.py
import os, configparser
import json

def load():
    config = configparser.ConfigParser()

    # 1) If Electron passed an explicit path, use it
    env_path = os.environ.get("ALGO_CONFIG")
    if env_path and os.path.isfile(env_path):
        config.read(env_path)
        return config

    # 2) Fallback: config.ini sitting next to this file
    here = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(here, "config.ini")
    if os.path.isfile(local_path):
        config.read(local_path)
        return config

    # 3) Last fallback: current working dir (only if you run scripts manually)
    cwd_path = os.path.join(os.getcwd(), "config.ini")
    if os.path.isfile(cwd_path):
        config.read(cwd_path)

    return config

def config_path():
    """For debugging in logs."""
    return (
        os.environ.get("ALGO_CONFIG")
        or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    )

def get_cash_ids():
    """
    Helper function to get cash ref_ids for stocks.
    Returns a dictionary: {"STOCK_NAME": cash_ref_id, ...}
    Note: These are Nubra ref_ids, not ExchangeInstrumentIDs
    """
    config = load()
    cash_ids_str = config.get("stocks", "cash_ids")
    return json.loads(cash_ids_str)

def get_stock_steps():
    """
    Helper function to get stock configuration (step, lot_size, depth).
    Returns a dictionary: {"STOCK_NAME": {"step": int, "lot_size": int, "depth": int}, ...}
    """
    config = load()
    stock_steps_str = config.get("stocks", "stock_steps")
    return json.loads(stock_steps_str)
