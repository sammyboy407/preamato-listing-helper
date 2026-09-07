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
import re
from pathlib import Path

from . import ai_client, ebay_template
from .data_loader import Product

# (Master File "Category", "SubCat2") pairs where a single eBay category
# mapping is wrong for a meaningful share of products sharing that SubCat2 —
# resolved per-product instead. Extend this set if another SubCat2 turns out
# to have the same problem (e.g. Mules/Sandals mixing flat and heeled styles).
AMBIGUOUS_SUBCATS = {
    ("Footwear", "Pumps"),
    # Added 05.09.26. The first 295-row batch dropped 11 products, every one
    # of them a mule: asked at combo level, with only the words "Footwear /
    # Mules / WOMEN" to go on, the model answered NONE rather than choose
    # between Heels, Sandals and Flats — and 11 listings silently never
    # appeared. Each mule's own title settles it (CHLOE ISLA HIGH HEEL
    # RAFFIA THONG MULE is a heel; VALENTINO BOWOW 75 METALLIC LTHR SANDAL
    # MULE is a sandal), which is what per-product resolution reads.
    ("Footwear", "Mules"),
}

# Footwear that the Master File does not file as footwear.
#
# Sammy, 06.09.26: "shoes slippers need to go under footwear". Two lounge
# slippers (a Givenchy and a Simon Miller) are recorded as Lifestyle / Home
# Accessories, so they were offered to the homeware template first — it is
# alphabetically first — and the Givenchy went out as Home Décor > Other Home
# Décor, with a Type of "Cherries" because the Home Décor Type list has no
# slipper in it, and no size anywhere because Home Décor has no shoe size
# aspect. It is a size 40 men's leather shoe.
#
# The Master File contradicts itself on these rows, and the contradiction is
# what makes them findable: Department reads "Mens Shoes" / "Ladies Shoes"
# and the customs Tariff Code is in chapter 6401-6405, which IS footwear.
# Either of those alone is enough.
FOOTWEAR_TARIFF_PREFIXES = ("6401", "6402", "6403", "6404", "6405")


def _looks_like_footwear(product) -> bool:
    if "shoe" in str(product.m("Department") or "").lower():
        return True
    tariff = "".join(c for c in str(product.m("Tariff Code") or "") if c.isdigit())
    return tariff.startswith(FOOTWEAR_TARIFF_PREFIXES)


def is_misfiled_footwear(product) -> bool:
    """A product whose own Category does not say Footwear while everything
    else about it does.

    Deliberately narrow. Products already filed as Footwear are left alone:
    295 rows went through on 05.09.26 and the Category column was right for
    every one of them, including the kids Moon Boot that correctly wanted the
    kidswear template rather than a shoes one.

    These are resolved per product rather than per combo, for the same reason
    Mules are: the combo here is ("Lifestyle", "Home Accessories", "WOMEN"),
    which the Simon Miller slipper shares with 34 genuine candles, vases and
    trays. One combo-level answer of "Women's Shoes" would drag all 35 into
    footwear. The product's own title is the only thing that can separate
    them."""
    if str(product.m("Category") or "").strip().lower() == "footwear":
        return False
    return _looks_like_footwear(product)


def covers_footwear(template) -> bool:
    """Whether a template has any shoe category at all, so the pipeline can
    offer a misfiled slipper the shoes templates before the homeware one."""
    return any("shoes" in str(c.category_name or "").lower() for c in template.categories)


# ---------------------------------------------------------------------------
# Gender guard on the chosen category.
#
# 07.09.26: seven men's designer sneakers (LANVIN, AMIRI, OUR LEGACY, three
# RICK OWENS DRKSHDW, MIHARAYASUHIRO) came out of the app filed as
# "Boys > Boys' Shoes" (57929). Templates are offered in alphabetical order,
# kidswear comes before menswear_shoes, and the combo
# ("Footwear", "Sneakers", "MEN") was put to the model against the kidswear
# candidate list. On the 295-row batch it had answered NONE for that same
# combo; on a rebuilt cache it answered "Boys' Shoes" instead. One AI call,
# a different answer, and men's £795 sneakers point at the children's
# section.
#
# They did not actually ship wrong, because match_department refuses to write
# "Men" into a Department list that only offers Boys and Unisex Kids, so
# Department and Type came out empty and eBay would have rejected all seven
# with 21919303. That is the safety net catching it at the door. This is the
# rule that stops it happening at all, and it is deterministic: no AI answer,
# on any run, can put an adult product in a kids category or a kids product
# in an adult one.
#
# Deliberately limited to the kids/adult split. Men-in-womenswear and
# vice versa is reported by validation but not blocked, because there are
# real products that legitimately cross it — a woman's cufflinks have
# nowhere to go but "Men's Jewellery > Cufflinks" in this account's
# templates — and a hard block there would silently drop them.
_KIDS_CATEGORY_RE = re.compile(
    r"\b(?:boys?|girls?|kids?|child|children|childrens|children's|baby|babies|"
    r"infant|infants|toddler|toddlers|newborn|junior|juniors)\b",
    re.IGNORECASE)
# "Women's" must not read as men's. \b saves us: the "men" inside "women"
# is preceded by a word character, so the boundary never opens there.
_MENS_CATEGORY_RE = re.compile(r"\bmen'?s?\b", re.IGNORECASE)
_WOMENS_CATEGORY_RE = re.compile(r"\b(?:women'?s?|ladies)\b", re.IGNORECASE)

KIDS_GENDERS = {
    "GIRL", "GIRLS", "BOY", "BOYS", "KID", "KIDS", "CHILD", "CHILDREN",
    "CHILDRENS", "UNISEX KIDS", "BABY", "INFANT", "TODDLER", "JUNIOR",
}
MENS_GENDERS = {"MEN", "MENS", "MAN", "MALE", "GENTS"}
WOMENS_GENDERS = {"WOMEN", "WOMENS", "WOMAN", "LADIES", "LADY", "FEMALE"}
UNISEX_GENDERS = {"UNISEX", "UNISEX ADULTS", "UNISEX ADULT"}


def product_audience(gender) -> str | None:
    """"kids", "men", "women", "unisex" or None for a Master File Gender."""
    value = " ".join(str(gender or "").strip().upper().replace("'", "").split())
    if not value:
        return None
    if value in KIDS_GENDERS:
        return "kids"
    if value in MENS_GENDERS:
        return "men"
    if value in WOMENS_GENDERS:
        return "women"
    if value in UNISEX_GENDERS:
        return "unisex"
    return None


def category_audience(category_name) -> str | None:
    """"kids", "men", "women" or None for an eBay category name."""
    name = str(category_name or "")
    if not name:
        return None
    if _KIDS_CATEGORY_RE.search(name):
        return "kids"
    if _WOMENS_CATEGORY_RE.search(name):
        return "women"
    if _MENS_CATEGORY_RE.search(name):
        return "men"
    return None


def template_audience(template) -> str | None:
    """The audience a whole template is for, when every category in it that
    says anything says the same thing.

    Needed because a template's category names are not all self-describing.
    kidswear.json carries six "Activewear > ..." categories whose names give
    no clue who they are for; without this, a men's tracksuit routed into
    kidswear's "Activewear > Tracksuits & Sets" would sail straight past a
    name-only check. Every named category in that file says Boys or Girls,
    so the file as a whole is unambiguous.

    Returns None for a mixed template (jewellery_watches has both Men's
    Jewellery and Children's Jewellery) and for a neutral one (homeware),
    which is the safe answer: no signal, no block."""
    found = {category_audience(c.category_name) for c in template.categories}
    found.discard(None)
    return found.pop() if len(found) == 1 else None


def effective_audience(category_name, template=None) -> str | None:
    return category_audience(category_name) or (template_audience(template) if template is not None else None)


def gender_conflict(gender, category_name, template=None) -> bool:
    """True when this category is for a different age group than this product.

    Adult (men/women/unisex) never goes in a kids category, and kids never
    goes in an adult one. Everything else is allowed through here."""
    pa = product_audience(gender)
    ca = effective_audience(category_name, template)
    if pa is None or ca is None:
        return False
    if pa == "kids":
        return ca in ("men", "women")
    return ca == "kids"


def eligible_categories(gender, template):
    """The categories in this template that this product's gender is allowed
    to be put in. Used to build the candidate list before the model sees it,
    so a wrong answer is never even available to give."""
    return [c for c in template.categories
            if not gender_conflict(gender, c.category_name, template)]


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


def _needs_its_own_answer(product) -> bool:
    """Whether this product is resolved from its own title rather than from
    its (Category, SubCat2, Gender) labels. Used by both build_mapping and
    lookup — they read the cache with different keys, so if they ever
    disagreed about a product, its mapping would be built under one key and
    read back under the other, and it would silently vanish from the file."""
    if (str(product.m("Category")), str(product.m("SubCat2"))) in AMBIGUOUS_SUBCATS:
        return True
    return is_misfiled_footwear(product)


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

    def _candidates_for(gender):
        """The candidate list this gender is allowed to be offered, and the
        text of it. Built per gender rather than once for the template, so
        the model is never shown a category it must not choose."""
        allowed = eligible_categories(gender, template)
        lines = "\n".join(f"- {c.category_id}: {c.category_name}" for c in allowed)
        return allowed, lines

    combos = {
        (str(p.m("Category")), str(p.m("SubCat2")), str(p.m("Gender")))
        for p in products
        if not _needs_its_own_answer(p)
    }
    for category, subcat2, gender in sorted(combos):
        key = _combo_key(category, subcat2, gender, template_fp)
        if key in cache:
            continue
        allowed, candidate_lines = _candidates_for(gender)
        if not allowed:
            # Every category in this template is for a different age group.
            # Recorded as a miss without spending an API call on it.
            cache[key] = {"category_id": None, "category_name": None,
                          "reasoning": "No category in this template is for this age group."}
            changed = True
            print(f"  [category map] {category} / {subcat2} / {gender} -> NO MATCH in this template")
            continue
        user = (
            f"Product's internal labels:\n"
            f"  Category: {category}\n"
            f"  SubCat2: {subcat2}\n"
            f"  Gender: {gender}\n\n"
            f"Candidate eBay categories (this template's coverage for this age group):\n{candidate_lines}"
        )
        cache[key] = _pick_category(SYSTEM_COMBO, user, allowed)
        changed = True
        status = f"{cache[key]['category_id']} ({cache[key]['category_name']})" if cache[key]['category_id'] else "NO MATCH in this template"
        print(f"  [category map] {category} / {subcat2} / {gender} -> {status}")

    ambiguous_products = [p for p in products if _needs_its_own_answer(p)]
    for p in ambiguous_products:
        key = _product_key(p.sku, template_fp)
        if key in cache:
            continue
        allowed, candidate_lines = _candidates_for(p.m("Gender"))
        if not allowed:
            cache[key] = {"category_id": None, "category_name": None,
                          "reasoning": "No category in this template is for this age group."}
            changed = True
            print(f"  [category map] {p.sku} -> NO MATCH in this template")
            continue
        user = (
            f"Product's internal labels:\n"
            f"  Category: {p.m('Category')}\n"
            f"  SubCat2: {p.m('SubCat2')}\n"
            f"  Gender: {p.m('Gender')}\n"
            f"  Title: {p.m('Clean Title Description')}\n\n"
            f"Candidate eBay categories (this template's coverage for this age group):\n{candidate_lines}"
        )
        cache[key] = _pick_category(SYSTEM_PER_PRODUCT, user, allowed)
        changed = True
        status = f"{cache[key]['category_id']} ({cache[key]['category_name']})" if cache[key]['category_id'] else "NO MATCH in this template"
        print(f"  [category map] {p.sku} ({p.m('Clean Title Description')}) -> {status}")

    if changed:
        save_cache(cache_path, cache)

    return cache


def lookup(cache: dict, product: Product, template: ebay_template.EbayTemplate) -> dict | None:
    template_fp = _template_fingerprint(template)
    category, subcat2, gender = str(product.m("Category")), str(product.m("SubCat2")), str(product.m("Gender"))

    if _needs_its_own_answer(product):
        entry = cache.get(_product_key(product.sku, template_fp))
    else:
        entry = cache.get(_combo_key(category, subcat2, gender, template_fp))

    if not entry or not entry.get("category_id"):
        return None

    # Applied on the way out as well as on the way in. The candidate list is
    # already filtered before the model sees it, but caches outlive code:
    # a category_mapping_N.json written before this rule existed still holds
    # the Boys' Shoes answer for ("Footwear", "Sneakers", "MEN"), and reading
    # it back unchecked would ship exactly the listing this rule exists to
    # stop. Rejecting it here also does the right thing at the pipeline
    # level: lookup returning None sends the product on to the next template,
    # which is where menswear_shoes picks it up.
    if gender_conflict(gender, entry.get("category_name"), template):
        return None
    return entry
