"""CLI entry point.

Usage:
    python3 -m src.main \\
        --master "/path/to/Master File.xlsx" \\
        --measurements "/path/to/Pictures and Measurements.csv" \\
        --template "/path/to/eBay-category-listing-template-....xlsx" \\
        --output "output/ebay_upload.csv"

Each of --master, --measurements, and --template accepts more than one path
(space-separated) — multiple master/measurements files are merged, and
multiple templates let one run cover your whole catalog across every
category you have a template for (each template only covers whichever
categories were selected when it was downloaded from Seller Hub). One
output CSV is written per template that ends up with matched products,
matching that template's Listings sheet column layout exactly — the
template itself is still read for its Categories/Aspects/Conditions data,
it just isn't the delivered output file.

--template is optional — omit it entirely to use the built-in category
catalog instead (src/builtin_catalog.py, built from a bundled export of
this account's own real listings). A real uploaded template is still
more accurate where it covers a category (official Required/Preferred/
Optional flags and closed value lists, vs. the built-in catalog's
best-effort inference from historical fill rates), so pass one when you
have it and rely on the built-in catalog for everything else.

By default listings start immediately. Pass --schedule-time to start them
at a future date/time instead — only applied to a template that actually
has a Schedule Time column.

Selling price defaults to 50% of RRP, rounded to the nearest 5. Pass
--price-percent to use a different percentage for the whole batch.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from . import config, pipeline

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--master", required=True, nargs="+", help="Path(s) to Master File .xlsx")
    parser.add_argument("--measurements", required=True, nargs="+", help="Path(s) to Pictures and Measurements .csv")
    parser.add_argument(
        "--template", required=False, nargs="+", default=None,
        help="Path(s) to eBay's own 'Create listings in bulk' category template .xlsx (see usage note above). "
             "Optional — omit to use the built-in category catalog (data/account_listings_export.csv) instead.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Base path to write output .csv to (defaults to output/Ebay-Upload-dd.mm.yy.csv, dated today). "
             "With multiple templates, each gets its own file named after this base + the template's name.",
    )
    parser.add_argument(
        "--schedule-time",
        default=None,
        help="Start listings at this future date/time instead of immediately, format "
             "'YYYY-MM-DD HH:MM:SS' (24-hour, GMT), e.g. '2026-08-28 13:30:00'. Only applied to a "
             "template that actually has a Schedule Time column — omit to list immediately.",
    )
    parser.add_argument(
        "--price-percent", type=float, default=config.START_PRICE_RATIO * 100,
        help=f"Selling price as a %% of RRP, applied to every listing and rounded to the nearest "
             f"5 (default {config.START_PRICE_RATIO * 100:.0f}).",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N matched products (for testing)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent AI calls for per-product content")
    parser.add_argument("--force-regenerate", action="store_true", help="Ignore the content cache and regenerate everything")
    args = parser.parse_args()

    if args.schedule_time:
        try:
            parsed = datetime.strptime(args.schedule_time, config.SCHEDULE_TIME_FORMAT)
        except ValueError:
            parser.error(f"--schedule-time must be in the format 'YYYY-MM-DD HH:MM:SS', got: {args.schedule_time!r}")
        if parsed <= datetime.now():
            parser.error("--schedule-time must be in the future — eBay listings can't be scheduled in the past.")

    if not (0 < args.price_percent <= 100):
        parser.error(f"--price-percent must be between 0 and 100, got: {args.price_percent}")

    def on_progress(msg: str, frac: float | None) -> None:
        print(msg)

    output_path = args.output or str(DEFAULT_OUTPUT_DIR / pipeline.default_output_filename())

    results, considered, uncovered = pipeline.run(
        master_path=args.master,
        measurements_path=args.measurements,
        template_path=args.template,
        output_path=output_path,
        cache_dir=args.cache_dir,
        limit=args.limit,
        workers=args.workers,
        force_regenerate=args.force_regenerate,
        schedule_time=args.schedule_time,
        price_percent=args.price_percent,
        on_progress=on_progress,
    )

    print(f"\n{len(results)} output file(s) written:")
    for r in results:
        print(f"  {r.output_path}  ({len(r.rows)} listing(s): {', '.join(r.category_names)})")

    if args.schedule_time and not any(row.get("Schedule Time") for r in results for row in r.rows):
        print(
            f"\nNote: --schedule-time was given, but none of the given template(s) have a "
            f"Schedule Time column — listings will start immediately instead."
        )
    if uncovered:
        print(f"\n{len(uncovered)} product(s) not covered by any given template: {uncovered}")


if __name__ == "__main__":
    main()
