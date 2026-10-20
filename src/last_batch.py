"""The most recent run's report, kept on disk for the app's "Last Batch" tab.

st.session_state.report holds everything a run produced, but only for the
browser tab that ran it: a fresh tab, or a second person opening the app,
sees nothing. So every run also writes the report here, overwriting the
previous one, since only the most recent batch is ever wanted.

Kept free of Streamlit so the tests can exercise it directly. app.py only
draws what batch_view() returns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "last_batch_report.json"

# Per-SKU statuses, in the order the tab shows them. The first two need
# someone to act; a clean-looking run has been scrolled past before with
# refusals in it (07.09.26, see validation.Issue.blocking), so these lead.
FAILED = "failed"
HELD_BACK = "held_back"
UNCOVERED = "uncovered"
UNMATCHED = "unmatched"
LISTED = "listed"

NEEDS_ACTION = (FAILED, HELD_BACK)
SKIPPED = (UNCOVERED, UNMATCHED)
_ORDER = {FAILED: 0, HELD_BACK: 1, UNCOVERED: 2, UNMATCHED: 3, LISTED: 4}


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
    """Whichever report ran most recently, by its ISO timestamp."""
    present = [r for r in reports if r]
    if not present:
        return None
    return max(present, key=lambda r: r.get("ran_at_iso") or "")


def batch_view(report: dict) -> dict:
    """Everything the Last Batch tab shows, already grouped: needs_action
    (failed and held back), skipped (uncovered and unmatched) and listed."""
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
        },
        "needs_action": [e for e in entries if e["status"] in NEEDS_ACTION],
        "skipped": [e for e in entries if e["status"] in SKIPPED],
        "listed": [e["sku"] for e in entries if e["status"] == LISTED],
    }
