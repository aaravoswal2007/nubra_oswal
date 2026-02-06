#!/usr/bin/env python3
"""
Phase 1 acceptance: instrument_dict (key_name → ref_id) + ensure_nubra().
Run from nubra_oswal: python phase1_verify.py
- Prints instrument count and sample; resolves ADANIGREEN_920_CE.
- Optionally checks ensure_nubra() (will prompt login if not already).
"""

from instrument_dict_nubra import ensure_instrument_dict

def main():
    instrument_dict = ensure_instrument_dict()
    print(f"[Phase 1] instrument_dict: {len(instrument_dict)} instruments (key_name → ref_id)")
    for k, v in list(instrument_dict.items())[:5]:
        print(f"  {k} -> {v}")
    key = "ADANIGREEN_920_CE"
    if key in instrument_dict:
        print(f"  {key} -> {instrument_dict[key]}")
    else:
        print(f"  (no {key} in dict)")

    # Optional: ensure Nubra client (may prompt login)
    try:
        from nubra_client import ensure_nubra, _env_nubra
        nubra = ensure_nubra()
        print(f"[Phase 1] ensure_nubra() OK (env={_env_nubra()})")
    except Exception as e:
        print(f"[Phase 1] ensure_nubra() skipped or failed: {e}")

if __name__ == "__main__":
    main()
