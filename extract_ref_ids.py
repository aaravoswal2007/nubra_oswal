#!/usr/bin/env python3
"""
Extract ref_id column from option_ref_ids_feb_filtered_by_strikes.csv
and save as a Python list.
"""

import pandas as pd
import json

def main():
    # Read the CSV
    df = pd.read_csv("option_ref_ids_feb_filtered_by_strikes.csv")
    
    # Extract ref_id column as a list
    ref_ids = df["ref_id"].tolist()
    
    print(f"Total ref_ids: {len(ref_ids)}")
    print(f"First 10 ref_ids: {ref_ids[:10]}")
    
    # Save as Python list file (.py)
    with open("ref_ids_list.py", "w") as f:
        f.write("# List of ref_ids from option_ref_ids_feb_filtered_by_strikes.csv\n")
        f.write(f"ref_ids = {ref_ids}\n")
    print("\n✅ Saved as Python list to: ref_ids_list.py")
    
    # Save as JSON array
    with open("ref_ids_list.json", "w") as f:
        json.dump(ref_ids, f, indent=2)
    print("✅ Saved as JSON array to: ref_ids_list.json")
    
    # Save as plain text (one per line)
    with open("ref_ids_list.txt", "w") as f:
        for ref_id in ref_ids:
            f.write(f"{ref_id}\n")
    print("✅ Saved as text file (one per line) to: ref_ids_list.txt")

if __name__ == "__main__":
    main()
