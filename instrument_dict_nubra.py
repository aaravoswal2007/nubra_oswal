#!/usr/bin/env python3
"""
Phase 1: Nubra instrument map (key_name → ref_id).
Same key_name format as Oswal: {asset}_{strike_price//100}_{option_type}.
Build from option_ref_ids_feb_filtered_by_strikes.csv; optionally cache to JSON.
"""

import csv
import json
from pathlib import Path

from typing import Dict

import pandas as pd

_BASE = Path(__file__).resolve().parent
DEFAULT_CSV = "option_ref_ids_feb_filtered_by_strikes.csv"
DEFAULT_JSON = "instrument_dict_nubra.json"

# Module-level dict: key_name (str) -> ref_id (int). Loaded on first use.
instrument_dict: dict[str, int] = {}


def _key_name(asset: str, strike_price: int, option_type: str) -> str:
    """Same formula as subscribe_orderbook / Oswal."""
    return f"{asset}_{strike_price // 100}_{option_type}"


def load_from_csv(csv_path: str | Path | None = None) -> dict[str, int]:
    """Build key_name → ref_id from CSV. Does not modify module-level instrument_dict."""
    path = _BASE / (csv_path or DEFAULT_CSV)
    mapping: dict[str, int] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ref_id = int(row["ref_id"])
                asset = (row.get("asset") or "").strip()
                strike_price = int(row.get("strike_price") or 0)
                option_type = (row.get("option_type") or "").strip()
                key_name = _key_name(asset, strike_price, option_type)
                mapping[key_name] = ref_id
            except (ValueError, KeyError):
                continue
    return mapping


def load_tick_size_map(csv_path: str | Path | None = None) -> dict[int, int]:
    """Build ref_id → tick_size (paise) from CSV. Returns dict[int, int]."""
    path = _BASE / (csv_path or DEFAULT_CSV)
    mapping: dict[int, int] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ref_id = int(row["ref_id"])
                tick_size = int(row.get("tick_size") or 5)  # default 5 paise if missing
                mapping[ref_id] = tick_size
            except (ValueError, KeyError):
                continue
    return mapping


def load_from_excel_latest(folder: Path | None = None) -> Dict[str, int]:
    """
    Load key_name → ref_id from the latest instrument_dict_YYYY-MM-DD.xlsx file
    produced by instruments_to_sub.py / instrument_file_manager.save_instrument_dict_to_file.
    """
    folder = folder or _BASE
    pattern = "instrument_dict_*.xlsx"
    files = sorted(folder.glob(pattern))
    if not files:
        print("[instrument_dict_nubra] No instrument_dict_*.xlsx found; will fall back to CSV.")
        return {}
    latest = files[-1]
    print(f"[instrument_dict_nubra] Loading instrument_dict from Excel: {latest.name}")
    df = pd.read_excel(latest)
    mapping: Dict[str, int] = {}
    for _, row in df.iterrows():
        stock = str(row.get("Stock") or "").strip().upper()
        strike_raw = row.get("StrikePrice")
        opttype = str(row.get("OptionType") or "").strip().upper()
        ref_id = row.get("ExchangeInstrumentID")
        try:
            ref_id_int = int(ref_id)
        except (TypeError, ValueError):
            continue
        # Normalize strike: if it's a number, convert to int if whole (920.0 -> 920), else keep as string
        strike = ""
        if strike_raw is not None:
            try:
                strike_float = float(strike_raw)
                if strike_float == int(strike_float):
                    strike = str(int(strike_float))  # 920.0 -> "920"
                else:
                    strike = str(strike_float)  # 920.5 -> "920.5"
            except (TypeError, ValueError):
                strike = str(strike_raw).strip()
        if opttype == "SPOT" or (not strike and not opttype):
            key_name = stock
        elif strike and opttype:
            key_name = f"{stock}_{strike}_{opttype}"
        elif strike:
            key_name = f"{stock}_{strike}"
        else:
            key_name = stock
        mapping[key_name] = ref_id_int
    # Debug: show a few entries, especially ADANIGREEN_920_CE if present
    print(f"[instrument_dict_nubra] Loaded {len(mapping)} entries from Excel.")
    # Check both formats (with/without .0)
    for key in ["ADANIGREEN_920_CE", "ADANIGREEN_920.0_CE"]:
        if key in mapping:
            print(f"[instrument_dict_nubra] Excel mapping: {key} -> {mapping[key]}")
            break
    # Show sample keys for debugging
    sample_keys = [k for k in mapping.keys() if "ADANIGREEN" in k and "920" in k][:5]
    if sample_keys:
        print(f"[instrument_dict_nubra] Sample ADANIGREEN_920 keys from Excel: {sample_keys}")
    return mapping


def save_to_json(mapping: dict[str, int], json_path: str | Path | None = None) -> Path:
    """Save key_name → ref_id to JSON. Returns path written."""
    path = _BASE / (json_path or DEFAULT_JSON)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    return path


def load_from_json(json_path: str | Path | None = None) -> dict[str, int] | None:
    """Load key_name → ref_id from JSON. Returns None if file missing or invalid."""
    path = _BASE / (json_path or DEFAULT_JSON)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError):
        return None


def load_instrument_dict(
    *,
    use_json_cache: bool = True,
    csv_path: str | Path | None = None,
    json_path: str | Path | None = None,
) -> dict[str, int]:
    """
    Load instrument_dict (key_name → ref_id).
    - If use_json_cache and JSON file exists, load from JSON.
    - Else load from CSV; if use_json_cache, save to JSON for next time.
    Updates and returns the module-level instrument_dict.
    """
    global instrument_dict
    if use_json_cache:
        cached = load_from_json(json_path)
        if cached is not None:
            instrument_dict = cached
            print(f"[instrument_dict_nubra] Loaded instrument_dict from JSON cache ({len(instrument_dict)} entries).")
            if "ADANIGREEN_920_CE" in instrument_dict:
                print(f"[instrument_dict_nubra] JSON mapping: ADANIGREEN_920_CE -> {instrument_dict['ADANIGREEN_920_CE']}")
            return instrument_dict
    # Prefer latest Excel snapshot if available
    mapping = load_from_excel_latest()
    if not mapping:
        # Fallback to CSV if no Excel snapshot
        mapping = load_from_csv(csv_path)
        print(f"[instrument_dict_nubra] Loaded instrument_dict from CSV ({len(mapping)} entries).")
        if "ADANIGREEN_920_CE" in mapping:
            print(f"[instrument_dict_nubra] CSV mapping: ADANIGREEN_920_CE -> {mapping['ADANIGREEN_920_CE']}")
    instrument_dict = mapping
    if use_json_cache and mapping:
        save_to_json(mapping, json_path)
    return instrument_dict


def ensure_instrument_dict() -> dict[str, int]:
    """Return module-level instrument_dict, loading from CSV/JSON if empty."""
    global instrument_dict
    if not instrument_dict:
        load_instrument_dict()
    return instrument_dict


if __name__ == "__main__":
    load_instrument_dict()
    print(f"Loaded {len(instrument_dict)} instruments (key_name → ref_id)")
    sample = list(instrument_dict.items())[:5]
    for k, v in sample:
        print(f"  {k} -> {v}")
    if "ADANIGREEN_920_CE" in instrument_dict:
        print(f"  ADANIGREEN_920_CE -> {instrument_dict['ADANIGREEN_920_CE']}")
