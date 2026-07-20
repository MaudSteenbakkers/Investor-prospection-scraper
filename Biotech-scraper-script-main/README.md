[README.md](https://github.com/user-attachments/files/30189196/README.md)
# Biotech/Pharma Prospection Scraper

Takes a list of company names + website URLs, visits each site, and detects
which therapeutic areas (indications) that company appears to work on --
matched against InnoSer's HubSpot indication list. Originally built in
Google Colab; migrated to GitHub Actions in July 2026.

## What it does

1. Crawls each company's website (pipeline/portfolio/science pages preferred)
2. Filters out companies that look like CROs, CDMOs, diagnostics/tools
   companies, or academic/non-profit orgs, rather than drug developers
3. Matches page text against a curated indication keyword list, requiring
   real evidence of intent (e.g. "Phase 2", "our lead candidate") outside
   of dedicated pipeline pages -- a bare mention of a disease name on an
   About page doesn't count
4. Outputs a results file (one row per company, with matched indications)
   and an audit file (every keyword match/rejection, with the reasoning)

## How to run it

1. Upload your input Excel file into the `input/` folder of this repo
   (GitHub web UI: **Add file > Upload files**). It needs at minimum a
   company name column and a website URL column.
2. Go to the **Actions** tab > **Biotech Prospection Scraper** > **Run workflow**
3. Fill in:
   - **input_file**: path to your uploaded file, e.g. `input/companies.xlsx`
   - **chunk_count**: how many parallel jobs to split the list across (default 4).
     Each job has a 6-hour hard limit -- for ~1000 companies, 4-6 chunks keeps
     each comfortably under that. More companies or slower sites -> more chunks.
   - **company_col** / **website_col**: only if your column names differ from
     the defaults (`company_name`, `website_domain`)
4. Click **Run workflow**. It'll run several jobs in parallel (one per chunk),
   then a final **merge** job combines them.
5. Once finished, download the **final-results** artifact from the workflow
   run page -- it contains `*_results_merged.xlsx` (import this into HubSpot)
   and `*_audit_merged.xlsx` (for double-checking anything that looks off).

**Max recommended batch size: 1000 companies per run.**

## One-time setup (already done as of July 2026, documented for reference)

- Repo is **private** -- only accounts you add as collaborators can see it
  or its run history.
- An Anthropic API key is stored as a repo secret (`ANTHROPIC_API_KEY`),
  used only for a small number of ambiguous company-type classifications
  per run (typically well under $1/run in API cost). Never put this key
  directly in code -- if it needs rotating, generate a new one at
  console.anthropic.com and update the secret in Settings > Secrets and
  variables > Actions.
- Playwright (headless browser) is installed automatically by the workflow,
  used only as a fallback for the small number of pages whose pipeline
  content looks JS-rendered rather than plain HTML.

## Local testing (optional)

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in your own ANTHROPIC_API_KEY for local testing
export $(cat .env | xargs)
python src/main.py --input path/to/companies.xlsx --output-dir output
```

## Troubleshooting

- **A run fails partway through**: check the failed chunk's job log in the
  Actions tab -- other chunks are unaffected (`fail-fast: false`), so you
  only need to re-run the failed chunk_index, not the whole batch.
- **Results look sparse for a company you know has a public pipeline**:
  check the audit file for that company's `page_url` rows -- if the pipeline
  page uses images instead of text, the `manual_check` column will flag it
  for manual review.
- **Merge step finds no chunk files**: confirm `chunk_count` and
  `input_file` matched between the run and what you expect -- the merge
  job looks for files matching the input file's base name.

## Who owns this

Maud Steenbakkers (AI Implementation, InnoSer). Ping her with questions or
if this needs a change to the keyword list, exclusion rules, or HubSpot
column mapping.
