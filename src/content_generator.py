"""Per-product AI content generation, driven entirely by the eBay template's
per-category aspect rules (src/ebay_template.py) — which fields exist, which
are Required/Preferred/Optional, and which have a closed list of valid
values eBay will actually accept.

Field handling strategy, decided per aspect:
  - Brand, Department, Country of Origin, Type, Size-family (Size / UK Shoe
    Size / Waist Size etc.), MPN: resolved deterministically in Python from
    data we already have (aspect_matching.py) — these are lookups/
    normalization, not judgment calls, so no AI call is spent on them.
    Brand, Department (via Gender) and Type (via SubCat2) all read the
    Master File — confirmed 04.09.26 that Colour/Material/Type/Gender/Brand
    all originate there and are simply carried onto the Measurements file
    at the Orbitvu photography stage, so Master is the correct source for
    them (unlike Size, which is verified fresh at that stage — see
    _resolve_size).
  - Physical measurement aspects (Pit to Pit / Length / Arm / Waist Laying
    Flat / Inside Leg, all "(inches)"): resolved deterministically straight
    from the Pictures & Measurements file's matching column — see
    MEASUREMENT_ASPECTS. Never AI-guessed, never sourced from the Master
    File (same reasoning as Size: these are only trustworthy once the item
    has actually been measured).
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

# The content cache is keyed partly on a fingerprint of the code that
# actually decides an item's size, so that changing any of it automatically
# invalidates every cached result rather than relying on someone remembering
# to bump a version constant. That matters because a stale cache entry is
# indistinguishable from a fresh one in the output file: after the 04.09.26
# fix, a re-run would otherwise have quietly re-served the EU 45 -> UK 4.5
# results it had already stored.
#
# CACHE_VERSION is still here for changes the fingerprint can't see (a
# different prompt, a new deterministic field), and is mixed in alongside it.
CACHE_VERSION = "v3"


def _sizing_sources() -> list:
    """Every function whose behaviour decides the size written to a listing.

    Kept as an explicit list rather than inlined into _sizing_fingerprint so
    a test can check it against the sizing code itself — an edit to a
    function that isn't in here would leave the cache key unchanged, and a
    stale cached size is indistinguishable from a fresh one in the output
    file. That is exactly how the 04.09.26 batch went out with the old
    conversion after the fix was already deployed."""
    return [
        _resolve_size, _is_size_aspect,
        aspect_matching.enforce_title_size,
        aspect_matching.match_shoe_size_uk, aspect_matching.match_shoe_size_eu,
        aspect_matching.match_size, aspect_matching.size_display,
        aspect_matching.size_display_for,
        aspect_matching.fuzzy_match, aspect_matching.parse_shoe_size,
        aspect_matching.parse_shoe_size_range, aspect_matching._resolve_range_end,
        aspect_matching._normalise_size_marker, aspect_matching.is_assumed_shoe_system,
        aspect_matching._eu_to_uk_table, aspect_matching._us_to_uk_table,
        aspect_matching.bare_number_system, aspect_matching.assumed_shoe_system,
    ]


def _sizing_fingerprint() -> str:
    """A short hash of every function and table that determines the size
    written to a listing. Any edit to them changes the hash, which changes
    every cache key, which forces a regeneration."""
    import hashlib
    import inspect

    parts = []
    for fn in _sizing_sources():
        try:
            parts.append(inspect.getsource(fn))
        except (OSError, TypeError):  # pragma: no cover - source always available in practice
            parts.append(repr(fn))
    parts.append(repr(sorted(aspect_matching.EU_TO_UK_MENS_SHOE_SIZE.items())))
    parts.append(repr(sorted(aspect_matching.EU_TO_UK_WOMENS_SHOE_SIZE.items())))
    parts.append(repr(sorted(aspect_matching.US_TO_UK_MENS_SHOE_SIZE.items())))
    parts.append(repr(sorted(aspect_matching.US_TO_UK_WOMENS_SHOE_SIZE.items())))
    parts.append(repr(aspect_matching.BARE_NUMBER_SHOE_SYSTEM))
    parts.append(repr(sorted(aspect_matching.US_SIZED_BRANDS)))
    parts.append(repr(sorted(aspect_matching.SIZE_ALIASES.items())))
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:12]


LARGE_LIST_THRESHOLD = 40

# Aspects that are never filled, whatever eBay says about them. Each one is
# either meaningless for this stock or unknowable from the data, and every
# one of them showed up as a confident-looking wrong value on the 04.09.26
# batch ("Character: Aladdin", "Performance/Activity: American Football",
# "Theme: 20s", "Unit Type: 10ml", "Warmth Weight: 400") because the AI was
# being forced to pick something. The account's own Optiseller-era history
# left these blank or "Not Applicable" throughout. A blank Optional/
# Preferred field costs nothing; a wrong one misleads buyers and search.
NEVER_FILL_ASPECTS = {
    "C:Character", "C:Character Family", "C:Theme", "C:Performance/Activity",
    "C:Unit Type", "C:Unit Quantity", "C:Number in Pack", "C:Custom Bundle",
    "C:Bundle Description", "C:Set Includes", "C:Model", "C:Product Line",
    "C:Personalisation Instructions", "C:California Prop 65 Warning",
    "C:Year Manufactured", "C:Release Year", "C:Reference Number", "C:Signed",
    "C:Seller Warranty", "C:Warmth Weight", "C:Fabric Weight", "C:Compatible Model",
    "C:Required Tools", "C:Mounting", "C:Labels & Certifications", "C:Certification",
    "C:Cleat Type", "C:Heel to Toe Drop", "C:Pronation",
}

# Aspects resolved deterministically in Python — never asked of the AI.
DETERMINISTIC_ASPECTS = {"C:Brand", "C:Department", "C:Country of Origin", "C:MPN", "C:Type"}

# Physical garment measurements (inches) — always taken from the verified
# Pictures & Measurements file, same trust reasoning as Size/Colour/Material
# (see _resolve_size): these are measured against the real physical item, so
# they're never AI-guessed and never fall back to the Master File (which
# doesn't carry these columns at all). Aspect name == "C:" + the
# measurements file's own column name.
MEASUREMENT_ASPECTS = {
    "C:Pit to Pit (inches)",
    "C:Length (inches)",
    "C:Arm (inches)",
    "C:Waist Laying Flat (inches)",
    "C:Inside Leg (inches)",
}

# Aspects eBay allows multiple selected values for (entered pipe-separated in
# the bulk CSV, e.g. "Casual|Workwear|Travel"). Sammy asked specifically for
# Occasion 04.09.26: pick every occasion that genuinely fits, not just one,
# since buyers filter search results by it. Only Occasion is hand-listed
# here for now — other eBay aspects that also support multiple values
# (Features, Character, Theme, etc.) aren't confirmed as wanted here yet.
# For a template loaded via scripts/fetch_ebay_category_aspects.py (see
# ebay_template.AspectSpec.multi), eBay's own real cardinality answer is
# used automatically as well — this set only fills the gap for .xlsx-sourced
# templates, which have no column that says whether a field is multi-select.
MULTI_SELECT_ASPECTS = {"C:Occasion"}


# What goes in a field we deliberately aren't filling. Sammy's rule,
# 04.09.26: "dont fill with random info just say not specified" — an
# explicit placeholder rather than a blank, matching how the account's own
# Optiseller-era listings handled these (Character: "Not Applicable" on 161
# listings, Chest Size / Waist Size: "Not Specified", etc.).
#
# Only ever written where eBay definitely accepts a value that isn't on the
# aspect's own list (see ebay_template.AspectSpec.free_text): for a
# SELECTION_ONLY aspect, "Not Specified" is an invalid value and would get
# the whole listing rejected — the same class of failure as a missing
# required field, which is exactly what this pipeline exists to avoid. A
# blank and a "Not Specified" mean the same thing to eBay's search anyway,
# so the placeholder is presentation, never worth risking a rejection for.
PLACEHOLDER_VALUE = "Not Specified"


def _placeholder_for(spec: ebay_template.AspectSpec) -> str | None:
    """The value to write into an aspect we're deliberately not filling, or
    None to leave it blank because eBay wouldn't accept a placeholder."""
    if spec.values:
        for candidate in (PLACEHOLDER_VALUE, "Not Applicable", "Unspecified", "Does Not Apply"):
            if candidate in spec.values:
                return candidate
    return PLACEHOLDER_VALUE if spec.free_text else None


def _set_placeholder(specifics: dict, name: str, spec: ebay_template.AspectSpec) -> None:
    """Marks an aspect as deliberately not filled. Writes the placeholder
    where eBay accepts one, otherwise clears the field."""
    placeholder = _placeholder_for(spec)
    if placeholder:
        specifics[name] = placeholder
    else:
        specifics.pop(name, None)


def _is_multi_select(name: str, spec: ebay_template.AspectSpec) -> bool:
    return spec.multi or name in MULTI_SELECT_ASPECTS


# Aspects with "Size" in the name that are NOT a size value at all, so must
# not be resolved from the Measurements file's Size column. "Size Type" is
# Regular/Petite/Tall/Plus/Maternity — a fit category, which the AI enum
# path picks correctly from its short closed list (the account's real
# Optiseller output filled it as "Regular" on every clothing row). Before
# this was split out, it was silently skipped as an unmatched size; after
# the raw-size pass-through in _resolve_size it would instead have been
# filled with the garment's actual size (e.g. "40"), which eBay rejects.
NOT_A_SIZE_ASPECTS = {"C:Size Type"}


def _is_size_aspect(name: str) -> bool:
    return "Size" in name and name not in DETERMINISTIC_ASPECTS and name not in NOT_A_SIZE_ASPECTS


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
    if name == "C:Type":
        # Confirmed 04.09.26: Type is Master File's SubCat2 column (like
        # Colour/Material/Brand/Gender, entered at intake and carried onto
        # the Measurements file at photography — Master is the source).
        raw = m.get("SubCat2")
        if spec.values and len(spec.values) == 1:
            # eBay only allows one Type in this category (e.g. Jumpers &
            # Cardigans -> "Jumper", Boots -> "Boot"), so that IS the
            # answer — matching SubCat2 ("Knitwear") against it just fails
            # and left Type blank on the 04.09.26 batch.
            return spec.values[0]
        if not raw:
            return None
        if spec.values:
            return aspect_matching.fuzzy_match(raw, spec.values, cutoff=0.5)
        return str(raw).strip()
    return None


def _resolve_measurement(name: str, product: Product) -> str | None:
    """Pit to Pit / Length / Arm / Waist Laying Flat / Inside Leg — read
    straight off the measurements file's matching column, verified-source
    only (see MEASUREMENT_ASPECTS)."""
    raw = product.measurements.get(name[len("C:"):])
    raw = str(raw).strip() if raw is not None else ""
    return raw or None


def _resolve_size(name: str, product: Product, spec: ebay_template.AspectSpec) -> str | None:
    m, meas = product.master, product.measurements
    # Sizing NEVER falls back to the Master File. The Master File's Size
    # column can be wrong — it's entered earlier in the process, before the
    # item is ever physically handled. The Pictures & Measurements file is
    # filled in when the item is actually photographed and measured, so
    # it's the one point in the pipeline where size gets verified against
    # the real physical item. Falling back to the Master File here would
    # silently reintroduce exactly the wrong-size-shipped/customer-service/
    # negative-feedback risk that the measuring step exists to prevent —
    # confirmed as an explicit business rule 04.09.26.
    raw = meas.get("Size")
    if name == "C:UK Shoe Size":
        # Measurements file's raw shoe "Size" is in EU sizing, not UK — see
        # aspect_matching.match_shoe_size_uk / parse_shoe_size. Converted
        # via the gender-appropriate table, never matched directly against
        # the UK list and never passed through raw: the raw number is on a
        # different scale, so an unconverted value in a UK field is a wrong
        # size, not a differently-formatted right one.
        return aspect_matching.match_shoe_size_uk(raw, spec.values, m.get("Gender"),
                                                  brand=m.get("Brand"))
    if name == "C:EU Shoe Size":
        return aspect_matching.match_shoe_size_eu(raw, spec.values, brand=m.get("Brand"))
    if name in ("C:US Shoe Size", "C:AU Shoe Size"):
        # Deliberately left blank (both are Preferred, never Required).
        # 04.09.26: these were being filled by the same broken direct match
        # that put "4.5" in the UK field. The account's history does fill
        # them (US = UK+2 women / UK+0.5 men, AU = US women / UK men) but
        # that's a convention to confirm with Sammy before automating —
        # a blank is harmless, a wrong size is not.
        return None
    matched = aspect_matching.match_size(raw, spec.values)
    if matched or name != "C:Size":
        # Other size-family aspects (Waist Size, Chest Size, Ring Size, Cup
        # Size...) measure something the plain Size column doesn't, so an
        # unmatched raw value is left blank rather than passed through.
        return matched
    # No match against eBay's list — write the measured size through as-is.
    # Sammy's call, 04.09.26: "these just need to be pushed into the SIZE
    # column." Backed by the real Optiseller output file this account used
    # to upload with (PreamatoFashionP45OutputFinalAug2026...xlsx): it
    # writes raw sizes like 46, 39, 31, "33/32", "XXL", "4T" straight into
    # C:Size for categories whose eBay list only "recommends" IT 46 / 2XL /
    # etc., and eBay accepted every one — so Size on clothing categories is
    # a free-text aspect with suggested values, not a closed list. The list
    # is still matched against first so casing/aliases get normalised where
    # they can be ("xl" -> "XL", "os" -> "One Size"); this is only the
    # fallback for a size on a scale eBay's suggestions don't include.
    raw = str(raw).strip() if raw is not None else ""
    return raw or None


def classify_aspects(
    category_id: str, template: ebay_template.EbayTemplate
) -> tuple[
    dict[str, ebay_template.AspectSpec],
    dict[str, ebay_template.AspectSpec],
    dict[str, ebay_template.AspectSpec],
    dict[str, ebay_template.AspectSpec],
]:
    """Splits a category's aspects into (enum_specs, hybrid_specs, multi_specs, skipped).
    enum_specs: small closed lists -> strict schema enum, single value.
    hybrid_specs: large closed list but Required, or any colour-named aspect
        -> AI free-text guess + Python fuzzy-match against the real list.
    multi_specs: aspects eBay allows multiple selected values for (see
        MULTI_SELECT_ASPECTS) -> AI picks every value that applies, not just one.
    (Deterministic and free-text-blank aspects are handled outside this;
    skipped ones are simply not returned at all by the caller.)
    """
    aspects = template.aspects.get(str(category_id), {})
    enum_specs: dict[str, ebay_template.AspectSpec] = {}
    hybrid_specs: dict[str, ebay_template.AspectSpec] = {}
    multi_specs: dict[str, ebay_template.AspectSpec] = {}
    skipped: dict[str, ebay_template.AspectSpec] = {}

    for name, spec in aspects.items():
        if name in DETERMINISTIC_ASPECTS or name in MEASUREMENT_ASPECTS or _is_size_aspect(name):
            continue
        if name in NEVER_FILL_ASPECTS:
            skipped[name] = spec
            continue
        if spec.values is None:
            skipped[name] = spec  # free text, not inferable — left blank
            continue
        if _is_multi_select(name, spec):
            # Same large-list rule as single-value aspects: a non-Required
            # multi-select with a huge list (Character: 271 values, Theme:
            # 133...) isn't worth a prompt full of options and a guess.
            if len(spec.values) > LARGE_LIST_THRESHOLD and spec.level != "REQUIRED" and name not in MULTI_SELECT_ASPECTS:
                skipped[name] = spec
            else:
                multi_specs[name] = spec
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

    return enum_specs, hybrid_specs, multi_specs, skipped


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


def _product_brief(product: Product, size_for_title: str | None = None) -> str:
    m, meas = product.master, product.measurements
    lines = [
        f"SKU: {product.sku}",
        f"Brand: {m.get('Brand')}",
        f"Internal title: {m.get('Clean Title Description')}",
        f"Category / SubCategory: {m.get('Category')} / {m.get('SubCat2')}",
        f"Gender: {m.get('Gender')}",
        # Colour and Material: sourced from the Master File — confirmed
        # 04.09.26 that these are entered at intake, then carried onto the
        # Measurements file at the Orbitvu photography stage (so the two
        # should always agree; Master is the origin). Measurements is kept
        # as a fallback only for a row where Master happens to be blank.
        # Size, by contrast, stays Measurements-only — see _resolve_size —
        # since size is the one field actually verified against the
        # physical item at that stage, not just carried over.
        f"Colour (raw, may not match eBay's exact wording): "
        f"{m.get('Colour') or meas.get('Colour') or '(not recorded)'}",
        # The size the listing will actually carry, already resolved and
        # converted in Python (see generate_for_product). The AI must use
        # this string as-is in the title — on 04.09.26 it was converting
        # the raw EU shoe size to UK itself, so the title and the item
        # specific could disagree. If nothing was resolved, size stays out
        # of the title rather than being guessed from anywhere else.
        f"Size — use EXACTLY this in the title, do not convert or reformat it: "
        f"{size_for_title or '(no size — leave size out of the title)'}",
        f"Raw size as recorded in the Measurements file, for context only: "
        f"{meas.get('Size') or '(not measured)'}",
        f"Composition/Material (raw, may be messy): "
        f"{m.get('Composition') or meas.get('Material') or '(not recorded)'}",
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
    multi_specs: dict[str, ebay_template.AspectSpec],
    product_text: str,
) -> tuple[dict, str]:
    item_specific_props = {}
    item_specific_required = []
    field_notes = []

    # Values that appear word-for-word in the product's own text are a far
    # stronger signal than an inference: a "FRILLY TIERED MINI SKIRT" is
    # Style "Mini", not "Tulip" (04.09.26). Surfaced per field as a hint.
    text_tokens = set(re.findall(r"[a-z0-9]+", product_text.lower()))

    def _literal_hits(values: list[str]) -> list[str]:
        hits = []
        for v in values:
            vt = re.findall(r"[a-z0-9]+", v.lower())
            if vt and all(t in text_tokens for t in vt):
                hits.append(v)
        return hits

    for name, spec in enum_specs.items():
        item_specific_props[name] = {"type": "string", "enum": spec.values}
        hits = _literal_hits(spec.values)
        hint = f" These values appear literally in the item's own data, so prefer one of them unless it's clearly wrong: {', '.join(hits)}." if hits else ""
        if spec.level == "REQUIRED":
            item_specific_required.append(name)
            field_notes.append(f"  {name} (REQUIRED): choose exactly one from its enum list.{hint}")
        else:
            field_notes.append(
                f"  {name} ({spec.level}, optional): choose one from its enum list ONLY if the "
                f"product data clearly supports it; otherwise leave this field out entirely. A "
                f"blank is fine, a guess is not.{hint}"
            )

    for name, spec in multi_specs.items():
        required = spec.level == "REQUIRED"
        item_specific_props[name] = {
            "type": "array",
            "items": {"type": "string", "enum": spec.values},
            "minItems": 1 if required else 0,
        }
        if required:
            item_specific_required.append(name)
        field_notes.append(
            f"  {name} ({spec.level}): eBay allows MULTIPLE values here — return every one "
            f"from its enum list that genuinely fits this item, not just the single best match. "
            f"Buyers filter listings by this field, so under-selecting loses search visibility, "
            f"but only include values that are actually true of the item — don't pad the list "
            f"with ones that don't apply"
            + ("." if required else ", and return an empty list if none genuinely apply.")
            + f" Full list: {', '.join(spec.values)}"
        )

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
   type + the most distinctive attribute (colour/print/material), include the
   Size string given in the product data EXACTLY as written (never convert it
   to another sizing system or invent a UK/EU/US equivalent) and "RRP {{amount}}"
   (no currency symbol, just the number) near the end if space allows, in the
   terse keyword-dense style eBay buyers search with (not a grammatical
   sentence). Do not exceed 80 characters under any circumstance.
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
    cache_key = f"{CACHE_VERSION}-{_sizing_fingerprint()}::{product.sku}::{category.category_id}"

    if not force:
        with _CACHE_LOCK:
            cache = _load_cache(cache_dir)
            if cache_key in cache:
                return cache[cache_key]

    # 1. Everything that can be resolved without the AI is resolved FIRST —
    #    brand, department, type, measurements and, critically, size — so
    #    the AI is handed the exact size string the listing will carry and
    #    can't disagree with it (04.09.26: the title said "UK 11" from the
    #    AI's own EU->UK conversion while the item specific said 4.5).
    aspects = template.aspects.get(str(category.category_id), {})
    resolved: dict[str, str] = {}
    for name, spec in aspects.items():
        if name in DETERMINISTIC_ASPECTS:
            value = _resolve_deterministic(name, product, spec)
        elif name in MEASUREMENT_ASPECTS:
            value = _resolve_measurement(name, product)
        elif _is_size_aspect(name):
            value = _resolve_size(name, product, spec)
            if not value and spec.level == "REQUIRED":
                raise ValueError(f"required field {name!r}: {_size_failure_detail(name, product, spec)}")
        else:
            continue
        if value:
            resolved[name] = value

    raw_size = product.measurements.get("Size")
    size_for_title = aspect_matching.size_display_for(product, resolved)

    # 2. The AI call, for the fields that genuinely need judgment.
    enum_specs, hybrid_specs, multi_specs, skipped = classify_aspects(category.category_id, template)
    product_text = _product_brief(product, size_for_title)
    schema, system = _build_schema_and_system(category, enum_specs, hybrid_specs, multi_specs, product_text)

    result = ai_client.call_structured(
        system=system,
        user=product_text,
        tool_name="submit_listing_content",
        input_schema=schema,
    )

    # Brand is always upper case in the title (Sammy's request, 04.09.26) —
    # enforced here rather than left to the AI, since prompt instructions
    # aren't a guarantee. If the AI's title already contains the brand name
    # (it's told to front-load it — see the system prompt above), just fix
    # its casing; if it somehow left it out entirely, prepend it rather than
    # silently shipping a title with no brand at all.
    brand_raw = str(product.master.get("Brand") or "").strip()
    title = result.get("title", "")
    if brand_raw:
        if re.search(re.escape(brand_raw), title, flags=re.IGNORECASE):
            title = re.sub(re.escape(brand_raw), brand_raw.upper(), title, count=1, flags=re.IGNORECASE)
        else:
            title = f"{brand_raw.upper()} {title}".strip()
        result["title"] = title

    # The title's size must be the SAME size as the item specifics — Sammy,
    # 04.09.26: "it cant show UK7 in the title and then 7.5 in the item
    # specifics - too many customer questions." The prompt hands the AI the
    # exact resolved string and tells it not to convert, but a prompt is not
    # a guarantee (the 04.09.26 batch had a title reading "UK 11" while
    # C:UK Shoe Size said 4.5), so it's enforced in Python here, same as the
    # brand casing above.
    result["title"] = aspect_matching.enforce_title_size(result.get("title", ""), size_for_title)

    if len(result.get("title", "")) > 80:
        result["title"] = result["title"][:80].rstrip()

    specifics = result.get("item_specifics", {}) or {}

    # 3. Validate what the AI returned. The API does not hard-enforce
    #    JSON-schema `enum` server-side — it's guidance, not a guarantee —
    #    so every value is checked against the real list. The rule
    #    throughout: a Required field always ends up with a valid value; a
    #    non-Required field is dropped rather than filled with a fallback.
    #    (04.09.26: the old "or spec.values[0]" fallbacks are what produced
    #    "Character: Aladdin" and "Performance/Activity: American Football".)
    for name, spec in enum_specs.items():
        value = specifics.get(name)
        if value in (spec.values or []):
            continue
        matched = aspect_matching.fuzzy_match(value, spec.values, cutoff=0.5) if value else None
        if matched:
            specifics[name] = matched
        elif spec.level == "REQUIRED":
            specifics[name] = aspect_matching.fuzzy_match(value, spec.values, cutoff=0.3) or spec.values[0]
        else:
            _set_placeholder(specifics, name, spec)

    # Fuzzy-validate the hybrid (large-list) fields against the real values —
    # the AI's free-text guess must still resolve to something eBay accepts.
    for name, spec in hybrid_specs.items():
        guess = specifics.get(name)
        matched = aspect_matching.fuzzy_match(guess, spec.values, cutoff=0.5)
        if matched:
            specifics[name] = matched
        elif _is_colour_aspect(name):
            # Last resort for a colour field: fall back to the item's own
            # raw colour text rather than an unvalidated AI guess. Master
            # File first — Colour is entered at intake and carried onto the
            # Measurements file at photography, so Master is the source of
            # truth here (unlike Size — see _resolve_size).
            raw_colour = product.master.get("Colour") or product.measurements.get("Colour")
            specifics[name] = aspect_matching.fuzzy_match(raw_colour, spec.values, cutoff=0.4) or (guess or "")
        # else: leave the AI's free-text guess as-is (best effort; not in the
        # sampled list shown to it doesn't necessarily mean it's wrong).

    # Multi-select fields (e.g. Occasion): the AI returns a JSON array; keep
    # only values that are literally in the real closed list, joined with
    # "|" (eBay's bulk-CSV convention for a multi-value cell). A Required
    # multi-select that ends up empty falls back to the closest single
    # match; a non-Required one is simply left blank.
    for name, spec in multi_specs.items():
        guesses = specifics.get(name)
        if not isinstance(guesses, list):
            guesses = [guesses] if guesses else []
        matched = []
        for guess in guesses:
            m2 = aspect_matching.fuzzy_match(guess, spec.values, cutoff=0.5)
            if m2 and m2 not in matched:
                matched.append(m2)
        if matched:
            specifics[name] = "|".join(matched)
        elif spec.level == "REQUIRED":
            specifics[name] = spec.values[0]
        else:
            _set_placeholder(specifics, name, spec)

    # Anything the AI returned for a field it wasn't asked about (or one on
    # the never-fill list) is discarded — only vetted fields reach the file.
    allowed = set(enum_specs) | set(hybrid_specs) | set(multi_specs)
    for name in list(specifics):
        if name not in allowed:
            specifics.pop(name, None)

    # Every aspect deliberately left to the placeholder rule — never-fill
    # fields, free-text fields nothing can infer, and large non-required
    # lists — gets "Not Specified" rather than a blank cell, wherever eBay
    # accepts a value that isn't on the aspect's own list.
    for name, spec in skipped.items():
        _set_placeholder(specifics, name, spec)

    # 4. Layer in the deterministic/size fields from step 1 — these always
    #    win over anything the AI said.
    specifics.update(resolved)

    # Size Type defaults to "Regular" when the AI didn't commit to one and
    # the category offers it: it's the fit category (Regular/Petite/Tall/
    # Plus), the stock is standard designer sizing, and the account's own
    # history filled it as Regular on 647 of 648 listings that had it.
    size_type_spec = aspects.get("C:Size Type")
    if size_type_spec and not specifics.get("C:Size Type") and size_type_spec.values and "Regular" in size_type_spec.values:
        specifics["C:Size Type"] = "Regular"

    # Measurements are always written as item specifics, whether or not this
    # category's eBay aspect list happens to define them — Sammy's request,
    # 04.09.26: "keep the size we put into the orbitvu csv in the title,
    # along with the measurements in custom specifics and descriptions for
    # buyers to understand." That matters most exactly where eBay defines no
    # measurement aspect at all, which is every clothing category checked
    # (Skirts, Jumpers, Coats, Dresses): eBay's bulk format turns any
    # unrecognised "C:<name>" column into a custom, buyer-visible item
    # specific, and the account's own Optiseller-era uploads shipped these
    # five columns exactly this way and were accepted. Without this, a
    # department-template run put the measurements in the description only.
    for name in MEASUREMENT_ASPECTS:
        if not specifics.get(name):
            value = _resolve_measurement(name, product)
            if value:
                specifics[name] = value

    # Country of Origin is always appended to the output header (see
    # build.EXTRA_COLUMNS) regardless of whether this category's own eBay
    # Aspects happen to define it — Sammy asked (04.09.26) for it to always
    # be filled in, not just present as an empty column. The deterministic
    # step above already resolves it whenever the category's Aspects define
    # it with a usable closed list to fuzzy-match against; this is the
    # backstop for every other case (no Country of Origin aspect for this
    # category at all, or its closed list is empty/too sparse to match).
    if not specifics.get("C:Country of Origin"):
        raw_country = str(product.master.get("Country of Origin") or "").strip()
        if raw_country:
            alias = aspect_matching.COUNTRY_ALIASES.get(raw_country.lower())
            specifics["C:Country of Origin"] = alias or raw_country

    result["item_specifics"] = specifics

    with _CACHE_LOCK:
        cache = _load_cache(cache_dir)
        cache[cache_key] = result
        _save_cache(cache_dir, cache)
    return result


def _size_failure_detail(name: str, product: Product, spec: ebay_template.AspectSpec) -> str:
    """Plain-language reason a Required size field couldn't be filled, so
    the SKU is skipped with something actionable in the run summary rather
    than a blank or guessed size (the wrong-size-shipped / customer-service
    / negative-feedback risk the Measurements step exists to prevent)."""
    raw_size = product.measurements.get("Size")
    if not raw_size:
        return (
            "no size in the Pictures & Measurements file for this SKU — Master File "
            "size is never used as a fallback (it isn't verified against the physical "
            "item). Add this SKU's size to the Measurements file and re-run."
        )
    if "Shoe Size" in name:
        system, number = aspect_matching.parse_shoe_size(raw_size, product.master.get("Brand"))
        gender = str(product.master.get("Gender") or "").strip().upper()
        if system is None and number is not None:
            # Only reachable if BARE_NUMBER_SHOE_SYSTEM is set back to None.
            return (
                f"the Measurements file size {raw_size!r} is a bare number too small to be "
                f"an EU shoe size, so it could be UK or US — record it as 'EU 38' / 'UK 5' "
                f"style in the Measurements file so there's no ambiguity, and re-run."
            )
        if system in ("EU", "US"):
            if gender not in ("MEN", "WOMEN"):
                return (
                    f"the Measurements file has an {system} size ({raw_size!r}) but the Master "
                    f"File Gender is {gender or '(blank)'!r}, and {system}->UK differs by gender "
                    f"(EU 43 is UK 9 for men, UK 10 for women) — so converting it would be a "
                    f"guess. Either set Gender to MEN or WOMEN, or record the UK size directly "
                    f"(e.g. 'UK 9') in the Measurements file."
                )
            return (
                f"the Measurements file size {raw_size!r} is an {system} size outside the "
                f"{'men' if gender == 'MEN' else 'women'}'s {system}->UK conversion table in "
                f"aspect_matching.py — check the value, or extend the table if it's a real size."
            )
        return (
            f"the Measurements file size {raw_size!r} isn't a recognisable shoe size "
            f"(expected e.g. '45', 'EU 45' or 'UK 11')."
        )
    sample_values = ", ".join(spec.values[:12]) if spec.values else "(no closed list)"
    more = f", +{len(spec.values) - 12} more" if spec.values and len(spec.values) > 12 else ""
    return (
        f"the Measurements file has a size for this SKU ({raw_size!r}), but it "
        f"couldn't be matched to a value eBay accepts for this category's {name!r} "
        f"field: {sample_values}{more}."
    )
