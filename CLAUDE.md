# Preamato Listing Helper

Read this before making changes — it's the fast path to full context instead of re-deriving it from the code.

## What this is

A Streamlit app + CLI (`app.py` / `src/main.py`) that replaces manual eBay bulk-upload work for
Sammy's preloved-luxury eBay store. It takes a Master File (product data), a Pictures &
Measurements file, and optionally a real eBay bulk-listing template, and produces a ready-to-upload
eBay file — AI-written SEO titles/descriptions (via the Anthropic API), mapped categories, and
fixed business constants (VAT%, shipping/return/payment profiles, etc.) filled in. Goal: replace
Optiseller (~£3k/month) and cut listing turnaround from ~2 weeks to same-day.

This is a real, currently-used production tool for a real business — not a demo. Get changes
right; a wrong category mapping or a missing required field can suppress or break a live listing.

## Architecture

- `app.py` — Streamlit UI.
- `src/main.py` — CLI entry point (same pipeline as the UI).
- `src/pipeline.py` — orchestration: loads sources, maps categories, generates AI content, writes
  output. Read this first to understand the overall flow.
- `src/data_loader.py` — reads/merges Master File(s) + Measurements file(s), matches products
  (measurements file's `Name` column against master file's `SKU`; the measurements file's own
  `SKU` column is actually the barcode, not used for matching).
- `src/ebay_template.py` — parses a real uploaded eBay "Create listings in bulk" template. Uses
  `python_calamine`, not `openpyxl` — real eBay template exports are malformed for openpyxl.
- `src/builtin_catalog.py` — fallback used when no template is uploaded: builds a synthetic
  template from `data/account_listings_export.csv` (a "Download All Listings" export of this
  account's own historical listings). **That CSV is currently missing from the repo** — it existed
  only on Felix's machine and was never committed. Until Sammy re-exports and adds it, the
  no-template fallback path won't work; uploading a real template each run does work.
- `src/category_mapping.py` — maps each product to a category actually present in the given
  template (AI-assisted, cached). Deliberately scoped to only the template's own categories, not
  eBay's full tree — see `src/category_codes.py` below for why.
- `src/category_codes.py` — loader/keyword-search over eBay's full category tree
  (`data/Ebay Category Codes.csv`, also not yet generated — see below). Currently a **standalone
  reference/lookup tool only** (`python3 -m src.category_codes "query"`), intentionally NOT wired
  into the live pipeline: the full tree carries no Required/Preferred/Optional aspect data, so
  auto-picking from it could produce listings missing required item specifics. Get sign-off from
  Sammy before changing that.
- `src/content_generator.py` / `src/ai_client.py` / `src/brand_blurb.py` — AI title/description
  generation, cached per SKU.
- `src/config.py` — real business constants (VAT%, location, shipping/return/payment profile
  names, price ratio, etc.). These are Sammy's actual account settings, not placeholders.
- `src/aspect_matching.py`, `src/schema.py`, `src/build.py` — item-specific field matching and
  final row assembly.
- `ASSUMPTIONS.md` — detailed, per-category notes on what's tested vs. untested, and every
  judgment call baked into the pipeline (pricing ratio, condition grading defaults, category
  terminology bridging, etc.). Read before touching category-mapping or content-generation logic.
- `README.md` — setup and usage instructions.

## Known gaps / open items (as of 03.09.26)

1. `data/account_listings_export.csv` (built-in catalog source) — missing, needs Sammy to
   re-export "Download All Listings" from Seller Hub.
2. `data/Ebay Category Codes.csv` (full category reference) — not yet generated. Run
   `scripts/fetch_ebay_category_tree.py` once Sammy has free `developer.ebay.com` API keys
   (App ID / Cert ID) — see that script's docstring for the exact steps.
3. Not yet tested end-to-end with real Master File + Measurements file + a live Anthropic API key
   — only static analysis (`py_compile`, `pyflakes`) and manual review so far.
4. Jewellery and Lifestyle (homeware) categories are completely untested — see ASSUMPTIONS.md.
   Treat AI output for those categories with real skepticism until validated.
5. `STORE_CATEGORY_MAP` in `src/config.py` is empty — no source data gives Sammy's private eBay
   Store category IDs yet.

## Working conventions

- Run `python3 -m py_compile <changed files>` and `python3 -m pyflakes <changed files>` before
  calling a change done — this codebase has caught real bugs this way before (missing imports,
  missing dependencies).
- `requirements.txt` must list every third-party import actually used — check
  `python-calamine`/`openpyxl`/`anthropic`/`streamlit` stay in sync with what's imported.
- Cache files (`cache/category_mapping*.json`, `cache/content_cache.json`) are gitignored and
  per-environment — don't try to commit or "fix" them.
- The full project history and business context lives in Sammy's Claude Project ("ebay listing
  creation") — worth checking there for anything this file doesn't cover.
