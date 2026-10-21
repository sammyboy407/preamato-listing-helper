"""The most recent run's report, kept on disk for the app's "Last Batch" tab.

st.session_state.report holds everything a run produced, but only for the
browser tab that ran it: a fresh tab, or a second person opening the app,
sees nothing. So every run also writes the report here, overwriting the
previous one, since only the most recent batch is ever wanted.

It also carries the source data (Stock Data File and Orbitvu rows) of every
SKU that failed or was held back, so Quick Edit can correct it in place and
re-run just those SKUs through the normal pipeline — every check included —
without the original files being uploaded again.

Kept free of Streamlit so the tests can exercise it directly. app.py only
draws what batch_view() returns.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .data_loader import Product

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "last_batch_report.json"

# Per-SKU statuses, in the order the tab shows them. The first two need
# someone to act; a clean-looking run has been scrolled past before with
# refusals in it (07.09.26, see validation.Issue.blocking), so these lead.
FAILED = "failed"
HELD_BACK = "held_back"
UNCOVERED = "uncovered"
UNMATCHED = "unmatched"
FIXED = "fixed"
LISTED = "listed"

NEEDS_ACTION = (FAILED, HELD_BACK)
SKIPPED = (UNCOVERED, UNMATCHED)
_ORDER = {FAILED: 0, HELD_BACK: 1, UNCOVERED: 2, UNMATCHED: 3, FIXED: 4, LISTED: 5}


def _split_failure(text: str) -> tuple:
    """pipeline.run reports a failure as "SKU: reason"."""
    sku, sep, reason = str(text).partition(": ")
    return (sku, reason) if sep else (str(text), "")


def sku_statuses(report: dict) -> List[dict]:
    """One entry per SKU the run touched: {"sku", "status", "reason"},
    failures and held-back rows first, listed last, SKU order within each."""
    entries = []
    for text in report.get("failed") or []:
        sku, reason = _split_failure(text)
        entries.append({"sku": sku, "status": FAILED, "reason": reason})
    for sku, reasons in report.get("held_back") or []:
        entries.append({"sku": str(sku), "status": HELD_BACK, "reason": "; ".join(reasons)})
    for sku in report.get("uncovered") or []:
        entries.append({"sku": str(sku), "status": UNCOVERED,
                        "reason": "not covered by any template's categories"})
    for sku in report.get("unmatched") or []:
        entries.append({"sku": str(sku), "status": UNMATCHED,
                        "reason": "no matching row in any Stock Data File"})
    for fixed in report.get("fixed") or []:
        entries.append({"sku": str(fixed["sku"]), "status": FIXED,
                        "reason": f"fixed by Quick Edit, in {fixed['file']}"})
    for sku in report.get("listed") or []:
        entries.append({"sku": str(sku), "status": LISTED, "reason": ""})
    return sorted(entries, key=lambda e: (_ORDER[e["status"]], e["sku"]))


def save(report: dict, path: Path = DEFAULT_PATH) -> None:
    """Writes the report with its per-SKU statuses, replacing the last one.
    Written to a temporary file first, so a crash mid-write never leaves a
    half-written report for the tab to choke on."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report, skus=sku_statuses(report))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path = DEFAULT_PATH) -> Optional[dict]:
    """The last saved report, or None when there isn't a readable one."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def newest(*reports: Optional[dict]) -> Optional[dict]:
    """Whichever report ran most recently, by its ISO timestamp. Between
    two copies of the same batch, the one Quick Edit updated last wins."""
    present = [r for r in reports if r]
    if not present:
        return None
    return max(present, key=lambda r: (r.get("ran_at_iso") or "", r.get("updated_at") or ""))


def batch_view(report: dict) -> dict:
    """Everything the Last Batch tab shows, already grouped: needs_action
    (failed and held back), skipped (uncovered and unmatched), fixed (by
    Quick Edit) and listed."""
    entries = sku_statuses(report)
    return {
        "ran_at": report.get("ran_at") or "",
        "output_names": report.get("output_names") or "",
        "counts": {
            "processed": report.get("considered", 0),
            "listed": report.get("total_rows", 0),
            "held_back": len(report.get("held_back") or []),
            "failed": len(report.get("failed") or []),
            "uncovered": len(report.get("uncovered") or []),
            "unmatched": len(report.get("unmatched") or []),
            "fixed": len(report.get("fixed") or []),
        },
        "needs_action": [e for e in entries if e["status"] in NEEDS_ACTION],
        "skipped": [e for e in entries if e["status"] in SKIPPED],
        "fixed": [e for e in entries if e["status"] == FIXED],
        "listed": [e["sku"] for e in entries if e["status"] == LISTED],
    }


# --- Quick Edit ---------------------------------------------------------

def _json_safe(value):
    """A source cell as something JSON keeps exactly. Numbers stay numbers
    (an RRP has to come back as one), a NaN becomes empty, and anything
    else openpyxl can hand over (a date) becomes its text."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    return str(value)


def snapshot(product: Product) -> dict:
    """A product's source rows, ready to save. Columns with no header are
    dropped: nothing reads them, and JSON can't key on None."""
    return {
        "master": {str(k): _json_safe(v) for k, v in product.master.items() if k is not None},
        "measurements": {str(k): _json_safe(v)
                         for k, v in product.measurements.items() if k is not None},
    }


def product_from_snapshot(sku: str, snap: dict) -> Product:
    return Product(sku=sku, master=dict(snap["master"]), measurements=dict(snap["measurements"]))


def as_text(value) -> str:
    """How a source cell shows in the edit table."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def apply_edits(original: Dict[str, object], edited: Dict[str, str]) -> Tuple[dict, bool]:
    """original with the edit table's text applied. Returns (new row,
    whether anything actually changed).

    A field whose text is unchanged keeps its original value and type
    untouched. A changed number field ("695" -> "650") goes back as a
    number when the new text is one, since price and RRP are computed from
    it. Clearing a field sets it to None, the same as an empty cell."""
    result = dict(original)
    changed = False
    for name, text in edited.items():
        text = "" if text is None else str(text).strip()
        old = original.get(name)
        if text == as_text(old).strip():
            continue
        changed = True
        if text == "":
            result[name] = None
        elif isinstance(old, (int, float)) and not isinstance(old, bool):
            try:
                number = float(text)
                result[name] = int(number) if number.is_integer() and isinstance(old, int) else number
            except ValueError:
                result[name] = text
        else:
            result[name] = text
    return result, changed


def apply_fix(
    report: dict,
    edited: Dict[str, dict],
    listed: List[str],
    held_back: List[tuple],
    failed: List[str],
    uncovered: List[str],
    output_name: Optional[str],
    files: List[str],
    updated_at: str,
) -> dict:
    """The report after a Quick Edit re-run of the SKUs in `edited`
    ({sku: snapshot}). Each re-run SKU leaves its old failed/held-back entry
    and lands wherever this run put it: fixed (it reached the upload file),
    or back in failed / held back / uncovered with its NEW reason. Its saved
    source data becomes the edited version, so a second edit starts from
    the first one rather than from the original mistake. `files` is every
    file the re-run wrote that someone needs: the FIXED upload file and its
    Linnworks notes."""
    rerun = set(edited)
    out = dict(report)
    out["failed"] = [f for f in report.get("failed") or []
                     if _split_failure(f)[0] not in rerun] + list(failed)
    out["held_back"] = [(s, r) for s, r in report.get("held_back") or []
                        if str(s) not in rerun] + [(s, list(r)) for s, r in held_back]
    out["uncovered"] = [s for s in report.get("uncovered") or []
                        if str(s) not in rerun] + list(uncovered)
    out["fixed"] = [f for f in report.get("fixed") or [] if f["sku"] not in rerun] + [
        {"sku": sku, "file": output_name or ""} for sku in listed]
    out["fixed_files"] = list(report.get("fixed_files") or []) + [f for f in files if f]
    products = dict(report.get("products") or {})
    products.update(edited)
    out["products"] = products
    out["not_listed"] = (len(out["failed"]) + len(out["held_back"]) + len(out["uncovered"])
                         + len(report.get("unmatched") or []))
    out["updated_at"] = updated_at
    return out
