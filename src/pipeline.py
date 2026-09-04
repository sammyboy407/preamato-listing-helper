"""Reusable pipeline orchestration, shared by the CLI (main.py) and the
Streamlit UI (app.py). Reports progress via an optional callback instead of
printing directly, so a UI can render it live.

Supports multiple Master Files, multiple Measurements files (merged), and
multiple eBay templates (each template only covers the categories its
seller selected when downloading it — giving several lets one run cover
your whole catalog across every category you care about). Each product is
assigned to the first given template whose categories cover it; one output
file is written per template that ends up with at least one matched row,
since each template's Listings sheet has its own column layout and its own
Categories/Aspects/BusinessPolicy sheets to preserve.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from . import (brand_blurb, build, builtin_catalog, category_mapping, config,
               content_generator, data_loader, ebay_template, validation)
from . import aspect_matching

ProgressFn = Callable[[str, float | None], None]

# The API-generated department templates (see
# scripts/fetch_ebay_category_aspects.py) — one per department, covering
# menswear/womenswear clothing, shoes and accessories, jewellery & watches,
# homeware, and kidswear, sourced straight from eBay's own Taxonomy and
# Metadata APIs rather than a manually downloaded Seller Hub .xlsx. Used
# automatically below when no template is manually uploaded/passed, since
# this now covers the account's real catalog far more completely than
# builtin_catalog.py's single bundled-export fallback did. That fallback is
# kept as the final safety net for a fresh checkout before anyone has run
# the fetch script (data/templates/ won't exist yet).
DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


def _default_department_templates() -> list[Path]:
    if not DEFAULT_TEMPLATES_DIR.is_dir():
        return []
    return sorted(DEFAULT_TEMPLATES_DIR.glob("*.json"))


def _noop(msg: str, frac: float | None = None) -> None:
    pass


def default_output_filename(today: date | None = None) -> str:
    """e.g. 'Ebay-Upload-25.08.26.csv'."""
    return f"Ebay-Upload-{(today or date.today()):%d.%m.%y}.csv"


def _per_template_output_path(base_output_path: str | Path, template_path: str | Path, index: int, total: int) -> Path:
    base = Path(base_output_path)
    if total == 1:
        return base
    template_stem = Path(template_path).stem
    # Keep it filesystem-friendly and short-ish. The index is always
    # included (not just appended on collision) so two templates whose
    # names happen to share their first 40 sanitized characters — e.g.
    # two same-day eBay template exports differing only near the end of
    # their timestamp — can never produce the same output path.
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in template_stem)[:40]
    return base.with_name(f"{base.stem}__{index + 1}_{safe_stem}{base.suffix}")


@dataclass
class TemplateResult:
    template_path: str
    output_path: str
    category_names: list[str]
    rows: list[dict] = field(default_factory=list)


def run(
    master_path: str | Path | list,
    measurements_path: str | Path | list,
    template_path: str | Path | list | None,
    output_path: str | Path,
    cache_dir: str | Path,
    limit: int | None = None,
    workers: int = 4,
    force_regenerate: bool = False,
    schedule_time: str | None = None,
    price_percent: float = config.START_PRICE_RATIO * 100,
    combine_output: bool = True,
    on_progress: ProgressFn = _noop,
) -> tuple[list[TemplateResult], int, list[str], list[str]]:
    """Runs the full pipeline. Returns (template_results, num_products_considered,
    uncovered_skus, failed) — uncovered_skus lists products whose (Category,
    SubCat2, Gender) doesn't match any category in ANY of the given templates,
    and failed lists "SKU: reason" for every product that was dropped during
    generation (an unresolvable Required size, an API error that survived its
    retries).

    Both are returned rather than only logged: on a 6-row test run a skipped
    SKU is obvious, but on a 295-row batch the log line scrolls away and the
    run ends on a green "generated 220 listings" with no hint that 75 are
    missing or which ones. The caller is expected to show them.

    template_path is optional — pass None/[] to skip uploading a template
    manually and fall back automatically to data/templates/*.json, the
    API-generated department templates (menswear/womenswear clothing, shoes
    and accessories, jewellery & watches, homeware, kidswear — see
    _default_department_templates above and
    scripts/fetch_ebay_category_aspects.py), which by now cover the real
    catalog far more completely than a single manually uploaded .xlsx ever
    did. If that directory doesn't exist yet (a fresh checkout before
    anyone has run the fetch script), falls back further to the built-in
    category catalog (see builtin_catalog.py), built from a bundled export
    of this account's own real listings. A manually uploaded template still
    takes priority and is still stricter/more accurate (real Required/
    Preferred/Optional flags and closed value lists) where it covers a
    category, so pass one when you specifically want to override the
    department defaults for a category.

    combine_output controls whether all matched rows land in one CSV or one
    per template. Since running with no manual upload now spans up to 9
    department templates, a real batch used to always come back as up to 9
    separate files even though every department template shares the exact
    same base column layout (FIXED_LISTING_HEADERS_PREFIX) — needlessly
    inconvenient for a real bulk upload, where one file covering every
    category is normal (a real eBay bulk template already mixes many
    categories' rows in one sheet). Defaults to True; set False to get the
    old one-file-per-template behaviour back."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    template_paths = template_path if isinstance(template_path, list) else ([template_path] if template_path else [])
    used_default_departments = False
    if not template_paths:
        default_templates = _default_department_templates()
        if default_templates:
            template_paths = default_templates
            used_default_departments = True

    on_progress("Loading source files...", 0.0)
    products = data_loader.load_products(master_path, measurements_path)
    if limit:
        products = products[:limit]
    if not products:
        raise ValueError(
            "No products matched between the master file(s) and the measurements file(s). "
            "Check that SKUs line up (measurements file's 'Name' column vs master file's 'SKU' column)."
        )
    on_progress(f"{len(products)} products to process.", 0.05)

    if template_paths:
        label = "department templates" if used_default_departments else "eBay template(s)"
        on_progress(f"Loading {len(template_paths)} {label}...", 0.07)
        templates = [ebay_template.load_template(p) for p in template_paths]
        for p, t in zip(template_paths, templates):
            on_progress(f"  {Path(p).name}: covers {len(t.categories)} categories.", None)
    else:
        on_progress("No template uploaded — using the built-in category catalog...", 0.07)
        templates = [builtin_catalog.build_template()]
        template_paths = ["Built-in catalog"]
        on_progress(f"  Built-in catalog: covers {len(templates[0].categories)} categories.", None)

    cat_caches = []
    for idx, (tpath, template) in enumerate(zip(template_paths, templates)):
        on_progress(
            f"Mapping products against template "
            f"{idx + 1}/{len(templates)} ({Path(tpath).name})...",
            0.1 + 0.06 * idx / max(len(templates), 1),
        )
        cat_cache_path = Path(cache_dir) / f"category_mapping_{idx}.json"
        cat_caches.append(category_mapping.build_mapping(products, template, cat_cache_path))
    on_progress("Category mapping done.", 0.18)

    # Assign each product to the first template (in the given order) whose
    # categories cover it.
    assignments: list[tuple] = []  # (product, template_idx, category_entry)
    uncovered_skus: list[str] = []
    for p in products:
        match = None
        for idx, (template, cat_cache) in enumerate(zip(templates, cat_caches)):
            entry = category_mapping.lookup(cat_cache, p, template)
            if entry:
                match = (idx, entry)
                break
        if match:
            assignments.append((p, match[0], match[1]))
        else:
            uncovered_skus.append(p.sku)

    if uncovered_skus:
        on_progress(
            f"{len(uncovered_skus)} product(s) aren't covered by any given template's "
            f"categories and will be skipped: {uncovered_skus}", None
        )
    if not assignments:
        # Not an error — the templates given just don't cover any of these
        # products' categories. Report it plainly and produce no output
        # files, rather than forcing a bad fit or crashing.
        on_progress(
            "None of the matched products fall into a category covered by any given "
            "template — no output file produced.", 1.0
        )
        return [], 0, uncovered_skus, []

    brands = {str(p.m("Brand")) for p, _, _ in assignments if p.m("Brand")}
    on_progress(f"Building brand descriptions for {len(brands)} brand(s)...", 0.2)
    blurb_cache = brand_blurb.build_blurbs(brands, cache_dir)
    on_progress("Brand descriptions done.", 0.25)

    on_progress(f"Generating AI content for {len(assignments)} product(s)...", 0.25)
    ai_results: dict[str, dict] = {}
    errors: list[str] = []

    def _generate(p, template_idx, category_entry):
        template = templates[template_idx]
        category = template.category_by_id(category_entry["category_id"])
        return p.sku, content_generator.generate_for_product(
            p, category, template, cache_dir, force=force_regenerate,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate, p, idx, entry): p
            for p, idx, entry in assignments
        }
        done = 0
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                sku, result = fut.result()
                ai_results[sku] = result
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p.sku}: {e}")
            done += 1
            frac = 0.25 + 0.65 * (done / len(assignments))
            on_progress(f"[{done}/{len(assignments)}] {p.sku} done", frac)

    if errors:
        on_progress(f"{len(errors)} product(s) failed and will be skipped: {errors}", None)

    on_progress("Assembling output rows...", 0.92)
    results_by_template: dict[int, TemplateResult] = {}
    issues: list[validation.Issue] = []
    for p, idx, entry in assignments:
        if p.sku not in ai_results:
            continue
        template = templates[idx]
        category = template.category_by_id(entry["category_id"])
        row = build.build_row(p, ai_results[p.sku], category, template, blurb_cache, schedule_time, price_percent)

        # Deterministic checks, run per row before anything is written (see
        # validation.py). Free — no AI call, no measurable time — and they
        # cover the failures that have actually reached eBay or a buyer:
        # an empty REQUIRED item specific, a title that lost its size, a
        # listing that contradicts itself, a mistyped measurement.
        specifics = ai_results[p.sku].get("item_specifics", {})
        issues.extend(validation.check_row(
            p, row, category, template,
            size_for_display=aspect_matching.size_display(
                p.measurements.get("Size"),
                uk_shoe=specifics.get("C:UK Shoe Size"),
                eu_shoe=specifics.get("C:EU Shoe Size"),
                clothing_size=specifics.get("C:Size"),
            ),
        ))

        if idx not in results_by_template:
            results_by_template[idx] = TemplateResult(
                template_path=str(template_paths[idx]),
                output_path=str(_per_template_output_path(output_path, template_paths[idx], idx, len(template_paths))),
                category_names=[],
            )
        tr = results_by_template[idx]
        tr.rows.append(row)
        if category.category_name not in tr.category_names:
            tr.category_names.append(category.category_name)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if combine_output and len(results_by_template) > 1:
        # One CSV covering every matched row across every template that
        # contributed any, rather than one file per template. Safe to merge
        # even across templates with different listing_headers (a manual
        # .xlsx upload could in principle differ from the department
        # defaults) since output_headers takes the union of every row's own
        # keys, not just the base template's — no column, and so no C:
        # value, is ever lost by merging. #INFO preamble rows are taken from
        # whichever template contributed the first row; that's only really
        # meaningful for a real downloaded .xlsx (the JSON department
        # templates all share the exact same FIXED_INFO_ROWS anyway).
        ordered_idxs = sorted(results_by_template)
        combined_rows: list[dict] = []
        combined_category_names: list[str] = []
        combined_listing_headers: list[str] = []
        for idx in ordered_idxs:
            tr = results_by_template[idx]
            combined_rows.extend(tr.rows)
            for name in tr.category_names:
                if name not in combined_category_names:
                    combined_category_names.append(name)
            for h in templates[idx].listing_headers:
                if h not in combined_listing_headers:
                    combined_listing_headers.append(h)
        combined_template = ebay_template.EbayTemplate(
            listing_headers=combined_listing_headers,
            categories=[],
            aspects={},
            info_rows=templates[ordered_idxs[0]].info_rows,
        )
        combined_path = Path(output_path)
        build.write_csv(combined_rows, combined_template, combined_path)
        on_progress(
            f"Wrote {len(combined_rows)} rows (from {len(ordered_idxs)} department templates) "
            f"to one file: {combined_path}",
            None,
        )
        template_results = [
            TemplateResult(
                template_path="combined",
                output_path=str(combined_path),
                category_names=combined_category_names,
                rows=combined_rows,
            )
        ]
    else:
        template_results = []
        for idx in sorted(results_by_template):
            tr = results_by_template[idx]
            build.write_csv(tr.rows, templates[idx], tr.output_path)
            on_progress(f"Wrote {len(tr.rows)} rows to {tr.output_path}", None)
            template_results.append(tr)

    # The checks report goes next to the CSV as well as into the run log, so
    # it can be read after the fact rather than scrolled back to.
    # The report opens with a reconciliation the numbers have to satisfy —
    # products in, rows out, and every one that didn't make it, named. That
    # is the first thing to read before uploading a batch.
    rows_out = sum(len(tr.rows) for tr in template_results)
    header = [
        "COUNTS",
        f"  {len(products)} product(s) read from the Pictures & Measurements file(s)",
        f"  {len(assignments)} matched a category and were processed",
        f"  {rows_out} listing(s) written to the CSV",
    ]
    if uncovered_skus:
        header.append(f"  {len(uncovered_skus)} skipped: no template covers their category")
    if errors:
        header.append(f"  {len(errors)} skipped: failed during generation (listed below)")
    if not uncovered_skus and not errors and rows_out == len(products):
        header.append("  Nothing was dropped.")
    header.append("")

    if errors:
        header.append(f"SKIPPED DURING GENERATION ({len(errors)}) — these are NOT in the CSV:")
        header.extend(f"  {e}" for e in errors)
        header.append("")
    if uncovered_skus:
        header.append(f"NO MATCHING CATEGORY ({len(uncovered_skus)}) — these are NOT in the CSV:")
        header.extend(f"  {sku}" for sku in uncovered_skus)
        header.append("")

    report = "\n".join(header) + validation.summarise(issues)
    report_path = Path(output_path).with_name(Path(output_path).stem + "_checks.txt")
    report_path.write_text(report + "\n", encoding="utf-8")

    reviews = [i for i in issues if i.kind == "REVIEW"]
    fixes = [i for i in issues if i.kind == "FIX"]
    if fixes:
        on_progress(f"{len(fixes)} thing(s) corrected automatically.", None)
    if reviews:
        on_progress(
            f"{len(reviews)} thing(s) worth a look across "
            f"{len({i.sku for i in reviews})} listing(s) — see {report_path.name}:", None)
        for issue in reviews:
            on_progress(f"    {issue.sku}: {issue.message}", None)
    else:
        on_progress("All listings passed every check.", None)

    on_progress(f"Done — {len(template_results)} output file(s) written.", 1.0)

    return template_results, len(assignments), uncovered_skus, errors
