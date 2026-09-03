# Assumptions

What the pipeline assumes, organized by Master File category. "Tested" means real
photographed products in that category have actually been run through and checked —
"untested" means the logic exists (schema, prompts) but has never seen real data.

Coverage so far, from the 32 currently-photographed products:

| Category | SubCat2 | Count | Status |
|---|---|---|---|
| Accessories | Bags | 11 | Tested |
| Footwear | Pumps, Sneakers, Mules, Flat Shoes | 9 | Tested |
| Ready to Wear | Tops, Skirts, Swimwear, Loungewear | 12 | Tested |
| Jewellery and Watches | — | 0 | **Untested** |
| Lifestyle | Tableware, Home Accessories, Candles, etc. | 0 | **Untested** |

---

## Global (applies to every category)

- **Matching**: only a product with a row in the measurements/pictures CSV gets listed —
  no photo, no listing. Matched on the measurements file's `Name` column against the
  master file's `SKU` (the measurements file's own `SKU` column is actually the barcode,
  not used for matching). Matching is exact, case-sensitive.
- **Pricing**: `Start price` = exactly 50% of `Rounded RRP`, for every category. This
  ratio was derived from the 62-row example file (all Ready to Wear) and applied
  uniformly — never separately confirmed for Jewellery or Lifestyle pricing.
- **Condition grading**: defaults toward 3000 (Used/Pre-owned) unless the condition
  notes or their absence clearly indicate New. Same rubric for every category — no
  allowance for categories where "new with tags" might be more common (e.g. certain
  jewellery or homeware stock).
- **Condition notes source**: read from the measurements file's `Description` column.
  Its `Condition Description` column is unused/always empty in this export — a quirk
  of this specific file, not a general rule. If a future export actually populates
  `Condition Description`, this pipeline will silently ignore it unless updated.
- **Missing-data inference** (Season, Colour): only fires when the source field is
  blank, never overrides real data, and is restricted to signals present in *this
  specific item's own data* — its name, material/composition, and category. It does
  **not** infer Country of Origin or anything else from general brand knowledge (e.g.
  "this brand is usually made in Italy") — that isn't data about the specific item, so
  it stays blank rather than being guessed. Also not applied to physical measurements
  (Pit to Pit, Length, Arm, Waist, Inside Leg) — those require actually measuring the
  item.
- **StoreCategory**: always blank. No source file gives your private eBay Store's
  category IDs — see `src/config.py`'s `STORE_CATEGORY_MAP`.
- **Schedule Time**: a static fixed timestamp (`src/config.py`), not calculated from
  when the script actually runs. Every listing in every run gets the same value unless
  you edit it.
- **Workflow constants** (Format, Duration, Currency, VAT%, Location, shipping/return/
  payment profile names, Best Offer Enabled) are fixed values copied from your one
  example file and applied to every category identically — no per-category variation
  (e.g. no different shipping profile for heavy vs. lightweight items).
- **Brand-authority paragraph**: one sentence generated per *brand*, reused for every
  item of that brand regardless of category. The fixed second sentence — "ideal for the
  {Brand} collector or anyone building a designer wardrobe" — uses clothing/fashion
  framing ("wardrobe") for every category, including ones where that phrasing may not
  fit well (see Lifestyle, below).
- **eBay category tree**: bundled as a static UK-site CSV, assumed not to change. Goes
  stale if eBay restructures categories and the file isn't manually refreshed.
- **Category ID mapping** is cached per (Category, SubCat2, Gender) *combination*, not
  per product — every product sharing a combo gets the same eBay category ID. A wrong
  mapping for one combo silently applies to every item in it.

---

## Ready to Wear — Tested (12 products: Tops, Skirts, Swimwear, Loungewear)

- Category mapping resolved cleanly to eBay's standard `Women's/Men's Clothing`
  subtree (e.g. Tops → "Shirts & Tops", Swimwear → "Swimwear").
- Item specifics (Fit, Neckline, Sleeve Length, Pattern, etc.) filled conditionally —
  no issues observed in the sample, but only 4 SubCat2 types out of ~20 possible in
  your master file have actually been exercised (no Dresses, Jackets, Coats, Trousers,
  Jeans, Knitwear, etc. yet — those have distinct item-specific fields, like Jacket
  Lapel Style, that have never fired for real).

## Footwear — Tested (9 products: Pumps, Sneakers, Mules, Flat Shoes)

- **Terminology bridging assumption**: your internal labels don't match eBay's UK
  category names, so the AI was instructed to translate: "Pumps" → eBay "Heels";
  "Sneakers" → eBay "Trainers". "Mules" has no dedicated eBay category in the UK tree
  used, so it was mapped to the closest fit, "Flats" — worth a manual sanity check if
  Mules volume grows.
- No shoe-specific item-specific columns exist in your eBay template (no Heel Height,
  Shoe Width, etc.) — footwear items get the same generic fields as everything else
  (Style, Material, Pattern, Occasion).

## Accessories / Bags — Tested (11 products, all Bags)

- Bags map to eBay's dedicated `Women's/Men's Bags & Handbags` category, a direct
  child of Women/Men rather than nested under "...Accessories" — matches how eBay
  actually structures it, not necessarily how your master file's "Accessories" label
  would suggest.
- Other Accessories SubCat2 types in your master file (Hats, Sunglasses) have not been
  tested — only Bags has real sample data so far.

## Jewellery and Watches — Untested (0 products)

- The ~30 jewellery-specific item-specific columns (Metal, Metal Purity, Main Stone,
  Main Stone Colour/Shape/Creation/Treatment, Hallmarked, Diamond Clarity/Colour Grade,
  Number of Diamonds, Ring Size, Band Width, Chain Type, Necklace Length, etc.) exist
  in the schema and the AI is instructed to fill what applies — but this has never run
  against a real jewellery item, so accuracy/category-mapping correctness for this
  branch is unverified.
- The brand-blurb paragraph's fashion-heritage framing ("known for effortlessly cool
  tailoring...", "designer wardrobe") was written with clothing/accessories in mind —
  may read a little off for a watch or fine jewellery piece, though it isn't
  necessarily wrong.
- The condition rubric's bias toward "Used" may not fit jewellery as well as clothing
  — unworn jewellery in original packaging is arguably more common in resale than
  unworn clothing.

## Lifestyle (Tableware, Home Accessories, Candles, Homewear, Soft Furnishings) — Untested (0 products)

- Biggest untested gap. These aren't fashion items at all, and the entire Description
  template assumes a fashion/designer-wardrobe context:
  - The brand blurb talks about "iconic names in [French/British/Italian] fashion" —
    for a homeware or candle brand line, this framing may be inaccurate or odd.
  - "Ideal for the {Brand} collector or anyone building a designer **wardrobe**" makes
    no sense for tableware or candles.
  - The item-specific field list has no dedicated homeware fields (no Dimensions,
    Room, Capacity, Scent, etc.) — these items would get whatever generic fields the
    AI decides loosely apply (Material, Pattern, Colour), which is unlikely to match
    what eBay buyers actually filter homeware listings by.
- Recommend treating this category as needing a dedicated review (and possibly a
  different Description template) before your first Lifestyle listing goes out,
  rather than trusting the current output for it.

---

## Built-in category catalog (no template upload required)

Uploading an eBay "Create listings in bulk" template is now optional. When none is given, the
app falls back to `src/builtin_catalog.py`, which builds a synthetic template from
`data/account_listings_export.csv` — a bundled "Download All Listings" export of this account's
own ~1,500 real, currently-live listings (a 3Dsellers export, not eBay's own bulk-upload format).

This is a genuinely different kind of source than a real template, with real trade-offs:

- **No official Required/Preferred/Optional flags or closed value lists.** Instead, for each of
  the 110 real category IDs in the export, any item-specific column filled in on ≥60% of that
  category's historical rows is treated as effectively required, using the real distinct values
  seen as its value pool (same fuzzy-match/hybrid-AI machinery as a real template's Aspects). A
  column filled in less often is treated as optional; never-filled-in columns are left out of
  that category's aspect set entirely. This is inference from real outcomes, not eBay's own
  rules — a genuinely new Required field eBay added since these listings went up wouldn't be
  caught.
- **No real eBay category names for most categories** — only numeric IDs. 17 category IDs have
  a real path name (confirmed from actual template downloads during development, hardcoded in
  `KNOWN_CATEGORY_NAMES`); the other ~93 get an honest placeholder like
  `Category 63864 (name not verified — check Seller Hub)` rather than a guessed/fabricated path
  string. Tried resolving the rest via eBay's public search pages (`_sacat=<id>`); the fetch
  timed out both attempts, so this was not pursued further. Category ID (not name) is what's
  actually authoritative for where a listing lands — the name column is bookkeeping — so this is
  a cosmetic gap, not a listing-accuracy one, but it does mean many rows will show a placeholder
  in that column until someone fills in the real names by hand.
- **Mojibake repair.** A meaningful share of values in the bundled export had been double-encoded
  (UTF-8 bytes previously decoded as latin-1 and re-saved, e.g. a real en-dash "–" turned into
  three characters "â\x80\x93") — likely from a prior export/import round-trip through a tool
  that mishandled encoding. `builtin_catalog._fix_mojibake` repairs this on load by round-tripping
  through latin-1; verified against all ~10,750 distinct aspect values extracted with zero
  remaining mojibake sequences.
- **The fixed prefix/suffix listing columns and the `#INFO` preamble are hardcoded**, not
  re-derived from the export (which doesn't have them at all — they're specific to eBay's own
  bulk-upload format). Confirmed identical across five real template downloads regardless of
  which categories were selected, so there's nothing category-specific being lost by hardcoding
  them.

An uploaded real template is still authoritative for whichever categories it covers — this is a
fallback for when one isn't available, not a replacement for uploading one when accuracy matters
most for a category (e.g. one with unusually strict Required fields).

## Suggested next test

Before relying on this for Jewellery/Lifestyle listings, run a small `--limit` batch
once even 2-3 real products from each of those categories have photos, and manually
review the output the same way we validated Ready to Wear/Footwear/Bags.
