"""Per-product AI content generation, driven entirely by the eBay template's
per-category aspect rules (src/ebay_template.py) — which fields exist, which
are Required/Preferred/Optional, and which have a closed list of valid
values eBay will actually accept.

Field handling strategy, decided per aspect:
  - Brand, Department, Country of Origin, Size-family (Size / UK Shoe Size /
    Waist Size etc.), MPN: resolved deterministically in Python from data we
    already have (aspect_matching.py) — these are lookups/normalization, not
    judgment calls, so no AI call is spent on them.
  - Colour-family aspects (any name containing "Colour"): AI proposes a
    value (free text, informed by a sample of valid options), then Python
    fuzzy-matches it against the real closed list. Handles huge lists like
    Bags' 519-value "Exterior Colour" that can't be enumerated in a prompt.
  - Any other Required aspect with a closed list too large to enumerate
    (>40 values, e.g. Bags' "Exterior Material"): same AI-guess +
    fuzzy-match hybrid as colour, since it can't be skipped.
  - Any aspect with a closed list of <=40 values: given to the AI as a
    strict JSON-schema enum, so the model structurally cannot return an
    invalid value.
  - Free-text aspects with no closed list (Personalisation Instructions,
    Unit Quantity, Handle Drop, Strap Drop, Fabric Weight): left blank —
    none of these are Required in any category we've seen, and none are
    inferable without physically handling the item.
  - Aspects with a closed list >40 values that are Preferred/Optional
    (Product Line, Model, Character, Theme, etc.): skipped — safe to omit,
    and not worth the prompt cost/hallucination risk for a non-required field.

The free-text "Material:" line used in the Description (build.py) is
generated separately from any eBay item-specific column, since eBay's own
Material aspect is often Preferred/Optional with a huge list, and skipped
under the option above.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from . import aspect_matching, ai_client, ebay_template
from .data_loader import Product, split_image_urls

_CACHE_LOCK = threading.Lock()

LARGE_LIST_THRESHOLD = 40

# Aspects resolved deterministically in Python — never asked of the AI.
DETERMINISTIC_ASPECTS = {"C:Brand", "C:Department", "C:Country of Origin", "C:MPN"}


def _is_size_aspect(name: str) -> bool:
    return "Size" in name and name not in DETERMINISTIC_ASPECTS


def _is_colour_aspect(name: str) -> bool:
    return "colour" in name.lower()


def _resolve_deterministic(name: str, product: Product, spec: ebay_template.AspectSpec) -> str | None:
    m, meas = product.master, product.measurements
    if name == "C:Brand":
        return aspect_matching.match_brand(m.get("Brand"), spec.values)
    if name == "C:Department":
        return aspect_matching.match_department(m.get("Gender"), spec.values)
    if name == "C:Country of Origin":
        return aspect_matching.match_country(m.get("Country of Origin"), spec.values)
    if name == "C:MPN":
        return "Does Not Apply"
    return None


def _resolve_size(name: str, product: Product, spec: ebay_template.AspectSpec) -> str | None:
    m, meas = product.master, product.measurements
    raw = meas.get("Size") or m.get("Size")
    if name == "C:UK Shoe Size":
        # Master file's raw shoe "Size" is in EU sizing, not UK — see
        # aspect_matching.EU_TO_UK_WOMENS_SHOE_SIZE / _MENS_SHOE_SIZE.
        return aspect_matching.match_shoe_size_uk(raw, spec.values, m.get("Gender"))
    if name == "C:EU Shoe Size":
        return aspect_matching.match_size(raw, spec.values)
    return aspect_matching.match_size(raw, spec.values)


def classify_aspects(
    category_id: str, template: ebay_template.EbayTemplate
) -> tuple[dict[str, ebay_template.AspectSpec], dict[str, ebay_template.AspectSpec], dict[str, ebay_template.AspectSpec]]:
    """Splits a category's aspects into (enum_specs, hybrid_specs, skipped).
    enum_specs: small closed lists -> strict schema enum.
    hybrid_specs: large closed list but Required, or any colour-named aspect
        -> AI free-text guess + Python fuzzy-match against the real list.
    (Deterministic and free-text-blank aspects are handled outside this;
    skipped ones are simply not returned at all by the caller.)
    """
    aspects = template.aspects.get(str(category_id), {})
    enum_specs: dict[str, ebay_template.AspectSpec] = {}
    hybrid_specs: dict[str, ebay_template.AspectSpec] = {}
    skipped: dict[str, ebay_template.AspectSpec] = {}

    for name, spec in aspects.items():
        if name in DETERMINISTIC_ASPECTS or _is_size_aspect(name):
            continue
        if spec.values is None:
            skipped[name] = spec  # free text, not inferable — left blank
            continue
        if _is_colour_aspect(name):
            hybrid_specs[name] = spec
            continue
        if len(spec.values) <= LARGE_LIST_THRESHOLD:
            enum_specs[name] = spec
        elif spec.level == "REQUIRED":
            hybrid_specs[name] = spec
        else:
            skipped[name] = spec

    return enum_specs, hybrid_specs, skipped


def _condition_rubric(category: ebay_template.CategorySpec) -> str:
    lines = [f"  {cid} = {label}" for cid, label in category.conditions]
    return (
        "Choose the eBay Condition ID that best matches the item's actual state, "
        "from exactly these options for this category:\n" + "\n".join(lines) + "\n"
        "Preloved designer resale should usually be one of the Pre-owned tiers "
        "(Excellent/Good/Fair) based on the condition notes: Excellent = like new, "
        "no visible wear; Good = light wear consistent with gentle use, well "
        "maintained; Fair = noticeable wear or flaws. Only use a 'New' tier if the "
        "notes (or their absence alongside other signals) clearly indicate unworn."
    )


def _product_brief(product: Product) -> str:
    m, meas = product.master, product.measurements
    lines = [
        f"SKU: {product.sku}",
        f"Brand: {m.get('Brand')}",
        f"Internal title: {m.get('Clean Title Description')}",
        f"Category / SubCategory: {m.get('Category')} / {m.get('SubCat2')}",
        f"Gender: {m.get('Gender')}",
        f"Colour (raw, may not match eBay's exact wording): {meas.get('Colour') or m.get('Colour')}",
        f"Size (raw): {meas.get('Size') or m.get('Size')}",
        f"Composition/Material (raw, may be messy): {meas.get('Material') or m.get('Composition')}",
        f"Country of Origin (raw): {m.get('Country of Origin')}",
        f"Internal quality grade: {m.get('Quality')}",
        f"Condition notes (from inspection): {meas.get('Description') or '(none given)'}",
        f"RRP: {m.get('Rounded RRP')}",
        f"Measurements (inches) - Pit to Pit: {meas.get('Pit to Pit (inches)') or 'n/a'}, "
        f"Length: {meas.get('Length (inches)') or 'n/a'}, "
        f"Arm: {meas.get('Arm (inches)') or 'n/a'}, "
        f"Waist laying flat: {meas.get('Waist Laying Flat (inches)') or 'n/a'}, "
        f"Inside leg: {meas.get('Inside Leg (inches)') or 'n/a'}",
        f"Number of product photos available: {len(split_image_urls(meas.get('Images 2D link')))}",
    ]
    return "\n".join(lines)


def _relevant_sample(values: list[str], query_text: str, n: int = 40) -> list[str]:
    """Ranks a large closed list by keyword overlap with the product's own
    text (title/composition/colour), so a correct-but-alphabetically-late
    value (e.g. "Leather" for a "Calf Leather" item) isn't missed just
    because a plain alphabetical slice happened to stop before it."""
    query_tokens = set(re.findall(r"[a-z0-9]+", query_text.lower()))
    if not query_tokens:
        return values[:n]

    def score(value: str) -> int:
        value_tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
        return len(query_tokens & value_tokens)

    scored = sorted(values, key=lambda v: (-score(v), v))
    top = [v for v in scored if score(v) > 0][:n]
    if len(top) < n:
        top += [v for v in values if v not in top][: n - len(top)]
    return top


def _build_schema_and_system(
    category: ebay_template.CategorySpec,
    enum_specs: dict[str, ebay_template.AspectSpec],
    hybrid_specs: dict[str, ebay_template.AspectSpec],
    product_text: str,
) -> tuple[dict, str]:
    item_specific_props = {}
    item_specific_required = []
    field_notes = []

    for name, spec in enum_specs.items():
        item_specific_props[name] = {"type": "string", "enum": spec.values}
        item_specific_required.append(name)
        field_notes.append(f"  {name} ({spec.level}): choose exactly one from its enum list")

    for name, spec in hybrid_specs.items():
        item_specific_props[name] = {"type": "string"}
        item_specific_required.append(name)
        sample = _relevant_sample(spec.values, product_text, n=40)
        more = f" (+{len(spec.values) - len(sample)} more not shown)" if len(spec.values) > len(sample) else ""
        field_notes.append(
            f"  {name} ({spec.level}): free text — this field has a large eBay-defined list; "
            f"pick the option that best matches THIS item's own data, even if it's not in the "
            f"sample below — these are just illustrative, not exhaustive. Trust the given "
            f"composition/material data at face value: if it names a specific material (e.g. "
            f"'Calf Leather', 'Cotton'), that IS the real material — answer 'Leather'/'Cotton' "
            f"etc., not a synthetic/faux alternative, unless the source data itself says "
            f"faux/synthetic/vegan. Sample valid values: {', '.join(sample)}{more}"
        )

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "condition_id": {
                "type": "integer",
                "enum": [cid for cid, _ in category.conditions],
            },
            "condition_description": {"type": "string"},
            "material_summary": {
                "type": "string",
                "description": (
                    "Short free-text material description for customer-facing copy, e.g. "
                    "'100% Wool' or '70% Cotton, 30% Polyester' — cleaned up from the raw "
                    "composition data. This is NOT an eBay item-specific field, just prose."
                ),
            },
            "item_specifics": {
                "type": "object",
                "properties": item_specific_props,
                "required": item_specific_required,
            },
        },
        "required": ["title", "condition_id", "condition_description", "material_summary", "item_specifics"],
    }

    system = f"""You are writing an eBay UK listing for a preloved designer fashion reseller,
for a product in the eBay category "{category.category_name}" (ID {category.category_id}).

1. Write an SEO-optimised eBay Title: max 80 characters, front-load Brand + item
   type + the most distinctive attribute (colour/print/material), include Size
   and "RRP {{amount}}" (no currency symbol, just the number) near the end if
   space allows, in the terse keyword-dense style eBay buyers search with (not
   a grammatical sentence). Do not exceed 80
   characters under any circumstance.
2. {_condition_rubric(category)}
3. Write a short ConditionDescription (buyer-facing, plain language version of
   any condition notes given; if none given and condition is New, say so briefly).
   Vary your sentence opening and structure — this is one of many listings from
   the same seller, so don't default to starting every one with "Preloved, ..."
   or reusing the same phrase pattern each time.
4. Write material_summary as described in the schema.
5. Fill in every field listed below (all are required in this response) using
   only genuinely supported inferences from the product data given — never
   invent facts not implied by the data:
{chr(10).join(field_notes)}
"""
    return schema, system


def _cache_path_for(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "content_cache.json"


def _load_cache(cache_dir: str | Path) -> dict:
    p = _cache_path_for(cache_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_cache(cache_dir: str | Path, cache: dict) -> None:
    _cache_path_for(cache_dir).write_text(json.dumps(cache, indent=2, sort_keys=True))


def generate_for_product(
    product: Product,
    category: ebay_template.CategorySpec,
    template: ebay_template.EbayTemplate,
    cache_dir: str | Path,
    force: bool = False,
) -> dict:
    cache_key = f"{product.sku}::{category.category_id}"

    if not force:
        with _CACHE_LOCK:
            cache = _load_cache(cache_dir)
            if cache_key in cache:
                return cache[cache_key]

    enum_specs, hybrid_specs, _skipped = classify_aspects(category.category_id, template)
    product_text = _product_brief(product)
    schema, system = _build_schema_and_system(category, enum_specs, hybrid_specs, product_text)

    result = ai_client.call_structured(
        system=system,
        user=product_text,
        tool_name="submit_listing_content",
        input_schema=schema,
    )

    if len(result.get("title", "")) > 80:
        result["title"] = result["title"][:80].rstrip()

    specifics = result.get("item_specifics", {})

    # The API does not hard-enforce JSON-schema `enum` server-side — it's
    # guidance, not a guarantee. Validate every enum field and coerce any
    # value that isn't literally one of the allowed options.
    for name, spec in enum_specs.items():
        value = specifics.get(name)
        if value not in (spec.values or []):
            specifics[name] = aspect_matching.fuzzy_match(value, spec.values, cutoff=0.3) or spec.values[0]

    # Fuzzy-validate the hybrid (large-list) fields against the real values —
    # the AI's free-text guess must still resolve to something eBay accepts.
    for name, spec in hybrid_specs.items():
        guess = specifics.get(name)
        matched = aspect_matching.fuzzy_match(guess, spec.values, cutoff=0.5)
        if matched:
            specifics[name] = matched
        elif _is_colour_aspect(name):
            # Last resort for a colour field: fall back to the item's own
            # raw colour text rather than an unvalidated AI guess.
            raw_colour = product.measurements.get("Colour") or product.master.get("Colour")
            specifics[name] = aspect_matching.fuzzy_match(raw_colour, spec.values, cutoff=0.4) or (guess or "")
        # else: leave the AI's free-text guess as-is (best effort; not in the
        # sampled list shown to it doesn't necessarily mean it's wrong).

    # Layer in the deterministic fields.
    aspects = template.aspects.get(str(category.category_id), {})
    for name, spec in aspects.items():
        if name in DETERMINISTIC_ASPECTS:
            value = _resolve_deterministic(name, product, spec)
            if value:
                specifics[name] = value
        elif _is_size_aspect(name):
            value = _resolve_size(name, product, spec)
            if value:
                specifics[name] = value

    result["item_specifics"] = specifics

    with _CACHE_LOCK:
        cache = _load_cache(cache_dir)
        cache[cache_key] = result
        _save_cache(cache_dir, cache)
    return result
