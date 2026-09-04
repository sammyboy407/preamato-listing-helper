"""Sizing safety net.

Every rule here exists because getting it wrong ships a listing with the
wrong size on it — a return, a customer-service exchange, and potentially a
negative review. Sammy, 04.09.26: "these have to be watertight, we cannot
afford mistakes on sizing."

Several of these lock in the behaviour of real bugs that reached a real
output file, so a future change can't quietly reintroduce them:

  * EU 45 boots came out as UK 4.5, because the fuzzy matcher stripped
    punctuation and compared "45" to "4.5" as equal.
  * A title read "UK 11" while the item specific said 4.5, because the AI
    was doing its own conversion for the title.
  * UNISEX footwear silently used the women's conversion table, a full size
    out for anything sized as men's.

Run it with no arguments, from anywhere:

    python3 tests/test_sizing.py

Exits non-zero and prints every failure if anything regresses. Deliberately
plain asserts and no test-framework dependency, so it runs on a stock
macOS python3 with nothing installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The sizing rules themselves are pure Python — src/aspect_matching.py imports
# nothing beyond the standard library, which is why enforce_title_size lives
# there. The end-to-end test below does need the rest of the app, and that
# reaches openpyxl / python_calamine / anthropic (see requirements.txt). Those
# are installed on Streamlit Cloud, not necessarily on a laptop — and this
# suite has to run anywhere, before every batch, with nothing installed. So
# stand in stubs for exactly those three when they're missing. The list is
# deliberately closed: a genuinely broken import inside src/ still fails
# loudly rather than being papered over.
import types  # noqa: E402

for _dep in ("anthropic", "openpyxl", "python_calamine"):
    try:
        __import__(_dep)
    except ModuleNotFoundError:  # pragma: no cover - depends on the machine
        _stub = types.ModuleType(_dep)

        class _Unavailable(Exception):
            pass

        class _StubAttr:
            """Stands in for any attribute the app touches while importing.
            Subclasses Exception so it works where an exception class is
            expected (ai_client builds a tuple of them at import time), and
            raises if anything actually tries to use it."""
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)

            def __init__(self, *args, **kwargs):
                raise RuntimeError(f"{_dep} is not installed in this environment")

        def _make(name, _dep=_dep):
            return type(name, (Exception,), {})

        class _StubModule(types.ModuleType):
            def __getattr__(self, name):
                value = _make(name)
                setattr(self, name, value)
                return value

        _stub.__class__ = _StubModule
        sys.modules[_dep] = _stub

from src import aspect_matching as am  # noqa: E402

FAILURES: list[str] = []


def check(label, got, expected):
    if got != expected:
        FAILURES.append(f"{label}\n      expected: {expected!r}\n      got:      {got!r}")


# eBay's real UK Shoe Size lists, as the department templates carry them.
UK_MENS = ["2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5", "6", "6.5", "7", "7.5", "8",
           "8.5", "9", "9.5", "10", "10.5", "11", "11.5", "12", "12.5", "13", "13.5", "14"]
UK_WOMENS = ["1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5", "6", "6.5", "7",
             "7.5", "8", "8.5", "9", "9.5", "10", "10.5", "11"]


def test_no_wrong_shoe_size_is_ever_produced():
    """The core rule: an EU size converts correctly, or nothing comes out.

    Every expected UK value below is cross-checked against the account's own
    789-listing history (data/account_listings_export.csv), where a person
    entered the EU and UK sizes by hand.
    """
    # Women: UK = EU - 33. History has 35->2, 36->3, 37->4, 38->5, 39->6,
    # 40->7, 41->8, all matching.
    for eu, uk in [("34", "1"), ("35", "2"), ("36", "3"), ("37", "4"), ("38", "5"),
                   ("39", "6"), ("40", "7"), ("41", "8"), ("42", "9"), ("43", "10")]:
        check(f"women EU {eu}", am.match_shoe_size_uk(eu, UK_WOMENS, "WOMEN"), uk)
    # Half sizes sit half a UK size up.
    for eu, uk in [("36.5", "3.5"), ("38.5", "5.5"), ("40.5", "7.5")]:
        check(f"women EU {eu}", am.match_shoe_size_uk(eu, UK_WOMENS, "WOMEN"), uk)

    # Men: UK = EU - 34 from 41 up. History has 41->7, 42->8, 43->9, 44->10,
    # 45->11, all matching.
    for eu, uk in [("39", "6"), ("40", "6.5"), ("41", "7"), ("42", "8"), ("43", "9"),
                   ("44", "10"), ("45", "11"), ("46", "12"), ("47", "13")]:
        check(f"men EU {eu}", am.match_shoe_size_uk(eu, UK_MENS, "MEN"), uk)

    # The exact pair that shipped wrong on 04.09.26.
    check("ROA boots EU 45 (men) must be UK 11, never 4.5",
          am.match_shoe_size_uk("45", UK_MENS, "MEN"), "11")
    check("Attico EU 35 (women) must be UK 2, never 3.5",
          am.match_shoe_size_uk("35", UK_WOMENS, "WOMEN"), "2")


def test_numbers_only_ever_match_exactly():
    """The root cause of the UK 4.5 bug: "45" and "4.5" compared equal once
    punctuation was stripped, and difflib scores them 0.8. Numbers must
    match exactly, after normalising leading zeros and trailing .0 only."""
    check("45 must not match 4.5", am.fuzzy_match("45", ["4.5", "45.5"]), None)
    check("35 must not match 3.5", am.fuzzy_match("35", ["3.5"]), None)
    check("10 must not match 10.5", am.fuzzy_match("10", ["10.5"]), None)
    check("8 must not match 18", am.fuzzy_match("8", ["18", "38"]), None)
    # Formatting differences are still bridged.
    check("08 -> 8", am.fuzzy_match("08", ["8"]), "8")
    check("8.0 -> 8", am.fuzzy_match("8.0", ["8"]), "8")
    check("4.50 -> 4.5", am.fuzzy_match("4.50", ["4.5"]), "4.5")
    # Text matching must keep working — brands rely on it.
    check("brand punctuation still fuzzy",
          am.fuzzy_match("dolce&gabbana", ["Dolce & Gabbana"]), "Dolce & Gabbana")


def test_ambiguous_sizes_are_refused_not_guessed():
    """Anything that could be read two ways produces nothing, so the SKU is
    skipped with a message rather than listed with a coin-flip size."""
    # A bare number below EU range could be UK or US.
    check("bare 7 is ambiguous", am.match_shoe_size_uk("7", UK_WOMENS, "WOMEN"), None)
    check("bare 5.5 is ambiguous", am.match_shoe_size_uk("5.5", UK_MENS, "MEN"), None)
    # US sizing has no conversion table.
    check("US 9 refused", am.match_shoe_size_uk("US 9", UK_MENS, "MEN"), None)
    # Gender decides the table, so it must be known (EU 43 is UK 9 for men,
    # UK 10 for women — a full size apart).
    check("UNISEX refused", am.match_shoe_size_uk("43", UK_MENS, "UNISEX"), None)
    check("blank gender refused", am.match_shoe_size_uk("43", UK_MENS, None), None)
    check("GIRL refused", am.match_shoe_size_uk("37", UK_WOMENS, "GIRL"), None)
    # Junk and ranges.
    check("range refused", am.match_shoe_size_uk("39/40", UK_WOMENS, "WOMEN"), None)
    check("non-numeric refused", am.match_shoe_size_uk("abc", UK_WOMENS, "WOMEN"), None)
    check("out-of-table EU refused", am.match_shoe_size_uk("60", UK_MENS, "MEN"), None)
    # An explicit UK size is used as-is, never re-converted.
    check("UK 7 stays 7", am.match_shoe_size_uk("UK 7", UK_WOMENS, "WOMEN"), "7")
    check("UK 11 stays 11", am.match_shoe_size_uk("UK 11", UK_MENS, "MEN"), "11")


def test_size_is_shown_in_the_system_it_was_recorded_in():
    """Sammy, 04.09.26: "if its an EU size in the orbitvu file i.e 45 it
    needs to read EU 45 in the item title and then in the UK size item
    specifics we need to convert it to UK 11"."""
    check("EU stays EU in the title",
          am.size_display("45", uk_shoe="11", eu_shoe="45"), "EU 45")
    check("description shows both",
          am.size_display("45", uk_shoe="11", eu_shoe="45", both=True), "EU 45 (UK 11)")
    check("a recorded UK size leads with UK",
          am.size_display("UK 7", uk_shoe="7", eu_shoe="40", both=True), "UK 7 (EU 40)")
    check("EU works even where the category has no EU aspect",
          am.size_display("40", uk_shoe="7", both=True), "EU 40 (UK 7)")
    # Clothing is never converted — shown exactly as recorded.
    for raw, resolved in [("03", "03"), ("L", "L"), ("One Size", "One Size")]:
        check(f"clothing {raw} unchanged",
              am.size_display(raw, clothing_size=resolved), resolved)


def test_title_can_never_contradict_the_item_specifics():
    """The AI is handed the resolved size and told not to convert it, but a
    prompt is not a guarantee — on 04.09.26 a title read "UK 11" while the
    item specific said 4.5. The title is rewritten in Python regardless of
    what came back."""
    wrong = "ROA Katharina Hiking Boots Rust Suede Men's UK 7 EU 39 RRP 395"
    check("an invented size is replaced",
          am.enforce_title_size(wrong, "EU 45"),
          "ROA Katharina Hiking Boots Rust Suede Men's EU 45 RRP 395")
    check("a correct size is tidied to one mention",
          am.enforce_title_size("ROA Boots UK 11 EU45 RRP 395", "EU 45"),
          "ROA Boots EU 45 RRP 395")
    check("'Size 12' replaced for clothing",
          am.enforce_title_size("SIMONE ROCHA Mini Skirt White Size 12 RRP 695", "8"),
          "SIMONE ROCHA Mini Skirt White 8 RRP 695")
    check("an unmarked but correct size isn't duplicated",
          am.enforce_title_size("STONE ISLAND Overcoat Black L RRP 1045", "L"),
          "STONE ISLAND Overcoat Black L RRP 1045")
    check("'One Size' isn't duplicated",
          am.enforce_title_size("BRAND Jumper Wool One Size Black RRP 200", "One Size"),
          "BRAND Jumper Wool Black One Size RRP 200")
    # Numbers that are not sizes must survive untouched.
    check("heel height and RRP survive",
          am.enforce_title_size("ATTICO Loafer Black 20mm UK 2 EU35 RRP 695", "EU 35"),
          "ATTICO Loafer Black 20mm EU 35 RRP 695")
    # With no resolved size, a size claim is removed rather than left to
    # contradict a blank item specific.
    check("unsupported size claim removed",
          am.enforce_title_size("BRAND Trainers UK 9 Black", None),
          "BRAND Trainers Black")


def test_generated_listing_agrees_with_itself_end_to_end():
    """The checks above test the pieces. This one runs a real product
    through generate_for_product() against the real department templates,
    with the AI mocked to return a deliberately wrong size, and asserts the
    finished listing agrees with itself.

    It exists because testing the pieces isn't enough: removing the call to
    _enforce_title_size from the pipeline left every unit test passing while
    the actual output regressed.
    """
    import json
    import tempfile
    from src import ai_client, content_generator, ebay_template
    from src.data_loader import Product

    templates_dir = Path(__file__).resolve().parent.parent / "data" / "templates"
    menswear = templates_dir / "menswear_shoes.json"
    if not menswear.exists():
        print("  (skipped end-to-end check: data/templates not present in this checkout)")
        return

    template = ebay_template.load_template(menswear)
    category = template.category_by_id("11498")  # Men's Shoes > Boots
    product = Product(
        sku="TEST-001",
        master={"Brand": "ROA", "Gender": "MEN", "Colour": "Brown",
                "Category": "Footwear", "SubCat2": "Boots", "Rounded RRP": 395,
                "Country of Origin": "ITA", "Clean Title Description": "ROA HIKING BOOT"},
        measurements={"Size": "45", "Description": "Good condition."},
    )

    def fake_ai(system, user, tool_name, input_schema, **kwargs):
        props = input_schema["properties"]["item_specifics"]["properties"]
        required = set(input_schema["properties"]["item_specifics"].get("required", []))
        specifics = {}
        for name, spec in props.items():
            if name not in required:
                continue
            if spec.get("type") == "array":
                specifics[name] = spec["items"]["enum"][:1]
            elif "enum" in spec:
                specifics[name] = spec["enum"][0]
            else:
                specifics[name] = "Leather"
        return {
            # A wrong size, in the wrong system, exactly as the real 04.09.26
            # batch produced.
            "title": "ROA Hiking Boots Brown Suede UK 4.5 EU 39 RRP 395",
            "condition_id": category.conditions[0][0],
            "condition_description": "Good condition.",
            "material_summary": "Suede",
            "item_specifics": specifics,
        }

    original = ai_client.call_structured
    ai_client.call_structured = fake_ai
    try:
        with tempfile.TemporaryDirectory() as cache_dir:
            result = content_generator.generate_for_product(
                product, category, template, cache_dir, force=True)
    finally:
        ai_client.call_structured = original

    specifics = result["item_specifics"]
    title = result["title"]
    check("end-to-end: UK Shoe Size item specific", specifics.get("C:UK Shoe Size"), "11")
    check("end-to-end: EU Shoe Size item specific", specifics.get("C:EU Shoe Size"), "45")
    check("end-to-end: title carries the recorded EU size",
          "EU 45" in title, True)
    for bad in ("UK 4.5", "EU 39", "UK 7"):
        if bad in title:
            FAILURES.append(f"end-to-end: title still contains the AI's invented {bad!r}: {title!r}")


def test_changing_a_sizing_rule_invalidates_the_cache():
    """A stale cache entry is indistinguishable from a fresh one in the
    output file, so after a sizing fix a re-run must not re-serve the old
    answer. The cache key carries a fingerprint of the sizing code and
    tables; this proves the fingerprint actually moves when they do."""
    from src import aspect_matching, content_generator

    baseline = content_generator._sizing_fingerprint()

    original = dict(aspect_matching.EU_TO_UK_MENS_SHOE_SIZE)
    try:
        aspect_matching.EU_TO_UK_MENS_SHOE_SIZE["45"] = "12"
        check("changing a conversion table changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.EU_TO_UK_MENS_SHOE_SIZE.clear()
        aspect_matching.EU_TO_UK_MENS_SHOE_SIZE.update(original)

    check("fingerprint is stable when nothing changed",
          content_generator._sizing_fingerprint(), baseline)


def test_conversion_tables_are_internally_consistent():
    """Guards against a typo in a table: every entry must be a real eBay UK
    value, and sizes must increase with EU size, never jump backwards."""
    for name, table, valid in [("women's", am.EU_TO_UK_WOMENS_SHOE_SIZE, UK_WOMENS),
                               ("men's", am.EU_TO_UK_MENS_SHOE_SIZE, UK_MENS)]:
        entries = sorted(((float(eu), float(uk)) for eu, uk in table.items()))
        for eu, uk in entries:
            if str(uk).replace(".0", "") not in [v.replace(".0", "") for v in valid]:
                FAILURES.append(f"{name} table: EU {eu} -> UK {uk} is not a valid eBay UK size")
        for (eu_a, uk_a), (eu_b, uk_b) in zip(entries, entries[1:]):
            if uk_b < uk_a:
                FAILURES.append(
                    f"{name} table goes backwards: EU {eu_a}->UK {uk_a} then EU {eu_b}->UK {uk_b}")
            if uk_b - uk_a > 1:
                FAILURES.append(
                    f"{name} table jumps more than a full size: EU {eu_a}->UK {uk_a} "
                    f"then EU {eu_b}->UK {uk_b}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"Ran {len(tests)} sizing checks.")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S) — do not upload a batch until these pass:\n")
        for f in FAILURES:
            print(f"  ✗ {f}\n")
        return 1
    print("All sizing rules hold. ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
