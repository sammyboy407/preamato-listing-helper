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

_SHOE_SIZE_RE = re.compile(r"^\s*(uk|eu|eur|us|usa|it|fr|jp)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def parse_shoe_size(raw: str | None) -> tuple[str | None, str | None]:
    """Splits a raw shoe size into (system, number) — ("EU", "45"),
    ("UK", "7"), etc. A bare number is EU if it's in EU range (>= 33 —
    no UK/US adult size gets that high, no EU size is lower); a bare number
    below that is read as BARE_NUMBER_SHOE_SYSTEM (see there — currently UK,
    Sammy's call, since her stock is UK-sourced). Use is_assumed_shoe_system
    to tell an assumed reading apart from an explicit one.
    The number is canonicalised ("40.0" -> "40")."""
    if raw is None:
        return None, None
    m = _SHOE_SIZE_RE.match(str(raw))
    if not m:
        return None, None
    prefix, number = m.group(1), _canonical_number(m.group(2))
    if number is None:
        return None, None
    if prefix:
        return _normalise_size_marker(prefix), number
    return ("EU" if float(number) >= 33 else BARE_NUMBER_SHOE_SYSTEM), number


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


def parse_shoe_size_range(raw: str | None) -> tuple[str | None, str | None, str | None]:
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

    # An explicit marker on either end applies to both ("US 9-10" is a US
    # range, not a US size next to a UK one). Two different explicit markers
    # is not a range anyone meant to write, so it's refused. Belt and braces:
    # since _resolve_range_end puts BOTH ends through one system's table, a
    # mixed range like "UK 6-EU 39" already fails there (39 isn't a UK size,
    # 6 isn't an EU one). This just refuses it earlier and for the honest
    # reason, rather than relying on the tables not overlapping.
    markers = {_normalise_size_marker(m.group(1)) for m in ends if m.group(1)}
    if len(markers) > 1:
        return None, None, None
    if markers:
        system = markers.pop()
    else:
        # Neither end marked: both fall under the bare-number rule, and both
        # have to land in the same system ("30-40" is not a range, it's a
        # typo spanning two scales).
        systems = {parse_shoe_size(n)[0] for n in numbers}
        if len(systems) != 1:
            return None, None, None
        system = systems.pop()
    if system is None:
        return None, None, None
    if float(low) >= float(high):
        return None, None, None
    return system, low, high


def _resolve_range_end(number, system, valid_values, gender):
    """One end of a range, through the same conversion as a single size."""
    if system == "UK":
        return fuzzy_match(number, valid_values)
    table = _eu_to_uk_table(gender) if system == "EU" else _us_to_uk_table(gender) if system == "US" else None
    if table is None:
        return None
    converted = table.get(number)
    return fuzzy_match(converted, valid_values) if converted else None


def match_shoe_size_uk(raw: str | None, valid_values: list[str] | None, gender: str | None) -> str | None:
    """Resolves the UK Shoe Size aspect from a raw Measurements-file size.
    An explicit "UK x" is matched exactly; an EU size (explicit "EU x", or a
    bare number in EU range) is converted via the gender-appropriate table
    above. A US size converts via the gender-appropriate US table. Anything
    else — an EU or US size outside the tables, a size with no recognisable
    number, an EU/US size whose gender isn't MEN or WOMEN — returns None so a
    Required field is never filled with a guess."""
    if not raw or not valid_values:
        return None
    system, number = parse_shoe_size(raw)
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

    # Not a single size — try it as a range ("2.5-3.5").
    range_system, low, high = parse_shoe_size_range(raw)
    if range_system:
        low_uk = _resolve_range_end(low, range_system, valid_values, gender)
        high_uk = _resolve_range_end(high, range_system, valid_values, gender)
        if low_uk and high_uk and low_uk != high_uk:
            return f"{low_uk}-{high_uk}"
    return None


def is_assumed_shoe_system(raw: str | None) -> bool:
    """True when the size carries no system marker and was read as
    BARE_NUMBER_SHOE_SYSTEM by assumption rather than because the value said
    so. Used to flag those rows for spot checking — a bare "9" read as UK 9
    is right for UK-sourced stock and half a size out if the pair was
    actually marked US."""
    if raw is None or BARE_NUMBER_SHOE_SYSTEM is None:
        return False
    text = str(raw)
    m = _SHOE_SIZE_RE.match(text)
    if m:
        if m.group(1):
            return False
        number = _canonical_number(m.group(2))
        return number is not None and float(number) < 33
    # A bare range ("2.5-3.5") is read as UK on exactly the same assumption.
    parts = _RANGE_SPLIT_RE.split(text.strip())
    if len(parts) != 2:
        return False
    ends = [_SHOE_SIZE_RE.match(part) for part in parts]
    if not all(ends) or any(e.group(1) for e in ends):
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


def match_shoe_size_eu(raw: str | None, valid_values: list[str] | None) -> str | None:
    """Resolves the EU Shoe Size aspect: only from a raw size that actually
    IS an EU size (explicit "EU x" or a bare number in EU range) — a "UK 7"
    is never written into an EU field."""
    if not raw or not valid_values:
        return None
    system, number = parse_shoe_size(raw)
    if system == "EU":
        return fuzzy_match(number, valid_values)
    range_system, low, high = parse_shoe_size_range(raw)
    if range_system == "EU":
        low_eu, high_eu = fuzzy_match(low, valid_values), fuzzy_match(high, valid_values)
        if low_eu and high_eu and low_eu != high_eu:
            return f"{low_eu}-{high_eu}"
    return None


def size_display(raw_size, uk_shoe=None, eu_shoe=None, clothing_size=None, both=False):
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
        system, number = parse_shoe_size(raw_size)
        if system is None:
            # A range ("2.5-3.5") is still recorded in a definite system, so
            # it follows the same rule: shown in the system it was recorded
            # in, with the conversion alongside in the description.
            range_system, low, high = parse_shoe_size_range(raw_size)
            if range_system:
                system, number = range_system, f"{low}-{high}"
        if system == "UK":
            primary, other = f"UK {number}", (f"EU {eu_shoe}" if eu_shoe else None)
        elif system == "EU":
            primary, other = f"EU {number}", (f"UK {uk_shoe}" if uk_shoe else None)
        else:
            primary = f"UK {uk_shoe}" if uk_shoe else f"EU {eu_shoe}"
            other = f"EU {eu_shoe}" if uk_shoe and eu_shoe else None
        return f"{primary} ({other})" if both and other else primary
    return clothing_size or None


# An explicit size mention in a title: a system marker followed by a number
# ("UK 7", "EU45", "IT 40", "Sz 03", "Size 8"). Deliberately requires the
# marker, so a bare number that isn't a size — a heel height ("20mm"), an
# "RRP 395", a model name with digits — is never touched.
_TITLE_SIZE_RE = re.compile(
    r"\b(?:UK|EU|EUR|US|USA|IT|FR|JP|Size|Sz)\s*\.?\s*"
    r"(?:\d+(?:\.\d+)?|(?:[2-9]?X{0,3}[SML]|One\s+Size)\b)",
    re.IGNORECASE,
)
_TITLE_RRP_RE = re.compile(r"\bRRP\b.*$", re.IGNORECASE)


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
        return f"{head} {size_for_display} {rrp.group(0)}".strip()
    return f"{cleaned} {size_for_display}".strip()
