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


def fuzzy_match(value: str | None, valid_values: list[str] | None, cutoff: float = 0.6) -> str | None:
    """Best-effort match of a raw value against a closed list. Exact
    (case-insensitive) match wins; otherwise the closest by similarity
    ratio, or None if nothing clears the cutoff."""
    if not value or not valid_values:
        return None
    value = str(value).strip()
    if not value:
        return None

    lower_map = {v.lower(): v for v in valid_values}
    if value.lower() in lower_map:
        return lower_map[value.lower()]

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


def match_size(raw: str | None, valid_values: list[str] | None) -> str | None:
    if not raw or not valid_values:
        return None
    raw = str(raw).strip()
    alias = SIZE_ALIASES.get(raw.lower())
    if alias:
        exact = fuzzy_match(alias, valid_values, cutoff=0.9)
        if exact:
            return exact
    return fuzzy_match(raw, valid_values, cutoff=0.85)


# Footwear EU -> UK conversion. The master file's raw shoe "Size" is in EU
# sizing, but eBay's Shoes categories require "UK Shoe Size" — these aren't
# the same numbers, so a direct fuzzy_match on the raw value silently fails
# (e.g. EU "40" isn't a valid UK size string, so it just won't match,
# leaving a Required field blank). Standard conversion tables — separate for
# women's and men's since they diverge (EU 40 is UK 6.5 for women, UK 6 for
# men): applying the wrong table for the product's actual gender would
# silently produce a wrong-but-plausible-looking size, so the caller must
# pass the resolved gender rather than this defaulting to one table.
EU_TO_UK_WOMENS_SHOE_SIZE = {
    "34": "1.5", "34.5": "2", "35": "2", "35.5": "2.5", "36": "3", "36.5": "3.5",
    "37": "4", "37.5": "4.5", "38": "5", "38.5": "5.5", "39": "6", "39.5": "6.5",
    "40": "6.5", "40.5": "7", "41": "7.5", "41.5": "8", "42": "8", "42.5": "8.5",
    "43": "9",
}
EU_TO_UK_MENS_SHOE_SIZE = {
    "39": "5.5", "39.5": "6", "40": "6", "40.5": "6.5", "41": "7", "41.5": "7.5",
    "42": "8", "42.5": "8.5", "43": "9", "43.5": "9.5", "44": "9.5", "44.5": "10",
    "45": "10.5", "45.5": "11", "46": "11", "46.5": "11.5", "47": "12",
}


def match_shoe_size_uk(raw: str | None, valid_values: list[str] | None, gender: str | None) -> str | None:
    """Matches a raw (assumed EU-sizing) shoe size against a UK Shoe Size
    aspect's valid list, converting via the gender-appropriate EU->UK table
    first since the raw number and the target aspect use different systems."""
    if not raw or not valid_values:
        return None
    direct = match_size(raw, valid_values)
    if direct:
        return direct
    table = EU_TO_UK_MENS_SHOE_SIZE if str(gender or "").strip().upper() == "MEN" else EU_TO_UK_WOMENS_SHOE_SIZE
    uk_equivalent = table.get(str(raw).strip())
    return fuzzy_match(uk_equivalent, valid_values, cutoff=0.9) if uk_equivalent else None
