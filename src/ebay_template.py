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


def load_template(path: str | Path) -> EbayTemplate:
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
