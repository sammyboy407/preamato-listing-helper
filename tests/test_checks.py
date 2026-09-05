"""Tests for the deterministic listing checks (src/validation.py).

Same principle as the sizing suite: each check is fed input that should trip
it and input that shouldn't, so a check can't quietly stop working or start
crying wolf. A noisy check is nearly as bad as a missing one — if the report
is full of things that don't matter, the one that does gets skimmed past.

    python3 tests/test_checks.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Same stubs as tests/test_sizing.py — see the comment there.
for _dep in ("anthropic", "openpyxl", "python_calamine"):
    try:
        __import__(_dep)
    except ModuleNotFoundError:  # pragma: no cover - depends on the machine
        _stub = types.ModuleType(_dep)

        class _StubModule(types.ModuleType):
            def __getattr__(self, name):
                value = type(name, (Exception,), {})
                setattr(self, name, value)
                return value

        _stub.__class__ = _StubModule
        sys.modules[_dep] = _stub

from src import validation  # noqa: E402
from src.data_loader import Product  # noqa: E402
from src.ebay_template import AspectSpec, CategorySpec, EbayTemplate  # noqa: E402

FAILURES: list[str] = []


def check(label, got, expected):
    if got != expected:
        FAILURES.append(f"{label}\n      expected: {expected!r}\n      got:      {got!r}")


CATEGORY = CategorySpec(category_id="63864", category_name="Women's Clothing > Skirts",
                        conditions=[(3000, "Pre-owned - Good")])


def make_template(aspects=None):
    return EbayTemplate(
        listing_headers=[],
        categories=[CATEGORY],
        aspects={"63864": aspects or {}},
        info_rows=[],
    )


def make_product(measurements=None, brand="SIMONE ROCHA"):
    return Product(
        sku="TEST-001",
        master={"Brand": brand, "Gender": "WOMEN", "Colour": "White"},
        measurements=measurements or {},
    )


def good_row(**overrides):
    row = {
        "Custom label (SKU)": "TEST-001",
        "Title": "SIMONE ROCHA Tiered Mini Skirt White 8 RRP 695",
        "Item photo URL": "http://a|http://b|http://c",
        "Start price": 350,
        "OriginalRetailPrice": 695,
        "Description": "Brand: SIMONE ROCHA <br>\nSize: 8 <br>",
        "C:Style": "Mini",
    }
    row.update(overrides)
    return row


def run(row, product=None, aspects=None, size=None):
    return validation.check_row(product or make_product(), row, CATEGORY,
                                make_template(aspects), size_for_display=size)


def messages(issues, kind=None):
    return [i.message for i in issues if kind is None or i.kind == kind]


def test_a_clean_listing_produces_no_noise():
    """The most important test here. A check that fires on a good listing
    makes the whole report worthless."""
    check("clean listing is silent", run(good_row(), size="8"), [])


def test_missing_required_item_specific_is_caught():
    """The exact failure that got every listing rejected by eBay on
    04.09.26 ("The item specific Brand is missing")."""
    aspects = {
        "C:Brand": AspectSpec("C:Brand", "REQUIRED", None),
        "C:Colour": AspectSpec("C:Colour", "REQUIRED", None),
        "C:Pattern": AspectSpec("C:Pattern", "OPTIONAL", None),
    }
    issues = run(good_row(**{"C:Brand": "Simone Rocha"}), aspects=aspects, size="8")
    found = messages(issues)
    check("empty REQUIRED aspect is reported", any("C:Colour is required" in m for m in found), True)
    check("filled REQUIRED aspect is not reported", any("C:Brand is required" in m for m in found), False)
    check("empty OPTIONAL aspect is not reported", any("C:Pattern" in m for m in found), False)


def test_self_contradicting_listing_is_caught_but_not_silently_changed():
    """Sammy's Simone Rocha: Style "Mini", Skirt Length "Midi". Reported,
    never auto-corrected — the code can't tell which side is wrong, and
    guessing is how a wrong listing ships."""
    row = good_row(**{"C:Style": "Mini", "C:Skirt Length": "Midi"})
    issues = run(row, size="8")
    check("contradiction reported", any("contradict" in m for m in messages(issues)), True)
    check("nothing auto-corrected", messages(issues, "FIX"), [])
    check("the row is left exactly as it was", row["C:Skirt Length"], "Midi")

    consistent = good_row(**{"C:Style": "Mini", "C:Skirt Length": "Short"})
    check("a consistent pair is silent",
          any("contradict" in m for m in messages(run(consistent, size="8"))), False)


def test_title_problems():
    long_title = "SIMONE ROCHA " + ("Very Long Description " * 6) + "RRP 695"
    row = good_row(Title=long_title)
    issues = run(row, size=None)
    check("over-length title is trimmed", len(row["Title"]) <= validation.MAX_TITLE_LENGTH, True)
    check("the trim is reported as a FIX", any("trimmed" in m for m in messages(issues, "FIX")), True)

    check("missing brand is reported",
          any("brand" in m for m in messages(run(good_row(Title="Tiered Mini Skirt White 8")))), True)
    check("a title missing the resolved size is reported",
          any("resolved size" in m for m in messages(run(good_row(Title="SIMONE ROCHA Skirt White"), size="8"))), True)
    check("repeated words are reported",
          any("repeats" in m for m in messages(run(good_row(Title="SIMONE ROCHA Skirt Skirt White 8"), size="8"))), True)


def test_photos_and_price():
    check("no photos reported",
          any("no photos" in m for m in messages(run(good_row(**{"Item photo URL": ""})), )), True)
    check("too few photos reported",
          any("only 1 photo" in m for m in messages(run(good_row(**{"Item photo URL": "http://a"})))), True)
    check("zero price reported",
          any("zero or missing" in m for m in messages(run(good_row(**{"Start price": 0})))), True)
    check("price above RRP reported",
          any("above the RRP" in m for m in messages(run(good_row(**{"Start price": 900})))), True)
    check("a sane price is silent",
          any("price" in m for m in messages(run(good_row(), size="8"))), False)


def test_mistyped_measurements():
    """A slipped decimal (230 for 23) or a centimetre value in an inches
    column. Bounds are wide on purpose — this is for data-entry slips, not
    for second-guessing an unusual garment."""
    typo = make_product({"Pit to Pit (inches)": "230"})
    check("implausible measurement reported",
          any("plausible" in m for m in messages(run(good_row(), product=typo, size="8"))), True)

    cm = make_product({"Length (inches)": "90"})  # 90cm typed into an inches column
    check("a cm value reported",
          any("plausible" in m for m in messages(run(good_row(), product=cm, size="8"))), True)

    real = make_product({"Pit to Pit (inches)": "23", "Length (inches)": "37", "Arm (inches)": "26"})
    check("real measurements are silent",
          any("plausible" in m for m in messages(run(good_row(), product=real, size="8"))), False)

    check("non-numeric measurement reported",
          any("isn't a number" in m for m in
              messages(run(good_row(), product=make_product({"Arm (inches)": "approx 26"}), size="8"))), True)


def test_empty_description_line():
    """A bare "Type: " line in the buyer-facing description looks
    unfinished. Seen on a real 04.09.26 listing."""
    row = good_row(Description="Brand: SIMONE ROCHA <br>\nType:  <br>\nSize: 8 <br>")
    check("empty description line reported",
          any("line is empty" in m for m in messages(run(row, size="8"))), True)
    check("a complete description is silent",
          any("line is empty" in m for m in messages(run(good_row(), size="8"))), False)


def test_an_assumed_uk_size_is_recorded_as_a_note_not_a_review():
    """A bare "9" is converted (Sammy's call — the stock is UK-sourced), but
    every row that relied on that assumption has to be named in the report so
    a few can be checked against the physical shoes. Silent conversion is the
    dangerous version."""
    product = make_product({"Size": "9"}, brand="ROA")
    row = good_row(Title="ROA Boots Brown UK 9 RRP 395", **{"C:UK Shoe Size": "9"})
    issues = run(row, product=product, size="UK 9")
    assumed = [i for i in issues if i.kind == "NOTE"]
    check("the assumption is recorded", len(assumed), 1)
    # Guarded rather than indexed: if the kind regresses to REVIEW this list
    # is empty, and a clean named failure is far more use than an IndexError
    # traceback halfway through the suite.
    check("it says what it did", assumed[0].message if assumed else None, "9 -> UK 9")
    check("it carries a group heading", bool(assumed and assumed[0].group), True)
    # The whole point: it must NOT compete with the things that need her.
    check("it is not a REVIEW", messages(issues, "REVIEW"), [])

    explicit = make_product({"Size": "UK 9"}, brand="ROA")
    check("an explicit UK size records nothing",
          [i for i in run(row, product=explicit, size="UK 9") if i.kind == "NOTE"], [])

    eu = make_product({"Size": "45"}, brand="ROA")
    check("an EU size records nothing",
          [i for i in run(row, product=eu, size="EU 45") if i.kind == "NOTE"], [])

    check("a row with no shoe size records nothing",
          [i for i in run(good_row(), product=product, size="8") if i.kind == "NOTE"], [])


def test_notes_never_crowd_out_the_things_that_need_a_person():
    """65 signed-off assumptions plus one real contradiction: the
    contradiction has to be the first thing on the page, not buried."""
    issues = [validation.Issue(f"SKU-{n}", "NOTE", f"{n} -> UK {n}", group="read as UK")
              for n in range(60)]
    issues.insert(30, validation.Issue("SKU-REAL", "REVIEW", "Style says 'Mini' but it says 'Midi'"))
    report = validation.summarise(issues)
    lines = report.splitlines()

    def first_line_containing(text):
        return next((i for i, l in enumerate(lines) if text in l), None)

    review_at = first_line_containing("Mini")
    note_at = first_line_containing("read as UK")
    check("the REVIEW is in the report", review_at is not None, True)
    check("the notes are in the report", note_at is not None, True)
    check("the REVIEW comes before the notes",
          review_at is not None and note_at is not None and review_at < note_at, True)
    check("the notes are one grouped block, not 60 SKU headings",
          sum(1 for l in lines if "read as UK" in l), 1)
    check("but every SKU is still named", sum(1 for l in lines if "-> UK" in l), 60)

    # A batch whose only entries are signed-off assumptions still reads as a
    # pass, because nothing in it needs acting on.
    notes_only = validation.summarise([
        validation.Issue("SKU-1", "NOTE", "9 -> UK 9", group="read as UK")])
    check("a notes-only batch reads as a pass",
          notes_only.startswith("All listings passed every check."), True)
    check("and still names the SKU", "SKU-1" in notes_only, True)


def test_a_range_size_is_named_in_the_report():
    """The range is a value off eBay's dropdown list. It has been accepted
    before, but each one is named so a rejection is spotted on the first
    upload rather than found later."""
    product = make_product({"Size": "2.5-3.5"}, brand="MOON BOOT")
    row = good_row(Title="MOON BOOT Snow Boots White UK 2.5-3.5 RRP 250",
                   **{"C:UK Shoe Size": "2.5-3.5"})
    check("range size named",
          any("sized as a range" in m for m in messages(run(row, product=product, size="UK 2.5-3.5"))), True)

    single = good_row(Title="MOON BOOT Snow Boots White UK 3 RRP 250", **{"C:UK Shoe Size": "3"})
    check("a single size is not named",
          any("sized as a range" in m for m in
              messages(run(single, product=make_product({"Size": "UK 3"}), size="UK 3"))), False)


def test_the_report_reads_like_something_a_person_would_act_on():
    check("a clean batch says so", validation.summarise([]), "All listings passed every check.")
    report = validation.summarise([
        validation.Issue("SKU-1", "REVIEW", "something to look at"),
        validation.Issue("SKU-1", "REVIEW", "something else"),
        validation.Issue("SKU-2", "FIX", "something corrected"),
    ])
    check("groups by SKU", report.count("SKU-1"), 1)
    check("counts the reviews", "2 thing(s) to look at" in report, True)
    check("counts the fixes separately", "1 thing(s) corrected automatically" in report, True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"Ran {len(tests)} listing-check tests.")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):\n")
        for f in FAILURES:
            print(f"  ✗ {f}\n")
        return 1
    print("All listing checks behave correctly. ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
