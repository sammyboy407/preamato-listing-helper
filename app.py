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
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import branding, config, ebay_template, pipeline  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "cache"
OUTPUT_DIR = APP_DIR / "output"

st.set_page_config(page_title="Preamato Listing Helper", page_icon="⬛", layout="centered")

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

/* Header ------------------------------------------------------------- */
.preamato-logo {{
    margin: 0.25rem 0 0.5rem 0;
}}
.preamato-logo img {{
    width: 208px;
    height: auto;
    display: block;
}}
.preamato-subtitle {{
    font-size: 0.72rem;
    font-weight: 500;
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
    letter-spacing: 0.14em;
    font-size: 0.78rem !important;
    color: var(--ink) !important;
    border-bottom: 1px solid var(--ink);
    padding-bottom: 0.55rem;
    margin-top: 2.25rem !important;
}}

/* Anything clickable turns matrix green under the cursor ---------------- */
.stButton > button, .stDownloadButton > button {{
    border-radius: 0 !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 700 !important;
    font-size: 0.78rem !important;
    border: 1.5px solid var(--ink) !important;
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

/* File uploaders */
[data-testid="stFileUploaderDropzone"] {{
    border-radius: 0 !important;
    border: 1px dashed var(--ink) !important;
    background-color: var(--canvas) !important;
    transition: border-color 90ms linear;
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
}}
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
[data-testid="stFileUploader"] label p {{
    font-weight: 500 !important;
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
    if rep["schedule_time"]:
        out += ["", f"Scheduled to go live at {rep['schedule_time']} GMT, not on upload."]
    if rep["held_back"]:
        out += ["", f"KEPT OUT of the upload file — eBay would refuse these "
                    f"({len(rep['held_back'])}). The upload file itself is safe as it is:"]
        out += [f"  - {sku}: {'; '.join(reasons)}" for sku, reasons in rep["held_back"]]
    if rep["failed"]:
        out += ["", f"FAILED, not in the file ({len(rep['failed'])}). "
                    f"Fix at source and re-run:"]
        out += [f"  - {f}" for f in rep["failed"]]
    if rep["uncovered"]:
        out += ["", f"NOT COVERED by any uploaded template ({len(rep['uncovered'])}):"]
        out += [f"  - {sku}" for sku in rep["uncovered"]]
    if not (rep["held_back"] or rep["failed"] or rep["uncovered"]):
        out += ["", "Nothing was held back, failed or skipped. Every product listed."]
    return "\n".join(out) + "\n"


def _render_report(rep: dict) -> None:
    """Renders the run report. Called from the persistent bottom section
    rather than from inside the run block, so it survives a re-run."""
    st.info(
        f"{rep['considered']} product(s) processed, {rep['total_rows']} listing(s) "
        f"written, {rep['not_listed']} not listed."
    )
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
            results, considered, uncovered, failed, held_back = pipeline.run(
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
            st.session_state.report = {
                "ran_at": datetime.now().strftime("%d.%m.%y %H:%M"),
                "considered": considered,
                "total_rows": total_rows,
                "not_listed": len(failed) + len(uncovered) + len(held_back),
                "held_back": list(held_back.reasons),
                "held_back_path": held_back.path,
                "failed": list(failed),
                "uncovered": list(uncovered),
                "schedule_time": schedule_time_str,
                "schedule_missed": schedule_missed,
                "output_names": ", ".join(Path(r["output_path"]).name for r in persisted),
                "report_name": f"{stem}_run report.txt",
            }

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
