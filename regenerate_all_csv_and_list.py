#!/usr/bin/env python3
"""
Regenerate all CSVs and ref_ids list in PROD environment.

Pipeline:
1. get_option_instruments.py (PROD) -> option_ref_ids_all.csv, option_ref_ids_filtered.csv
2. filter_feb_expiry.py -> option_ref_ids_feb_expiry.csv
3. filter_feb_by_strikes.py -> option_ref_ids_feb_filtered_by_strikes.csv
4. extract_ref_ids.py -> ref_ids_list.json, ref_ids_list.txt, ref_ids_list.py
"""

import subprocess
import sys

def run(script_name, description):
    print(f"\n{'='*60}")
    print(f"Step: {description}")
    print(f"Running: python {script_name}")
    print('='*60)
    result = subprocess.run([sys.executable, script_name])
    if result.returncode != 0:
        print(f"\n❌ Failed: {script_name} exited with code {result.returncode}")
        sys.exit(result.returncode)
    print(f"✅ Done: {script_name}\n")

def main():
    print("Regenerating all CSVs and ref_ids list (PROD environment)")
    
    run("get_option_instruments.py", "Fetch instruments from Nubra PROD, save option_ref_ids_all.csv and option_ref_ids_filtered.csv")
    run("filter_feb_expiry.py", "Filter February expiry -> option_ref_ids_feb_expiry.csv")
    run("filter_feb_by_strikes.py", "Filter by strike ranges -> option_ref_ids_feb_filtered_by_strikes.csv")
    run("extract_ref_ids.py", "Extract ref_ids -> ref_ids_list.json, .txt, .py")
    
    print("\n" + "="*60)
    print("✅ All done. Generated files:")
    print("  - option_ref_ids_all.csv")
    print("  - option_ref_ids_filtered.csv")
    print("  - option_ref_ids_feb_expiry.csv")
    print("  - option_ref_ids_feb_filtered_by_strikes.csv")
    print("  - ref_ids_list.json")
    print("  - ref_ids_list.txt")
    print("  - ref_ids_list.py")
    print("="*60)

if __name__ == "__main__":
    main()
