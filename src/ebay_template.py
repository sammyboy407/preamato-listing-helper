"""Parses an eBay "Create listings in bulk" category template (the multi-sheet
.xlsx Seller Hub exports, containing Listings/Categories/Aspects/BusinessPolicy/
ListingStaticData/ConditionDescriptors sheets).

This is eBay's real upload format — richer and stricter than the old flat
single-sheet "File Exchange" layout: it scopes every listing to a specific
handful of categories the seller selected when downloading the template, and
for each of those categories it defines exactly which item-specific ("C:")
fields are Required/Preferred/Optional, which of them have a closed list of
valid values (and what those values are), and which Condition IDs are valid
(with category-specific labels — e.g. shoes use "New with box" wording where
clothing uses "New with tags").

Uses python_calamine instead of openpyxl to read: these template files often
contain a font `family` value openpyxl's schema rejects (family > 14), which
raises on load. calamine reads it more permissively.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import python_calamine


@dataclass
class CategorySpec:
    category_id: str
    category_name: str
    # (condition_id, label) pairs valid for this specific category, e.g.
    # [(1500, "New without tags"), (1000, "New with tags"), (2990, "Pre-owned - Excellent"), ...]
    conditions: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class AspectSpec:
    name: str  # e.g. "C:Colour"
    level: str  # "REQUIRED" | "PREFERRED" | "OPTIONAL"
    values: list[str] | None  # None means free text (no closed list)
    # True when eBay allows multiple selected values for this aspect (its
    # own itemToAspectCardinality == "MULTI"). Only ever set when this
    # template was loaded from scripts/fetch_ebay_category_aspects.py's API
    # output (see load_json_template) — a real, per-aspect answer from eBay,
    # rather than content_generator.MULTI_SELECT_ASPECTS's hand-maintained
    # guesswork (which is what an .xlsx-sourced template still has to rely
    # on, since the Seller Hub download has no column for this at all — see
    # that module's docstring). Defaults to False so existing .xlsx-sourced
    # templates behave exactly as before.
    multi: bool = False
    # eBay's own aspectMode: "FREE_TEXT" (values is a list of suggestions —
    # any other value is accepted too) or "SELECTION_ONLY" (values is a hard
    # closed list — anything else gets the listing rejected). None means
    # unknown: either an .xlsx-sourced template, or a JSON template
    # generated before scripts/fetch_ebay_category_aspects.py started
    # capturing this. Unknown is treated as strict everywhere it's used, so
    # a missing answer can never cause a rejected listing.
    mode: str | None = None

    @property
    def free_text(self) -> bool:
        """True only when eBay definitely accepts a value that isn't on this
        aspect's list. An aspect with no list at all is free text by
        definition; otherwise it takes an explicit FREE_TEXT from eBay."""
        return self.values is None or self.mode == "FREE_TEXT"


@dataclass
class EbayTemplate:
    listing_headers: list[str]
    categories: list[CategorySpec]
    # aspects[category_id][aspect_name] -> AspectSpec
    aspects: dict[str, dict[str, AspectSpec]]
    # The "#INFO" rows that precede the header row in the Listings sheet
    # (e.g. "#INFO,Version=1.0,,Template=fx_category_template_EBAY_GB,...").
    # eBay's uploader identifies which template an upload matches by these —
    # a CSV missing them is rejected with "We couldn't identify your
    # template. Make sure you haven't changed the first line of the
    # template you downloaded." — so any output file must reproduce them
    # verbatim as its own first lines. See build.write_csv.
    info_rows: list[list[str]] = field(default_factory=list)

    def category_by_id(self, category_id: str) -> CategorySpec | None:
        return next((c for c in self.categories if c.category_id == str(category_id)), None)


def _sheet(wb, name) -> list[list]:
    return wb.get_sheet_by_name(name).to_python()


def _parse_categories(wb) -> list[CategorySpec]:
    rows = _sheet(wb, "Categories")
    specs = []
    for row in rows[1:]:  # skip header
        if not row or not str(row[0]).strip():
            continue
        name = str(row[0]).strip()
        cat_id = str(row[1]).strip().rstrip(".0") if str(row[1]).endswith(".0") else str(row[1]).strip()
        conditions = []
        for cell in row[2:]:
            text = str(cell).strip()
            if not text or "-" not in text:
                continue
            cid_str, label = text.split("-", 1)
            try:
                conditions.append((int(float(cid_str)), label.strip()))
            except ValueError:
                continue
        specs.append(CategorySpec(category_id=cat_id, category_name=name, conditions=conditions))
    return specs


def _clean_category_id(raw) -> str:
    s = str(raw).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _parse_aspects(wb) -> dict[str, dict[str, AspectSpec]]:
    rows = _sheet(wb, "Aspects")
    out: dict[str, dict[str, AspectSpec]] = {}
    for row in rows[1:]:  # skip header
        if not row or not str(row[0]).strip():
            continue
        cat_id = _clean_category_id(row[0])
        aspect_name = str(row[1]).strip()
        level = str(row[4]).strip()
        values = [str(v).strip() for v in row[5:] if str(v).strip()]
        out.setdefault(cat_id, {})[aspect_name] = AspectSpec(
            name=aspect_name, level=level, values=values or None
        )
    return out


def _parse_listing_sheet_preamble(wb) -> tuple[list[str], list[list[str]]]:
    """Returns (header_row, info_rows) — info_rows are every row above the
    header row, verbatim (padded/truncated to the header's width so the
    resulting CSV has a consistent column count throughout)."""
    rows = _sheet(wb, "Listings")
    for i, row in enumerate(rows[:10]):
        if row and str(row[0]).startswith("*Action"):
            header = [str(c) for c in row]
            width = len(header)

            def _norm(r):
                cells = [("" if c is None else str(c)) for c in r]
                return (cells + [""] * width)[:width]

            info_rows = [_norm(r) for r in rows[:i]]
            return header, info_rows
    raise ValueError("Could not find the header row in the template's 'Listings' sheet.")


def load_json_template(path: str | Path) -> EbayTemplate:
    """Loads a template produced by scripts/fetch_ebay_category_aspects.py —
    the API-driven alternative to downloading a real "Create listings in
    bulk" .xlsx from Seller Hub by hand. Same EbayTemplate shape either way,
    so build.py/content_generator.py/category_mapping.py don't need to know
    or care which source a given template came from."""
    import json

    data = json.loads(Path(path).read_text())

    categories = [
        CategorySpec(
            category_id=str(c["category_id"]),
            category_name=c["category_name"],
            conditions=[(int(cid), label) for cid, label in c.get("conditions", [])],
        )
        for c in data.get("categories", [])
    ]

    aspects: dict[str, dict[str, AspectSpec]] = {}
    for cat_id, cat_aspects in data.get("aspects", {}).items():
        aspects[str(cat_id)] = {
            name: AspectSpec(
                name=name,
                level=spec.get("level", "OPTIONAL"),
                values=spec.get("values"),
                multi=bool(spec.get("multi", False)),
                mode=spec.get("mode"),
            )
            for name, spec in cat_aspects.items()
        }

    return EbayTemplate(
        listing_headers=data.get("listing_headers", []),
        categories=categories,
        aspects=aspects,
        info_rows=data.get("info_rows", []),
    )


def load_template(path: str | Path) -> EbayTemplate:
    """Dispatches on file extension: a .json file is API-sourced (see
    load_json_template above); anything else is treated as a real eBay
    "Create listings in bulk" .xlsx export, parsed as before."""
    if str(path).lower().endswith(".json"):
        return load_json_template(path)

    wb = python_calamine.CalamineWorkbook.from_path(str(path))
    header, info_rows = _parse_listing_sheet_preamble(wb)
    return EbayTemplate(
        listing_headers=header,
        categories=_parse_categories(wb),
        aspects=_parse_aspects(wb),
        info_rows=info_rows,
    )


def supports_schedule_time(path: str | Path) -> bool:
    """Cheap check — just the header row, no full parse — for a UI to
    decide whether to offer the scheduling option before a full run."""
    try:
        wb = python_calamine.CalamineWorkbook.from_path(str(path))
        header, _ = _parse_listing_sheet_preamble(wb)
        return "Schedule Time" in header
    except Exception:  # noqa: BLE001 - any parse failure just means "no"
        return False


def supports_schedule_time_bytes(data: bytes) -> bool:
    """Same as supports_schedule_time, but for in-memory bytes (e.g. a
    Streamlit-uploaded file that hasn't been saved to disk yet)."""
    try:
        wb = python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(data))
        header, _ = _parse_listing_sheet_preamble(wb)
        return "Schedule Time" in header
    except Exception:  # noqa: BLE001 - any parse failure just means "no"
        return False
