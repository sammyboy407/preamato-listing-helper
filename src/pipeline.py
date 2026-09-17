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

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import (brand_blurb, build, builtin_catalog, category_mapping, qc, config,
               content_generator, data_loader, ebay_template, validation)
from . import aspect_matching

# Optional[float], not "float | None". This is a runtime expression, not an
# annotation, so `from __future__ import annotations` does not defer it, and
# PEP 604 unions in that position need Python 3.10. Sammy's Mac runs the
# 3.9 that ships with Apple's Command Line Tools, so `import pipeline` blew
# up there on 06.09.26 the first time a test imported this module — while
# passing on every newer Python, including the one Streamlit Cloud runs.
ProgressFn = Callable[[str, Optional[float]], None]

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


# Templates whose turn comes last. Sammy, 07.09.26: "we mainly sell mens and
# womens so these departments should come before kids".
#
# Order matters because the first template whose mapping returns a match wins,
# and the default templates were offered in plain alphabetical order, which put
# kidswear ahead of menswear_shoes and womenswear_shoes. That is how seven
# men's sneakers came to be asked against the kids category list at all.
#
# fix 26's gender guard already makes it impossible for an adult product to
# land in a kids category, so this is not what stops that bug — it stops the
# question being asked. Every adult product now meets its own department
# first, which is one fewer AI call per combination and one less place for an
# answer to come back different on a rebuilt cache.
#
# Kidswear is still offered, just last, so genuine kids stock is unaffected:
# the three Boots / GIRL and the Moon Boot Kids reach it exactly as before,
# because no adult template will take them (the same guard, in the other
# direction).
LAST_TEMPLATE_STEMS = ("kidswear",)


def _template_rank(path: Path) -> tuple:
    return (1 if path.stem.lower().startswith(LAST_TEMPLATE_STEMS) else 0, path.name)


def _default_department_templates() -> list[Path]:
    if not DEFAULT_TEMPLATES_DIR.is_dir():
        return []
    return sorted(DEFAULT_TEMPLATES_DIR.glob("*.json"), key=_template_rank)


def _noop(msg: str, frac: float | None = None) -> None:
    pass


def default_output_filename(now=None) -> str:
    """e.g. 'Ebay-Upload-14.09.26-1540.csv'.

    The time is in there because it was not, and two runs on the same day
    silently overwrote each other. 14.09.26 had five runs: the QTN02
    re-run, the Brook St batch, and three attempts at BRK02-001-026. Only
    the last one's file survived, and the NEEDS ATTENTION file and checks
    report that go beside it were overwritten too — so the record of what
    was held back went with them.

    Minutes, not seconds: two runs inside the same minute is not a thing
    that happens here, and a filename someone has to read aloud is worth
    keeping short."""
    stamp = now or datetime.now()
    return f"Ebay-Upload-{stamp:%d.%m.%y-%H%M}.csv"


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


def _template_order(product, templates) -> list[int]:
    """The order this product is offered to the templates.

    Normally the order they were given in, which for the department defaults
    is alphabetical. That put homeware ahead of menswear_shoes, so a Givenchy
    lounge slipper filed as Lifestyle / Home Accessories was offered to
    homeware first, matched Home Décor > Other Home Décor, and never reached
    the shoes template. Sammy, 06.09.26: "shoes slippers need to go under
    footwear."

    So a product whose labels say homeware while its Department and customs
    tariff code say footwear is offered the shoe templates first. Only first,
    not exclusively: if every shoe template says no, the rest are still tried,
    so nothing can be lost by this rule — the worst case is the order it had
    before.

    And a product is offered its OWN department before anybody else's.

    09.09.26 is why. Of 128 garments, 108 listed and all eighteen womenswear
    Tops were lost — every single one. menswear_clothing comes before
    womenswear_clothing alphabetically and it carries "Shirts & Tops > Casual
    Shirts & Tops", which is a perfectly good answer to the question "where
    does Tops / WOMEN go?" if nobody has mentioned that a women's template
    exists. It matched there, C:Department could not then be filled — a
    women's product in a men's category — and all eighteen were held back.

    Nothing was broken. The category mapping answered a reasonable question
    reasonably, the department guard caught it, and fix 27 kept them out of
    the file rather than letting eBay refuse them. It was the ORDER that was
    wrong: they should never have been asked.

    Same shape as the mules, the slipper and the Boys' Shoes before it. The
    first template that says yes wins, so what matters is who is asked first.
    Sammy already had the instinct on 07.09.26 — "we mainly sell mens and
    womens so these departments should come before kids". This is that rule
    finished: own department first, then the neutral templates, then everyone
    else, and kids last of all, which the file ordering already does.

    Still only an ordering. Every template is still tried, so a woman's
    cufflinks can still land in Men's Jewellery when that is genuinely the
    only category that fits."""
    order = list(range(len(templates)))
    product_audience = category_mapping.product_audience(product.m("Gender"))

    def rank(i: int) -> tuple:
        # A misfiled slipper still meets the shoe templates first.
        shoe = 0 if (misfiled and category_mapping.covers_footwear(templates[i])) else 1
        template_audience = category_mapping.template_audience(templates[i])
        if product_audience in (None, "unisex") or template_audience is None:
            gender = 1                      # nothing to go on, or a neutral template
        elif template_audience == product_audience:
            gender = 0                      # this product's own department
        else:
            gender = 2                      # somebody else's
        return (shoe, gender, i)

    misfiled = category_mapping.is_misfiled_footwear(product)
    return sorted(order, key=rank)


@dataclass
class HeldBack:
    """The rows eBay would refuse, kept out of the upload file.

    Returned as its own object rather than a fifth loose tuple element of
    strings, because the caller has to be able to show the reason next to
    the SKU: "QTN02-001-922: C:Department is required by eBay for this
    category but is empty" is actionable, "7 rows failed" is not."""
    skus: list[str] = field(default_factory=list)
    reasons: list = field(default_factory=list)   # [(sku, [reason, ...]), ...]
    path: str | None = None

    def __bool__(self) -> bool:
        return bool(self.skus)

    def __len__(self) -> int:
        return len(self.skus)


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
    photo_qc: bool = False,
    on_progress: ProgressFn = _noop,
) -> tuple[list[TemplateResult], int, list[str], list[str], "HeldBack"]:
    """Runs the full pipeline. Returns (template_results, num_products_considered,
    uncovered_skus, failed, held_back) — uncovered_skus lists products whose
    (Category, SubCat2, Gender) doesn't match any category in ANY of the given
    templates, failed lists "SKU: reason" for every product that was dropped
    during generation (an unresolvable Required size, an API error that
    survived its retries), and held_back is the rows eBay would refuse, which
    are written to their own file instead of the upload file.

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

    # Pre-flight, before a single AI call. Sammy, 14.09.26: "i want all
    # items to have RRP". A missing RRP means a £0 start price, which
    # validation blocks, so the product does not list — and until now the
    # only way to find that out was to wait for the whole run and read the
    # held-back list. BRK02-001-026 cost three separate runs that way.
    #
    # Named here instead, within seconds of pressing Generate, so the
    # Master File can be fixed before the expensive part happens rather
    # than after it. Deliberately not fatal: a batch where one row is
    # missing an RRP should still list the other forty.
    # 2.4 — what did I just read? 14.09.26: the working copy of a sheet and
    # the master built from it are indistinguishable in a file picker, and
    # the wrong one was uploaded twice. Counts that look wrong are obvious
    # in one second; a wrong file is not.
    departments = sorted({str(p.m("Category") or "?") for p in products})
    with_rrp = sum(1 for p in products if (p.m("Rounded RRP") or 0))
    on_progress(
        f"Read {len(products)} product(s): {with_rrp} with an RRP, "
        f"{len(products) - with_rrp} without. Departments: {', '.join(departments)}.", 0.05)

    # The pre-flight. Everything below is checked BEFORE a single AI call,
    # because until 14.09.26 the only way to learn any of it was to wait
    # out the whole run and read the held-back list. That day: 8 listings
    # lost to a blank size, 2 to a bare number, and BRK02-001-026 held out
    # of three separate runs for one empty RRP cell.
    #
    # None of these stop the run. A batch where one row is short should
    # still list the other forty.
    warnings = []

    no_rrp = [p.sku for p in products if not (p.m("Rounded RRP") or 0)]
    if no_rrp:
        warnings.append(
            f"{len(no_rrp)} product(s) have NO RRP in the Master File, so they will price "
            f"at £0 and be held out of the upload file: {', '.join(no_rrp)}")

    # A blank Size is always fatal — the Pictures & Measurements file is the
    # only source, the Master File is never a fallback (it is not verified
    # against the physical item), so there is nothing to fall back to.
    no_size = [p.sku for p in products if not str(p.meas("Size") or "").strip()]
    if no_size:
        warnings.append(
            f"{len(no_size)} product(s) have NO SIZE in the Orbitvu file and cannot list. "
            f"The Master File size is never used as a fallback: {', '.join(no_size)}")

    # A bare number is not always wrong — a bare shoe number is read as UK
    # by Sammy's 04.09.26 rule — but in any clothing category that takes
    # EU/FR/IT it is refused, because an IT 38 and an FR 38 are different
    # garments. Worth naming, not worth blocking.
    bare = [p.sku for p in products
            if re.fullmatch(r"\d+(?:\.\d+)?", str(p.meas("Size") or "").strip())]
    if bare:
        warnings.append(
            f"{len(bare)} product(s) have a BARE NUMBER size with no scale marker. Fine for "
            f"shoes, refused in any clothing category that takes EU/FR/IT sizes. Record it "
            f"as e.g. 'EU 38' if these are garments: {', '.join(bare)}")

    if warnings:
        on_progress(
            "HEADS UP before the slow part — fix these now and re-run, or let it go and "
            "these products will be missing from the file:\n  " + "\n  ".join(warnings), 0.05)

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
        for idx in _template_order(p, templates):
            entry = category_mapping.lookup(cat_caches[idx], p, templates[idx])
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
        return [], 0, uncovered_skus, [], HeldBack()

    # (brand, condition tier) rather than brand alone: a brand with both new
    # and preloved stock in one batch needs a paragraph for each, or the new
    # items open by calling themselves pre-owned (16.09.26, 16 rows). The
    # tier comes off the inspection note, which is the same thing
    # aspect_matching.enforce_condition decides the condition id from later
    # — read here because the blurbs are built before any AI call.
    brand_tiers = {
        (str(p.m("Brand")),
         brand_blurb.NEW if aspect_matching.notes_say_new(p.measurements.get("Description"))
         else brand_blurb.PRELOVED)
        for p, _, _ in assignments if p.m("Brand")
    }
    brands = {b for b, _ in brand_tiers}
    on_progress(f"Building brand descriptions for {len(brands)} brand(s)...", 0.2)
    blurb_cache = brand_blurb.build_blurbs(brand_tiers, cache_dir)
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
    # Rows eBay would refuse. Kept out of the upload file entirely and
    # written to their own file instead — see validation.Issue.blocking.
    held: list[tuple[int, dict, list[str]]] = []   # (template_idx, row, reasons)
    held_skus: list[str] = []
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
        row_issues = validation.check_row(
            p, row, category, template,
            size_for_display=aspect_matching.size_display_for(p, specifics),
        )
        issues.extend(row_issues)

        # A row eBay will refuse does not go in the upload file. It goes in
        # the needs-attention file with its reasons, so that what the app
        # hands over is a file that uploads clean.
        reasons = validation.blocking_reasons(row_issues)

        # QC tier 1: two of our own fields disagreeing with each other.
        # Free, certain, and it is exactly what a SAINT LAURENT shoulder bag
        # needed to not go live reading "Style: Backpack" on 16.09.26. Held
        # out rather than merely reported, Sammy's call: a revise costs far
        # more than a re-run.
        reasons = list(reasons) + qc.contradictions(row, p.master.get("Composition"))

        if reasons:
            held.append((idx, row, reasons))
            held_skus.append(p.sku)
            continue

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

    # QC tier 2: every assembled listing against its own main photograph.
    # The only tier that catches source data that is wrong in the same way
    # across every field -- the Brook St sheet had Product name and Colour
    # shuffled and 24 of 48 would have listed as the wrong item. Nothing but
    # the picture disagrees with that.
    if photo_qc and results_by_template:
        checked = [(idx, row) for idx, tr in results_by_template.items() for row in tr.rows]
        on_progress(f"QC: checking {len(checked)} listing(s) against their photographs...", 0.93)
        flagged: dict[str, list[str]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for _idx, row in checked:
                urls = build.split_image_urls(row.get("Item photo URL"))
                futures[pool.submit(qc.check_against_photo, row,
                                    urls[0] if urls else None)] = row
            done = 0
            for fut in as_completed(futures):
                row = futures[fut]
                done += 1
                on_progress(f"[QC {done}/{len(checked)}] {row.get('Custom label (SKU)')}",
                            0.93 + 0.05 * done / max(len(checked), 1))
                try:
                    problems = fut.result()
                except Exception:  # noqa: BLE001 - QC never fails a batch
                    problems = []
                if problems:
                    flagged[str(row.get("Custom label (SKU)"))] = problems
        if flagged:
            for idx, tr in list(results_by_template.items()):
                keep = []
                for row in tr.rows:
                    sku = str(row.get("Custom label (SKU)"))
                    if sku in flagged:
                        held.append((idx, row, flagged[sku]))
                        held_skus.append(sku)
                    else:
                        keep.append(row)
                tr.rows = keep
            results_by_template = {i: t for i, t in results_by_template.items() if t.rows}
        on_progress(f"QC done: {len(flagged)} held back, "
                    f"{len(checked) - len(flagged)} clean.", 0.98)


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

    # The rows eBay would refuse, in their own file, with the reason in a
    # column of its own. Same shape as the upload file — the #INFO preamble,
    # the same headers — so once the underlying data is fixed it can be
    # uploaded as-is, minus the WHY column, which eBay ignores anyway
    # because it is not one of its fields.
    held_path = None
    if held:
        held_rows = []
        held_headers: list[str] = []
        for idx, row, reasons in held:
            annotated = dict(row)
            annotated["WHY THIS ROW IS HELD BACK"] = "; ".join(reasons)
            held_rows.append(annotated)
            for h in templates[idx].listing_headers:
                if h not in held_headers:
                    held_headers.append(h)
        held_template = ebay_template.EbayTemplate(
            listing_headers=held_headers,
            categories=[],
            aspects={},
            info_rows=templates[held[0][0]].info_rows,
        )
        base = Path(output_path)
        held_path = base.with_name(f"{base.stem} NEEDS ATTENTION{base.suffix}")
        build.write_csv(held_rows, held_template, held_path)
        on_progress(
            f"{len(held)} row(s) eBay would refuse were kept OUT of the upload file "
            f"and written to {held_path.name} instead.", None)

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
    if held:
        header.append(f"  {len(held)} held back: eBay would refuse them (listed below)")
    if not uncovered_skus and not errors and not held and rows_out == len(products):
        header.append("  Nothing was dropped.")
    header.append("")

    if held:
        header.append(
            f"HELD BACK ({len(held)}) — these are NOT in the upload file. They are in "
            f"{Path(held_path).name} with the reason in the last column. Fix the "
            f"underlying data and re-run; do not upload them as they are:")
        for _idx, row, reasons in held:
            header.append(f"  {row.get('Custom label (SKU)')}: {'; '.join(reasons)}")
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
    notes = [i for i in issues if i.kind == "NOTE"]
    if fixes:
        on_progress(f"{len(fixes)} thing(s) corrected automatically.", None)
    if notes:
        # Signed-off assumptions: counted here, spelled out in the report.
        # Never listed line by line in the log, which is where the REVIEWs
        # need to be visible.
        on_progress(f"{len(notes)} listing(s) relied on a signed-off assumption "
                    f"(see {report_path.name}).", None)
    if reviews:
        on_progress(
            f"{len(reviews)} thing(s) worth a look across "
            f"{len({i.sku for i in reviews})} listing(s) — see {report_path.name}:", None)
        for issue in reviews:
            on_progress(f"    {issue.sku}: {issue.message}", None)
    else:
        on_progress("All listings passed every check.", None)

    on_progress(f"Done — {len(template_results)} output file(s) written.", 1.0)

    return template_results, len(assignments), uncovered_skus, errors, HeldBack(
        skus=held_skus,
        reasons=[(str(row.get("Custom label (SKU)")), rs) for _idx, row, rs in held],
        path=str(held_path) if held_path else None,
    )
