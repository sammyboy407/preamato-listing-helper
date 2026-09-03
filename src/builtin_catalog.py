"""Builds a synthetic EbayTemplate from a bundled export of this account's
own real, currently-live eBay listings (data/account_listings_export.csv —
a "Download All Listings" export, not eBay's own bulk-upload template),
so a run no longer requires uploading a fresh template every time.

This is a genuinely different kind of source than a real eBay "Create
listings in bulk" template (see ebay_template.py):
  - It has no Required/Preferred/Optional flags and no official closed
    value lists per category — just whatever ~280 possible item-specific
    columns happened to be filled in on ~1,500 real historical listings.
  - It has no eBay category *names*, only numeric category IDs (the
    Ebay_Category_1 column) — a handful of names below are filled in from
    real templates seen during development; everything else gets an
    honest placeholder rather than a guessed/fabricated path string,
    since a wrong Category ID would break a listing but Category ID (not
    name) is what's authoritative — the name column is bookkeeping.

Validation strategy (best-effort from real data, since there's no official
Aspects sheet to consult): for each category, any item-specific column
that's filled in on a large share of that category's historical rows is
treated as effectively required, using the real distinct values seen as
its closed-list-like value pool; a column with real values on only a few
rows is treated as optional; a column never filled in for that category
is left out of its aspect set entirely, matching how a real category's
Aspects would simply not list an inapplicable field.

The #INFO preamble and the fixed prefix/suffix listing columns are
identical across every real per-category template observed (they don't
vary with which categories were selected when downloading) — hardcoded
here from several real templates rather than re-derived, since there's
nothing category-specific about them to get from this export.
"""
from __future__ import annotations

import csv
import time
from collections import Counter, defaultdict
from pathlib import Path

from .ebay_template import AspectSpec, CategorySpec, EbayTemplate

DEFAULT_EXPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "account_listings_export.csv"

REQUIRED_FILL_RATE = 0.6

# Columns in the export that describe the listing itself, not an
# eBay item-specific ("C:") field — everything else in the header row is
# treated as a candidate aspect column.
NON_ASPECT_COLUMNS = {
    "Site", "StoreId", "ItemId", "SKU", "Title", "ConditionId", "ConditionDisplayName",
    "Price", "SpecialPrice", "Qty", "Watchers", "QuantitySold", "OutOfStock",
    "Ebay_Category_1", "Ebay_Category_2", "Store_Category_1", "Store_Category_2",
    "Weight", "Image", "ProductEAN", "ProductISBN", "ProductUPC", "ProductMPN",
    "ProductGTIN", "ProductEPID", "Description",
}

# Real eBay category paths confirmed from actual "Create listings in bulk"
# template downloads during development — everything else in the export
# only has a numeric ID, no confirmed name.
KNOWN_CATEGORY_NAMES: dict[str, str] = {
    "95672": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Trainers",
    "62107": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Sandals",
    "63866": "/Clothes, Shoes & Accessories/Women/Women's Clothing/Jumpers & Cardigans",
    "53557": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Boots",
    "45333": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Flats",
    "53120": "/Clothes, Shoes & Accessories/Men/Men's Shoes/Formal Shoes",
    "15709": "/Clothes, Shoes & Accessories/Men/Men's Shoes/Trainers",
    "57991": "/Clothes, Shoes & Accessories/Men/Men's Clothing/Shirts & Tops/Formal Shirts",
    "57990": "/Clothes, Shoes & Accessories/Men/Men's Clothing/Shirts & Tops/Casual Shirts & Tops",
    "15687": "/Clothes, Shoes & Accessories/Men/Men's Clothing/Shirts & Tops/T-Shirts",
    "169291": "/Clothes, Shoes & Accessories/Women/Women's Bags & Handbags",
    "63867": "/Clothes, Shoes & Accessories/Women/Women's Clothing/Swimwear",
    "53159": "/Clothes, Shoes & Accessories/Women/Women's Clothing/Tops & Shirts",
    "55793": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Heels",
    "63861": "/Clothes, Shoes & Accessories/Women/Women's Clothing/Dresses",
    "11554": "/Clothes, Shoes & Accessories/Women/Women's Clothing/Jeans",
    "11632": "/Clothes, Shoes & Accessories/Women/Women's Shoes/Slippers",
}

# Identical across every real template seen (Aug-25-2026 through Sept-1-2026
# downloads) regardless of which categories were selected — only the C:
# aspect columns in between vary per template.
LISTING_PREFIX_HEADERS = [
    "*Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)", "Custom label (SKU)",
    "Category ID", "Category name", "Title", "Relationship", "Relationship details",
    "Schedule Time", "P:EPID", "Start price", "Quantity", "Item photo URL", "VideoID",
    "Condition ID", "Description", "Format", "Duration", "Buy It Now price",
    "Best Offer Enabled", "Best Offer Auto Accept Price", "Minimum Best Offer Price",
    "VAT%", "Immediate pay required", "Location", "Shipping service 1 option",
    "Shipping service 1 cost", "Shipping service 1 priority", "Shipping service 2 option",
    "Shipping service 2 cost", "Shipping service 2 priority", "Max dispatch time",
    "Returns accepted option", "Returns within option", "Refund option",
    "Return shipping cost paid by", "Shipping profile name", "Return profile name",
    "Payment profile name", "ProductCompliancePolicyID", "Regional ProductCompliancePolicies",
]
LISTING_SUFFIX_HEADERS = [
    "Product Safety Pictograms", "Product Safety Statements", "Product Safety Component",
    "Regulatory Document Ids", "Manufacturer Name", "Manufacturer AddressLine1",
    "Manufacturer AddressLine2", "Manufacturer City", "Manufacturer Country",
    "Manufacturer PostalCode", "Manufacturer StateOrProvince", "Manufacturer Phone",
    "Manufacturer Email", "Manufacturer ContactURL", "Responsible Person 1",
    "Responsible Person 1 Type", "Responsible Person 1 AddressLine1",
    "Responsible Person 1 AddressLine2", "Responsible Person 1 City",
    "Responsible Person 1 Country", "Responsible Person 1 PostalCode",
    "Responsible Person 1 StateOrProvince", "Responsible Person 1 Phone",
    "Responsible Person 1 Email", "Responsible Person 1 ContactURL",
]

# A handful of common aspects placed first (cosmetic ordering only — rows
# are written by header name lookup, so this has no functional effect).
_PRIORITY_ASPECTS = ["Brand", "Department", "Colour", "Country of Origin", "Size", "Style", "Material"]


def _category_name(category_id: str) -> str:
    return KNOWN_CATEGORY_NAMES.get(category_id, f"Category {category_id} (name not verified — check Seller Hub)")


def _fix_mojibake(s: str) -> str:
    """Some rows in the export have UTF-8 bytes that were previously
    decoded as latin-1 and re-saved (classic double-encoding — e.g. a
    genuine en-dash "–" turned into the three characters "â\\x80\\x93").
    Round-tripping through latin-1 repairs it; already-correct text with
    any character outside latin-1's 0-255 range fails the encode step, so
    this is a safe no-op there."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _load_rows(export_path: str | Path) -> list[dict]:
    with open(export_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() if h else h for h in (reader.fieldnames or [])]
        return [
            {k: (_fix_mojibake(v) if isinstance(v, str) else v) for k, v in row.items()}
            for row in reader
        ]


def build_template(export_path: str | Path = DEFAULT_EXPORT_PATH) -> EbayTemplate:
    rows = _load_rows(export_path)
    aspect_columns = [h for h in rows[0].keys() if h and h not in NON_ASPECT_COLUMNS] if rows else []

    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cat_id = (row.get("Ebay_Category_1") or "").strip()
        if cat_id:
            by_category[cat_id].append(row)

    categories: list[CategorySpec] = []
    aspects: dict[str, dict[str, AspectSpec]] = {}
    all_aspect_names: set[str] = set()

    for cat_id, cat_rows in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        conditions = sorted(
            {(int(r["ConditionId"]), r["ConditionDisplayName"].strip())
             for r in cat_rows if (r.get("ConditionId") or "").strip() and (r.get("ConditionDisplayName") or "").strip()}
        )
        categories.append(CategorySpec(category_id=cat_id, category_name=_category_name(cat_id), conditions=conditions))

        cat_aspects: dict[str, AspectSpec] = {}
        n = len(cat_rows)
        for col in aspect_columns:
            values_seen = [r[col].strip() for r in cat_rows if (r.get(col) or "").strip()]
            if not values_seen:
                continue
            fill_rate = len(values_seen) / n
            level = "REQUIRED" if fill_rate >= REQUIRED_FILL_RATE else "OPTIONAL"
            distinct_values = sorted(set(values_seen))
            aspect_name = f"C:{col}"
            cat_aspects[aspect_name] = AspectSpec(name=aspect_name, level=level, values=distinct_values)
            all_aspect_names.add(aspect_name)
        aspects[cat_id] = cat_aspects

    def _sort_key(name: str) -> tuple[int, str]:
        bare = name[2:]  # strip "C:"
        return (_PRIORITY_ASPECTS.index(bare) if bare in _PRIORITY_ASPECTS else len(_PRIORITY_ASPECTS), name)

    c_headers = sorted(all_aspect_names, key=_sort_key)
    listing_headers = LISTING_PREFIX_HEADERS + c_headers + LISTING_SUFFIX_HEADERS

    info_rows = [
        ["#INFO", f"Created={int(time.time() * 1000)}", "", "", "", "", " Indicates missing required fields"] + [""] * (len(listing_headers) - 7),
        ["#INFO", "Version=1.0", "", "Template=fx_category_template_EBAY_GB", "", "", " Indicates missing recommended field"] + [""] * (len(listing_headers) - 7),
        ["#INFO"] + [""] * (len(listing_headers) - 1),
    ]

    return EbayTemplate(
        listing_headers=listing_headers,
        categories=categories,
        aspects=aspects,
        info_rows=info_rows,
    )
