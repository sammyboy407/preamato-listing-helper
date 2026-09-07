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
  NOTE    an assumption the code made that Sammy has already signed off, kept
          for the audit trail. Collapsed into one grouped block at the end of
          the report rather than one line per SKU: on the 295-row footwear
          parcel the bare-number assumption applies to 65 rows, and 65 lines
          of something already decided would bury the handful of REVIEWs that
          actually need her.

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
    kind: str  # "FIX", "REVIEW" or "NOTE"
    message: str
    group: str | None = None  # NOTE only: the heading these are collected under

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
    _check_misfiled_footwear(sku, product, row, issues)
    _check_category_gender(sku, product, row, issues)
    _check_collaboration_title(sku, row, issues)
    _check_internal_references(sku, row, issues)
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


def _explain_bad_measurement(column, value, low, high):
    """Names the most likely correct reading rather than just saying the
    number is wrong. Sammy, 04.09.26: garments are measured laying flat, so
    a pit to pit of 230 is a slipped decimal for 23, not a mystery.

    Order matters. A Length of 90 divides by 10 into a plausible 9, but 90 is
    far more likely to be 90cm (35.4 inches) typed into an inches column, so
    centimetres is tested first over the range where someone would plausibly
    have typed them.

    Deliberately a suggestion, never an auto-correction. 230 could be 23 with
    a stray zero, 30 with a stray 2, or 20 with a stray 3 — three readings
    that all produce a believable number on a live listing. The code cannot
    tell which, so it names the likeliest and lets a person settle it in two
    seconds, the same principle as the mini/midi skirt."""
    as_cm = value / 2.54
    if 40 <= value <= 130 and low <= as_cm <= high:
        return (f"{column} is {value:g}, outside the plausible {low}-{high} inches — "
                f"this looks like centimetres ({value:g}cm is {as_cm:.1f} inches)")
    shifted = value / 10
    if low <= shifted <= high:
        return (f"{column} is {value:g}, impossible for a garment measured laying flat — "
                f"most likely a slipped decimal for {shifted:g}. Check the scan sheet")
    return (f"{column} is {value:g}, outside the plausible {low}-{high} inches — "
            f"check for a typo or a cm value")


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
            issues.append(Issue(sku, "REVIEW", _explain_bad_measurement(column, value, low, high)))


def _check_assumed_size(sku, product, row, issues):
    """A shoe size recorded as a bare number ("9") carries no system marker,
    so reading it as UK 9 is an assumption, not a fact — right for UK-sourced
    stock, half a size out if the pair was actually marked US 9.

    Sammy's call, 04.09.26, having looked at the brands: her stock comes
    through UK retail, so the number someone read off the label was the UK
    one. Listed as UK, and recorded here as a NOTE rather than a REVIEW so
    the assumption stays auditable without burying the report.

    Brands on aspect_matching.US_SIZED_BRANDS are the exception and get their
    own group, since the reading and the evidence behind it are different.
    Only fires where it matters — a row that ended up with a UK shoe size."""
    uk_size = _text(row.get("C:UK Shoe Size"))
    if not uk_size:
        return
    raw = product.measurements.get("Size")
    brand = product.master.get("Brand")
    assumed = aspect_matching.assumed_shoe_system(raw, brand)
    if not assumed:
        return
    if assumed == "UK":
        group = ("recorded as a bare number with no UK/EU/US marker, listed as UK "
                 "(Sammy's call 04.09.26, given the brands come through UK retail). "
                 "Spot-check any of these against the shoe if a size query comes in")
        issues.append(Issue(sku, "NOTE", f"{_text(raw)} -> UK {uk_size}", group=group))
    else:
        # A brand on aspect_matching.US_SIZED_BRANDS. Still an assumption, but
        # a better-evidenced one, so it is recorded separately rather than
        # buried among the UK ones.
        group = (f"recorded as a bare number, read as {assumed} because the brand is on the "
                 f"US-sized list (aspect_matching.US_SIZED_BRANDS). Recording '{assumed} 10' "
                 f"style at intake would remove the guesswork entirely")
        issues.append(Issue(sku, "NOTE",
                            f"{_text(brand)}: {_text(raw)} -> {assumed} {_text(raw)} -> UK {uk_size}",
                            group=group))


def _check_internal_references(sku, row, issues):
    """Anything a buyer should never see: a stockist name, or our own
    grading codes.

    Sammy, 06.09.26. The scrubber removes these, so this check should never
    fire — which is exactly why it is here. It fires only if the scrubber
    missed a phrasing, and a REVIEW is right because the wording has to be
    read by a person rather than patched blindly. 35 listings went live
    saying "QTNDAM2" before it existed."""
    from . import aspect_matching
    for field in ("Title", "ConditionDescription", "Description"):
        text = _text(row.get(field))
        if not text:
            continue
        if aspect_matching._RETAILER_RE.search(text):
            found = aspect_matching._RETAILER_RE.search(text).group(0)
            issues.append(Issue(
                sku, "REVIEW",
                f"{field} names a stockist ({found}) — it must not appear in a listing"))
        if re.search(r"QTNDAM|internal (?:quality )?grad", text, re.IGNORECASE):
            issues.append(Issue(
                sku, "REVIEW",
                f"{field} still carries an internal grading reference"))


def _check_collaboration_title(sku, row, issues):
    """A title naming a second brand.

    eBay's search manipulation policy bans "extra brand names" in a title,
    and enforces it per brand rather than evenly: 12 collaboration titles
    went up on 06.09.26 and exactly one was refused, the Nike one, with
    error 240. The known-refused brands are stripped automatically
    (aspect_matching.TITLE_BLOCKED_BRANDS); the rest are listed here so that
    if eBay ever refuses another one, it is obvious which title to look at
    and which brand to add to that list."""
    title = _text(row.get("Title"))
    if not re.search(r"\s[xX]\s", title):
        return
    issues.append(Issue(
        sku, "NOTE",
        f"{sku}: {title}",
        group="Collaboration titles (eBay can refuse a second brand, error 240)"))


def _check_misfiled_footwear(sku, product, row, issues):
    """A product the Master File files as homeware while its own Department
    and customs tariff code say it is a shoe.

    The routing now sends these to the shoes templates (see
    category_mapping.is_misfiled_footwear), but the underlying row is still
    self-contradictory, so it is named here. Two in 1,752 on 06.09.26, both
    lounge slippers, and the one that reached a listing went out as Home
    Décor with a Type of "Cherries". The fix belongs in the Master File; this
    is so nobody has to find it by reading a listing."""
    from . import category_mapping
    if not category_mapping.is_misfiled_footwear(product):
        return
    issues.append(Issue(
        sku, "NOTE",
        f"{sku}: filed as {_text(product.m('Category'))} / "
        f"{_text(product.m('SubCat2'))} but Department reads "
        f"{_text(product.m('Department'))}, so it is listed as "
        f"{_text(row.get('Category name'))}. Worth correcting in the Master File",
        group="Filed as homeware, listed as footwear"))


def _check_category_gender(sku, product, row, issues):
    """A listing whose category is for a different person than the product.

    The kids/adult half of this is blocked outright before a row is ever
    built (category_mapping.gender_conflict), so anything reported here is a
    men's item in a women's category or the reverse. That one is not blocked,
    because this account's templates genuinely force some crossings — a
    woman's cufflinks have nowhere to go but Men's Jewellery > Cufflinks —
    and dropping those silently would be worse than listing them. So it is
    named instead, and a human decides.

    07.09.26: seven men's sneakers were filed as Boys' Shoes. The kids case
    can no longer happen; this is the same check kept live for the case that
    can."""
    from . import category_mapping
    audience = category_mapping.product_audience(product.m("Gender"))
    if audience not in ("men", "women"):
        return
    category_name = _text(row.get("Category name"))
    listed = category_mapping.category_audience(category_name)
    if listed in ("men", "women") and listed != audience:
        issues.append(Issue(
            sku, "REVIEW",
            f"Gender says {_text(product.m('Gender'))} but it is listed in "
            f"{category_name!r}"))


def _check_range_size(sku, product, row, issues):
    """A boot sold to fit a span of sizes ("2.5-3.5", "45/47" — Moon Boot
    being the usual case).

    Until 06.09.26 the band went into the item specific whole. eBay refused
    it: UK Shoe Size is Required and takes one value off a fixed list, so
    two Moon Boots were rejected out of the first 295 row batch. The specific
    now carries the middle size and the title and description carry the band,
    which is Sammy's rule and what a buyer needs to read anyway. Each one is
    still named in the report, because it is the one place a listing says a
    different size in two places on purpose."""
    raw = _text(product.measurements.get("Size"))
    if "-" not in raw and "/" not in raw:
        return
    uk_size = _text(row.get("C:UK Shoe Size"))
    if not uk_size:
        return
    issues.append(Issue(
        sku, "NOTE",
        f"{sku}: sized as a band ({raw}), listed at UK {uk_size} with the full band in "
        f"the title",
        group="Boots sold across a size band"))


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
    if not [i for i in issues if i.kind != "NOTE"]:
        lines = ["All listings passed every check."]
        for group in sorted({i.group or "" for i in issues}):
            in_group = [i for i in issues if (i.group or "") == group]
            lines.append("")
            lines.append(f"{len(in_group)} listing(s) {group}:")
            for issue in in_group:
                lines.append(f"  {issue.sku}  {issue.message}")
        return "\n".join(lines)

    reviews = [i for i in issues if i.kind == "REVIEW"]
    fixes = [i for i in issues if i.kind == "FIX"]
    notes = [i for i in issues if i.kind == "NOTE"]
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
    for group in sorted({i.group or "" for i in notes}):
        in_group = [i for i in notes if (i.group or "") == group]
        lines.append("")
        lines.append(f"{len(in_group)} listing(s) {group}:")
        for issue in in_group:
            lines.append(f"  {issue.sku}  {issue.message}")
    return "\n".join(lines)
