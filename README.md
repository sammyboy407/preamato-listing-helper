# Preamato Listing Helper

Combines the Master File (product data), the Pictures & Measurements file (photos + condition
notes for whatever's currently been photographed), and eBay's category list into a ready-to-upload
eBay File Exchange spreadsheet — with AI-written SEO titles/descriptions, condition grading, and
item specifics.

## Setup

```bash
python3 -m pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # your own key
```

## Run — UI (recommended for non-technical use)

```bash
streamlit run app.py
```

Opens a browser tab. Paste your API key, upload the Master File and the Pictures & Measurements
file, and click Generate. eBay's category list is bundled in — see below.

## Run — CLI

```bash
python3 -m src.main \
  --master "/path/to/Master File.xlsx" \
  --measurements "/path/to/Pictures and Measurements.csv" \
  --output "output/ebay_upload.xlsx"
```

Useful flags:
- `--limit 3` — only process the first 3 matched products (cheap smoke test before a full run)
- `--workers 4` — how many products to generate AI content for concurrently
- `--force-regenerate` — ignore the content cache and re-call the AI for every product
- `--categories` — override the bundled eBay category file with a different one (see below)

## eBay category list is built in

`data/Ebay Category Codes.csv` is bundled with the project — eBay's category tree changes rarely,
so there's no need to re-upload it each run. If eBay ever restructures categories, replace that
file with an updated export (same column layout: L1..L6 + Category ID) and both the UI and CLI
will pick it up automatically.

## How it works

1. **Matching**: only products present in the measurements file are processed — no photos, no
   listing. Matched on the measurements file's `Name` column against the master file's `SKU`
   (note: the measurements file's own `SKU` column is actually the barcode, not the SKU).
2. **Category mapping**: the ~90 distinct (Category, SubCat2, Gender) combos in your master file
   are mapped once to a real eBay leaf Category ID via Claude, cached in `cache/category_mapping.json`.
   Re-running never re-spends on a combo already mapped — delete that file to force a refresh.
3. **Per-product AI content**: one Claude call per SKU returns the eBay Title, Description,
   Condition ID, ConditionDescription, and every applicable item-specific (`C:`) field. Cached
   per-SKU in `cache/content_cache.json`.
4. **Deterministic fields**: SKU, price (Start price = 50% of RRP, matching every example row),
   quantity, image URLs, brand/colour/size/country, and the measurement-in-inches columns are
   copied directly from source data — never AI-generated.
5. **Fixed constants**: Format, Duration, Currency, VAT%, Location, shipping/return/payment
   profile names, Best Offer Enabled — taken from your example Output.xlsx. Edit `src/config.py`
   if these ever change.

## Known gap: StoreCategory

Your private eBay Store's category IDs aren't derivable from any file you gave me — the example
output uses ~4 different StoreCategory IDs with no visible source data or rule. `src/config.py`
has an empty `STORE_CATEGORY_MAP` (keyed by the master file's `Category` column) — fill it in with
your real Store category IDs once you have that mapping, otherwise the output column is left blank.

## Cost note

Every product costs one Claude API call for content generation, plus a one-off call per distinct
product-type combo for category mapping. Use `--limit` for a cheap test run before processing the
full batch.
