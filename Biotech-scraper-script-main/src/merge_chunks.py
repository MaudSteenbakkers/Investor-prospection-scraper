"""
Merge chunked output files from a GitHub Actions matrix run into single
results/audit files. Run after all matrix jobs complete and their
artifacts have been downloaded into one directory.

USAGE:
    python merge_chunks.py --input-dir output --base-name companies
"""

import argparse
import glob
import os

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, help="Directory containing chunked output files")
    parser.add_argument("--base-name", required=True, help="Base filename used for the original input")
    args = parser.parse_args()

    results_files = sorted(glob.glob(os.path.join(args.input_dir, f"{args.base_name}_results_chunk*.xlsx")))
    audit_files = sorted(glob.glob(os.path.join(args.input_dir, f"{args.base_name}_audit_chunk*.xlsx")))

    if not results_files:
        raise SystemExit(f"No chunked results files found matching {args.base_name}_results_chunk*.xlsx in {args.input_dir}")

    print(f"Merging {len(results_files)} results chunks and {len(audit_files)} audit chunks...")

    results_df = pd.concat([pd.read_excel(f) for f in results_files], ignore_index=True)
    if "_original_row" in results_df.columns:
        results_df = results_df.sort_values("_original_row").drop(columns=["_original_row"]).reset_index(drop=True)

    audit_df = pd.concat([pd.read_excel(f) for f in audit_files], ignore_index=True) if audit_files else pd.DataFrame()

    merged_results_path = os.path.join(args.input_dir, f"{args.base_name}_results_merged.xlsx")
    merged_audit_path = os.path.join(args.input_dir, f"{args.base_name}_audit_merged.xlsx")

    results_df.to_excel(merged_results_path, index=False)
    audit_df.to_excel(merged_audit_path, index=False)

    print(f"\u2705 Merged results: {merged_results_path}")
    print(f"\u2705 Merged audit: {merged_audit_path}")


if __name__ == "__main__":
    main()
