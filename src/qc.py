"""Quality control on a finished listing, before it reaches eBay.

Sammy, 16.09.26, after a SAINT LAURENT Loulou Puffer went live reading
"Style: Backpack": "maybe we should look into building a QC process on the
app after the app generates the file - it should check ... all the listings
and compare what we are selling with the images."

Two tiers, cheapest first, because that bag never needed a photograph.

TIER 1, free and instant, no AI at all: an aspect that contradicts data the
app already holds. The title said "Shoulder Bag" and the aspect said
Backpack. The composition said "Leather 100" four times over and the aspect
said Acetate. Both are two of our own fields disagreeing with each other,
which is decidable in microseconds and has no false positives.

TIER 2, one vision call per listing: the whole listing against its own main
photograph. This is the only tier that catches the case where the SOURCE
data is wrong, because then every field agrees and every one of them is
wrong. That is not hypothetical here — on the Brook St batch the product
name and colour columns were shuffled against everything else and 24 of 48
would have listed as the wrong item. Nothing but the picture disagrees.

WHAT IT DOES WITH AN ANSWER. It reports. It never silently rewrites a
listing, because a vision model that is 95% right still turns one listing in
twenty from correct into wrong, and nobody would know which. A row it is
confident about is held out of the upload file with its reason, the same
treatment a missing size already gets, and a person decides.

WHEN IT IS UNSURE IT SAYS SO, and an unsure answer never holds a row. A
studio photograph of a folded knit genuinely does not show whether the
sleeves are raglan. The model is told to answer UNSURE for anything the
photograph cannot settle, and only a confident, specific disagreement counts.

NEVER RAISES. A batch of 75 listings does not die because one CDN link went
stale or one API call timed out. A QC tier that cannot run leaves the listing
exactly as it was, which is where it would have been without this file.
"""
from __future__ import annotations

import re

from . import ai_client, config

# ---------------------------------------------------------------------------
# Tier 1 — our own fields, disagreeing with each other
# ---------------------------------------------------------------------------

# An aspect value on the left means the title should contain one of the words
# on the right. Only shapes a title actually names: a listing that calls
# itself a "Handbag" is not claiming a shape, and nothing fires.
_STYLE_WORDS: dict[str, list[str]] = {
    "Backpack": [r"\bback\s?pack\b", r"\brucksack\b"],
    "Belt Bag": [r"\bbelt bag\b", r"\bbum\s?bag\b", r"\bwaist bag\b"],
    "Bucket Bag": [r"\bbucket\b"],
    "Clutch": [r"\bclutch\b", r"\bpouch\b", r"\bpochette\b"],
    "Crossbody": [r"\bcross[\s-]?body\b"],
    "Hobo": [r"\bhobo\b"],
    "Satchel": [r"\bsatchel\b"],
    "Shoulder Bag": [r"\bshoulder\b", r"\bshldr\b"],
    "Top Handle Bag": [r"\btop[\s-]?handle\b"],
    "Tote": [r"\btote\b", r"\bshopper\b"],
}

# Aspects whose value has to be findable in the item's own composition.
# Checked as a family rather than a word: eBay's "Leather" is our
# "Lambskin 100", and flagging that pair would be noise, not a finding.
_MATERIAL_FAMILIES: dict[str, list[str]] = {
    "leather": ["leather", "lambskin", "lamb skin", "lamb", "calfskin", "calf",
                "nappa", "suede", "sheepskin", "goatskin", "goat skin", "shearling",
                "hide", "cowhide", "cow leather", "patent"],
    "cotton": ["cotton", "denim", "canvas", "corduroy", "poplin", "jersey"],
    "wool": ["wool", "cashmere", "mohair", "alpaca", "merino", "tweed", "felt"],
    "silk": ["silk", "satin", "chiffon", "organza"],
    "linen": ["linen", "flax", "ramie"],
    "polyester": ["polyester", "poly", "microfibre", "microfiber"],
    "nylon": ["nylon", "polyamide"],
    "viscose": ["viscose", "rayon", "modal", "lyocell", "tencel", "cupro", "acetate"],
    "acrylic": ["acrylic"],
    "elastane": ["elastane", "spandex", "lycra", "elastodiene"],
    "rubber": ["rubber", "eva", "latex"],
    "metal": ["metal", "brass", "steel", "aluminium", "zamak", "silver", "gold"],
    "plastic": ["plastic", "resin", "pvc", "acrylonitrile", "abs", "polycarbonate",
                "polyurethane", "perspex"],
    "straw": ["straw", "raffia", "wicker", "rattan", "jute"],
}

# C:Fabric Type is deliberately NOT here. Its values (Twill, Terry, Knit,
# Jersey, Denim...) describe how a cloth is made, not what fibre it is made
# of, so they can never be found in a composition. Checking it held back 5
# correct listings on 21.09.26 (an Alexander Wang "Essential Terry"
# sweatshirt for saying Terry, two knits for saying Knit) and eBay accepted
# every one of them as soon as they were uploaded by hand.
_MATERIAL_ASPECTS = ("C:Material", "C:Exterior Material", "C:Outer Shell Material",
                     "C:Upper Material")


def _families(text: str) -> set:
    """Every material family named in a piece of free text."""
    lowered = str(text or "").lower()
    found = set()
    for family, words in _MATERIAL_FAMILIES.items():
        if any(re.search(rf"\b{re.escape(w)}", lowered) for w in words):
            found.add(family)
    return found


def contradictions(row: dict, composition=None) -> list:
    """Every place two of our own fields disagree. Free, and certain."""
    problems = []
    title = str(row.get("Title") or "")
    lowered = title.lower()

    style = str(row.get("C:Style") or "").strip()
    if style in _STYLE_WORDS:
        named = [s for s, pats in _STYLE_WORDS.items()
                 if any(re.search(p, lowered) for p in pats)]
        if named and style not in named:
            problems.append(
                f"C:Style says {style!r} but the title says {' / '.join(named)}: {title!r}")

    comp_families = _families(composition)
    if comp_families:
        for aspect in _MATERIAL_ASPECTS:
            value = str(row.get(aspect) or "").strip()
            if not value:
                continue
            # A multi-value cell is fine if ANY of its values is in the
            # composition; eBay's list is coarser than ours.
            listed = [v for v in value.split("|") if v.strip()]
            # A value that names no family we know cannot be judged, so it
            # is never evidence of a contradiction. Only a value we CAN read
            # (Cotton Blend -> cotton) against a composition that has none
            # of it counts. Absence of knowledge is not a finding.
            judged = [v for v in listed if _families(v)]
            if judged and not any(_families(v) & comp_families for v in judged):
                problems.append(
                    f"{aspect} says {value!r} but the composition is {composition!r}")
    return problems


# ---------------------------------------------------------------------------
# Tier 2 — the listing against its own photograph
# ---------------------------------------------------------------------------

SYSTEM = (
    "You are the last check before a preloved designer listing goes live on eBay. "
    "You are shown ONE studio photograph of the item, on a plain white background, "
    "and the listing that has been written for it.\n\n"
    "Your job is to catch a listing that describes a DIFFERENT ITEM from the one in "
    "the photograph, or that states something about it which the photograph plainly "
    "contradicts.\n\n"
    "Judge only the item. Ignore the white sweep, hangers, mannequins, stands, "
    "shadows, reflections, colour-reference cards, tags and packaging.\n\n"
    "Report a problem ONLY when you are confident and can say what is wrong and what "
    "it should be. Things that count: the wrong kind of garment or bag entirely, a "
    "clearly wrong colour, a clearly wrong material, a stated feature the item "
    "visibly does not have, a stated count that is visibly wrong.\n\n"
    "Things that do NOT count, and must never be reported:\n"
    "- anything the photograph cannot settle: fibre percentages, size, measurements, "
    "country of origin, RRP, brand, season, whether tags are attached\n"
    "- a detail hidden by the angle, the fold or the crop\n"
    "- wording you would have phrased differently\n"
    "- a shade of the same colour, or a broader word for the same material "
    "(lambskin described as leather is correct, not a problem)\n\n"
    "A wrong listing held back costs a few minutes. A correct listing held back on a "
    "guess costs a sale and the team's trust in this check. When the photograph does "
    "not let you tell, say the listing is fine.\n\n"
    "Set confident to true ONLY where you would be willing to stop the listing going "
    "live on the strength of what you can see."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "boolean",
            "description": "True if the photograph is consistent with the listing.",
        },
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string",
                              "description": "What is wrong, e.g. 'C:Style' or 'Title'."},
                    "listed": {"type": "string", "description": "What the listing says."},
                    "observed": {"type": "string",
                                 "description": "What the photograph actually shows."},
                    "confident": {"type": "boolean"},
                },
                "required": ["field", "listed", "observed", "confident"],
            },
        },
    },
    "required": ["matches", "problems"],
}

# Aspects worth putting in front of the model. Everything a photograph cannot
# possibly settle is left out on purpose — it is prompt cost that can only
# produce a false alarm.
_SHOWN_ASPECTS = (
    "C:Type", "C:Style", "C:Colour", "C:Exterior Colour", "C:Material",
    "C:Exterior Material", "C:Outer Shell Material", "C:Upper Material",
    "C:Pattern", "C:Closure", "C:Features", "C:Department", "C:Sleeve Length",
    "C:Neckline", "C:Dress Length", "C:Skirt Length", "C:Jacket/Coat Length",
)


def _listing_text(row: dict) -> str:
    lines = [f"Title: {row.get('Title')}"]
    for name in _SHOWN_ASPECTS:
        value = str(row.get(name) or "").strip()
        if value:
            lines.append(f"{name[2:]}: {value}")
    condition = str(row.get("ConditionDescription") or "").strip()
    if condition:
        lines.append(f"Condition note: {condition}")
    return "\n".join(lines)


def check_against_photo(row: dict, image_url=None, model: str = config.MODEL) -> list:
    """Confident, specific disagreements between the listing and its photo.

    Returns [] for a clean listing, for an unsure one, and for any failure at
    all. Never raises: one stale CDN link must not cost a batch."""
    if not image_url or not str(image_url).startswith("http"):
        return []
    try:
        result = ai_client.call_structured(
            system=SYSTEM,
            user="Does this listing describe the item in the photograph?\n\n"
                 + _listing_text(row),
            tool_name="check_listing",
            input_schema=_SCHEMA,
            image_url=image_url,
            max_retries=2,
            model=model,
        )
    except Exception:  # noqa: BLE001 - QC never fails a batch
        return []
    if result.get("matches"):
        return []
    problems = []
    for problem in result.get("problems") or []:
        if not isinstance(problem, dict) or not problem.get("confident"):
            continue
        field = str(problem.get("field") or "the listing").strip()
        listed = str(problem.get("listed") or "").strip()
        observed = str(problem.get("observed") or "").strip()
        if not observed:
            continue
        problems.append(
            f"{field} says {listed!r} but the photograph shows {observed!r}")
    return problems
