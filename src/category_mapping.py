"""Maps each product to one of the categories actually present in the given
eBay template.

A template only ever covers a handful of categories (whatever the seller
selected when downloading it from Seller Hub), so for most (Category,
SubCat2, Gender) combos this is a small, cheap, one-off choice per distinct
combo — not a search across eBay's full ~14k-category tree, and not a call
per product.

Some SubCat2 labels, though, genuinely span multiple eBay categories within
the SAME combo — e.g. this account's "Pumps" SubCat2 contains both flat
pumps ("A.EMERY MAUDE FLAT PUMP") and heeled pumps ("MANOLO BLAHNIK HANGISI
90 PUMP" — the "90" is a heel height in mm, not a flag word; most heeled
items in this data have no explicit "heel" keyword at all and rely on
recognising the named model/silhouette). A single combo-level mapping is
wrong for roughly half of "Pumps" either way it's resolved. AMBIGUOUS_SUBCATS
routes these to per-product resolution instead, using the item's own title
as the deciding signal — more AI calls, but only for the specific
(Category, SubCat2) pairs known to need it; everything else keeps the cheap
per-combo path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import ai_client, ebay_template
from .data_loader import Product

# (Master File "Category", "SubCat2") pairs where a single eBay category
# mapping is wrong for a meaningful share of products sharing that SubCat2 —
# resolved per-product instead. Extend this set if another SubCat2 turns out
# to have the same problem (e.g. Mules/Sandals mixing flat and heeled styles).
AMBIGUOUS_SUBCATS = {
    ("Footwear", "Pumps"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "category_id": {
            "type": "string",
            "description": (
                "The Category ID of the best-matching candidate, copied exactly. "
                "Use the literal string 'NONE' if none of the candidates are a "
                "reasonable fit for this product type."
            ),
        },
        "reasoning": {"type": "string", "description": "One short sentence on why."},
    },
    "required": ["category_id", "reasoning"],
}

SYSTEM_COMBO = (
    "You are matching a product from a preloved designer fashion reseller's internal "
    "catalog labels to the correct eBay category, choosing only from a short list of "
    "candidates (this template only covers a subset of eBay's full category tree). "
    "Internal labels may use different terminology than eBay's (e.g. internal 'Sneakers' = "
    "eBay 'Trainers'). If none of the candidates are a reasonable match for this product's "
    "type/gender, say so — do not force a bad fit."
)

SYSTEM_PER_PRODUCT = (
    "You are matching one specific product from a preloved designer fashion reseller's "
    "internal catalog to the correct eBay category, choosing only from a short list of "
    "candidates. This product's internal SubCat2 label alone is not a reliable signal — "
    "it's used for a mix of item styles that map to different eBay categories. Base your "
    "answer on the product's actual title: recognise real footwear conventions (e.g. a bare "
    "2-3 digit number after a shoe's model name is very often a heel height in millimetres, "
    "signalling a heeled shoe even with no literal word 'heel' in the title; 'FLAT'/'BALLET'/"
    "'BALLERINA' signal a flat shoe; a named silhouette you recognise as a heeled style — e.g. "
    "a court shoe, stiletto, slingback pump — should be treated as heeled even without an "
    "explicit heel-height number). If you genuinely can't tell, pick the more general/likely "
    "candidate rather than guessing wildly. If none of the candidates fit at all, say so."
)


def _template_fingerprint(template: ebay_template.EbayTemplate) -> str:
    """A short hash of exactly which categories this template covers. Mixed
    into cache keys so switching to a template with a different category
    selection can never reuse a stale mapping from a previous template —
    without this, a cached category_id not present in the new template
    would resolve to None downstream and crash, or worse, a coincidentally
    still-valid-looking ID could mask a mismatch."""
    ids = ",".join(sorted(c.category_id for c in template.categories))
    return hashlib.sha256(ids.encode()).hexdigest()[:12]


def _combo_key(category: str, subcat2: str, gender: str, template_fp: str) -> str:
    return f"{template_fp}::combo::{category}|{subcat2}|{gender}"


def _product_key(sku: str, template_fp: str) -> str:
    return f"{template_fp}::product::{sku}"


def load_cache(cache_path: str | Path) -> dict:
    p = Path(cache_path)
    return json.loads(p.read_text()) if p.exists() else {}


def save_cache(cache_path: str | Path, cache: dict) -> None:
    Path(cache_path).write_text(json.dumps(cache, indent=2, sort_keys=True))


def _pick_category(system: str, user: str, candidates: list[ebay_template.CategorySpec]) -> dict:
    result = ai_client.call_structured(
        system=system, user=user, tool_name="pick_category", input_schema=SCHEMA
    )
    raw_id = result.get("category_id", "")
    chosen = next((c for c in candidates if c.category_id == raw_id), None) if raw_id and raw_id != "NONE" else None
    return {
        "category_id": chosen.category_id if chosen else None,
        "category_name": chosen.category_name if chosen else None,
        "reasoning": result.get("reasoning", ""),
    }


def build_mapping(
    products: list[Product],
    template: ebay_template.EbayTemplate,
    cache_path: str | Path,
) -> dict[str, dict]:
    """Returns a cache dict — combo-level and per-product entries mixed
    together, keyed distinctly (see _combo_key / _product_key). Use lookup()
    to read it back correctly for a given product."""
    cache = load_cache(cache_path)
    changed = False
    template_fp = _template_fingerprint(template)

    candidate_lines = "\n".join(
        f"- {c.category_id}: {c.category_name}" for c in template.categories
    )

    combos = {
        (str(p.m("Category")), str(p.m("SubCat2")), str(p.m("Gender")))
        for p in products
        if (str(p.m("Category")), str(p.m("SubCat2"))) not in AMBIGUOUS_SUBCATS
    }
    for category, subcat2, gender in sorted(combos):
        key = _combo_key(category, subcat2, gender, template_fp)
        if key in cache:
            continue
        user = (
            f"Product's internal labels:\n"
            f"  Category: {category}\n"
            f"  SubCat2: {subcat2}\n"
            f"  Gender: {gender}\n\n"
            f"Candidate eBay categories (this template's full coverage):\n{candidate_lines}"
        )
        cache[key] = _pick_category(SYSTEM_COMBO, user, template.categories)
        changed = True
        status = f"{cache[key]['category_id']} ({cache[key]['category_name']})" if cache[key]['category_id'] else "NO MATCH in this template"
        print(f"  [category map] {category} / {subcat2} / {gender} -> {status}")

    ambiguous_products = [
        p for p in products
        if (str(p.m("Category")), str(p.m("SubCat2"))) in AMBIGUOUS_SUBCATS
    ]
    for p in ambiguous_products:
        key = _product_key(p.sku, template_fp)
        if key in cache:
            continue
        user = (
            f"Product's internal labels:\n"
            f"  Category: {p.m('Category')}\n"
            f"  SubCat2: {p.m('SubCat2')}\n"
            f"  Gender: {p.m('Gender')}\n"
            f"  Title: {p.m('Clean Title Description')}\n\n"
            f"Candidate eBay categories (this template's full coverage):\n{candidate_lines}"
        )
        cache[key] = _pick_category(SYSTEM_PER_PRODUCT, user, template.categories)
        changed = True
        status = f"{cache[key]['category_id']} ({cache[key]['category_name']})" if cache[key]['category_id'] else "NO MATCH in this template"
        print(f"  [category map] {p.sku} ({p.m('Clean Title Description')}) -> {status}")

    if changed:
        save_cache(cache_path, cache)

    return cache


def lookup(cache: dict, product: Product, template: ebay_template.EbayTemplate) -> dict | None:
    template_fp = _template_fingerprint(template)
    category, subcat2, gender = str(product.m("Category")), str(product.m("SubCat2")), str(product.m("Gender"))

    if (category, subcat2) in AMBIGUOUS_SUBCATS:
        entry = cache.get(_product_key(product.sku, template_fp))
    else:
        entry = cache.get(_combo_key(category, subcat2, gender, template_fp))

    if not entry or not entry.get("category_id"):
        return None
    return entry
