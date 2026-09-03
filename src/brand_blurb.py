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
is a genuine, pre owned piece from one of fashion's most recognisable luxury houses, ideal for \
the Saint Laurent collector or anyone building a designer wardrobe.\""""


def _style_for_brand(brand: str) -> str:
    idx = int(hashlib.sha256(brand.encode()).hexdigest(), 16) % len(STYLE_DIRECTIVES)
    return STYLE_DIRECTIVES[idx]


def _authenticity_for_brand(brand: str) -> str:
    # Different salt than _style_for_brand so the two picks don't correlate
    # (a brand landing on style #2 shouldn't always also land on phrase #2).
    idx = int(hashlib.sha256(f"authenticity:{brand}".encode()).hexdigest(), 16) % len(AUTHENTICITY_PHRASES)
    return AUTHENTICITY_PHRASES[idx]


def _cache_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "brand_blurb_cache.json"


def _load(cache_dir: str | Path) -> dict:
    p = _cache_path(cache_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def _save(cache_dir: str | Path, cache: dict) -> None:
    _cache_path(cache_dir).write_text(json.dumps(cache, indent=2, sort_keys=True))


def build_blurbs(brands: set[str], cache_dir: str | Path) -> dict[str, str]:
    """Returns {brand: opening_paragraph}, generating + caching any brand not
    already cached."""
    cache = _load(cache_dir)
    changed = False

    for brand in sorted(b for b in brands if b):
        if brand in cache:
            continue
        system = SYSTEM_TEMPLATE.format(
            style_directive=_style_for_brand(brand),
            authenticity_phrase=_authenticity_for_brand(brand),
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
            )
        # Force the brand mention to match the source data's exact casing,
        # regardless of how the model happened to style it (e.g. "Jil
        # Sander" vs the master file's "JIL SANDER").
        paragraph = re.sub(re.escape(brand), brand, paragraph, count=1, flags=re.IGNORECASE)
        cache[brand] = paragraph
        changed = True
        print(f"  [brand blurb] {brand}")

    if changed:
        _save(cache_dir, cache)

    return cache
