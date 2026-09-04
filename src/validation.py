"""Deterministic checks run on every finished listing row, before the CSV is
written.

Sammy's call, 04.09.26: she'd rather a batch took ten minutes and needed no
manual checking than took ninety seconds and needed every row read. At 50
listings a day the bottleneck is her attention, not compute.

Everything here is a rule the code can simply *know* — no AI call, no extra
time, nothing that can itself be wrong in the way a model can. Two kinds of
outcome:

  FIX     something unambiguous was corrected automatically, and the listing
          is fine. Reported so there's a record, not because anyone needs to
          act on it.
  REVIEW  something looks wrong but the code can't tell which side is wrong.
          Named with both values so a human can settle it in a few seconds.

A REVIEW never blocks the file. A listing with a genuinely fatal problem (no
required size) is already skipped upstream in content_generator; these are
the quieter problems that would otherwise reach eBay or a buyer.

The point of the split: silently "correcting" something ambiguous is how a
wrong size ends up on a listing. If the code doesn't know, it says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import aspect_matching, ebay_template
from .data_loader import Product, split_image_urls

MAX_TITLE_LENGTH = 80

# Physical measurements far outside these are almost certainly a typo (230
# for 23, or a centimetre value typed into an inches column) rather than a
# real garment. Deliberately wide: the job is catching data-entry slips, not
# second-guessing an unusual piece.
MEASUREMENT_BOUNDS_INCHES = {
    "Pit to Pit (inches)": (10, 36),
    "Length (inches)": (6, 72),
    "Arm (inches)": (6, 40),
    "Waist Laying Flat (inches)": (8, 32),
    "Inside Leg (inches)": (18, 40),
}

# Style values that assert a length, against the length values that
# contradict them. Sammy's Simone Rocha skirt came out Style "Mini" with
# Skirt Length "Midi" — a buyer filtering for midi finds a mini. Only pairs
# that genuinely can't both be true are listed.
LENGTH_CONTRADICTIONS = {
    "C:Skirt Length": {
        "Mini": {"Midi", "Long"},
        "Maxi": {"Short", "Knee Length"},
    },
    "C:Dress Length": {
        "Mini": {"Midi", "Maxi", "Long"},
        "Maxi": {"Short", "Mini", "Knee Length"},
    },
}


@dataclass
class Issue:
    sku: str
    kind: str  # "FIX" or "REVIEW"
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.sku}: {self.message}"


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def check_row(
    product: Product,
    row: dict,
    category: ebay_template.CategorySpec,
    template: ebay_template.EbayTemplate,
    size_for_display: str | None = None,
) -> list[Issue]:
    """Every check for one finished row. Mutates `row` only for FIX-class
    problems, where there's exactly one correct answer."""
    sku = _text(row.get("Custom label (SKU)")) or product.sku
    issues: list[Issue] = []
    aspects = template.aspects.get(str(category.category_id), {})

    _check_required_aspects(sku, row, aspects, issues)
    _check_title(sku, row, product, size_for_display, issues)
    _check_photos(sku, row, issues)
    _check_price(sku, row, issues)
    _check_measurements(sku, product, issues)
    _check_assumed_size(sku, product, row, issues)
    _check_range_size(sku, product, row, issues)
    _check_length_contradictions(sku, row, issues)
    _check_description_gaps(sku, row, issues)

    return issues


def _check_required_aspects(sku, row, aspects, issues):
    """Every REQUIRED item specific must carry a value. This is the exact
    failure that got all of 04.09.26's listings rejected by eBay ("The item
    specific Brand is missing"), so it's worth catching in our own file
    rather than in eBay's response an hour later."""
    missing = [
        name for name, spec in aspects.items()
        if spec.level == "REQUIRED" and not _text(row.get(name))
    ]
    for name in sorted(missing):
        issues.append(Issue(sku, "REVIEW", f"{name} is required by eBay for this category but is empty — eBay will reject this listing"))


def _check_title(sku, row, product, size_for_display, issues):
    title = _text(row.get("Title"))
    if not title:
        issues.append(Issue(sku, "REVIEW", "no title"))
        return

    if len(title) > MAX_TITLE_LENGTH:
        row["Title"] = title[:MAX_TITLE_LENGTH].rstrip()
        issues.append(Issue(sku, "FIX", f"title was {len(title)} characters, trimmed to eBay's {MAX_TITLE_LENGTH}"))
        title = row["Title"]

    brand = _text(product.master.get("Brand"))
    if brand and brand.lower() not in title.lower():
        issues.append(Issue(sku, "REVIEW", f"title doesn't mention the brand ({brand!r}): {title!r}"))

    # The size in the title is enforced in code (aspect_matching.
    # enforce_title_size), so a mismatch here means that enforcement broke.
    # Cheap to verify, and it's the check that would have caught the
    # "UK 11 in the title, 4.5 in the specifics" bug on its own.
    if size_for_display and size_for_display.lower() not in title.lower():
        issues.append(Issue(sku, "REVIEW", f"title doesn't carry the resolved size ({size_for_display!r}): {title!r}"))

    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", title)]
    repeated = sorted({w for w in words if words.count(w) > 1})
    if repeated:
        issues.append(Issue(sku, "REVIEW", f"title repeats {', '.join(repeated)} — wasted characters: {title!r}"))


def _check_photos(sku, row, issues):
    urls = [u for u in _text(row.get("Item photo URL")).split("|") if u.strip()]
    if not urls:
        issues.append(Issue(sku, "REVIEW", "no photos — eBay will reject this listing"))
    elif len(urls) < 3:
        issues.append(Issue(sku, "REVIEW", f"only {len(urls)} photo(s)"))


def _check_price(sku, row, issues):
    try:
        price = float(_text(row.get("Start price")) or 0)
    except ValueError:
        issues.append(Issue(sku, "REVIEW", f"start price isn't a number: {row.get('Start price')!r}"))
        return
    try:
        rrp = float(_text(row.get("OriginalRetailPrice")) or 0)
    except ValueError:
        rrp = 0.0

    if price <= 0:
        issues.append(Issue(sku, "REVIEW", "start price is zero or missing"))
    elif rrp and price > rrp:
        issues.append(Issue(sku, "REVIEW", f"start price £{price:.0f} is above the RRP of £{rrp:.0f}"))


def _check_measurements(sku, product, issues):
    """Catches a slipped decimal or a centimetre value in an inches column.
    Wide bounds on purpose — this is for data-entry slips, not unusual
    garments."""
    for column, (low, high) in MEASUREMENT_BOUNDS_INCHES.items():
        raw = _text(product.measurements.get(column))
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            issues.append(Issue(sku, "REVIEW", f"{column} isn't a number: {raw!r}"))
            continue
        if not (low <= value <= high):
            issues.append(Issue(
                sku, "REVIEW",
                f"{column} is {value:g}, outside the plausible {low}–{high} inches — check for a typo or a cm value"))


def _check_assumed_size(sku, product, row, issues):
    """A shoe size recorded as a bare number ("9") carries no system marker,
    so reading it as UK 9 is an assumption, not a fact — right for UK-sourced
    stock, half a size out if the pair was actually marked US 9.

    Sammy's call, 04.09.26: convert them so the stock lists, but name every
    one here so a few can be checked against the physical shoes before the
    batch goes up. Only fires where it matters — a row that actually ended up
    with a UK shoe size on it."""
    uk_size = _text(row.get("C:UK Shoe Size"))
    if not uk_size:
        return
    raw = product.measurements.get("Size")
    if aspect_matching.is_assumed_shoe_system(raw):
        issues.append(Issue(
            sku, "REVIEW",
            f"size was recorded as a bare {_text(raw)!r} with no UK/EU/US marker, so it was "
            f"read as UK {uk_size} — check this pair against the shoe before uploading"))


def _check_range_size(sku, product, row, issues):
    """A boot sold to fit a span of sizes ("2.5-3.5", "45/47" — Moon Boot
    being the usual case) carries that range into the item specific rather
    than being collapsed to one end. This account has already sold one that
    way (UK Shoe Size "10.5-12"), so eBay accepts it, but the value is off
    eBay's dropdown list and acceptance can vary by category — so each one is
    named here to be watched on its first upload rather than discovered as a
    rejection."""
    uk_size = _text(row.get("C:UK Shoe Size"))
    if "-" not in uk_size:
        return
    raw = _text(product.measurements.get("Size"))
    issues.append(Issue(
        sku, "REVIEW",
        f"sized as a range ({raw!r}), listed as UK {uk_size} rather than collapsed to one "
        f"end — check eBay accepts the range on this category"))


def _check_length_contradictions(sku, row, issues):
    """A listing that contradicts itself sends buyers to the message inbox.
    Deliberately reported rather than auto-corrected: with Style "Mini" and
    Skirt Length "Midi" the code has no way to know which is right, and
    picking one silently is how a wrong listing ships."""
    style = _text(row.get("C:Style"))
    if not style:
        return
    for field, rules in LENGTH_CONTRADICTIONS.items():
        value = _text(row.get(field))
        if value and value in rules.get(style, set()):
            issues.append(Issue(
                sku, "REVIEW",
                f"Style says {style!r} but {field} says {value!r} — these contradict each other"))


def _check_description_gaps(sku, row, issues):
    """An empty "Type: " line in the buyer-facing description looks
    unfinished. Seen on a real listing 04.09.26 where Type couldn't be
    resolved for the category."""
    description = _text(row.get("Description"))
    blank_labels = re.findall(r"(?:^|<br>|\n)\s*([A-Z][A-Za-z /()]{2,30}):\s*(?=<br>|\n|$)", description)
    for label in sorted(set(blank_labels)):
        issues.append(Issue(sku, "REVIEW", f"the description's {label!r} line is empty"))


def summarise(issues: list[Issue]) -> str:
    """A short human-readable report, grouped so a batch of 50 reads as a
    handful of exceptions rather than a wall of text."""
    if not issues:
        return "All listings passed every check."

    reviews = [i for i in issues if i.kind == "REVIEW"]
    fixes = [i for i in issues if i.kind == "FIX"]
    lines = []
    if reviews:
        skus = sorted({i.sku for i in reviews})
        lines.append(f"{len(reviews)} thing(s) to look at, across {len(skus)} listing(s):")
        for sku in skus:
            lines.append(f"  {sku}")
            for issue in [i for i in reviews if i.sku == sku]:
                lines.append(f"      {issue.message}")
    if fixes:
        lines.append(f"{len(fixes)} thing(s) corrected automatically:")
        for issue in fixes:
            lines.append(f"  {issue.sku}: {issue.message}")
    return "\n".join(lines)
