"""Deterministic (non-AI) normalization of source data against an eBay
aspect's closed list of valid values. Used for fields we already have real
data for (Brand, Colour, Department, Country of Origin) — matching against
the exact valid values is a lookup/fuzzy-match problem, not a judgment call,
so it doesn't need an AI call.
"""
from __future__ import annotations

import difflib
import re


def _squash(s: str) -> str:
    """Lowercased, alphanumeric-only — so 'DOLCE & GABBANA' and
    'Dolce&Gabbana' compare equal despite spacing/punctuation differences."""
    return re.sub(r"[^a-z0-9]", "", s.lower())

# The master file uses ISO 3166-1 alpha-3 codes (confirmed: TUR, ITA, CHN,
# PRT, GBR, etc. — NOT the 2-letter alpha-2 codes this table originally had,
# which meant common codes like TUR/PRT/GBR silently matched nothing at all,
# while a few others (CHN, IND, USA) only happened to work by fuzzy-match
# coincidence against the full country name, not by design. Alpha-3 is the
# primary key now; alpha-2 and a few plain-word forms are kept as a
# secondary fallback in case a different source file uses those instead.
COUNTRY_ALIASES = {
    # ISO 3166-1 alpha-3 (primary — matches this account's actual data)
    "afg": "Afghanistan", "alb": "Albania", "dza": "Algeria", "arg": "Argentina",
    "arm": "Armenia", "aus": "Australia", "aut": "Austria", "aze": "Azerbaijan",
    "bgd": "Bangladesh", "blr": "Belarus", "bel": "Belgium", "bol": "Bolivia",
    "bih": "Bosnia and Herzegovina", "bra": "Brazil", "bgr": "Bulgaria",
    "khm": "Cambodia", "can": "Canada", "chl": "Chile", "chn": "China",
    "col": "Colombia", "hrv": "Croatia", "cub": "Cuba", "cyp": "Cyprus",
    "cze": "Czech Republic", "dnk": "Denmark", "ecu": "Ecuador", "egy": "Egypt",
    "est": "Estonia", "eth": "Ethiopia", "fin": "Finland", "fra": "France",
    "geo": "Georgia", "deu": "Germany", "gha": "Ghana", "grc": "Greece",
    "slv": "El Salvador", "cxr": "Christmas Island", "hnd": "Honduras",
    "nic": "Nicaragua", "cri": "Costa Rica", "pan": "Panama",
    "dom": "Dominican Republic", "mus": "Mauritius", "mdg": "Madagascar",
    "lka": "Sri Lanka", "tha": "Thailand", "twn": "Taiwan",
    "svn": "Slovenia", "srb": "Serbia", "mda": "Moldova", "lux": "Luxembourg",
    "gtm": "Guatemala", "hkg": "Hong Kong", "hun": "Hungary", "isl": "Iceland",
    "ind": "India", "idn": "Indonesia", "irn": "Iran", "irq": "Iraq",
    "irl": "Ireland", "isr": "Israel", "ita": "Italy", "jpn": "Japan",
    "jor": "Jordan", "kaz": "Kazakhstan", "ken": "Kenya", "kor": "South Korea",
    "kwt": "Kuwait", "lva": "Latvia", "lbn": "Lebanon", "ltu": "Lithuania",
    "mkd": "North Macedonia", "mys": "Malaysia", "mlt": "Malta", "mar": "Morocco",
    "mmr": "Myanmar", "npl": "Nepal", "nld": "Netherlands", "nzl": "New Zealand",
    "nga": "Nigeria", "nor": "Norway", "pak": "Pakistan", "per": "Peru",
    "phl": "Philippines", "pol": "Poland", "prt": "Portugal", "qat": "Qatar",
    "rou": "Romania", "rus": "Russian Federation", "sau": "Saudi Arabia",
    "srb": "Serbia", "sgp": "Singapore", "svk": "Slovakia", "svn": "Slovenia",
    "zaf": "South Africa", "esp": "Spain", "lka": "Sri Lanka", "swe": "Sweden",
    "che": "Switzerland", "twn": "Taiwan", "tha": "Thailand", "tun": "Tunisia",
    "tur": "Türkiye", "ukr": "Ukraine", "are": "United Arab Emirates",
    "gbr": "United Kingdom", "usa": "United States", "ury": "Uruguay",
    "uzb": "Uzbekistan", "ven": "Venezuela", "vnm": "Vietnam",
    "mus": "Mauritius", "mex": "Mexico",
    # alpha-2 / plain-word fallback
    "us": "United States", "uk": "United Kingdom", "gb": "United Kingdom",
    "uae": "United Arab Emirates", "it": "Italy", "fr": "France", "de": "Germany",
    "es": "Spain", "pt": "Portugal", "cn": "China", "in": "India", "jp": "Japan",
    "kr": "South Korea", "vn": "Vietnam", "tr": "Türkiye", "be": "Belgium",
    "nl": "Netherlands", "ch": "Switzerland", "pl": "Poland", "ro": "Romania",
    "bg": "Bulgaria", "mu": "Mauritius", "ma": "Morocco", "tn": "Tunisia",
    "kh": "Cambodia", "id": "Indonesia", "bd": "Bangladesh", "lk": "Sri Lanka",
    "mx": "Mexico", "br": "Brazil",
}

GENDER_TO_DEPARTMENT = {
    "WOMEN": "Women",
    "MEN": "Men",
    "UNISEX": "Unisex Adults",
    # Kids. eBay's kids categories offer Girls / Boys / Unisex Kids, and
    # none of them were here, so a Moon Boot Kids crib boot went up with an
    # empty Department and was refused outright: "The item specific
    # Department is missing", error 21919303, 06.09.26. Department is
    # Required in every kids shoe category.
    "GIRL": "Girls",
    "GIRLS": "Girls",
    "BOY": "Boys",
    "BOYS": "Boys",
    "KIDS": "Unisex Kids",
    "UNISEX KIDS": "Unisex Kids",
    "CHILD": "Unisex Kids",
    "CHILDREN": "Unisex Kids",
}


_NUMERIC_RE = re.compile(r"^\d+(\.\d+)?$")


def _canonical_number(s: str) -> str | None:
    """'08' -> '8', '8.0' -> '8', '4.50' -> '4.5'; None if not a plain number."""
    s = str(s).strip()
    if not _NUMERIC_RE.match(s):
        return None
    f = float(s)
    return str(int(f)) if f == int(f) else str(f)


def fuzzy_match(value: str | None, valid_values: list[str] | None, cutoff: float = 0.6) -> str | None:
    """Best-effort match of a raw value against a closed list. Exact
    (case-insensitive) match wins; otherwise the closest by similarity
    ratio, or None if nothing clears the cutoff.

    Plain numbers are the exception: they only ever match exactly (after
    canonicalising "08"/"8.0" -> "8"), never by squash or similarity.
    Found 04.09.26 on a real batch: _squash strips the "." so "45" and
    "4.5" compared equal, and an EU 45 boot was written to eBay as UK 4.5
    — a wrong size, the one thing this pipeline must never produce. The
    same collision exists for every X vs X.Y pair (35/3.5, 10/1.0...), and
    difflib scores "45" vs "4.5" at 0.8, so neither loose tier is safe for
    numbers."""
    if not value or not valid_values:
        return None
    value = str(value).strip()
    if not value:
        return None

    lower_map = {v.lower(): v for v in valid_values}
    if value.lower() in lower_map:
        return lower_map[value.lower()]

    canon = _canonical_number(value)
    if canon is not None:
        for v in valid_values:
            if _canonical_number(v) == canon:
                return v
        return None

    squashed_map = {_squash(v): v for v in valid_values}
    if _squash(value) in squashed_map:
        return squashed_map[_squash(value)]

    matches = difflib.get_close_matches(value.lower(), [v.lower() for v in valid_values], n=1, cutoff=cutoff)
    return lower_map[matches[0]] if matches else None


def match_country(raw: str | None, valid_values: list[str] | None) -> str | None:
    if not raw or not valid_values:
        return None
    raw = str(raw).strip()
    alias = COUNTRY_ALIASES.get(raw.lower())
    if alias:
        exact = fuzzy_match(alias, valid_values, cutoff=0.9)
        if exact:
            return exact
    return fuzzy_match(raw, valid_values, cutoff=0.75)


def looks_like_country_code(raw) -> bool:
    """True for something shaped like an ISO country code rather than a
    country name — "SLV", "CXR", "IT". Used to stop an unrecognised code
    being written into a listing as though it were a country."""
    text = str(raw or "").strip()
    return bool(text) and len(text) <= 3 and text.isalpha()


def match_department(gender: str | None, valid_values: list[str] | None) -> str | None:
    if not gender or not valid_values:
        return None
    target = GENDER_TO_DEPARTMENT.get(str(gender).strip().upper())
    if not target:
        return None
    return fuzzy_match(target, valid_values, cutoff=0.9)


def match_brand(raw: str | None, valid_values: list[str] | None) -> str:
    """Brand is required and can never be blank. Prefer an exact/close
    match against eBay's suggested list (fixes casing/spacing mismatches
    like "J.W.ANDERSON" vs "J.W. Anderson"), but fall back to the raw
    brand name rather than "Unbranded" — a wrong guess of "Unbranded" for
    a known designer item is worse than an unmatched-but-correct name."""
    if not raw:
        return "Unbranded"
    # High cutoff deliberately: brand names are short proper nouns, so a
    # looser threshold produces confident-looking but wrong matches (e.g.
    # "Demellier" -> "Ellie", "Ganni" -> "Giovanni" both scored ~0.7-0.77).
    # The squash-exact tier in fuzzy_match already catches legitimate
    # punctuation/spacing variants (e.g. "Dolce & Gabbana" -> "Dolce&Gabbana"
    # scores 0.96) without needing a loose threshold here.
    matched = fuzzy_match(raw, valid_values, cutoff=0.9)
    return matched or str(raw).strip()


SIZE_ALIASES = {
    "os": "One Size",
    "o/s": "One Size",
    "one size": "One Size",
}


# The account's own colour vocabulary, which is a family of colours rather
# than a colour. eBay's Colour list has no entry for any of them, and a
# fuzzy match on the word alone is not just useless but actively wrong:
# 07.09.26, across all 1,752 products, "Neutrals" (118 items) scored closest
# to "Purple" and "Metallic" (133) closest to "Yellow". A beige coat listed
# as purple is worse than no colour at all.
#
# These only ever apply as a last resort — the model answers the colour
# first, reading the item's own title, and a metallic gold bag whose title
# says gold matches Gold long before this map is consulted. Every listing
# whose colour comes from one of these families is named in the checks
# report so it can be eyeballed (validation._check_colour_family).
#
# Burgundy is a dark red, not a brown, which is what the fuzzy match made
# of it. That one is not a guess.
COLOUR_FAMILY_ALIASES = {
    "neutrals": "Beige",
    "neutral": "Beige",
    "metallic": "Silver",
    "burgundy": "Red",
}


def match_colour(raw, valid_values: list[str] | None) -> str | None:
    """eBay colour from the item's own colour text, family words included."""
    if not raw or not valid_values:
        return None
    text = " ".join(str(raw).strip().split())
    alias = COLOUR_FAMILY_ALIASES.get(text.lower())
    if alias:
        return fuzzy_match(alias, valid_values, cutoff=0.9)
    return fuzzy_match(text, valid_values, cutoff=0.4)


# Clothing size markers. eBay's own clothing Size lists carry the marker in
# front — "IT 50", "EU 40", "US 2", "FR 38" — and the team writes it behind:
# "50 IT", "34 IT". Same size, written the other way round, and worth a lot
# because it turns a free-text size into one of eBay's own values, which is
# what makes a listing show up in a size-filtered search.
#
# Sammy briefed the team on 07.09.26 to put the marker on every shoe size.
# The first clothing file back, 08.09.26, has it on the clothing too.
_SIZE_MARKERS = ("UK", "EU", "US", "IT", "FR", "JP", "DE", "USA")
_TRAILING_MARKER_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[-/]?\s*(" + "|".join(_SIZE_MARKERS) + r")\s*$",
    re.IGNORECASE)
_JOINED_MARKER_RE = re.compile(
    r"^\s*(" + "|".join(_SIZE_MARKERS) + r")\s*[-/]?\s*(\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE)


def _marker_first(raw: str) -> str | None:
    """"50 IT" and "IT50" -> "IT 50". None when there is no marker to move."""
    m = _TRAILING_MARKER_RE.match(raw)
    if m:
        return f"{m.group(2).upper()} {m.group(1)}"
    m = _JOINED_MARKER_RE.match(raw)
    if m and raw.strip().upper() != f"{m.group(1).upper()} {m.group(2)}":
        return f"{m.group(1).upper()} {m.group(2)}"
    return None


def _size_format_variants(raw: str) -> list[str]:
    """Cheap, unambiguous rewrites of a raw size string worth trying before
    giving up — never a scale conversion (that's a real judgment call, see
    the EU->UK shoe tables below), just other ways the exact same size gets
    written. Found needed 04.09.26: a Skirts size recorded as "03" failed to
    match a valid "3"/"8"/etc, and a Knitwear size recorded as "UK 8" failed
    to match a valid plain "8" — both are formatting differences, not
    different sizes, but the plain fuzzy_match cutoff (0.85) isn't loose
    enough to bridge either one reliably on its own (e.g. "03" vs "3" scores
    well under it). Order doesn't matter for correctness since every variant
    here is equivalent to the original by construction."""
    variants = [raw]
    # The marker moved to the front, tried BEFORE it is stripped, because
    # eBay's own clothing lists carry it there: "50 IT" should reach
    # "IT 50" rather than settle for a bare "50". Where eBay's list has no
    # marked form (its shoe lists do not), this variant simply matches
    # nothing and the stripped one below is used exactly as before.
    marked = _marker_first(raw)
    if marked:
        variants.append(marked)
        # And the bare number, last, for the lists that carry no marked
        # form at all: men's jeans offer waist inches 24-40 and IT 42-60,
        # so a Rick Owens "34 IT" has no "IT 34" to match and 34 is the
        # closer answer than shipping the raw string. Tried after the
        # marked form so it never wins where eBay does offer one.
        variants.append(marked.split(" ", 1)[1])
    # "UK 8", "eu38", "US 6" etc — a units prefix, not a different scale.
    no_prefix = re.sub(r"^(uk|eu|us|it|fr)\s*", "", raw, flags=re.IGNORECASE).strip()
    if no_prefix and no_prefix != raw:
        variants.append(no_prefix)
    # Leading zeros on an otherwise-plain number ("03" -> "3"): same size,
    # just zero-padded — try it for the raw string and for the prefix-
    # stripped one above, in case both applied (e.g. "UK 03").
    for v in list(variants):
        if re.fullmatch(r"0+\d+(\.\d+)?", v):
            variants.append(v.lstrip("0") or "0")
    return variants


def match_size(raw: str | None, valid_values: list[str] | None) -> str | None:
    if not raw or not valid_values:
        return None
    raw = str(raw).strip()
    alias = SIZE_ALIASES.get(raw.lower())
    if alias:
        exact = fuzzy_match(alias, valid_values, cutoff=0.9)
        if exact:
            return exact
    for variant in _size_format_variants(raw):
        matched = fuzzy_match(variant, valid_values, cutoff=0.85)
        if matched:
            return matched
    return None


# Footwear EU -> UK conversion. The Measurements file's raw shoe "Size" is
# in EU sizing, but eBay's Shoes categories require "UK Shoe Size" — these
# aren't the same numbers, so the raw value must be converted, never
# matched directly against the UK list (the old "try a direct match first"
# step is exactly how EU 45 became UK 4.5 on 04.09.26 — see fuzzy_match).
#
# Tables are separate for women's and men's since they diverge, and are
# aligned to this account's OWN listing history (data/
# account_listings_export.csv, 789 listings, EU/UK pairs entered by hand
# in the Optiseller era): women EU 35->2, 36->3, 37->4, 38->5, 39->6,
# 40->7, 41->8 (i.e. UK = EU - 33); men 41->7, 42->8, 43->9, 44->10,
# 45->11 (UK = EU - 34), with 39->6 / 40->6.5 at the small end. Half EU
# sizes sit half a UK size up. Anything outside these ranges returns None
# so the SKU is skipped with a clear message rather than guessed.
EU_TO_UK_WOMENS_SHOE_SIZE = {
    "34": "1", "34.5": "1.5", "35": "2", "35.5": "2.5", "36": "3", "36.5": "3.5",
    "37": "4", "37.5": "4.5", "38": "5", "38.5": "5.5", "39": "6", "39.5": "6.5",
    "40": "7", "40.5": "7.5", "41": "8", "41.5": "8.5", "42": "9", "42.5": "9.5",
    "43": "10", "43.5": "10.5", "44": "11",
}
EU_TO_UK_MENS_SHOE_SIZE = {
    "39": "6", "39.5": "6", "40": "6.5", "40.5": "7", "41": "7", "41.5": "7.5",
    "42": "8", "42.5": "8.5", "43": "9", "43.5": "9.5", "44": "10", "44.5": "10.5",
    "45": "11", "45.5": "11.5", "46": "12", "46.5": "12.5", "47": "13", "47.5": "13.5",
    "48": "14",
}

# Footwear US -> UK conversion. Sammy's call, 04.09.26, after 9 pairs in the
# QTN02 footwear parcel came in recorded as "US9"/"US11": women's UK = US - 2,
# men's UK = US - 0.5. Both are the standard published conversions and agree
# with the brands' own charts. Written out as tables rather than arithmetic so
# every value is visible and can be checked by eye, same as the EU tables.
US_TO_UK_WOMENS_SHOE_SIZE = {
    "4": "2", "4.5": "2.5", "5": "3", "5.5": "3.5", "6": "4", "6.5": "4.5",
    "7": "5", "7.5": "5.5", "8": "6", "8.5": "6.5", "9": "7", "9.5": "7.5",
    "10": "8", "10.5": "8.5", "11": "9", "11.5": "9.5", "12": "10",
    "12.5": "10.5", "13": "11",
}
US_TO_UK_MENS_SHOE_SIZE = {
    "5": "4.5", "5.5": "5", "6": "5.5", "6.5": "6", "7": "6.5", "7.5": "7",
    "8": "7.5", "8.5": "8", "9": "8.5", "9.5": "9", "10": "9.5", "10.5": "10",
    "11": "10.5", "11.5": "11", "12": "11.5", "12.5": "12", "13": "12.5",
    "13.5": "13", "14": "13.5", "15": "14.5",
}

# What a bare number below EU range means. Sammy's call, 04.09.26: her stock
# is UK-sourced and a shoe marked plainly "9" in this trade is a UK 9, so a
# bare number is read as UK rather than refused. That IS an assumption
# though, and a wrong one puts a US 9 (UK 8.5) on a listing as UK 9 — so
# every row resolved this way is flagged in the checks report for spot
# checking (see validation._check_assumed_size). Set to None to go back to
# refusing them outright.
BARE_NUMBER_SHOE_SYSTEM = "UK"

# Brands where a bare number means US rather than UK. Consulted ONLY for a
# number with no marker on it; an explicit "UK 6" or "EU 42" always wins,
# whatever the brand.
#
# Sammy spotted this on 05.09.26 in the first 295-row batch. Blackstock &
# Weber had three pairs recorded three different ways: "US10", "uk 6", and a
# bare "10". The US10 and the bare 10 are the same Penny Pony Loafer at the
# same RRP, and they went out listed half a size apart (UK 9.5 and UK 10),
# because the bare one fell under the UK default.
#
# Add a brand here only on evidence, not on nationality. The test that found
# this one: does the brand have explicitly US-marked sizes elsewhere in the
# same intake, and no UK-marked ones? GH Bass looks American but its other
# 19 items are in EU sizes that line up with the UK reading, so it stays off
# this list. On Running and Salomon were confirmed as UK by Sammy directly.
US_SIZED_BRANDS = {
    "BLACKSTOCK & WEBER",
    "VISVIM",
}


def _normalise_brand(brand) -> str:
    return " ".join(str(brand or "").strip().upper().split())


def bare_number_system(brand=None) -> str | None:
    """Which scale an unmarked number means, for this brand.

    The brand rule exists because the intake data is inconsistent, not
    because the brands are. The real fix is recording 'US 10' at intake, at
    which point this list stops mattering."""
    if _normalise_brand(brand) in US_SIZED_BRANDS:
        return "US"
    return BARE_NUMBER_SHOE_SYSTEM

# US CHILD sizes, the "C" scale: 1C, 2C. A separate scale from adult US
# sizing, which is why a bare "2" and a "2C" are different shoes.
#
# Deliberately two rows. Sammy's Moon Boot Kids crib boots are recorded as
# "1C-2C" and her Master File records the same shoe as EU 17, and an
# independent conversion chart gives US 1 = UK 0.5 = EU 16 and US 2 = UK 1 =
# EU 17, which agrees with her data exactly. Beyond 2C there is no evidence
# from this account and no second source, so those sizes are refused rather
# than filled in from memory. Add rows when a real pair turns up to confirm
# them, the same way the adult tables were built.
US_CHILD_TO_UK_SHOE_SIZE = {"1": "0.5", "2": "1"}
US_CHILD_TO_EU_SHOE_SIZE = {"1": "16", "2": "17"}

_SHOE_SIZE_RE = re.compile(
    r"^\s*(uk|eu|eur|us|usa|it|fr|jp)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(c)?\s*$",
    re.IGNORECASE)


def parse_shoe_size(raw: str | None, brand=None) -> tuple[str | None, str | None]:
    """Splits a raw shoe size into (system, number) — ("EU", "45"),
    ("UK", "7"), etc. A bare number is EU if it's in EU range (>= 33 —
    no UK/US adult size gets that high, no EU size is lower); a bare number
    below that is read as bare_number_system(brand) — UK by default, US for
    the brands in US_SIZED_BRANDS. Use is_assumed_shoe_system to tell an
    assumed reading apart from an explicit one.
    The number is canonicalised ("40.0" -> "40")."""
    if raw is None:
        return None, None
    m = _SHOE_SIZE_RE.match(str(raw))
    if not m:
        return None, None
    prefix, number = m.group(1), _canonical_number(m.group(2))
    if number is None:
        return None, None
    # A trailing C is the US child scale and overrides everything else: "2C"
    # and "US 2C" are the same shoe, and neither is an adult US 2.
    if m.group(3):
        return "USC", number
    if prefix:
        return _normalise_size_marker(prefix), number
    return ("EU" if float(number) >= 33 else bare_number_system(brand)), number


# Japanese shoe sizes are the length of the foot in centimetres, not a scale
# number: an adult pair runs roughly 21 to 31, in 0.5cm steps. So a "JP 41"
# is not a Japanese size at all — 41cm is not a foot.
#
# This exists because one arrived. QTN02-002-063, a Miharayasuhiro sneaker,
# came through marked JP41 on 07.09.26 after the team started marking every
# size with its country. The Master File records that same shoe as 41 and its
# three siblings, 42, 43 and 44, are live on eBay as EU — so it was an EU size
# with the wrong marker on it. There is no JP conversion table here yet,
# deliberately: nothing in this account's data confirms one, and the last
# thing a size needs is a table built from memory.
JP_SIZE_CM_RANGE = (21.0, 31.0)


def looks_like_japanese_size(number) -> bool:
    """True when a JP-marked number is plausibly a real Japanese size, i.e.
    a foot length in centimetres rather than a European scale number."""
    try:
        return JP_SIZE_CM_RANGE[0] <= float(number) <= JP_SIZE_CM_RANGE[1]
    except (TypeError, ValueError):
        return False


def _normalise_size_marker(prefix: str) -> str:
    """"eur"/"it"/"fr" all mean the EU scale; "usa" means US."""
    p = prefix.upper()
    return "EU" if p in ("EUR", "IT", "FR") else "US" if p == "USA" else p


# A shoe sized as a RANGE rather than a single number: "2.5-3.5", "10.5-12",
# "45/47". Not bad data — some boots genuinely are made to fit a span of
# sizes, Moon Boot being the obvious case, and this account has already sold
# one on eBay with UK Shoe Size set to "10.5-12" and EU Shoe Size "45/47"
# (data/account_listings_export.csv). eBay accepted it, so the range is
# passed through rather than being collapsed to one end, which would be a
# claim about fit that the boot doesn't make.
#
# Both ends still have to resolve to real sizes through the normal rules, so
# a range can't smuggle a value past the conversion tables. A range with a
# marker on one end only ("US 9-10") takes that marker for both.
_RANGE_SPLIT_RE = re.compile(r"\s*[-/\u2013]\s*")


def parse_shoe_size_range(raw: str | None, brand=None) -> tuple[str | None, str | None, str | None]:
    """Splits "2.5-3.5" into ("UK", "2.5", "3.5") — system, low, high — using
    exactly the same system rules as parse_shoe_size. Returns (None, None,
    None) for anything that isn't a two-ended range of plain numbers, which
    includes kids' US notation like "1C-2C" (the C means a child scale this
    code has no table for, so it is refused rather than guessed at)."""
    if raw is None:
        return None, None, None
    parts = _RANGE_SPLIT_RE.split(str(raw).strip())
    if len(parts) != 2 or not all(parts):
        return None, None, None

    ends = [_SHOE_SIZE_RE.match(part) for part in parts]
    if not all(ends):
        return None, None, None
    numbers = [_canonical_number(m.group(2)) for m in ends]
    if any(n is None for n in numbers):
        return None, None, None
    low, high = numbers
    # "1C-2C": the C on either end makes the whole band the child scale.
    child = any(m.group(3) for m in ends)

    # An explicit marker on either end applies to both ("US 9-10" is a US
    # range, not a US size next to a UK one). Two different explicit markers
    # is not a range anyone meant to write, so it's refused. Belt and braces:
    # since _resolve_range_end puts BOTH ends through one system's table, a
    # mixed range like "UK 6-EU 39" already fails there (39 isn't a UK size,
    # 6 isn't an EU one). This just refuses it earlier and for the honest
    # reason, rather than relying on the tables not overlapping.
    markers = {_normalise_size_marker(m.group(1)) for m in ends if m.group(1)}
    if child:
        markers = {"USC"}
    if len(markers) > 1:
        return None, None, None
    if markers:
        system = markers.pop()
    else:
        # Neither end marked: both fall under the bare-number rule, and both
        # have to land in the same system ("30-40" is not a range, it's a
        # typo spanning two scales).
        systems = {parse_shoe_size(n, brand)[0] for n in numbers}
        if len(systems) != 1:
            return None, None, None
        system = systems.pop()
    if system is None:
        return None, None, None
    if float(low) >= float(high):
        return None, None, None
    return system, low, high


def child_band_size(low: str, high: str) -> str | None:
    """Which child size a US "C" band actually gets listed as: the TOP one.

    This is the opposite of the adult band rule, which rounds down, and the
    reason is the scale rather than the shoe. Moon Boot Kids crib boots are
    a 1C-2C band; 1C is UK 0.5, and every eBay kids shoe list starts at UK 1,
    so the bottom of these bands is routinely a size eBay cannot express at
    all. Taking the top gives a size that exists, and it agrees with the
    Master File, which records that same boot as EU 17 — the 2C end.

    Both the UK and the EU field go through here, so they are always derived
    from one shoe and can never disagree with each other. The title still
    carries the whole band."""
    try:
        return high if float(high) >= float(low) else low
    except (TypeError, ValueError):
        return None


def _resolve_range_end(number, system, valid_values, gender):
    """One end of a range, through the same conversion as a single size."""
    if system == "UK":
        converted = number
    else:
        table = (_eu_to_uk_table(gender) if system == "EU"
                 else _us_to_uk_table(gender) if system == "US"
                 else US_CHILD_TO_UK_SHOE_SIZE if system == "USC"
                 else None)
        if table is None:
            return None
        converted = table.get(number)
        if not converted:
            return None
    return fuzzy_match(converted, valid_values)


def match_shoe_size_uk(raw: str | None, valid_values: list[str] | None, gender: str | None,
                       brand=None) -> str | None:
    """Resolves the UK Shoe Size aspect from a raw Measurements-file size.
    An explicit "UK x" is matched exactly; an EU size (explicit "EU x", or a
    bare number in EU range) is converted via the gender-appropriate table
    above. A US size converts via the gender-appropriate US table. Anything
    else — an EU or US size outside the tables, a size with no recognisable
    number, an EU/US size whose gender isn't MEN or WOMEN — returns None so a
    Required field is never filled with a guess."""
    if not raw or not valid_values:
        return None
    system, number = parse_shoe_size(raw, brand)
    if system == "UK":
        return fuzzy_match(number, valid_values)
    if system == "EU":
        # The men's and women's tables genuinely differ (EU 43 is UK 9 for
        # men, UK 10 for women), so the gender must be known — an
        # unrecognised one is refused rather than quietly defaulting to a
        # table. Found 04.09.26: UNISEX footwear was silently taking the
        # women's table, a full size out for anything sized as men's, on 5
        # pairs in one parcel. Record those as "UK 9" in the Measurements
        # file, or set the Master File Gender to MEN/WOMEN.
        table = _eu_to_uk_table(gender)
        if table is None:
            return None
        uk_equivalent = table.get(number)
        return fuzzy_match(uk_equivalent, valid_values) if uk_equivalent else None
    if system == "US":
        # Same gender requirement as EU: US->UK differs by gender (US 9 is
        # UK 7 for women, UK 8.5 for men), so an unknown gender is refused
        # rather than guessed.
        table = _us_to_uk_table(gender)
        if table is None:
            return None
        uk_equivalent = table.get(number)
        return fuzzy_match(uk_equivalent, valid_values) if uk_equivalent else None
    if system == "USC":
        # The child scale is the same for boys and girls, so no gender is
        # needed. Outside the two rows the table has evidence for, refused.
        uk_equivalent = US_CHILD_TO_UK_SHOE_SIZE.get(number)
        return fuzzy_match(uk_equivalent, valid_values) if uk_equivalent else None

    # Not a single size — try it as a range ("2.5-3.5").
    range_system, low, high = parse_shoe_size_range(raw, brand)
    if range_system == "USC":
        child = child_band_size(low, high)
        uk_equivalent = US_CHILD_TO_UK_SHOE_SIZE.get(child) if child else None
        return fuzzy_match(uk_equivalent, valid_values) if uk_equivalent else None
    if range_system:
        low_uk = _resolve_range_end(low, range_system, valid_values, gender)
        high_uk = _resolve_range_end(high, range_system, valid_values, gender)
        if low_uk and high_uk and low_uk != high_uk:
            return middle_size(low_uk, high_uk, valid_values)
    return None


def middle_size(low: str, high: str, valid_values: list[str]) -> str:
    """The size to put in the item specific for a boot sold across a band.

    Moon Boot builds one shell to fit UK 2.5 to 3.5 and prints the band on
    the box. UK Shoe Size is a Required aspect that takes one value from
    eBay's list, so a band has nowhere to go: two of them were refused on
    06.09.26. Sammy's rule: "put the middle number in the item specifics but
    2.5-3.5 in the title". The buyer sees the true band on the listing (the
    title and description are built from the raw size, not from this), and
    the specific carries a real size so the listing is findable and eBay
    accepts it.

    An even-length band rounds DOWN, because a slightly roomy boot is
    wearable and a tight one is a return."""
    try:
        low_i, high_i = valid_values.index(low), valid_values.index(high)
    except ValueError:
        return low
    if high_i < low_i:
        low_i, high_i = high_i, low_i
    return valid_values[(low_i + high_i) // 2]


def assumed_shoe_system(raw: str | None, brand=None) -> str | None:
    """The scale a size was read as BY ASSUMPTION, or None if the value said
    so itself. Lets the checks report name what was assumed rather than just
    that something was."""
    return bare_number_system(brand) if is_assumed_shoe_system(raw, brand) else None


def is_assumed_shoe_system(raw: str | None, brand=None) -> bool:
    """True when the size carries no system marker and was read by assumption
    rather than because the value said so. Used to flag those rows for spot
    checking — a bare "9" read as UK 9 is right for UK-sourced stock and half
    a size out if the pair was actually marked US."""
    if raw is None or bare_number_system(brand) is None:
        return False
    text = str(raw)
    m = _SHOE_SIZE_RE.match(text)
    if m:
        # A marker, or the C that names the child scale, makes it explicit.
        if m.group(1) or m.group(3):
            return False
        number = _canonical_number(m.group(2))
        return number is not None and float(number) < 33
    # A bare range ("2.5-3.5") is read as UK on exactly the same assumption.
    parts = _RANGE_SPLIT_RE.split(text.strip())
    if len(parts) != 2:
        return False
    ends = [_SHOE_SIZE_RE.match(part) for part in parts]
    if not all(ends) or any(e.group(1) or e.group(3) for e in ends):
        return False
    numbers = [_canonical_number(e.group(2)) for e in ends]
    return all(n is not None and float(n) < 33 for n in numbers)


def _eu_to_uk_table(gender):
    """The EU->UK table for this gender, or None when it isn't one of the
    two the tables are defined for."""
    g = str(gender or "").strip().upper()
    if g == "MEN":
        return EU_TO_UK_MENS_SHOE_SIZE
    if g == "WOMEN":
        return EU_TO_UK_WOMENS_SHOE_SIZE
    return None


def _us_to_uk_table(gender):
    """The US->UK table for this gender, or None when it isn't one of the
    two the tables are defined for."""
    g = str(gender or "").strip().upper()
    if g == "MEN":
        return US_TO_UK_MENS_SHOE_SIZE
    if g == "WOMEN":
        return US_TO_UK_WOMENS_SHOE_SIZE
    return None


def match_shoe_size_eu(raw: str | None, valid_values: list[str] | None, brand=None) -> str | None:
    """Resolves the EU Shoe Size aspect: only from a raw size that actually
    IS an EU size (explicit "EU x" or a bare number in EU range) — a "UK 7"
    is never written into an EU field."""
    if not raw or not valid_values:
        return None
    system, number = parse_shoe_size(raw, brand)
    if system == "EU":
        return fuzzy_match(number, valid_values)
    if system == "USC":
        return fuzzy_match(US_CHILD_TO_EU_SHOE_SIZE.get(number), valid_values)
    range_system, low, high = parse_shoe_size_range(raw, brand)
    if range_system == "USC":
        child = child_band_size(low, high)
        return fuzzy_match(US_CHILD_TO_EU_SHOE_SIZE.get(child), valid_values) if child else None
    if range_system == "EU":
        low_eu, high_eu = fuzzy_match(low, valid_values), fuzzy_match(high, valid_values)
        if low_eu and high_eu and low_eu != high_eu:
            return f"{low_eu}-{high_eu}"
    return None


def size_display(raw_size, uk_shoe=None, eu_shoe=None, clothing_size=None, both=False, brand=None):
    """The one size string the title and description are built from, so they
    can never disagree with each other or with the C: columns.

    Sammy's rule, 04.09.26: "if its an EU size in the orbitvu file i.e 45 it
    needs to read EU 45 in the item title and then in the UK size item
    specifics we need to convert it to UK 11." So the size is always shown
    in the system it was actually recorded in — labelled, so a buyer knows
    which scale they're reading — and the converted UK size lives in the
    item specific. With both=True (the description) the converted size is
    shown alongside in brackets, since that's where there's room to spell it
    out for buyers.

    Clothing sizes have no conversion and are shown exactly as recorded."""
    if uk_shoe or eu_shoe:
        system, number = parse_shoe_size(raw_size, brand)
        if system is None:
            # A range ("2.5-3.5") is still recorded in a definite system, so
            # it follows the same rule: shown in the system it was recorded
            # in, with the conversion alongside in the description.
            range_system, low, high = parse_shoe_size_range(raw_size, brand)
            if range_system:
                system, number = range_system, f"{low}-{high}"
        if system == "USC":
            # Sammy, 06.09.26: keep the sizing in the title. The box says
            # 1C-2C, so the listing says 1C-2C, with the UK size eBay was
            # given alongside it in the description. The item specific is a
            # single size because eBay allows nothing else; the title is
            # where the truth about the band lives.
            primary = "US " + "-".join(f"{n}C" for n in str(number).split("-"))
            other = f"UK {uk_shoe}" if uk_shoe else (f"EU {eu_shoe}" if eu_shoe else None)
        elif system == "UK":
            primary, other = f"UK {number}", (f"EU {eu_shoe}" if eu_shoe else None)
        elif system == "EU":
            primary, other = f"EU {number}", (f"UK {uk_shoe}" if uk_shoe else None)
        else:
            primary = f"UK {uk_shoe}" if uk_shoe else f"EU {eu_shoe}"
            other = f"EU {eu_shoe}" if uk_shoe and eu_shoe else None
        return f"{primary} ({other})" if both and other else primary
    return clothing_size or None


def size_display_for(product, specifics, both=False, clothing_fallback=False):
    """size_display wired up from a product and its resolved item specifics.

    Exists because this wiring — which keys to read, and remembering to pass
    the brand — was written out three times (content_generator for the title,
    build for the description, pipeline for the checks). Mutation testing on
    05.09.26 showed two of the three could silently drop the brand and leave
    every test green, which would have put "UK 10" in a description whose
    item specific said 9.5. One copy, one place to get right."""
    clothing = specifics.get("C:Size")
    if clothing_fallback and not clothing:
        clothing = product.measurements.get("Size")
    return size_display(
        product.measurements.get("Size"),
        uk_shoe=specifics.get("C:UK Shoe Size"),
        eu_shoe=specifics.get("C:EU Shoe Size"),
        clothing_size=clothing,
        both=both,
        brand=product.master.get("Brand"),
    )


# An explicit size mention in a title: a system marker followed by a number
# ("UK 7", "EU45", "IT 40", "Sz 03", "Size 8"). Deliberately requires the
# marker, so a bare number that isn't a size — a heel height ("20mm"), an
# "RRP 395", a model name with digits — is never touched.
# The trailing (?:\s*[-/\u2013]\s*\d+(?:\.\d+)?C?)? consumes the second half of a
# range, and the C covers the US child scale. Without it, "Snow Ankle Boots Size 2.5-3.5 RRP 195" lost only the
# "Size 2.5" and left an orphan "-3.5" behind, so the title shipped as
# "Snow Ankle Boots -3.5 UK 2.5-3.5 RRP 195" (two Moon Boots, 05.09.26).
_TITLE_SIZE_RE = re.compile(
    r"\b(?:UK|EU|EUR|US|USA|IT|FR|JP|Size|Sz)\s*\.?\s*"
    r"(?:\d+(?:\.\d+)?C?(?:\s*[-/\u2013]\s*\d+(?:\.\d+)?C?)?"
    r"|(?:[2-9]?X{0,3}[SML]|One\s+Size)\b)",
    re.IGNORECASE,
)
# "RRP" not followed by a letter, so "RRP 245" and "RRP245" both count. The
# \bRRP\b form missed "RRP245" (one GH Bass title, 06.09.26), so the trimmer
# did not know that was the RRP tail, and dropped it to make room.
# A child size with no marker in front of it. A bare number is normally left
# alone in a title, because "20mm" and "RRP 395" are not sizes — but a number
# with a C on it is nothing else, so it is stripped like any other size
# mention. Without this, "Crib Boots Pink 2C" kept the AI's 2C and got the
# resolved band put next to it.
_TITLE_BARE_CHILD_SIZE_RE = re.compile(
    r"\b\d+(?:\.\d+)?C(?:\s*[-/\u2013]\s*\d+(?:\.\d+)?C)?\b", re.IGNORECASE)

_TITLE_RRP_RE = re.compile(r"\bRRP(?![A-Za-z]).*$", re.IGNORECASE)

# A size marker with no number on it, sitting immediately before another size
# marker. "GH BASS Weejun Loafer Brown Leather UK Size EU 44 RRP 245": the AI
# wrote "UK Size", the real size was EU, and stripping the AI's size left the
# words "UK Size" stranded in front of the correct one. Applied repeatedly,
# so a chain collapses: "UK Size EU 44" -> "Size EU 44" -> "EU 44".
_DANGLING_MARKER_RE = re.compile(
    r"\b(?:UK|EU|EUR|US|USA|IT|FR|JP|Size|Sz)\b"
    r"(?=\s+(?:UK|EU|EUR|US|USA|IT|FR|JP|Size|Sz)\b)",
    re.IGNORECASE)


def _drop_dangling_markers(title: str) -> str:
    for _ in range(4):
        cleaned = _DANGLING_MARKER_RE.sub("", title)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned == title:
            return title
        title = cleaned
    return title


def enforce_title_size(title: str, size_for_display: str | None) -> str:
    """Guarantees the title's size agrees with the resolved item specifics.

    Every explicit size mention the AI wrote is removed and the one resolved
    size string is put back in its place — so a title can never carry a
    different size (or a different sizing system) from the C: columns. With
    no resolved size, the size mentions are simply removed rather than
    replaced: a title claiming a size the listing can't back up is worse
    than one that doesn't mention size at all."""
    if not title:
        return title
    cleaned = _TITLE_SIZE_RE.sub(" ", title)
    cleaned = _TITLE_BARE_CHILD_SIZE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bOne\s+Size\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if not size_for_display:
        return cleaned
    # An unmarked size the AI happened to write ("... Overcoat Black L RRP")
    # survives the strip above, since a bare letter or number is too risky to
    # remove blindly. If it's already the right size, leave it be rather than
    # printing it twice.
    if re.search(rf"(?<!\w){re.escape(size_for_display)}(?!\w)", cleaned, flags=re.IGNORECASE):
        return cleaned
    # Put the size just before the trailing "RRP ..." if there is one (the
    # house title format), otherwise on the end.
    rrp = _TITLE_RRP_RE.search(cleaned)
    if rrp:
        head = cleaned[: rrp.start()].rstrip()
        return _drop_dangling_markers(f"{head} {size_for_display} {rrp.group(0)}".strip())
    return _drop_dangling_markers(f"{cleaned} {size_for_display}".strip())


# Words that already tell a buyer who the item is for. If any of these is
# already in the title, no second one is added. "HOMME" is deliberately NOT
# here: it appears inside the brand COMME DES GARCON HOMME PLUS, and treating
# it as a gender word would silently skip those listings.
# Words that already say who a listing is for. Singulars included, and they
# were not: 07.09.26 shipped "LANVIN Mens Core Curb Sneaker White Leather
# Trainers Men EU 44 RRP 795". The AI had written "Men" on the end, this
# pattern only knew "Mens" and "Men's", so it saw no gender word and added
# one of its own. Two gender words in one title reads as a mistake, which is
# the exact thing enforce_title_gender declines to do further down.
#
# "men" cannot match inside "women": the boundary before it is closed by the
# "o", so \b never opens there.
_TITLE_GENDER_RE = re.compile(
    r"\b(?:mens|men's|men|womens|women's|women|man|woman|ladies|lady|gents|"
    r"unisex|boys|boy|girls|girl|kids|childrens|children's|children|child)\b",
    re.IGNORECASE)

# Master File Gender / eBay Department -> the word that goes in the title.
# Sammy, 06.09.26: "we need to add Mens Womens after each brand in the title,
# this is optimal for ebay search results". Unisex gets nothing, by her
# decision the same day: 14 items in the first batch, and a wrong word costs
# more than a missing one.
TITLE_GENDER_WORDS = {
    "MEN": "Mens",
    "MENS": "Mens",
    "WOMEN": "Womens",
    "WOMENS": "Womens",
}


def title_gender_word(department) -> str | None:
    return TITLE_GENDER_WORDS.get(" ".join(str(department or "").strip().upper().split()))


# Stockists and department stores. Sammy, 06.09.26: no reference to any of
# these in a listing. Where the stock came from is nobody's business but ours,
# and naming a retailer on a resale listing invites questions we do not want
# to answer.
#
# A sentence naming one of these is removed whole, because "bought from
# Browns" cannot be neutralised word by word. "Browns" needs the plural: the
# colour "Brown" appears in a third of these listings.
BLOCKED_RETAILERS = [
    r"Browns", r"Farfetch", r"Far Fetch", r"Selfridges", r"Harrods",
    r"Net-?a-?Porter", r"Matches\s?Fashion", r"Mytheresa", r"SSENSE",
    r"Harvey Nichols", r"Dover Street Market", r"Flannels", r"Bergdorf",
    r"Saks", r"Neiman Marcus", r"Nordstrom", r"Bloomingdale'?s",
    r"department store", r"concession",
]
_RETAILER_RE = re.compile(r"\b(?:" + "|".join(BLOCKED_RETAILERS) + r")\b", re.IGNORECASE)

# Internal grading language and codes. Sammy, 06.09.26: "leave out the
# QTNDAM2 references from condition descriptions and the listings", which is
# the same instruction already in the bulk upload SOP — the codes have no
# decode key, so nobody can say what QTNDAM2 actually means, and writing
# "minor factory defect" from an undecoded code states a guess to a buyer as
# fact.
#
# The code and the jargon go. What the item is actually like stays, because
# that is what stops a "not as described" case.
_INTERNAL_REWRITES = [
    # The bracketed code, with or without the words around it.
    (re.compile(r"\s*\([^()]*QTNDAM[^()]*\)", re.IGNORECASE), ""),
    (re.compile(r"\s*\(\s*(?:quality\s+)?grade[^()]*\)", re.IGNORECASE), ""),
    (re.compile(r"\bQTNDAM\s*\d*\b", re.IGNORECASE), ""),
    # The SKU itself, which is on the label and not for the description.
    (re.compile(r"\bQTN\d{2}[-\s]\d{3}[-\s]\d{3}\b", re.IGNORECASE), ""),
    # The jargon, rewritten rather than deleted, so the sentence survives and
    # the buyer keeps the warning.
    (re.compile(r"\b(?:our|the|its)?\s*internal quality grad(?:e|ing)\b", re.IGNORECASE),
     "our inspection"),
    (re.compile(r"\b(?:our|the|its)?\s*internal grad(?:e|ing)\b", re.IGNORECASE),
     "our inspection"),
    (re.compile(r"\bper our (?:internal )?(?:quality )?grading\b", re.IGNORECASE),
     "on inspection"),
    (re.compile(r"\b(?:quality )?grading code\b", re.IGNORECASE), "inspection"),
    (re.compile(r'\bthat fall outside standard\s+"?new"?\s+grading\b', re.IGNORECASE), ""),
    (re.compile(r"\binternal (?:quality )?grade\b", re.IGNORECASE), "our inspection"),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def scrub_internal_references(text) -> str:
    """Removes anything a buyer should never see: stockist names and internal
    grading codes.

    Two different treatments, deliberately. A retailer name takes its whole
    sentence with it, because there is no way to neutralise "originally from
    Browns" a word at a time. Internal grading language is rewritten instead,
    so "the internal quality grade indicates a minor factory defect" becomes
    "our inspection indicates a minor factory defect" — the jargon goes, the
    warning stays. Deleting that sentence would leave a defective item
    described as flawless, which is a worse outcome than the jargon."""
    text = str(text or "")
    if not text.strip():
        return text

    for pattern, replacement in _INTERNAL_REWRITES:
        text = pattern.sub(replacement, text)

    if _RETAILER_RE.search(text):
        kept = [s for s in _SENTENCE_SPLIT.split(text) if not _RETAILER_RE.search(s)]
        text = " ".join(kept)

    # Tidy up after the surgery.
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([,;:])\s*\.", ".", text)
    text = re.sub(r"\.\s*\.+", ".", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^(?:but|however|though|although|and|so)\b[\s,]*", "", text,
                  flags=re.IGNORECASE)
    # A rewrite can land mid-sentence and leave "... worn. our inspection
    # notes ...". Only the words this function introduces are re-capitalised,
    # so an intentional lower-case word like "eBay" is never touched.
    text = re.sub(r"(^|(?<=[.!?])\s+)(our|on|inspection)\b",
                  lambda m: m.group(1) + m.group(2).capitalize(), text)
    return text[:1].upper() + text[1:] if text else text


# Brands eBay refuses to see in a title that is not their own listing.
#
# eBay's search manipulation policy bans "extra brand names" in a title. In
# practice it is enforced per brand, not evenly: of 12 collaboration titles in
# the 05.09.26 batch, adidas x Wales Bonner, adidas x Dingyun Zhang, CDG x New
# Balance, CDG SHIRT x ASICS, MM6 x Salomon, NEW BALANCE CDG, ON RUNNING x FKA
# Twigs and RICK OWENS DRKSHDW x Converse all listed without complaint, and
# only "COMME DES GARCON HOMME PLUS x Nike Air Max TL2.5" was refused, with
# error 240.
#
# So this is a list of brands actually observed being refused on this account,
# not a guess at eBay's rules, and it grows the same way US_SIZED_BRANDS does:
# by evidence. The collaborating brand is not lost, it moves to the
# description, which the policy explicitly allows.
TITLE_BLOCKED_BRANDS = {
    "NIKE",
}

# The joiner in a collaboration title: "CDG x Nike", "MM6 X Salomon".
_COLLAB_JOINER = r"(?:\s+[xX]\s+)"


def collaborating_brand(title: str, own_brand=None) -> str | None:
    """The blocked brand named in a title that is not the item's own.
    Returns None when there is nothing to strip."""
    own = _normalise_brand(own_brand)
    for blocked in sorted(TITLE_BLOCKED_BRANDS):
        if blocked in own:
            continue
        if re.search(rf"\b{re.escape(blocked)}\b", title or "", flags=re.IGNORECASE):
            return blocked
    return None


def strip_blocked_brand(title: str, own_brand=None) -> str:
    """Removes a brand eBay will not accept in someone else's title, along
    with the "x" that joined it on.

    "COMME DES GARCON HOMME PLUS x Nike Air Max TL2.5 Sneaker Black" becomes
    "COMME DES GARCON HOMME PLUS Air Max TL2.5 Sneaker Black". The brand is
    dropped, the rest of the model name is kept, and the listing goes live
    instead of being refused."""
    blocked = collaborating_brand(title, own_brand)
    if not blocked:
        return title
    cleaned = re.sub(rf"{_COLLAB_JOINER}{re.escape(blocked)}\b", " ", title, flags=re.IGNORECASE)
    if cleaned == title:
        cleaned = re.sub(rf"\b{re.escape(blocked)}\b{_COLLAB_JOINER}", " ", title, flags=re.IGNORECASE)
    if cleaned == title:
        cleaned = re.sub(rf"\b{re.escape(blocked)}\b", " ", title, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# Trademarked words the source data uses generically, that eBay refuses
# under error 240 unless the item is genuinely that brand. This is a
# different failure to TITLE_BLOCKED_BRANDS above: that list is a second
# BRAND riding along in a collaboration title ("CDG x Nike"); this one is an
# everyday descriptive WORD that happens to be someone's trademark ("Velcro
# sneaker", "Velcro strap"), which eBay's own error message says to replace
# with the generic term it stands for rather than strip out.
#
# Observed: QTN02-002-741 (Y/PROJECT jeans, 09.09.26) was refused —
# "Y PROJECT Womens Velcro Multi Panel Straight Jeans Green 25 RRP 645" —
# with error 240: "Use the term Velcro in your listing title only if your
# item was made by VELCRO(R) Companies... please go back and remove Velcro
# from your title and use a descriptive term, such as 'hook and loop
# closure', instead." The Master File uses "Velcro" the same way for VEJA's
# Recife trainers (at least six rows, e.g. "VEJA Recife Low Top Velcro
# Sneaker Leather") — none of those had reached a title yet, but the same
# refusal is waiting the first time one does, so the fix is general rather
# than a one-off edit to this single jeans title.
#
# Grows the same way TITLE_BLOCKED_BRANDS does: by evidence, not by
# guessing every trademark eBay might object to.
GENERIC_TRADEMARK_REPLACEMENTS = {
    "velcro": "hook-and-loop",
}

_GENERIC_TRADEMARK_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in GENERIC_TRADEMARK_REPLACEMENTS) + r")\b",
    re.IGNORECASE,
)


def scrub_generic_trademarks(text: str) -> str:
    """Replaces a trademarked word used as everyday descriptive language
    (e.g. "Velcro" for a hook-and-loop strap) with the generic term eBay's
    own error 240 message asks for, so the listing ships instead of being
    refused.

    Capitalisation is preserved so the replacement reads naturally in a
    title-cased title, a shouted all-caps brand line, or a plain sentence:
    "Velcro" -> "Hook-And-Loop", "VELCRO" -> "HOOK-AND-LOOP",
    "velcro" -> "hook-and-loop"."""
    text = str(text or "")
    if not text.strip():
        return text

    def _replace(match: re.Match) -> str:
        word = match.group(0)
        replacement = GENERIC_TRADEMARK_REPLACEMENTS[word.lower()]
        if word.isupper():
            return replacement.upper()
        if word[0].isupper():
            return "-".join(part.capitalize() for part in replacement.split("-"))
        return replacement

    return _GENERIC_TRADEMARK_RE.sub(_replace, text)


# C:Type fallback for when the Master File's SubCat2 column is too generic
# to fuzzy-match a category's real Type options.
#
# 09.09.26. fix33 (and the reboot that made it take effect) correctly
# routed 18 womenswear Tops out of the wrong menswear category and into
# "Ready to Wear > Tops & Shirts" (53159), where they always belonged. That
# category requires C:Type from five real options — Blouse, Button-Up,
# Polo, Tank, T-Shirt — and content_generator's only source for it is
# SubCat2, which just says "Tops" for every one of them.
# fuzzy_match("Tops", these five, cutoff=0.5) scores 0.46 at best
# (Button-Up), under the cutoff every other deterministic match uses, so
# all eighteen came back with C:Type empty and were held out by the
# required-field guard — the exact same shape of loss fix33 had just fixed
# for C:Department, one field over.
#
# This was invisible while they were wrongly landing in menswear's "Shirts
# & Tops > Casual Shirts & Tops" (57990), which has exactly one Type value
# (Button-Up) — the single-value shortcut a few lines up in
# content_generator.py filled it regardless of what SubCat2 said, so it
# looked like it was working.
#
# Reads the product's own title instead, the same trust-the-source-text
# reasoning already used for colour-from-photo and the Mules/Pumps split:
# JACQUEMUS "Le Polo Marino..." says Polo on its face; RICK OWENS
# "...Draped Shoulder Blouse..." says Blouse. Checked most specific to
# least, so "Twisted T-Shirt" matches T-Shirt before the bare "shirt"
# pattern (Button-Up) is even tried, and OUR LEGACY's "Envelop
# Shirt...Corseted Long Sleeve..." matches the literal "Shirt" before the
# softer "corset" hint would have pulled it toward Tank instead.
_TYPE_TITLE_PATTERNS: list[tuple[str, list[str]]] = [
    ("T-Shirt", [r"\bt[\s-]?shirt\b", r"\btee\b", r"\bsweatshirt\b", r"\bjersey\b"]),
    ("Polo", [r"\bpolo\b"]),
    ("Blouse", [r"\bblouse\b", r"\bturtleneck\b"]),
    ("Button-Up", [r"\bshirt\b"]),
    ("Tank", [r"\btank\b", r"\bcami\b", r"\bvest\b", r"\bbustier\b",
              r"\bcorset(?:ed)?\b", r"\bstrapless\b", r"\bhalter\b"]),
]

# A title with none of the words above (RICK OWENS' "Shroud HNK SS Washed
# Denim Top" — no shirt, blouse, polo or tee word anywhere in it) falls
# through to Blouse, eBay's own closest thing to a catch-all for a dressy
# top that isn't a tee, tank or polo. Only used when Blouse is actually one
# of this category's real values — never invented for a category whose
# Type list doesn't offer it.
_TYPE_TITLE_FALLBACK = "Blouse"


def match_type_from_title(title: str | None, valid_values: list[str] | None) -> str | None:
    """Guesses C:Type from the product's own internal title when the raw
    SubCat2 value doesn't fuzzy-match any of a category's real options.
    See the comment above _TYPE_TITLE_PATTERNS for why this exists and how
    the priority order was chosen.

    Never returns a value that isn't actually in valid_values — for a
    category none of these patterns fit, this returns None exactly as a
    failed fuzzy_match would, rather than guessing."""
    if not title or not valid_values:
        return None
    lowered = str(title).lower()
    available = {v.lower(): v for v in valid_values}
    for canonical, patterns in _TYPE_TITLE_PATTERNS:
        real_value = available.get(canonical.lower())
        if not real_value:
            continue
        if any(re.search(pattern, lowered) for pattern in patterns):
            return real_value
    return available.get(_TYPE_TITLE_FALLBACK.lower())


def enforce_title_gender(title: str, department, brand=None) -> str:
    """Puts Mens or Womens straight after the brand.

    Not appended on the end, because eBay weights the front of a title and
    because a buyer scanning a results page reads the first few words. Not
    added at all when the title already says who it is for, which 17 of 282
    did in the first batch ("BURBERRY Rogue Loafer Black Calf Leather Men's
    Shoes EU 44"), since two gender words in one title reads as a mistake.

    The brand is found case-insensitively and the word goes after it. With no
    brand in the title the word goes at the front, which is where the brand
    would have been."""
    title = (title or "").strip()
    word = title_gender_word(department)
    if not title or not word:
        return title
    if _TITLE_GENDER_RE.search(title):
        return title

    brand_text = " ".join(str(brand or "").strip().split())
    if brand_text:
        match = re.search(re.escape(brand_text), title, flags=re.IGNORECASE)
        if match:
            return f"{title[:match.end()]} {word}{title[match.end():]}".strip()
    return f"{word} {title}"


# The internal colour families, as they turn up written in a title. The
# model is handed the resolved colour and told to use it, but a prompt is
# not a guarantee — 08.09.26, three of the first thirty garments went out
# reading "Neutral" while the item specific said Beige.
_TITLE_COLOUR_FAMILY_RE = re.compile(
    r"\b(?:neutrals?|neutral-toned|undyed)\b", re.IGNORECASE)


def enforce_title_colour(title: str, colour: str | None) -> str:
    """Swaps an internal colour family word in the title for the colour the
    listing actually carries.

    Deliberately narrow. It rewrites the family words and nothing else: a
    title saying "Black" on a black coat, or "Ecru" on a beige one, is left
    exactly alone, because a shade name a copywriter chose is a better title
    than a flattened one and only the family words are actively useless.
    "Neutral" is not a colour anybody searches eBay for; "Ecru" at least is.

    Metallic is deliberately NOT in the list. It is a real descriptive word
    for a real finish — "Metallic Leather Sandal" is a good title — and the
    resolved Gold or Silver almost always appears alongside it.

    Nothing to swap in means nothing is swapped out: with no resolved colour
    the title is returned untouched rather than losing a word."""
    title = (title or "").strip()
    colour = " ".join(str(colour or "").strip().split())
    if not title or not colour:
        return title
    if not _TITLE_COLOUR_FAMILY_RE.search(title):
        return title
    # If the resolved colour is already in the title, the family word is a
    # duplicate rather than a stand-in, so it comes out entirely.
    if re.search(rf"\b{re.escape(colour)}\b", title, flags=re.IGNORECASE):
        out = _TITLE_COLOUR_FAMILY_RE.sub("", title)
    else:
        out = _TITLE_COLOUR_FAMILY_RE.sub(colour, title, count=1)
        out = _TITLE_COLOUR_FAMILY_RE.sub("", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def trim_title(title: str, size_for_display: str | None = None, limit: int = 80) -> str:
    """Cuts an over-long title to eBay's limit without losing the parts that
    have to survive.

    The old rule was title[:80], a blind character chop. On 05.09.26 that
    turned "... x Nike Air Max TL2.5 Sneaker Black UK 7.5 RRP 395" into
    "... Sneaker Black UK" — a title ending in a bare "UK", with the size
    gone. That is precisely the title/specifics mismatch enforce_title_size
    exists to prevent, undone one line later.

    So words are dropped from the descriptive middle instead, working
    backwards from just before the size, and the brand at the front, the
    resolved size and the trailing "RRP ..." are all kept. Only if that
    still doesn't fit does it fall back to a cut, and even then on a word
    boundary rather than mid-word."""
    title = (title or "").strip()
    if len(title) <= limit:
        return title

    rrp_match = _TITLE_RRP_RE.search(title)
    tail = rrp_match.group(0).strip() if rrp_match else ""
    head = (title[: rrp_match.start()] if rrp_match else title).strip()

    # The size fragment is protected: find it in the head so the words before
    # it can be dropped without touching it.
    size = (size_for_display or "").strip()
    protected = ""
    if size and size in head:
        cut = head.rindex(size)
        protected = head[cut:].strip()
        head = head[:cut].strip()

    words = head.split()
    while words and len(" ".join(
            [w for w in (" ".join(words), protected, tail) if w])) > limit:
        words.pop()
    result = " ".join(w for w in (" ".join(words), protected, tail) if w)
    if len(result) <= limit:
        return result

    # Nothing left to drop and it still doesn't fit: cut on a word boundary.
    kept = []
    for word in result.split():
        if len(" ".join(kept + [word])) > limit:
            break
        kept.append(word)
    return " ".join(kept)
