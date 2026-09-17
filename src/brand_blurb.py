"""Generates the brand-authority opening paragraph used in every Description,
one Claude call per distinct brand (not per product — it never references
the specific item), cached to disk.

Two things this is deliberately designed around:

1. Variety without a fixed template. Earlier versions asked the AI to fill
   in one sentence of a fixed skeleton, and separately left the
   authenticity line to its own judgment — in both cases independent calls
   to the same prompt converged on near-identical phrasing across brands
   (byte-for-byte identical closing lines; 8/10 brands using "guaranteed to
   be the real thing/deal"). Forcing variety, not hoping for it, fixed
   this: each brand is deterministically assigned (by hashing the brand
   name) one of several opening angles AND one of several short ways to
   phrase the authenticity mention, using different hash salts so the two
   picks don't correlate.
2. Tone: elegant and editorial, closer to fashion magazine copy than a
   sales pitch, with authenticity mentioned only briefly in passing rather
   than dwelt on — not reassurance-desk language ("shop with confidence",
   "our team has done the legwork").
3. Condition tier. The paragraph names the item as pre-owned, so a brand
   is cached twice: once for preloved stock and once for new. On 16.09.26
   sixteen items listed as New with tags opened with "a genuine, pre-owned
   piece" — the blurb is per brand and never saw the condition, so a brand
   whose first item was preloved described every later new one the same
   way. The tier is read off the inspection note
   (aspect_matching.notes_say_new), which is decided before any AI call,
   so it is available here even though the condition id is not.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import ai_client

SCHEMA = {
    "type": "object",
    "properties": {
        "paragraph": {
            "type": "string",
            "description": "The full 2-3 sentence opening paragraph, per the system instructions.",
        },
    },
    "required": ["paragraph"],
}

# Each entry is a different angle to open on, so a batch of listings from
# different brands doesn't all read from the same mold. Assigned per-brand
# by hashing the brand name (see _style_for_brand) rather than left to the
# model to vary on its own.
STYLE_DIRECTIVES = [
    "Open with the brand's origin story or heritage (where/when it was founded, "
    "its place in fashion history).",
    "Open with a confident, editorial statement about the brand's reputation or "
    "standing in fashion today.",
    "Open with what the brand is best known for designing or its signature "
    "aesthetic/design philosophy.",
    "Open with why the brand is considered a lasting, worthwhile addition to a "
    "wardrobe rather than a fleeting trend.",
    "Open with the brand's most famous product line, era, or design detail it's "
    "particularly associated with.",
    "Open by naming what makes the brand distinctive to those who know it well, "
    "in a poised, confident voice.",
]

# Left entirely to its own judgment, the model tends to fall back on brisk
# reassurance-desk phrasing for the authenticity line ("you can shop with
# confidence", "no guesswork involved", "our team has done the legwork") —
# functional, but at odds with the elegant, editorial tone wanted here, and
# it also tends to repeat the same phrasing across brands. So each brand is
# assigned one of these short, understated ways to state it plainly — closer
# to "This is a genuine, pre-owned piece..." than to a customer-service
# assurance — using a different hash salt than the style directive so the
# two picks don't correlate.
AUTHENTICITY_PHRASES = [
    "a genuine, pre-owned piece",
    "an authentic piece, gently preloved",
    "a genuine find, previously loved",
    "a true piece from the house, pre-owned",
    "authentic and carefully preloved",
    "a genuine piece with a history of its own",
    "pre-owned, and entirely authentic",
    "a genuine piece, once cherished by another owner",
]

# The same idea for stock that is genuinely new. Nothing here may imply a
# previous owner or previous wear — that is the whole point of the split.
NEW_AUTHENTICITY_PHRASES = [
    "a genuine piece, new and unworn",
    "an authentic piece, unworn",
    "a genuine piece that has never been worn",
    "authentic, and new",
    "a true piece from the house, still unworn",
    "a genuine piece, brand new",
    "new and entirely authentic",
    "an authentic piece, new and never worn",
]

PRELOVED, NEW = "preloved", "new"


def blurb_key(brand: str, tier: str = PRELOVED) -> str:
    """The cache key for one brand at one condition tier. The preloved tier
    keys on the bare brand name so the blurbs cached before 17.09.26 — and
    every caller that still passes a plain brand — keep working unchanged."""
    return brand if tier != NEW else f"{brand}::{NEW}"


SYSTEM_TEMPLATE = """You are writing the opening paragraph of an eBay listing description for a \
preloved designer fashion reseller. The tone should be elegant and editorial — closer to fashion \
magazine copy than a sales pitch. Avoid customer-service reassurance language entirely (phrases \
like "you can shop with confidence", "no guesswork involved", "our team has done the legwork", \
"rest assured") — they undercut the elegance and this isn't the focus of the piece.

Write 2-3 sentences that:
- Say something true and specific about the brand: its heritage, reputation, design signature, \
or standing in fashion — {style_directive}
- Somewhere in the paragraph, briefly and plainly note that this is {authenticity_phrase} — a \
short factual mention woven naturally into a sentence, not its own dedicated reassurance, and \
not dwelt on further.
- Close with a poised, understated note on why this brand is worth owning — timelessness, \
craftsmanship, design pedigree — varying the phrasing each time rather than reusing a fixed \
closing line.

Be truthful — if you're not confident about specific claims for this brand, use safe, general true \
statements (e.g. "one of the most recognised names in contemporary luxury fashion") rather than \
inventing false specifics. Reproduce the brand name exactly as given to you — same capitalisation \
and punctuation — do not restyle it. This is one of many similar listings from the same seller, so \
avoid a template-y or mail-merge feel — don't force the sentence structure to feel identical to how \
you'd write it for a different brand.

Match this tone and focus (brand heritage and character first, authenticity mentioned only in \
passing, restrained and confident throughout — not this exact structure or wording, which is just \
one example of the register wanted):
"Authentic Saint Laurent, one of the most iconic names in Parisian luxury and a true icon of \
French fashion, known for effortlessly cool tailoring and pieces that never go out of style. This \
is {example_phrase} from one of fashion's most recognisable luxury houses, ideal for \
the Saint Laurent collector or anyone building a designer wardrobe."

{tier_rule}"""


# The one line the model must not get wrong, stated as its own rule at the
# very end of the prompt — after the example, which is the part most likely
# to be copied wholesale.
TIER_RULES = {
    PRELOVED: "This item is pre-owned. Describing it as such is correct.",
    NEW: "IMPORTANT: this particular item is NEW and has never been worn or used. "
         "Do not describe it as pre-owned, preloved, second-hand, previously loved, "
         "previously owned, vintage, or as having had a previous owner or a life "
         "before this one. Nothing in the paragraph may imply prior wear. The "
         "brand's own history and heritage is still what you open on — this item's "
         "history is not, because it does not have one.",
}

EXAMPLE_PHRASES = {
    PRELOVED: "a genuine, pre owned piece",
    NEW: "a genuine piece, new and unworn",
}


def _style_for_brand(brand: str) -> str:
    idx = int(hashlib.sha256(brand.encode()).hexdigest(), 16) % len(STYLE_DIRECTIVES)
    return STYLE_DIRECTIVES[idx]


def _authenticity_for_brand(brand: str, tier: str = PRELOVED) -> str:
    # Different salt than _style_for_brand so the two picks don't correlate
    # (a brand landing on style #2 shouldn't always also land on phrase #2).
    phrases = NEW_AUTHENTICITY_PHRASES if tier == NEW else AUTHENTICITY_PHRASES
    idx = int(hashlib.sha256(f"authenticity:{brand}".encode()).hexdigest(), 16) % len(phrases)
    return phrases[idx]


def _cache_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "brand_blurb_cache.json"


def _load(cache_dir: str | Path) -> dict:
    p = _cache_path(cache_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def _save(cache_dir: str | Path, cache: dict) -> None:
    _cache_path(cache_dir).write_text(json.dumps(cache, indent=2, sort_keys=True))


def build_blurbs(brands, cache_dir: str | Path) -> dict[str, str]:
    """Returns {blurb_key: opening_paragraph}, generating + caching any
    brand/tier pair not already cached.

    `brands` is either a set of (brand, tier) pairs or — for a caller that
    has no condition information — a plain set of brand names, which is
    read as the preloved tier. Look the result up with blurb_key(brand,
    tier); a bare brand name is still a valid key for the preloved tier."""
    cache = _load(cache_dir)
    changed = False

    wanted = {
        (b, t) for b, t in (
            item if isinstance(item, tuple) else (item, PRELOVED)
            for item in brands
        ) if b
    }

    for brand, tier in sorted(wanted):
        key = blurb_key(brand, tier)
        if key in cache:
            continue
        system = SYSTEM_TEMPLATE.format(
            style_directive=_style_for_brand(brand),
            authenticity_phrase=_authenticity_for_brand(brand, tier),
            example_phrase=EXAMPLE_PHRASES[tier],
            tier_rule=TIER_RULES[tier],
        )
        result = ai_client.call_structured(
            system=system,
            user=f"Brand: {brand}",
            tool_name="submit_brand_paragraph",
            input_schema=SCHEMA,
        )
        paragraph = result.get("paragraph", "").strip()
        if not paragraph:
            paragraph = (
                f"{brand} is one of the more distinctive names in contemporary designer fashion, "
                f"known for a consistent point of view and quality construction. This is a "
                f"genuine, pre-owned piece from the house, offered at a fraction of its original price."
                if tier != NEW else
                f"{brand} is one of the more distinctive names in contemporary designer fashion, "
                f"known for a consistent point of view and quality construction. This is a "
                f"genuine piece from the house, new and unworn, offered well below its original price."
            )
        # Force the brand mention to match the source data's exact casing,
        # regardless of how the model happened to style it (e.g. "Jil
        # Sander" vs the master file's "JIL SANDER").
        paragraph = re.sub(re.escape(brand), brand, paragraph, count=1, flags=re.IGNORECASE)
        cache[key] = paragraph
        changed = True
        print(f"  [brand blurb] {brand}" + ("" if tier != NEW else " (new)"))

    if changed:
        _save(cache_dir, cache)

    return cache
