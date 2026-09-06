"""Tests for the deterministic listing checks (src/validation.py).

Same principle as the sizing suite: each check is fed input that should trip
it and input that shouldn't, so a check can't quietly stop working or start
crying wolf. A noisy check is nearly as bad as a missing one — if the report
is full of things that don't matter, the one that does gets skimmed past.

    python3 tests/test_checks.py
"""
from __future__ import annotations

import pathlib
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


def test_a_bad_measurement_names_the_likely_reading():
    """Sammy, 04.09.26: garments are measured laying flat, so a pit to pit of
    230 is a slipped decimal for 23. Saying so turns a puzzle into a two
    second confirmation. Never auto-corrected: 230 could be 23, 30 or 20, and
    all three would look believable on a live listing."""
    slipped = make_product({"Pit to Pit (inches)": "230"})
    # One run, inspected several ways. Running twice against the same product
    # would hide an implementation that overwrites the measurement, since the
    # second run would see an already-corrected value and report nothing.
    issues = run(good_row(), product=slipped, size="8")
    found = messages(issues)
    check("names the slipped decimal", any("slipped decimal for 23" in m for m in found), True)
    check("says why it is impossible", any("laying flat" in m for m in found), True)
    check("nothing is auto-corrected", messages(issues, "FIX"), [])
    check("the measurement itself is left alone",
          slipped.measurements["Pit to Pit (inches)"], "230")

    # Centimetres wins over the divide-by-ten reading where both would fit:
    # a Length of 90 divides into a plausible 9, but is far more likely 90cm.
    cm = make_product({"Length (inches)": "90"})
    found = messages(run(good_row(), product=cm, size="8"))
    check("reads 90 as centimetres", any("looks like centimetres" in m for m in found), True)
    check("and converts it for them", any("35.4 inches" in m for m in found), True)
    check("not as a slipped decimal", any("slipped decimal" in m for m in found), False)

    # A pit to pit of 58 is 22.8 inches in cm, entirely plausible.
    check("reads 58 as centimetres",
          any("looks like centimetres" in m for m in
              messages(run(good_row(), product=make_product({"Pit to Pit (inches)": "58"}), size="8"))), True)

    # Nonsense that fits neither reading still gets named, just generically.
    weird = make_product({"Pit to Pit (inches)": "9999"})
    found = messages(run(good_row(), product=weird, size="8"))
    check("unexplainable values are still reported", any("9999" in m for m in found), True)
    check("without inventing a reading",
          any("slipped decimal" in m or "centimetres" in m for m in found), False)


def test_mistyped_measurements():
    """A slipped decimal (230 for 23) or a centimetre value in an inches
    column. Bounds are wide on purpose — this is for data-entry slips, not
    for second-guessing an unusual garment."""
    typo = make_product({"Pit to Pit (inches)": "230"})
    check("implausible measurement reported",
          len(messages(run(good_row(), product=typo, size="8"))), 1)

    cm = make_product({"Length (inches)": "90"})  # 90cm typed into an inches column
    check("a cm value reported",
          len(messages(run(good_row(), product=cm, size="8"))), 1)

    real = make_product({"Pit to Pit (inches)": "23", "Length (inches)": "37", "Arm (inches)": "26"})
    check("real measurements are silent",
          messages(run(good_row(), product=real, size="8")), [])

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


def test_a_us_brand_assumption_is_recorded_separately():
    """A bare number on a US-sized brand is still an assumption, but a
    different one with different evidence behind it, so it gets its own
    grouped block rather than being mixed in with the UK ones."""
    product = Product(sku="TEST-001",
                      master={"Brand": "BLACKSTOCK & WEBER", "Gender": "MEN", "Colour": "Brown"},
                      measurements={"Size": "10"})
    row = good_row(Title="BLACKSTOCK & WEBER Loafer Brown UK 9.5 RRP 445",
                   **{"C:UK Shoe Size": "9.5"})
    notes = [i for i in run(row, product=product, size="UK 9.5") if i.kind == "NOTE"]
    check("the assumption is recorded", len(notes), 1)
    check("it shows the whole chain", notes[0].message if notes else None,
          "BLACKSTOCK & WEBER: 10 -> US 10 -> UK 9.5")
    check("its heading names the brand rule",
          bool(notes and "US-sized list" in (notes[0].group or "")), True)

    # A UK-default brand keeps the original wording and its own heading.
    uk_product = Product(sku="TEST-002",
                         master={"Brand": "GH BASS", "Gender": "MEN", "Colour": "Brown"},
                         measurements={"Size": "10"})
    uk_notes = [i for i in run(good_row(**{"C:UK Shoe Size": "10"}), product=uk_product, size="UK 10")
                if i.kind == "NOTE"]
    check("the UK note is unchanged", uk_notes[0].message if uk_notes else None, "10 -> UK 10")
    check("and sits under a different heading",
          bool(uk_notes and notes and uk_notes[0].group != notes[0].group), True)

    # An explicitly marked size on a US brand is not an assumption at all.
    explicit = Product(sku="TEST-003",
                       master={"Brand": "BLACKSTOCK & WEBER", "Gender": "MEN", "Colour": "Brown"},
                       measurements={"Size": "US10"})
    check("an explicit US size records nothing",
          [i for i in run(row, product=explicit, size="UK 9.5") if i.kind == "NOTE"], [])


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


def test_a_size_band_is_named_in_the_report():
    """A boot sold across a band is the one listing that says a different
    size in two places on purpose: the middle size in the item specific,
    because eBay refuses a band there, and the whole band in the title,
    because that is what the buyer needs. Named in the report so nobody
    later reads it as the title/specifics bug it looks like."""
    product = make_product({"Size": "2.5-3.5"}, brand="MOON BOOT")
    row = good_row(Title="MOON BOOT Snow Boots White UK 2.5-3.5 RRP 250",
                   **{"C:UK Shoe Size": "3"})
    said = messages(run(row, product=product, size="UK 2.5-3.5"))
    check("the band is named", any("sized as a band" in m for m in said), True)
    check("and it names the size actually listed",
          any("UK 3" in m for m in said if "sized as a band" in m), True)

    single = good_row(Title="MOON BOOT Snow Boots White UK 3 RRP 250", **{"C:UK Shoe Size": "3"})
    check("a single size is not named",
          any("sized as a band" in m for m in
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


def test_a_mule_is_resolved_from_its_own_title():
    """The first 295-row batch dropped 11 products and every one was a mule.
    Asked at combo level with nothing but "Footwear / Mules / WOMEN" to go
    on, the model answered NONE rather than pick between Heels, Sandals and
    Flats, and the 11 listings silently never appeared (05.09.26).

    This checks the routing, not the answer: a mule must be looked up by its
    own SKU, so the model sees the title, and a combo-level NONE cached
    against "Footwear / Mules / WOMEN" must not be what decides it."""
    from src import category_mapping

    check("mules are on the per-product path",
          ("Footwear", "Mules") in category_mapping.AMBIGUOUS_SUBCATS, True)

    template = EbayTemplate(
        listing_headers=["*Action", "Custom label (SKU)"],
        categories=[
            CategorySpec("55793", "Women's Shoes > Heels", [(1000, "New with box")]),
            CategorySpec("62107", "Women's Shoes > Sandals", [(1000, "New with box")]),
        ],
        aspects={"55793": {}, "62107": {}},
        info_rows=[])
    fingerprint = category_mapping._template_fingerprint(template)

    mule = Product(
        sku="QTN02-001-828",
        master={"Category": "Footwear", "SubCat2": "Mules", "Gender": "WOMEN",
                "Clean Title Description": "JIMMY CHOO BING 100 GLITTER CRYSTAL PT TOE MULE"},
        measurements={})

    # A combo answer of NONE, exactly as the live run cached it.
    cache = {category_mapping._combo_key("Footwear", "Mules", "WOMEN", fingerprint):
             {"category_id": None, "category_name": None, "reasoning": "no fit"}}
    check("a combo-level NONE no longer decides a mule",
          category_mapping.lookup(cache, mule, template), None)

    cache[category_mapping._product_key(mule.sku, fingerprint)] = {
        "category_id": "55793", "category_name": "Women's Shoes > Heels",
        "reasoning": "a 100mm heeled mule"}
    resolved = category_mapping.lookup(cache, mule, template)
    check("and its own title does",
          resolved and resolved.get("category_id"), "55793")

    # A non-ambiguous footwear combo must still take the cheap combo path,
    # or every product costs an AI call.
    sneaker = Product(
        sku="QTN02-001-090",
        master={"Category": "Footwear", "SubCat2": "Sneakers", "Gender": "MEN",
                "Clean Title Description": "ALEXANDER MCQUEEN CANDID SNEAKER"},
        measurements={})
    combo_cache = {category_mapping._combo_key("Footwear", "Sneakers", "MEN", fingerprint):
                   {"category_id": "62107", "category_name": "x", "reasoning": "y"}}
    got = category_mapping.lookup(combo_cache, sneaker, template)
    check("everything else still resolves once per combo",
          got and got.get("category_id"), "62107")


def test_a_slipper_filed_as_homeware_is_still_a_shoe():
    """Sammy, 06.09.26: "shoes slippers need to go under footwear".

    A Givenchy lounge slipper is recorded as Lifestyle / Home Accessories, so
    it was offered to the homeware template first (alphabetically first) and
    went out as Home Décor > Other Home Décor, with a Type of "Cherries"
    because Home Décor's Type list has no slipper in it, and no size at all
    because Home Décor has no shoe size aspect. It is a size 40 men's leather
    shoe.

    The rule is deliberately narrow, and these checks are mostly about what
    it must NOT catch."""
    from src import category_mapping, pipeline

    givenchy = Product(
        sku="QTN02-001-613",
        master={"Category": "Lifestyle", "SubCat2": "Home Accessories",
                "Gender": "MEN", "Department": "Mens Shoes",
                "Tariff Code": "6403599900",
                "Clean Title Description": "GIVENCHY LABEL LOUNGE SLIPPER HOME ACCESSORIES"},
        measurements={})
    check("a slipper filed as homeware is spotted",
          category_mapping.is_misfiled_footwear(givenchy), True)

    # Department alone is enough, and so is the tariff code alone.
    check("Department alone is enough", category_mapping.is_misfiled_footwear(Product(
        sku="X", master={"Category": "Lifestyle", "SubCat2": "Home Accessories",
                         "Department": "Ladies Shoes"}, measurements={})), True)
    check("the tariff code alone is enough", category_mapping.is_misfiled_footwear(Product(
        sku="X", master={"Category": "Lifestyle", "SubCat2": "Home Accessories",
                         "Department": "Ladies Lifestyle", "Tariff Code": "6404199000"},
        measurements={})), True)

    # What it must not catch. A genuine candle shares the Givenchy's combo.
    candle = Product(
        sku="QTN02-000-001",
        master={"Category": "Lifestyle", "SubCat2": "Home Accessories",
                "Gender": "WOMEN", "Department": "Ladies Lifestyle",
                "Tariff Code": "3406000000",
                "Clean Title Description": "DIPTYQUE BAIES SCENTED CANDLE"},
        measurements={})
    check("a genuine homeware item is left alone",
          category_mapping.is_misfiled_footwear(candle), False)

    # And anything already filed as Footwear keeps the behaviour that got 283
    # listings right on 05.09.26, including the kids boot that correctly
    # wanted the kidswear template rather than a shoes one.
    kids_boot = Product(
        sku="QTN02-002-112",
        master={"Category": "Footwear", "SubCat2": "Boots", "Gender": "GIRL",
                "Department": "Kidswear", "Tariff Code": "6404199000"},
        measurements={})
    check("a product already filed as Footwear is untouched",
          category_mapping.is_misfiled_footwear(kids_boot), False)

    # The combo path is what would drag 34 candles into Women's Shoes, so a
    # misfiled slipper has to be resolved from its own title instead.
    check("and it is resolved from its own title, not its combo",
          category_mapping._needs_its_own_answer(givenchy), True)
    check("while the candle keeps the cheap combo path",
          category_mapping._needs_its_own_answer(candle), False)

    # Template order: shoes first for the slipper, unchanged for everyone
    # else, and never a template dropped.
    class FakeTemplate:
        def __init__(self, names):
            self.categories = [CategorySpec(str(i), n, []) for i, n in enumerate(names)]

    templates = [
        FakeTemplate(["Home Décor > Other Home Décor"]),      # homeware
        FakeTemplate(["Girls > Girls' Shoes"]),               # kidswear
        FakeTemplate(["Men's Shoes > Slippers"]),             # menswear_shoes
        FakeTemplate(["Women's Clothing > Dresses"]),         # womenswear_clothing
    ]
    order = pipeline._template_order(givenchy, templates)
    check("shoe templates are offered to the slipper first", order[0] in (1, 2), True)
    check("the homeware template is not dropped, only demoted",
          sorted(order), [0, 1, 2, 3])
    check("everyone else keeps the given order",
          pipeline._template_order(candle, templates), [0, 1, 2, 3])

    # build_mapping writes under one key and lookup reads under another, so
    # if they ever disagree about a product its mapping is built and then
    # never found, and it drops out of the file with no error at all. That is
    # how 11 mules went missing. Both sides go through _needs_its_own_answer
    # for exactly this reason, and this proves lookup honours it.
    real_template = EbayTemplate(
        listing_headers=["*Action", "Custom label (SKU)"],
        categories=[CategorySpec("11505", "Men's Shoes > Slippers", [(1000, "New with box")])],
        aspects={"11505": {}},
        info_rows=[])
    fingerprint = category_mapping._template_fingerprint(real_template)

    combo_only = {category_mapping._combo_key(
        "Lifestyle", "Home Accessories", "MEN", fingerprint):
        {"category_id": "10034", "category_name": "Home Décor > Other Home Décor",
         "reasoning": "home accessories"}}
    check("a combo answer does not decide a misfiled slipper",
          category_mapping.lookup(combo_only, givenchy, real_template), None)

    per_product = dict(combo_only)
    per_product[category_mapping._product_key(givenchy.sku, fingerprint)] = {
        "category_id": "11505", "category_name": "Men's Shoes > Slippers",
        "reasoning": "a leather lounge slipper"}
    found = category_mapping.lookup(per_product, givenchy, real_template)
    check("its own answer does, and is found where build_mapping wrote it",
          found and found.get("category_id"), "11505")

    # The other half of the same trap: build_mapping has to WRITE the key
    # lookup reads. Testing lookup alone would not catch build_mapping
    # answering per combo, which loses the product just as completely.
    import tempfile
    from src import ai_client
    asked = []

    def fake_ai(system, user, tool_name, input_schema, **kwargs):
        asked.append(user)
        return {"category_id": "11505", "reasoning": "a leather lounge slipper"}

    original = ai_client.call_structured
    ai_client.call_structured = fake_ai
    try:
        with tempfile.TemporaryDirectory() as d:
            built = category_mapping.build_mapping(
                [givenchy, candle], real_template, pathlib.Path(d) / "map.json")
    finally:
        ai_client.call_structured = original

    check("build_mapping writes the slipper under its own SKU",
          category_mapping._product_key(givenchy.sku, fingerprint) in built, True)
    check("and the round trip finds it",
          (category_mapping.lookup(built, givenchy, real_template) or {}).get("category_id"),
          "11505")
    check("and the model was shown the slipper's title, which is what decides it",
          any("LOUNGE SLIPPER" in u for u in asked), True)


def test_a_misfiled_slipper_is_named_in_the_report():
    """The routing now handles these, but the Master File row is still
    self-contradictory and the fix belongs there. Named so nobody has to
    find it by reading a listing, which is how the first one was found."""
    givenchy = make_product({}, brand="Givenchy")
    givenchy.master.update({
        "Category": "Lifestyle", "SubCat2": "Home Accessories",
        "Department": "Mens Shoes", "Tariff Code": "6403599900"})
    row = good_row(**{"Category name": "Men's Shoes > Slippers"})
    said = messages(run(row, product=givenchy, size="UK 6.5"))
    check("the contradiction is named",
          any("filed as Lifestyle" in m for m in said), True)
    check("and it says where the listing actually went",
          any("Men's Shoes > Slippers" in m for m in said), True)

    ordinary = make_product({}, brand="Givenchy")
    ordinary.master.update({
        "Category": "Footwear", "SubCat2": "Flat Shoes",
        "Department": "Mens Shoes", "Tariff Code": "6403599900"})
    check("an ordinary shoe is not named",
          any("filed as" in m for m in messages(run(good_row(), product=ordinary, size="UK 8"))),
          False)


def test_every_module_imports_on_this_python():
    """Every module in src/ must import cleanly on whatever Python is running
    this suite.

    06.09.26: pipeline.py had `ProgressFn = Callable[[str, float | None],
    None]` at module level. That is a runtime expression, not an annotation,
    so `from __future__ import annotations` does not defer it, and PEP 604
    unions there need Python 3.10. It imported fine everywhere it was ever
    run — Streamlit Cloud, and the machine the fix was written on — and blew
    up on Sammy's Mac, which runs the 3.9 that ships with Apple's Command
    Line Tools. It only surfaced because a new test happened to import
    pipeline; app.py, main.py, brand_blurb and data_loader are imported by no
    test at all and would have carried the same bug silently.

    So this imports all of them, on the interpreter actually in use. It is
    the cheapest test here and it covers the whole package."""
    import importlib
    import pkgutil
    import src

    failures = []
    for module in sorted(m.name for m in pkgutil.iter_modules(src.__path__)):
        try:
            importlib.import_module(f"src.{module}")
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            failures.append(f"src/{module}.py: {type(exc).__name__}: {exc}")
    for f in failures:
        FAILURES.append(f"does not import on Python {sys.version_info.major}."
                        f"{sys.version_info.minor}: {f}")
    check("every module in src/ imports", failures, [])


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
