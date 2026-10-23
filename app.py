"""Streamlit UI for the Preamato Listing Helper.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

import openpyxl
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import branding, config, data_loader, ebay_template, last_batch, pipeline  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "cache"
OUTPUT_DIR = APP_DIR / "output"

st.set_page_config(page_title="Preamato Listing Helper", page_icon="⬛", layout="wide")

BRAND_CSS = f"""
<style>
/* Brand guidelines, Version 02, August 2026. White is the canvas, black is
   the detail, 95/5 and deliberately not a 50/50 split. Matrix green is a
   hover state only, never a fill, so the ratio survives. */
:root {{
    --ink: {branding.BLACK};
    --canvas: {branding.WHITE};
    --rule: {branding.RULE};
    --muted: {branding.MUTED};
    --matrix: {branding.MATRIX};
    --matrix-ink: {branding.MATRIX_INK};
}}

/* No webfont is loaded on purpose. Helvetica Neue is installed on every Mac
   here, so the brand's first choice renders natively and the app does not
   wait on a font download to paint. */
html, body, [class*="css"], .stApp, button, input, textarea, select {{
    font-family: {branding.FONT_STACK} !important;
    -webkit-font-smoothing: antialiased;
}}

.stApp {{ background: var(--canvas); }}

/* Scale. Streamlit's defaults read small and thin against Helvetica Neue,
   so the root size goes up a step (every rem below follows it) and the
   page gets a readable measure rather than the narrow centred column or a
   full-width sprawl. Sammy, 23.10.26: "its all a bit small and thin". */
html {{ font-size: 17px; }}
.block-container {{
    max-width: 1100px !important;
    padding-top: 2.25rem !important;
    padding-bottom: 4rem !important;
}}
.stApp p, .stApp li {{ line-height: 1.55; }}
[data-testid="stCaptionContainer"], .stCaption, small {{
    color: #4A4A4A !important;
    font-size: 0.9rem !important;
    opacity: 1 !important;
}}

/* Header ------------------------------------------------------------- */
.preamato-logo {{
    margin: 0.25rem 0 0.5rem 0;
}}
.preamato-logo img {{
    width: 260px;
    height: auto;
    display: block;
}}
.preamato-subtitle {{
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.22em;
    margin-bottom: 1.6rem;
}}

/* Section headings. Uppercase and letter-spacing carry the cue, on a
   hairline rule rather than the 2px bar this used to have. */
h2, h3 {{
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-size: 1.05rem !important;
    color: var(--ink) !important;
    border-bottom: 2px solid var(--ink);
    padding-bottom: 0.6rem;
    margin-top: 2.5rem !important;
    margin-bottom: 0.9rem !important;
}}

/* Tabs: the Run / Last Batch switch is the main navigation, so it reads
   like it, with a black underline on the active one. */
[data-baseweb="tab-list"] {{ gap: 2rem; border-bottom: 1px solid var(--rule); }}
[data-baseweb="tab"] p {{
    font-size: 0.95rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}}
[data-baseweb="tab"]:hover p {{ color: var(--matrix-ink) !important; }}
[data-baseweb="tab-highlight"] {{ background-color: var(--ink) !important; height: 3px !important; }}

/* Anything clickable turns matrix green under the cursor ---------------- */
.stButton > button, .stDownloadButton > button {{
    border-radius: 0 !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
    min-height: 3rem;
    padding: 0.7rem 1.5rem !important;
    border: 2px solid var(--ink) !important;
    background-color: var(--ink) !important;
    color: var(--canvas) !important;
    transition: background-color 90ms linear, border-color 90ms linear, color 90ms linear;
}}
.stButton > button:hover, .stDownloadButton > button:hover,
.stButton > button:focus:hover, .stDownloadButton > button:focus:hover {{
    background-color: var(--matrix) !important;
    border-color: var(--matrix) !important;
    color: var(--ink) !important;
}}
.stButton > button:active, .stDownloadButton > button:active {{
    background-color: var(--matrix-ink) !important;
    border-color: var(--matrix-ink) !important;
    color: var(--canvas) !important;
}}

.stButton > button p, .stDownloadButton > button p {{
    font-size: inherit !important;
    font-weight: inherit !important;
}}
/* The one button that starts a batch is the biggest thing on the page. */
.stButton > button[kind="primary"] {{
    min-height: 3.6rem;
    font-size: 1.05rem !important;
    letter-spacing: 0.16em;
}}

/* Text inputs, number inputs and dropdowns: a solid black edge, square,
   and tall enough to hit easily. */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{
    border-radius: 0 !important;
    border: 1.5px solid var(--ink) !important;
    background-color: var(--canvas) !important;
    min-height: 2.9rem;
}}
[data-baseweb="input"] input, [data-baseweb="select"] div {{ font-size: 1rem !important; }}
[data-baseweb="input"] > div, [data-baseweb="input"] input,
[data-baseweb="base-input"] {{ background-color: var(--canvas) !important; }}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within {{
    border-color: var(--matrix-ink) !important;
}}

/* Run summaries and the Last Batch counts: the numbers are what someone
   is looking for, so they are big and bold. */
[data-testid="stMetricValue"] {{ font-size: 2.4rem !important; font-weight: 700 !important; }}
[data-testid="stMetricLabel"] p {{
    font-size: 0.8rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}}

/* Notices: square, like everything else, with readable type. */
[data-testid="stAlert"], [data-testid="stAlertContainer"] {{ border-radius: 0 !important; }}
[data-testid="stAlert"] p {{ font-size: 1rem !important; }}

/* File uploaders */
[data-testid="stFileUploaderDropzone"] {{
    border-radius: 0 !important;
    border: 1px dashed var(--ink) !important;
    background-color: var(--canvas) !important;
    transition: border-color 90ms linear;
}}
/* The dropzone's own small print stays at Streamlit's size, or the
   bigger root size squeezes it onto three lines and cuts it off. */
[data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stFileUploaderDropzoneInstructions"] small {{
    font-size: 0.8rem !important;
    line-height: 1.35;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    border-color: var(--matrix-ink) !important;
}}
[data-testid="stFileUploaderDropzone"] button {{
    border-radius: 0 !important;
    border: 1.5px solid var(--ink) !important;
    background-color: var(--canvas) !important;
    color: var(--ink) !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    transition: background-color 90ms linear, border-color 90ms linear;
}}
[data-testid="stFileUploaderDropzone"] button:hover {{
    background-color: var(--matrix) !important;
    border-color: var(--matrix) !important;
    color: var(--ink) !important;
}}

/* Every icon that is actually clickable: the uploader's cloud, the remove
   X on an uploaded file, the download arrow, the expander chevron, the
   little help question marks. Streamlit draws them all as inline SVG. */
[data-testid="stFileUploaderDropzone"]:hover svg,
[data-testid="stFileUploaderDeleteBtn"]:hover svg,
[data-testid="stExpander"] summary:hover svg,
[data-testid="stTooltipIcon"]:hover svg,
[data-testid="stHeaderActionElements"] button:hover svg,
button:hover svg, a:hover svg, summary:hover svg {{
    fill: var(--matrix-ink) !important;
    color: var(--matrix-ink) !important;
    stroke: var(--matrix-ink) !important;
}}
[data-testid="stFileUploaderDropzone"] button:hover svg {{
    fill: var(--ink) !important;
    color: var(--ink) !important;
    stroke: var(--ink) !important;
}}

/* Expanders, links, and the checkbox */
[data-testid="stExpander"] details {{
    border: 1px solid var(--rule) !important;
    border-radius: 0 !important;
}}
[data-testid="stExpander"] summary {{
    transition: color 90ms linear;
    padding-top: 0.8rem !important;
    padding-bottom: 0.8rem !important;
}}
[data-testid="stExpander"] summary p {{ font-weight: 700 !important; font-size: 1rem !important; }}
[data-testid="stExpander"] summary:hover {{
    color: var(--matrix-ink) !important;
}}
a, a:visited {{ color: var(--ink); text-decoration: underline; }}
a:hover {{ color: var(--matrix-ink) !important; }}

[data-testid="stCheckbox"]:hover [data-baseweb="checkbox"] div:first-child {{
    border-color: var(--matrix-ink) !important;
}}

/* Sliders. The handle is a control you drag, so it gets the same treatment. */
[data-testid="stSlider"] [role="slider"] {{
    transition: background-color 90ms linear, box-shadow 90ms linear;
}}
[data-testid="stSlider"]:hover [role="slider"] {{
    background-color: var(--matrix) !important;
    box-shadow: 0 0 0 1px var(--ink) !important;
}}

/* The progress bar is not clickable, so it stays black. */
.stProgress > div > div > div {{ background-color: var(--ink) !important; }}

[data-testid="stCheckbox"] label p, .stSlider label p,
.stNumberInput label p, .stTextInput label p,
[data-testid="stFileUploader"] label p, .stSelectbox label p,
.stDateInput label p, .stTimeInput label p {{
    font-weight: 700 !important;
    font-size: 1rem !important;
    letter-spacing: 0.01em;
}}
</style>
"""

st.markdown(BRAND_CSS, unsafe_allow_html=True)

st.markdown(
    f'<div class="preamato-logo"><img src="{branding.logo_data_uri()}" alt="PREAMATO"></div>',
    unsafe_allow_html=True,
)
st.markdown('<div class="preamato-subtitle">Listing Helper</div>', unsafe_allow_html=True)
st.caption(
    "Combines your Stock Data File, the Orbitvu file, and eBay's category list into "
    "a ready-to-upload eBay listing spreadsheet — with AI-written titles, descriptions, and "
    "item specifics."
)

if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.num_considered = None
if "report" not in st.session_state:
    # Everything a run has to say about what did NOT list. Kept in session
    # state for the same reason the results are: Streamlit re-runs the whole
    # script on any widget interaction, and a download click is a widget
    # interaction. Rendering these inside the `if run_clicked:` block meant
    # the first click of "Download" wiped every failure off the screen,
    # leaving the successful file and no record of the 4 SKUs missing from
    # it. 14.09.26, after a Brook St run where 4 of 18 did not list.
    st.session_state.report = None


def _report_text(rep: dict) -> str:
    """The same report as plain text, so it can be copied out, pasted into
    a message, or kept as a file next to the upload it belongs to."""
    out = [
        "Preamato Listing Helper — run report",
        f"Run at:     {rep['ran_at']}",
        f"Output:     {rep['output_names'] or '(no file written)'}",
        "",
        f"{rep['considered']} product(s) processed, "
        f"{rep['total_rows']} listing(s) written, {rep['not_listed']} not listed.",
    ]
    if rep.get("auction_days"):
        out += ["", f"Listed as {rep['auction_days']}-day auctions starting at "
                    f"GBP {config.AUCTION_START_PRICE}, no Best Offer."]
    if rep["schedule_time"]:
        out += ["", f"Scheduled to go live at {rep['schedule_time']} GMT, not on upload."]
    if rep["unmatched"]:
        out += ["", f"NO MASTER FILE ROW, never even considered ({len(rep['unmatched'])}). "
                    f"Check every Stock Data File for this batch was uploaded:"]
        out += [f"  - {sku}" for sku in rep["unmatched"]]
    if rep["held_back"]:
        out += ["", f"KEPT OUT of the upload file — eBay would refuse these "
                    f"({len(rep['held_back'])}). The upload file itself is safe as it is:"]
        out += [f"  - {sku}: {'; '.join(reasons)}" for sku, reasons in rep["held_back"]]
    if rep.get("linnworks_notes_path"):
        out += ["", f"{rep['linnworks_notes_count']} condition note(s) written for Linnworks: "
                    f"{Path(rep['linnworks_notes_path']).name}"]
    if rep["failed"]:
        out += ["", f"FAILED, not in the file ({len(rep['failed'])}). "
                    f"Fix at source and re-run:"]
        out += [f"  - {f}" for f in rep["failed"]]
    if rep["uncovered"]:
        out += ["", f"NOT COVERED by any uploaded template ({len(rep['uncovered'])}):"]
        out += [f"  - {sku}" for sku in rep["uncovered"]]
    if not (rep["unmatched"] or rep["held_back"] or rep["failed"] or rep["uncovered"]):
        out += ["", "Nothing was held back, failed or skipped. Every product listed."]
    return "\n".join(out) + "\n"


def _render_report(rep: dict) -> None:
    """Renders the run report. Called from the persistent bottom section
    rather than from inside the run block, so it survives a re-run."""
    st.info(
        f"{rep['considered']} product(s) processed, {rep['total_rows']} listing(s) "
        f"written, {rep['not_listed']} not listed."
    )
    if rep["unmatched"]:
        st.error(
            f"{len(rep['unmatched'])} SKU(s) were in your Orbitvu file but had NO MATCHING "
            f"ROW in any Stock Data File, so they were never even considered — this usually "
            f"means a Stock Data File for this batch wasn't uploaded:"
        )
        for sku in rep["unmatched"]:
            st.markdown(f"- **{sku}**")
    if rep["held_back"]:
        st.error(
            f"{len(rep['held_back'])} listing(s) would be refused by eBay and have been "
            f"KEPT OUT of the upload file. The file above is safe to upload as it is. "
            f"These need fixing at source and re-running:"
        )
        for sku, reasons in rep["held_back"]:
            st.markdown(f"- **{sku}** — {'; '.join(reasons)}")
        if rep["held_back_path"] and Path(rep["held_back_path"]).exists():
            with open(rep["held_back_path"], "rb") as fh:
                st.download_button(
                    f"Download {Path(rep['held_back_path']).name}",
                    fh,
                    file_name=Path(rep["held_back_path"]).name,
                    mime="text/csv",
                    use_container_width=True,
                    key="held_back_download",
                )
    if rep.get("linnworks_notes_path") and Path(rep["linnworks_notes_path"]).exists():
        st.success(
            f"{rep['linnworks_notes_count']} condition note(s) ready for Linnworks — "
            f"import this as an Extended Properties file (SKU, Property Name "
            f"\"ConditionNotes\", Property Value, Property Type)."
        )
        with open(rep["linnworks_notes_path"], "rb") as fh:
            st.download_button(
                f"Download {Path(rep['linnworks_notes_path']).name}",
                fh,
                file_name=Path(rep["linnworks_notes_path"]).name,
                mime="text/csv",
                use_container_width=True,
                key="linnworks_notes_download",
            )
    if rep["failed"]:
        st.error(
            f"{len(rep['failed'])} product(s) failed and are NOT in the file. "
            f"They need fixing and re-running:"
        )
        for f in rep["failed"]:
            st.markdown(f"- {f}")
    if rep["uncovered"]:
        st.warning(
            f"{len(rep['uncovered'])} product(s) aren't covered by any given template's "
            f"categories and were skipped: {', '.join(rep['uncovered'])}"
        )
    if rep["schedule_missed"]:
        st.warning(
            "You set a schedule time but it did not reach any row. Tell Claude — this "
            "should not happen since 09.09.26."
        )
    elif rep["schedule_time"]:
        st.info(f"Scheduled: these listings go live at {rep['schedule_time']} GMT, not on upload.")
    if rep.get("auction_days"):
        st.info(f"Auction: these listings run for {rep['auction_days']} days from "
                f"£{config.AUCTION_START_PRICE}, with no Best Offer.")

    text = _report_text(rep)
    with st.expander("Run report — copy or save this before you download", expanded=False):
        st.caption(
            "Everything above as plain text. Use the copy button in the corner of the "
            "box, or download it as a file to keep next to the upload."
        )
        st.code(text, language=None)
        st.download_button(
            "Download this report (.txt)",
            text.encode("utf-8"),
            file_name=rep["report_name"],
            mime="text/plain",
            use_container_width=True,
            key="run_report_download",
        )

_STATUS_LABELS = {
    last_batch.FAILED: ("❌", "FAILED"),
    last_batch.HELD_BACK: ("⛔", "HELD BACK"),
    last_batch.UNCOVERED: ("⚠️", "NOT COVERED"),
    last_batch.UNMATCHED: ("⚠️", "NO STOCK DATA ROW"),
}


def _field_editor(row: dict, key: str) -> dict:
    """One source row as an editable Field | Value table. Returns
    {field: text}; last_batch.apply_edits turns the text back into values."""
    frame = pd.DataFrame({"Field": list(row),
                          "Value": [last_batch.as_text(v) for v in row.values()]})
    edited = st.data_editor(frame, key=key, hide_index=True, use_container_width=True,
                            disabled=["Field"])
    return {field: ("" if pd.isna(value) else str(value))
            for field, value in zip(edited["Field"], edited["Value"])}


def _rerun_edited(rep: dict, edits: dict, api_key: str, workspace_id: str) -> None:
    """Re-runs the edited SKUs through pipeline.run, the same AI and every
    same check as a normal run, from their corrected data. Whatever passes
    goes into a new FIXED upload file; whatever doesn't stays in Needs
    action with its new reason."""
    if not api_key:
        st.error("Enter your Anthropic API key in the Run tab first.")
        return
    os.environ["ANTHROPIC_API_KEY"] = api_key
    if workspace_id.strip():
        os.environ["ANTHROPIC_WORKSPACE_ID"] = workspace_id.strip()
    else:
        os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)

    # The batch's own schedule, unless it has already passed: eBay refuses
    # a schedule time in the past, so those start immediately instead.
    schedule = rep.get("schedule_time")
    schedule_dropped = bool(
        schedule and datetime.strptime(schedule, config.SCHEDULE_TIME_FORMAT) <= datetime.now())
    if schedule_dropped:
        schedule = None

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / f"{Path(pipeline.default_output_filename()).stem}-FIXED.csv"
    products = [last_batch.product_from_snapshot(sku, snap) for sku, snap in edits.items()]
    progress_bar = st.progress(0.0)
    status_box = st.empty()

    def on_progress(msg: str, frac: float | None) -> None:
        status_box.text(msg)
        if frac is not None:
            progress_bar.progress(min(max(frac, 0.0), 1.0))

    try:
        results, _considered, uncovered, _unmatched, failed, held_back, linnworks_notes = pipeline.run(
            master_path=None,
            measurements_path=None,
            template_path=None,
            output_path=output_path,
            cache_dir=CACHE_DIR,
            # The cached answer was produced from the data being corrected.
            force_regenerate=True,
            schedule_time=schedule,
            price_percent=float(rep.get("price_percent") or config.START_PRICE_RATIO * 100),
            photo_qc=bool(rep.get("photo_qc", True)),
            on_progress=on_progress,
            products=products,
            auction_days=rep.get("auction_days"),
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"The re-run went wrong: {e}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())
        return

    listed = [str(row.get("Custom label (SKU)")) for r in results for row in r.rows]
    files = [r.output_path for r in results] + [linnworks_notes.path]
    updated = last_batch.apply_fix(
        rep, edits, listed, list(held_back.reasons), list(failed), list(uncovered),
        Path(results[0].output_path).name if results else None, files,
        datetime.now().isoformat())
    try:
        last_batch.save(updated)
    except OSError as e:
        st.warning(f"Couldn't save the fix for the Last Batch tab: {e}")
    if (st.session_state.report
            and st.session_state.report.get("ran_at_iso") == rep.get("ran_at_iso")):
        st.session_state.report = updated

    flash = f"Re-ran {len(edits)} SKU(s): {len(listed)} fixed"
    still = len(edits) - len(listed)
    if still:
        flash += f", {still} still need attention (new reasons below)"
    if schedule_dropped:
        flash += ". The batch's schedule time had passed, so these start immediately"
    st.session_state.quick_edit_flash = flash + "."
    st.rerun()


def _render_last_batch(api_key: str, workspace_id: str) -> None:
    """The Last Batch tab: the saved report, or this session's if newer.
    Anything that didn't reach a clean listing comes first, each SKU in its
    own red box rather than a line in a list. A clean-looking run has been
    scrolled past with refusals in it before (07.09.26). Quick Edit sits
    underneath, for correcting those SKUs and re-running just them."""
    rep = last_batch.newest(last_batch.load(), st.session_state.report)
    if not rep:
        st.info("No batch has been run yet")
        return
    view = last_batch.batch_view(rep)
    counts = view["counts"]

    flash = st.session_state.pop("quick_edit_flash", None)
    if flash:
        st.success(flash)

    st.markdown(f"**Ran at:** {view['ran_at']}  \n"
                f"**Output:** {view['output_names'] or '(no file written)'}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Processed", counts["processed"])
    c2.metric("Listed", counts["listed"] + counts["fixed"])
    c3.metric("Held back", counts["held_back"])
    c4.metric("Failed", counts["failed"])

    if view["needs_action"]:
        st.subheader(f"Needs action ({len(view['needs_action'])})")
        for e in view["needs_action"]:
            icon, label = _STATUS_LABELS[e["status"]]
            st.error(f"**{e['sku']}** · {label} — {e['reason']}", icon=icon)
    if view["skipped"]:
        st.subheader(f"Skipped ({len(view['skipped'])})")
        for e in view["skipped"]:
            icon, label = _STATUS_LABELS[e["status"]]
            st.warning(f"**{e['sku']}** · {label} — {e['reason']}", icon=icon)
    if not (view["needs_action"] or view["skipped"]):
        st.success("Nothing was held back, failed or skipped. Every product listed.")

    snapshots = rep.get("products") or {}
    editable = [e for e in view["needs_action"] if e["sku"] in snapshots]
    if editable:
        st.subheader("Quick edit")
        st.caption(
            "Correct a SKU's data below, then re-run it. Only edited SKUs are re-run, "
            "through the same AI and every same check as a normal run, and anything that "
            "now passes goes into a new FIXED upload file. Uses the API key from the Run tab."
        )
        # Keyed on when the report last changed, so the tables start fresh
        # from the corrected data after every re-run.
        stamp = rep.get("updated_at") or rep.get("ran_at_iso") or ""
        edits = {}
        for e in editable:
            sku, snap = e["sku"], snapshots[e["sku"]]
            _icon, label = _STATUS_LABELS[e["status"]]
            with st.expander(f"Edit {sku} ({label.lower()})"):
                st.caption(e["reason"])
                st.markdown("**Orbitvu file** (size, condition notes, photos, measurements)")
                meas_text = _field_editor(snap["measurements"], f"qe_meas_{sku}_{stamp}")
                st.markdown("**Stock Data File** (RRP, brand, category, colour)")
                master_text = _field_editor(snap["master"], f"qe_master_{sku}_{stamp}")
            measurements, meas_changed = last_batch.apply_edits(snap["measurements"], meas_text)
            master, master_changed = last_batch.apply_edits(snap["master"], master_text)
            if meas_changed or master_changed:
                edits[sku] = {"master": master, "measurements": measurements}
        button_label = (f"Re-run {len(edits)} edited SKU(s)" if edits
                        else "Edit a SKU above to re-run it")
        if st.button(button_label, disabled=not edits, use_container_width=True,
                     key="quick_edit_rerun"):
            _rerun_edited(rep, edits, api_key, workspace_id)
    elif view["needs_action"]:
        st.caption("Quick Edit isn't available for this batch: it ran before Quick Edit "
                   "existed, or its source data couldn't be saved.")

    if view["fixed"]:
        st.subheader(f"Fixed by Quick Edit ({len(view['fixed'])})")
        for e in view["fixed"]:
            st.success(f"**{e['sku']}** · {e['reason']}", icon="✅")
        for path in rep.get("fixed_files") or []:
            if Path(path).exists():
                with open(path, "rb") as fh:
                    st.download_button(f"Download {Path(path).name}", fh,
                                       file_name=Path(path).name, mime="text/csv",
                                       use_container_width=True, key=f"fixed_{path}")

    with st.expander(f"Listed ({len(view['listed'])})", expanded=False):
        if view["listed"]:
            st.markdown("\n".join(f"- {sku}" for sku in view["listed"]))
        else:
            st.caption("Nothing reached the upload file.")


run_tab, last_batch_tab = st.tabs(["Run", "Last Batch"])

with run_tab:
    st.subheader("1. Your Anthropic API key")
    api_key = st.text_input(
        "API key",
        type="password",
        value=os.environ.get("ANTHROPIC_API_KEY", ""),
        help="Get one at console.anthropic.com. Used only for this session — never saved to disk.",
        label_visibility="collapsed",
    )
    with st.expander("Workspace ID (only needed for some keys)"):
        st.caption(
            "Most keys don't need this — leave it blank. It's only required if your API key is "
            "\"identity-linked\" (a Personal or Service Account key) AND set to work across "
            "multiple workspaces rather than restricted to just one. If you see an error mentioning "
            "\"anthropic-workspace-id is required\", either paste that workspace's ID here (from "
            "console.anthropic.com > Settings > Workspaces), or simpler: go create a new key there "
            "restricted to a single workspace instead, which needs no ID at all."
        )
        workspace_id = st.text_input(
            "Workspace ID",
            value=os.environ.get("ANTHROPIC_WORKSPACE_ID", ""),
            placeholder="wrkspc_...",
            label_visibility="collapsed",
        )

    st.subheader("2. Upload your files")
    col1, col2 = st.columns(2)
    with col1:
        master_files = st.file_uploader(
            "Stock Data File (.xlsx)", type=["xlsx"], accept_multiple_files=True,
            help="Multiple files are merged — e.g. separate exports per supplier batch.",
        )
        template_files = st.file_uploader(
            "eBay category listing template(s) (.xlsx) — optional override",
            type=["xlsx"],
            accept_multiple_files=True,
            help=(
                "Optional. By default every run already covers the full catalog automatically "
                "(menswear/womenswear clothing, shoes and accessories, jewellery & watches, "
                "homeware, and kidswear — see data/templates/, generated straight from eBay's "
                "own API). Only upload a template here if you specifically want to override one "
                "or more of those departments with a real .xlsx downloaded by hand from Seller "
                "Hub > Create listings in bulk for a particular batch — an uploaded template "
                "takes priority over the matching department default where its categories "
                "overlap. One output file is produced per template (department default or "
                "manually uploaded) that ends up with matched products."
            ),
        )
        if not template_files:
            st.caption("No manual template uploaded — using the full department template set (menswear/womenswear clothing, shoes, accessories, jewellery & watches, homeware, kidswear).")
    with col2:
        measurements_files = st.file_uploader(
            "Orbitvu file (.csv)", type=["csv"], accept_multiple_files=True,
            help="Multiple files are merged — e.g. separate exports per photography batch.",
        )

    st.subheader("3. Options")
    opt_col1, opt_col2, opt_col3 = st.columns(3)
    with opt_col1:
        limit_enabled = st.checkbox("Test run only", value=True, help="Process just a few products first, to check quality before running the full batch.")
    with opt_col2:
        limit = st.number_input("Products to process", min_value=1, value=5, step=1, disabled=not limit_enabled)
    with opt_col3:
        workers = st.slider("Speed (parallel AI calls)", min_value=1, max_value=10, value=4)

    auction_enabled = st.checkbox(
        "List as auction (instead of fixed price)",
        value=False,
        help=f"Every listing in this batch goes up as an auction starting at "
             f"£{config.AUCTION_START_PRICE}, with no Buy It Now or Best Offer, on the "
             f"\"{config.AUCTION_PAYMENT_PROFILE}\" payment policy. eBay only auctions one "
             f"item, so a product with a quantity above 1 is held back.",
    )
    auction_days = None
    price_percent = int(config.START_PRICE_RATIO * 100)
    if auction_enabled:
        auction_col, _ = st.columns([1, 2])
        with auction_col:
            auction_days = st.selectbox(
                "Auction length (days)", options=list(config.AUCTION_DURATIONS),
                index=list(config.AUCTION_DURATIONS).index(config.AUCTION_DEFAULT_DAYS),
            )
        st.caption(f"Bidding starts at £{config.AUCTION_START_PRICE} on every listing, "
                   f"payment policy \"{config.AUCTION_PAYMENT_PROFILE}\".")
    else:
        price_percent = st.slider(
            "Selling price (% of RRP)",
            min_value=5, max_value=100, value=int(config.START_PRICE_RATIO * 100), step=5,
            help="Applied to every listing in the batch — start price is this % of the item's RRP, "
                 "rounded to the nearest £5.",
        )

    force_regenerate = st.checkbox(
        "Regenerate everything (ignore cache)",
        value=False,
        help="By default, products already processed in a previous run are reused for free. Check this to force fresh AI output for every product.",
    )

    photo_qc = st.checkbox(
        "QC every listing against its photograph (slower)",
        value=True,
        help="After the listings are written, checks each one against its own main "
             "photo and holds back anything the picture clearly contradicts. Adds "
             "roughly ten to twenty minutes on a full batch. The free checks — an "
             "aspect that disagrees with the title or the composition — always run.",
    )

    schedule_time_str = None
    schedule_invalid = False
    schedule_enabled = st.checkbox(
        "Schedule listings for a future time (instead of starting immediately)",
        value=False,
        help="Listings go live at this time instead of immediately. GMT, on the "
             "hour, and eBay allows up to three weeks ahead.",
    )
    if schedule_enabled:
        default_dt = datetime.now() + timedelta(days=1)
        sched_col1, sched_col2 = st.columns(2)
        with sched_col1:
            schedule_date = st.date_input("Start date", value=default_dt.date(), min_value=datetime.now().date())
        with sched_col2:
            schedule_time_val = st.time_input("Start time (GMT)", value=dt_time(default_dt.hour, 0))
        chosen_dt = datetime.combine(schedule_date, schedule_time_val)
        if chosen_dt <= datetime.now():
            schedule_invalid = True
        else:
            schedule_time_str = chosen_dt.strftime(config.SCHEDULE_TIME_FORMAT)

        # No "your template has no Schedule Time column" caption any more. The
        # column is written whenever a time is set, whatever the template's own
        # header row says — see build.build_row. 09.09.26.

    run_clicked = st.button("Generate eBay upload file", type="primary", use_container_width=True)

    st.divider()

    if run_clicked:
        problems = []
        if not api_key:
            problems.append("Enter your Anthropic API key.")
        if not master_files:
            problems.append("Upload at least one Stock Data File.")
        if not measurements_files:
            problems.append("Upload at least one Orbitvu file.")
        if schedule_enabled and schedule_invalid:
            problems.append("Scheduled start must be in the future.")

        if problems:
            for p in problems:
                st.error(p)
        else:
            os.environ["ANTHROPIC_API_KEY"] = api_key
            if workspace_id.strip():
                os.environ["ANTHROPIC_WORKSPACE_ID"] = workspace_id.strip()
            else:
                os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)

            tmp_dir = Path(tempfile.mkdtemp(prefix="plh_"))

            def _save_all(files):
                paths = []
                for f in files:
                    p = tmp_dir / f.name
                    p.write_bytes(f.getvalue())
                    paths.append(p)
                return paths

            master_paths = _save_all(master_files)
            measurements_paths = _save_all(measurements_files)
            template_paths = _save_all(template_files) if template_files else None

            OUTPUT_DIR.mkdir(exist_ok=True)
            output_path = OUTPUT_DIR / pipeline.default_output_filename()

            progress_bar = st.progress(0.0)
            status_box = st.empty()
            log_lines: list[str] = []

            def on_progress(msg: str, frac: float | None) -> None:
                log_lines.append(msg)
                status_box.text("\n".join(log_lines[-8:]))
                if frac is not None:
                    progress_bar.progress(min(max(frac, 0.0), 1.0))

            try:
                results, considered, uncovered, unmatched, failed, held_back, linnworks_notes = pipeline.run(
                    master_path=master_paths,
                    measurements_path=measurements_paths,
                    template_path=template_paths,
                    output_path=output_path,
                    cache_dir=CACHE_DIR,
                    limit=int(limit) if limit_enabled else None,
                    workers=int(workers),
                    force_regenerate=force_regenerate,
                    schedule_time=schedule_time_str,
                    price_percent=float(price_percent),
                    photo_qc=bool(photo_qc),
                    on_progress=on_progress,
                    auction_days=auction_days,
                )
                persisted = []
                for r in results:
                    persisted.append({
                        "output_path": r.output_path,
                        "rows": r.rows,
                        "category_names": r.category_names,
                    })
                st.session_state.results = persisted
                st.session_state.num_considered = considered
                total_rows = sum(len(r["rows"]) for r in persisted)

                schedule_missed = bool(
                    schedule_time_str
                    and not any(row.get("Schedule Time") for r in persisted for row in r["rows"])
                )
                stem = Path(output_path).stem
                ran_at = datetime.now()
                st.session_state.report = {
                    "ran_at": ran_at.strftime("%d.%m.%y %H:%M"),
                    # Full precision, so the Last Batch tab can tell which of
                    # this session's report and the saved one is newer.
                    "ran_at_iso": ran_at.isoformat(),
                    "listed": [str(row.get("Custom label (SKU)"))
                               for r in persisted for row in r["rows"]],
                    "considered": considered,
                    "total_rows": total_rows,
                    "not_listed": len(failed) + len(uncovered) + len(unmatched) + len(held_back),
                    "held_back": list(held_back.reasons),
                    "held_back_path": held_back.path,
                    "linnworks_notes_count": len(linnworks_notes),
                    "linnworks_notes_path": linnworks_notes.path,
                    "failed": list(failed),
                    "uncovered": list(uncovered),
                    "unmatched": list(unmatched),
                    "schedule_time": schedule_time_str,
                    "schedule_missed": schedule_missed,
                    "output_names": ", ".join(Path(r["output_path"]).name for r in persisted),
                    "report_name": f"{stem}_run report.txt",
                    # A Quick Edit re-run prices and checks the same way.
                    "price_percent": float(price_percent),
                    "photo_qc": bool(photo_qc),
                    "auction_days": auction_days,
                }
                # The source rows of every SKU that failed or was held back,
                # for Quick Edit in the Last Batch tab. The uploaded files
                # are deleted when this block ends, so it's now or never.
                problem_skus = ({str(sku) for sku, _ in held_back.reasons}
                                | {str(f).partition(": ")[0] for f in failed})
                if problem_skus:
                    try:
                        loaded, _ = data_loader.load_products(master_paths, measurements_paths)
                        st.session_state.report["products"] = {
                            p.sku: last_batch.snapshot(p) for p in loaded if p.sku in problem_skus}
                    except Exception as e:  # noqa: BLE001 - never costs the run itself
                        st.warning(f"Quick Edit won't be available for this batch: {e}")
                # For the Last Batch tab. A failed save costs that tab its data,
                # never the run itself, so it's reported and carried on from.
                try:
                    last_batch.save(st.session_state.report)
                except OSError as e:
                    st.warning(f"Couldn't save this run for the Last Batch tab: {e}")

                if persisted:
                    st.success(f"Done — generated {total_rows} listing(s) across {len(persisted)} output file(s).")
                else:
                    st.info("None of these products fall into a category covered by the template(s) you uploaded — no output file produced.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Something went wrong: {e}")
                with st.expander("Technical details"):
                    st.code(traceback.format_exc())
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    if st.session_state.results or st.session_state.report:
        st.subheader("Results")
        preview_cols = [
            "Custom label (SKU)", "Title", "Description", "Category ID", "Category name",
            "Start price", "Condition ID", "C:Brand",
        ]
        for r in (st.session_state.results or []):
            if not Path(r["output_path"]).exists():
                continue
            categories_label = ", ".join(name.rsplit("/", 1)[-1] for name in r["category_names"])
            st.markdown(f"**{Path(r['output_path']).name}** — {len(r['rows'])} listing(s): {categories_label}")
            with open(r["output_path"], "rb") as f:
                st.download_button(
                    f"Download {Path(r['output_path']).name}",
                    f,
                    file_name=Path(r["output_path"]).name,
                    mime="text/csv",
                    use_container_width=True,
                    key=r["output_path"],
                )
            preview_data = [
                {c: row.get(c) for c in preview_cols if c in row} for row in r["rows"]
            ]
            st.dataframe(preview_data, use_container_width=True, hide_index=True)
            st.markdown("")

        # After the files, so the download buttons are the first thing reached,
        # but on the same re-run-proof footing as them.
        if st.session_state.report:
            _render_report(st.session_state.report)

with last_batch_tab:
    _render_last_batch(api_key, workspace_id)
