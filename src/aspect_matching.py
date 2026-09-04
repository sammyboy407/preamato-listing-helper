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

_SHOE_SIZE_RE = re.compile(r"^\s*(uk|eu|eur|us|usa|it|fr|jp)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def parse_shoe_size(raw: str | None) -> tuple[str | None, str | None]:
    """Splits a raw shoe size into (system, number) — ("EU", "45"),
    ("UK", "7"), etc. A bare number is EU if it's in EU range (>= 33 —
    no UK/US adult size gets that high, no EU size is lower); a bare number
    below that is ambiguous (could be UK or US) and comes back with system
    None, so the caller can refuse to guess rather than pick a scale.
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
        p = prefix.upper()
        return ("EU" if p in ("EUR", "IT", "FR") else "US" if p == "USA" else p), number
    return ("EU" if float(number) >= 33 else None), number


def match_shoe_size_uk(raw: str | None, valid_values: list[str] | None, gender: str | None) -> str | None:
    """Resolves the UK Shoe Size aspect from a raw Measurements-file size.
    An explicit "UK x" is matched exactly; an EU size (explicit "EU x", or a
    bare number in EU range) is converted via the gender-appropriate table
    above. Anything else — a US size, a bare number that could be UK or US,
    an EU size outside the table — returns None so a Required field is
    never filled with a guess."""
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
    return None


def _eu_to_uk_table(gender):
    """The EU->UK table for this gender, or None when it isn't one of the
    two the tables are defined for."""
    g = str(gender or "").strip().upper()
    if g == "MEN":
        return EU_TO_UK_MENS_SHOE_SIZE
    if g == "WOMEN":
        return EU_TO_UK_WOMENS_SHOE_SIZE
    return None


def match_shoe_size_eu(raw: str | None, valid_values: list[str] | None) -> str | None:
    """Resolves the EU Shoe Size aspect: only from a raw size that actually
    IS an EU size (explicit "EU x" or a bare number in EU range) — a "UK 7"
    is never written into an EU field."""
    if not raw or not valid_values:
        return None
    system, number = parse_shoe_size(raw)
    return fuzzy_match(number, valid_values) if system == "EU" else None


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
