"""
InnoSer Prospection Indication Scraper -- migrated from Google Colab.

Takes an Excel file of company names + website URLs, visits each site,
and detects which therapeutic areas (indications) the company works on.

USAGE:
    python main.py --input companies.xlsx
    python main.py --input companies.xlsx --chunk-index 0 --chunk-count 4

CHUNKING (for GitHub Actions matrix jobs):
    A single run of 1000 companies used to take up to 12 hours, which
    exceeds GitHub Actions' 6-hour job limit. --chunk-index/--chunk-count
    let a matrix of parallel jobs each process a slice of the same input
    file; a separate merge step (see merge_chunks.py) combines the results
    afterward. Max recommended batch size per Maud: 1000 companies total,
    split across enough chunks that each chunk finishes well under 6 hours.

OUTPUT (per chunk, or single file if not chunked):
    <input_basename>_results[_chunkN].xlsx  -- original data + matched keywords
    <input_basename>_audit[_chunkN].xlsx    -- detailed keyword match/rejection log
"""

import argparse
import contextlib
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from config import CHILD_TO_PARENT
from crawler import crawl_website, normalize_url
from classify import extract_keywords, is_drug_developer


def clean_excel_string(text):
    if isinstance(text, str):
        return re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', text)
    return text


def process_company(idx, company, website, max_pages, use_playwright_fallback):
    """
    Runs _process_company on a worker thread but captures its print()
    output into a buffer, so log lines from concurrent companies don't
    interleave in the console -- each company's full log block is printed
    as one atomic chunk once it finishes.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result, audit_rows = _process_company(idx, company, website, max_pages, use_playwright_fallback)
    print(buf.getvalue(), end="")
    return result, audit_rows


def _process_company(idx, company, website, max_pages, use_playwright_fallback):
    """Process a single company. Returns a dict of results + list of audit rows."""
    print(f"\n{'='*60}")
    print(f"[{idx+1}] {company}")
    print(f"    URL: {website}")

    if pd.isna(website) or str(website).strip() == "":
        print("    \u2757 Skipped -- no website.")
        return {
            "idx": idx,
            "matched_keywords": "",
            "pages_crawled": 0,
            "company_type": "Unknown",
            "is_biotech_pharma": "Unknown",
            "manual_check": "",
        }, []

    soups_by_url = crawl_website(str(website), max_pages=max_pages,
                                  use_playwright_fallback=use_playwright_fallback)
    print(f"    Pages crawled: {len(soups_by_url)}")

    homepage_url = normalize_url(str(website))
    homepage_soup = soups_by_url.get(homepage_url) or (
        list(soups_by_url.values())[0] if soups_by_url else None
    )
    is_target, company_label = is_drug_developer(homepage_soup)
    print(f"    \U0001f3f7  Company type: {company_label}")

    company_keywords = set()
    pipeline_img_flag = False
    audit_rows = []

    for url, soup in soups_by_url.items():
        page_kws, page_audit, img_flag = extract_keywords(soup, company, url)
        company_keywords.update(page_kws)
        audit_rows.extend(page_audit)
        if img_flag:
            pipeline_img_flag = True

    parents_to_add = {CHILD_TO_PARENT[kw] for kw in company_keywords if kw in CHILD_TO_PARENT}
    if parents_to_add:
        print(f"    \u21b3 Auto-added parent categories: {sorted(parents_to_add)}")
    company_keywords.update(parents_to_add)

    hits = sorted(company_keywords)
    EM_DASH = "\u2014"
    print(f"    \u2713 Matched: {hits if hits else EM_DASH}")
    if pipeline_img_flag:
        print("    \u26a0 Pipeline may contain images -- manual check advised")

    return {
        "idx": idx,
        "matched_keywords": "; ".join(hits),
        "pages_crawled": len(soups_by_url),
        "company_type": company_label,
        "is_biotech_pharma": "Yes" if is_target else "No",
        "manual_check": (
            "\u26a0 Pipeline may contain images -- check website manually"
            if pipeline_img_flag else ""
        ),
    }, audit_rows


def main():
    parser = argparse.ArgumentParser(description="InnoSer prospection indication scraper")
    parser.add_argument("--input", required=True, help="Path to input Excel file")
    parser.add_argument("--output-dir", default="output", help="Directory for output files")
    parser.add_argument("--company-col", default="company_name", help="Column with company names")
    parser.add_argument("--website-col", default="website_domain", help="Column with website URLs")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages to crawl per company")
    parser.add_argument("--workers", type=int, default=3,
                         help="Number of companies to process concurrently")
    parser.add_argument("--no-playwright-fallback", action="store_true",
                         help="Disable the Playwright fallback for sparse pipeline pages")
    parser.add_argument("--chunk-index", type=int, default=0,
                         help="Which chunk this run processes (0-indexed)")
    parser.add_argument("--chunk-count", type=int, default=1,
                         help="Total number of chunks the input is split into")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    df = pd.read_excel(args.input)
    print(f"Loaded {len(df)} companies. Columns: {list(df.columns)}")

    if args.chunk_count > 1:
        df = df.iloc[args.chunk_index::args.chunk_count].reset_index(drop=False)
        df = df.rename(columns={"index": "_original_row"})
        print(f"Chunk {args.chunk_index}/{args.chunk_count}: processing {len(df)} companies")

    results_by_idx = {}
    all_audit_results = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                process_company,
                idx,
                row.get(args.company_col, f"row_{idx}"),
                row.get(args.website_col, ""),
                args.max_pages,
                not args.no_playwright_fallback,
            ): idx
            for idx, row in df.iterrows()
        }
        for future in as_completed(futures):
            result, audit_rows = future.result()
            results_by_idx[result["idx"]] = result
            all_audit_results.extend(audit_rows)

    df["matched_keywords"] = [results_by_idx[i]["matched_keywords"] for i in df.index]
    df["pages_crawled"] = [results_by_idx[i]["pages_crawled"] for i in df.index]
    df["company_type"] = [results_by_idx[i]["company_type"] for i in df.index]
    df["is_biotech_pharma"] = [results_by_idx[i]["is_biotech_pharma"] for i in df.index]
    df["manual_check"] = [results_by_idx[i]["manual_check"] for i in df.index]

    base_name = os.path.splitext(os.path.basename(args.input))[0]
    suffix = f"_chunk{args.chunk_index}" if args.chunk_count > 1 else ""
    results_file = os.path.join(args.output_dir, f"{base_name}_results{suffix}.xlsx")
    audit_file = os.path.join(args.output_dir, f"{base_name}_audit{suffix}.xlsx")

    df.to_excel(results_file, index=False)

    cleaned_audit_results = [
        {k: clean_excel_string(v) for k, v in entry.items()}
        for entry in all_audit_results
    ]
    pd.DataFrame(cleaned_audit_results).to_excel(audit_file, index=False)

    print(f"\n\u2705 Done!")
    print(f"   Results saved to: {results_file}")
    print(f"   Audit log saved to: {audit_file}")


if __name__ == "__main__":
    main()
