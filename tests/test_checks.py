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


def test_a_collaboration_title_is_named_in_the_report():
    """Twelve titles in the 06.09.26 upload named a second brand and exactly
    one was refused. The known-refused brands are stripped automatically, but
    the list can only grow by evidence, so every collaboration title is named
    in the report — when eBay refuses the next one, the report says which
    title to look at and which brand to add."""
    def collab_notes(issues):
        return [i for i in issues if (i.group or "").startswith("Collaboration titles")]

    row = good_row(Title="MM6 MAISON MARGIELA X Salomon XT-4 Mule Blue UK 3.5 RRP 295")
    notes = collab_notes(run(row, size="UK 3.5"))
    check("the collaboration is named", len(notes), 1)
    # Guarded, so a missing note fails as a named check rather than an
    # IndexError traceback that says nothing about which rule broke.
    check("and the note carries the title",
          bool(notes) and "MM6 MAISON MARGIELA X Salomon" in notes[0].message, True)
    check("as a NOTE, not something needing action",
          notes[0].kind if notes else None, "NOTE")

    plain = good_row(Title="AEYDE Womens Agata Suede Ankle Boot Black EU 39 RRP 545")
    check("an ordinary title is not", collab_notes(run(plain, size="EU 39")), [])


def test_condition_description_is_dropped_for_new_with_box():
    """eBay ignores ConditionDescription on condition 1000 and warns for
    every row that sends one: 32 of 282 on 06.09.26, a third of the warnings
    in the results file, which makes a real one harder to spot. The text is
    already a "Condition:" line in the description body, so nothing is lost.

    1500 and 1750 are also "new" in plain English but accept a description
    and did not warn, so they must keep sending one."""
    from src import build

    check("1000 is the only condition that drops it", build.CONDITION_NO_DESCRIPTION, 1000)

    def sent_for(condition_id):
        product = make_product({"Size": "8"}, brand="SIMONE ROCHA")
        product.master.update({"Rounded RRP": 695, "Colour": "White", "Season": "AW25",
                               "Category": "Ready to Wear", "Gender": "WOMEN"})
        ai_result = {"title": "SIMONE ROCHA Womens Tiered Mini Skirt White 8 RRP 695",
                     "condition_id": condition_id,
                     "condition_description": "Comes without its box.",
                     "material_summary": "Silk", "item_specifics": {}}
        row = build.build_row(product, ai_result, CATEGORY, make_template(), {},
                              price_percent=50.0)
        return row.get("ConditionDescription")

    check("1000 sends nothing", sent_for(1000), None)
    # 1500 and 1750 are also "new" in plain English but accept a description
    # and did not warn, so they must keep sending one.
    for condition_id in (1500, 1750, 2990, 3000, 3010):
        check(f"condition {condition_id} still sends one",
              sent_for(condition_id), "Comes without its box.")


def test_internal_references_never_reach_a_buyer():
    """Sammy, 06.09.26: no stockist names, and no QTNDAM codes.

    35 live listings said "QTNDAM2" and 23 said "internal quality grade"
    before this existed, because the raw code was being handed to the model
    in the prompt. Two different treatments, and the difference matters:
    a stockist name takes its whole sentence, a grading reference is
    rewritten so the buyer keeps the warning."""
    from src import aspect_matching as am

    real = ("Brand new and unworn, however the internal quality grade indicates minor "
            "cosmetic imperfections (QTNDAM2) that fall outside standard \"new\" grading. "
            "Please review the photos.")
    out = am.scrub_internal_references(real)
    check("the code goes", "QTNDAM" in out, False)
    check("the jargon goes", "internal quality grade" in out.lower(), False)
    check("the warning stays", "minor cosmetic imperfections" in out, True)
    check("and so does the instruction to look at the photos",
          "review the photos" in out.lower(), True)
    check("no stray brackets", "()" in out, False)
    check("no double spaces", "  " in out, False)

    # A stockist takes its sentence, because "originally from Browns" cannot
    # be neutralised a word at a time.
    check("a stockist sentence goes whole",
          am.scrub_internal_references(
              "Preloved pair originally from Browns. Some dirt on the sole."),
          "Some dirt on the sole.")
    for name in ("Farfetch", "Selfridges", "Harrods", "Net-a-Porter", "SSENSE",
                 "Mytheresa", "MatchesFashion", "Harvey Nichols", "department store"):
        text = f"Bought from {name} last season. Excellent condition."
        check(f"{name} removed", am.scrub_internal_references(text), "Excellent condition.")

    # The colour brown must survive. It appears in a third of these listings,
    # and "Browns" is only the stockist in the plural.
    for keep in ["Brown calf leather with light wear.",
                 "Dark brown suede, gently worn.",
                 "Gently worn with light scuffing to the sole."]:
        check(f"kept: {keep[:22]}", am.scrub_internal_references(keep), keep)

    # A rewrite landing at a sentence start is re-capitalised, but only the
    # words the scrubber itself introduces, so "eBay" is never touched.
    check("a rewritten sentence start is capitalised",
          am.scrub_internal_references(
              "Brand new. Internal quality grade notes a minor flaw."),
          "Brand new. Our inspection notes a minor flaw.")
    check("but an intentional lower-case word is not",
          "eBay" in am.scrub_internal_references("Never listed. eBay authenticated."), True)

    # The prompt itself must never carry the code. This is the root cause:
    # src/content_generator.py line 422 handed the model "Internal quality
    # grade: QTNDAM2" and it wrote about it 35 times.
    from src import content_generator
    graded = make_product({"Description": "Slight mark on the heel."})
    graded.master.update({"Quality": "QTNDAM2", "Rounded RRP": 695,
                          "Category": "Footwear", "SubCat2": "Flat Shoes"})
    brief = content_generator._product_brief(graded)
    check("the prompt does not contain the code", "QTNDAM" in brief.upper(), False)
    check("nor the words internal quality grade",
          "internal quality grade" in brief.lower(), False)
    check("but it does still flag that something was found",
          "imperfection" in brief.lower(), True)
    clean = make_product({"Description": "Good condition."})
    clean.master.update({"Quality": "", "Rounded RRP": 695})
    check("and says none when nothing was flagged",
          "flag: none" in content_generator._product_brief(clean).lower(), True)

    # End to end, because the prompt is not a guarantee. This is the same
    # shape as the brand casing and the size: asked for in the prompt,
    # enforced in Python.
    import tempfile
    from src import ai_client

    def dirty_ai(system, user, tool_name, input_schema, **kwargs):
        return {"title": "SIMONE ROCHA Womens Tiered Mini Skirt White 8 RRP 695",
                "condition_id": 3000,
                "condition_description": ("Sourced from Farfetch. Our internal quality "
                                          "grade (QTNDAM2) notes a small mark."),
                "material_summary": "Silk", "item_specifics": {}}

    original = ai_client.call_structured
    ai_client.call_structured = dirty_ai
    try:
        with tempfile.TemporaryDirectory() as d:
            generated = content_generator.generate_for_product(
                graded, CATEGORY, make_template(), d, force=True)
    finally:
        ai_client.call_structured = original
    said = generated.get("condition_description", "")
    check("end-to-end: no code reaches the listing", "QTNDAM" in said.upper(), False)
    check("end-to-end: no stockist either", "Farfetch" in said, False)
    check("end-to-end: no grading jargon",
          "internal quality grad" in said.lower(), False)
    check("end-to-end: and the mark is still declared", "small mark" in said, True)

    # The brand blurb is generated and cached separately, so it never passes
    # through the content generator. build_description is the net for it.
    from src import build
    blurbs = {"SIMONE ROCHA": "A Farfetch favourite for years. Known for tulle and pearls."}
    text = build.build_description(
        graded, {"condition_description": "Good condition.", "material_summary": "Silk",
                 "item_specifics": {}},
        CATEGORY, make_template(), blurbs)
    check("a stockist in the brand blurb is caught too", "Farfetch" in text, False)
    check("and the rest of the blurb survives", "tulle and pearls" in text, True)

    # And the report catches anything the scrubber missed.
    issues = run(good_row(ConditionDescription="Graded QTNDAM2 on arrival."), size="8")
    check("a surviving code is a REVIEW",
          any(i.kind == "REVIEW" and "internal grading" in i.message for i in issues), True)
    issues = run(good_row(Description="Sourced from Farfetch."), size="8")
    check("a surviving stockist is a REVIEW",
          any(i.kind == "REVIEW" and "stockist" in i.message for i in issues), True)
    check("a clean listing still says nothing", run(good_row(), size="8"), [])


def test_the_app_follows_the_brand_guidelines():
    """Brand guidelines Version 02, August 2026, plus Sammy's two renames on
    06.09.26.

    The visual layer is not otherwise covered by anything, and the file
    labels in particular are the sort of thing that gets half-renamed: the
    uploader says one name and the error message underneath still says the
    old one."""
    import ast
    import base64

    from src import branding

    check("white is the canvas", branding.WHITE, "#FFFFFF")
    check("black is the detail", branding.BLACK, "#000000")
    check("Helvetica Neue leads the stack",
          branding.FONT_STACK.startswith("'Helvetica Neue'"), True)
    check("with Aileron named as the open-license fallback",
          "Aileron" in branding.FONT_STACK, True)

    logo = base64.b64decode(branding.LOGO_PNG_BASE64)
    check("the logo is a real PNG", logo[:8], b"\x89PNG\r\n\x1a\n")
    check("and the data URI is well formed",
          branding.logo_data_uri().startswith("data:image/png;base64,iVBOR"), True)

    app = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    ast.parse(app)  # a syntax error here takes the whole app down

    # Both renames, everywhere they appear. The uploader label and the error
    # message shown when the file is missing are different strings, and a
    # half-done rename leaves them disagreeing.
    for old in ("Master File(s)", "Pictures & Measurements"):
        if old in app:
            FAILURES.append("app.py still says " + repr(old))
    for new_name in ("Stock Data File", "Orbitvu file"):
        check("app.py says " + repr(new_name), new_name in app, True)
    check("the uploader and the error message agree on Stock Data File",
          app.count("Stock Data File") >= 2, True)
    check("and on Orbitvu file", app.count("Orbitvu file") >= 2, True)

    # Colours come from the branding module, not hard-coded into the CSS.
    check("the app pulls its colours from branding",
          "branding.MATRIX" in app and "branding.BLACK" in app, True)
    check("and its logo too", "branding.logo_data_uri()" in app, True)


def test_ebays_own_cardinality_wins():
    """eBay error 21919309, 06.09.26: "Occasion should contain only one
    value." One Women's Sandals listing refused, while four Women's Heels
    listings in the same upload took two Occasions happily.

    eBay's answer really is per category, and the templates carry it. The
    bug was that MULTI_SELECT_ASPECTS overrode that answer instead of
    filling in for a template that has none, which is what its own comment
    said it was for."""
    from src import content_generator as cg
    from src.ebay_template import AspectSpec

    ebay_says_single = AspectSpec("C:Occasion", "OPTIONAL", ["Casual", "Formal"], multi=False)
    ebay_says_multi = AspectSpec("C:Occasion", "OPTIONAL", ["Casual", "Formal"], multi=True)
    ebay_never_said = AspectSpec("C:Occasion", "OPTIONAL", ["Casual", "Formal"])

    check("eBay saying single wins over the hand list",
          cg._is_multi_select("C:Occasion", ebay_says_single), False)
    check("eBay saying multi is honoured",
          cg._is_multi_select("C:Occasion", ebay_says_multi), True)
    check("the hand list still fills the gap for an xlsx template",
          cg._is_multi_select("C:Occasion", ebay_never_said), True)
    check("and an unknown aspect with no answer stays single",
          cg._is_multi_select("C:Pattern", ebay_never_said), False)

    # The real templates, the real categories, the real failure.
    from src import ebay_template
    templates = Path(__file__).resolve().parent.parent / "data" / "templates"
    if (templates / "womenswear_shoes.json").exists():
        t = ebay_template.load_template(templates / "womenswear_shoes.json")
        sandals = t.aspects["62107"]["C:Occasion"]
        heels = t.aspects["55793"]["C:Occasion"]
        check("Women's Sandals takes one Occasion",
              cg._is_multi_select("C:Occasion", sandals), False)
        check("Women's Heels takes several",
              cg._is_multi_select("C:Occasion", heels), True)


def test_a_kids_listing_gets_a_department():
    """eBay error 21919303, 06.09.26: "The item specific Department is
    missing." Department is Required in every kids shoe category and the
    gender map had no kids entries at all, so a Moon Boot Kids crib boot
    went up with the field empty and was refused."""
    from src import aspect_matching as am

    kids = ["Girls", "Unisex Kids"]
    boys = ["Boys", "Unisex Kids"]
    check("GIRL maps to Girls", am.match_department("GIRL", kids), "Girls")
    check("BOY maps to Boys", am.match_department("BOY", boys), "Boys")
    check("KIDS maps to Unisex Kids", am.match_department("KIDS", kids), "Unisex Kids")
    check("and the plural spellings too", am.match_department("GIRLS", kids), "Girls")

    # A kids gender must never be forced into an adult list, and vice versa.
    adults = ["Women", "Men", "Unisex Adults"]
    check("GIRL is not a Women's department", am.match_department("GIRL", adults), None)
    check("WOMEN is not a kids department", am.match_department("WOMEN", kids), None)
    check("the adult mappings are untouched",
          [am.match_department(g, adults) for g in ("WOMEN", "MEN", "UNISEX")],
          ["Women", "Men", "Unisex Adults"])


def _load_template(name):
    from src import ebay_template
    path = pathlib.Path(__file__).resolve().parent.parent / "data" / "templates" / name
    if not path.exists():
        return None
    return ebay_template.load_template(path)


def test_an_adult_product_can_never_be_listed_in_a_kids_category():
    """07.09.26. Seven men's sneakers — LANVIN, AMIRI, OUR LEGACY, three RICK
    OWENS DRKSHDW and a MIHARAYASUHIRO — came out of the app as
    "Boys > Boys' Shoes" (57929), £795 designer trainers pointing at the
    children's section.

    Templates are offered alphabetically and kidswear comes before
    menswear_shoes, so ("Footwear", "Sneakers", "MEN") was put to the model
    against the kids list. On the 295-row batch it answered NONE; on a
    rebuilt cache it answered Boys' Shoes. Nothing checked the answer.

    So the check is deterministic and lives on both sides of the cache:
    the candidate list the model sees is filtered first, and anything read
    back out of a cache written before this rule existed is rejected on the
    way out."""
    from src import category_mapping

    check("a men's product is not a kids product",
          category_mapping.gender_conflict("MEN", "Boys > Boys' Shoes"), True)
    check("nor a women's", category_mapping.gender_conflict("WOMEN", "Girls > Girls' Shoes"), True)
    check("nor a unisex adult one",
          category_mapping.gender_conflict("UNISEX", "Boys' Clothing (2-16 Years) > Jeans"), True)
    check("and a kids product is not an adult one",
          category_mapping.gender_conflict("GIRL", "Women's Shoes > Boots"), True)
    check("nor a men's", category_mapping.gender_conflict("BOYS", "Men's Shoes > Trainers"), True)

    # The crossings that must still be allowed, or real listings vanish.
    for gender, name in [
        ("MEN", "Men's Shoes > Trainers"),
        ("WOMEN", "Women's Shoes > Heels"),
        ("UNISEX", "Men's Shoes > Trainers"),
        ("UNISEX", "Women's Shoes > Trainers"),
        ("KIDS", "Boys > Boys' Shoes"),
        ("GIRL", "Girls > Girls' Shoes"),
        ("UNISEX KIDS", "Girls > Girls' Shoes"),
        # Not blocked here on purpose: this account's templates force some
        # men/women crossings (a woman's cufflinks have nowhere else to go),
        # so that case is reported by validation instead of dropped.
        ("WOMEN", "Men's Jewellery > Cufflinks"),
        ("MEN", "Women's Shoes > Trainers"),
        # No signal either way must never block.
        ("MEN", "Home Décor > Other Home Décor"),
        ("", "Boys > Boys' Shoes"),
        ("MEN", ""),
    ]:
        check(f"{gender!r} in {name!r} is allowed",
              category_mapping.gender_conflict(gender, name), False)

    # "Women's" must not read as "men's". The word boundary does the work,
    # but it is the kind of thing a later edit breaks silently.
    check("Women's Accessories is a women's category",
          category_mapping.category_audience("Women's Accessories > Belts"), "women")
    check("Men's Accessories is a men's category",
          category_mapping.category_audience("Men's Accessories > Belts"), "men")


def test_a_kids_category_is_never_even_offered_to_an_adult():
    """The guard above is the backstop. This is the part that stops the wrong
    answer existing: the candidate list handed to the model is filtered by
    age group before it is written, so "Boys' Shoes" is not on the menu for
    a men's sneaker at all."""
    from src import category_mapping

    kidswear = _load_template("kidswear.json")
    if kidswear is None:
        print("  (skipped: data/templates not present in this checkout)")
        return

    check("a men's product is offered nothing at all from kidswear",
          category_mapping.eligible_categories("MEN", kidswear), [])
    check("nor a women's", category_mapping.eligible_categories("WOMEN", kidswear), [])
    check("a kids product is offered the whole of it",
          len(category_mapping.eligible_categories("GIRL", kidswear)),
          len(kidswear.categories))

    menswear = _load_template("menswear_shoes.json")
    check("and menswear_shoes is untouched for a men's product",
          len(category_mapping.eligible_categories("MEN", menswear)),
          len(menswear.categories))
    check("but closed to a kids product",
          category_mapping.eligible_categories("BOY", menswear), [])


def test_a_templates_unnamed_categories_inherit_its_age_group():
    """kidswear.json carries thirteen "Activewear > ..." categories whose
    names say nothing about who they are for. A name-only check would let a
    men's tracksuit into "Activewear > Tracksuits & Sets" (260973) and never
    notice. Every category in that file that says anything says Boys or
    Girls, so the file itself settles it."""
    from src import category_mapping

    kidswear = _load_template("kidswear.json")
    if kidswear is None:
        print("  (skipped: data/templates not present in this checkout)")
        return

    check("the kidswear template as a whole is for kids",
          category_mapping.template_audience(kidswear), "kids")
    check("an unnamed kids category still blocks an adult",
          category_mapping.gender_conflict("MEN", "Activewear > Tracksuits & Sets", kidswear),
          True)
    check("and the same category name in the menswear template does not",
          category_mapping.gender_conflict(
              "MEN", "Activewear > Tracksuits & Sets", _load_template("menswear_clothing.json")),
          False)

    # A mixed template must give no signal — jewellery_watches has both
    # Men's Jewellery and Children's Jewellery, so inheriting from it would
    # be a guess, and a guess here drops listings.
    jewellery = _load_template("jewellery_watches.json")
    check("a mixed template says nothing", category_mapping.template_audience(jewellery), None)
    check("a neutral one says nothing either",
          category_mapping.template_audience(_load_template("homeware.json")), None)


def test_the_model_is_never_shown_a_category_it_must_not_choose():
    """The filter is only worth anything if build_mapping actually applies
    it. Testing eligible_categories on its own leaves the call site free to
    stop using it — which is how strip_blocked_brand quietly stopped being
    called on 06.09.26 with every unit test still green. So this one runs
    build_mapping with the AI stubbed and reads the prompt it would have
    sent."""
    import tempfile
    from src import ai_client, category_mapping

    kidswear = _load_template("kidswear.json")
    jewellery = _load_template("jewellery_watches.json")
    if kidswear is None or jewellery is None:
        print("  (skipped: data/templates not present in this checkout)")
        return

    mens = Product(
        sku="QTN02-001-922",
        master={"Brand": "LANVIN", "Gender": "MEN", "Category": "Footwear",
                "SubCat2": "Sneakers", "Clean Title Description": "LANVIN CURB SNEAKER"},
        measurements={"Size": "EU 44"},
    )

    prompts = []

    def fake_ai(system, user, tool_name, input_schema, **kwargs):
        prompts.append(user)
        return {"category_id": "57929", "reasoning": "stub"}

    original = ai_client.call_structured
    ai_client.call_structured = fake_ai
    try:
        with tempfile.TemporaryDirectory() as d:
            cache = category_mapping.build_mapping(
                [mens], kidswear, pathlib.Path(d) / "c.json")
            check("no API call is made when nothing in the template is allowed",
                  prompts, [])
            entry = category_mapping.lookup(cache, mens, kidswear)
            check("and the combo is recorded as a miss", entry, None)

            prompts.clear()
            category_mapping.build_mapping([mens], jewellery, pathlib.Path(d) / "j.json")
            check("a partly-allowed template is still asked", len(prompts), 1)
            check("but the kids categories are not in the prompt",
                  "Children's Jewellery" in prompts[0], False)
            check("and the men's ones still are",
                  "Men's Jewellery" in prompts[0], True)
    finally:
        ai_client.call_structured = original


def test_a_poisoned_category_cache_cannot_ship_a_kids_listing():
    """The bug did not come from the code, it came from a cache: the same
    combo answered NONE on 05.09.26 and "Boys' Shoes" on 07.09.26. Filtering
    the candidate list fixes new answers; it does nothing about the
    category_mapping_N.json already sitting in the cache directory. So the
    guard is applied on the way out too, and lookup returning None is
    exactly what sends the product on to the next template."""
    from src import category_mapping

    kidswear = _load_template("kidswear.json")
    if kidswear is None:
        print("  (skipped: data/templates not present in this checkout)")
        return

    product = Product(
        sku="QTN02-001-922",
        master={"Brand": "LANVIN", "Gender": "MEN", "Category": "Footwear",
                "SubCat2": "Sneakers", "Clean Title Description": "LANVIN CURB SNEAKER"},
        measurements={"Size": "EU 44"},
    )
    fp = category_mapping._template_fingerprint(kidswear)
    poisoned = {
        category_mapping._combo_key("Footwear", "Sneakers", "MEN", fp): {
            "category_id": "57929",
            "category_name": "Boys > Boys' Shoes",
            "reasoning": "what the model actually answered on 07.09.26",
        }
    }
    check("the cached kids answer is refused",
          category_mapping.lookup(poisoned, product, kidswear), None)

    # And the same cache still works for a product it is actually right for.
    kid = Product(
        sku="QTN02-000-001",
        master={"Brand": "MOON BOOT", "Gender": "KIDS", "Category": "Footwear",
                "SubCat2": "Sneakers", "Clean Title Description": "MOON BOOT ICON JUNIOR"},
        measurements={"Size": "EU 30"},
    )
    kid_cache = {
        category_mapping._combo_key("Footwear", "Sneakers", "KIDS", fp): {
            "category_id": "57929", "category_name": "Boys > Boys' Shoes", "reasoning": ""}
    }
    entry = category_mapping.lookup(kid_cache, kid, kidswear)
    check("a kids product still gets the kids category",
          entry and entry.get("category_id"), "57929")


def test_kidswear_is_offered_last():
    """Sammy, 07.09.26: "we mainly sell mens and womens so these departments
    should come before kids".

    The first template whose mapping returns a match wins, and the defaults
    were offered in plain alphabetical order — which put kidswear ahead of
    menswear_shoes and womenswear_shoes, and is how a men's sneaker came to
    be asked against the kids category list at all."""
    from src import pipeline

    names = [p.name for p in pipeline._default_department_templates()]
    if not names:
        print("  (skipped: data/templates not present in this checkout)")
        return

    check("kidswear is last", names[-1], "kidswear.json")
    for adult in ("menswear_shoes.json", "womenswear_shoes.json",
                  "menswear_clothing.json", "womenswear_clothing.json",
                  "menswear_accessories.json", "womenswear_accessories.json"):
        check(f"{adult} comes before kidswear", names.index(adult) < names.index("kidswear.json"), True)

    # Nothing else is reordered. Moving kidswear is the whole change; a
    # broader reshuffle would quietly move hair clips out of Costume
    # Jewellery and into Women's Accessories, which nobody asked for.
    others = [n for n in names if n != "kidswear.json"]
    check("every other template keeps its alphabetical order", others, sorted(others))
    check("and none of them is missing", len(names), 9)


def test_a_mens_item_in_a_womens_category_is_reported():
    """The half that is deliberately not blocked. Reported so a person
    decides, because this account's templates force some crossings and a
    silent drop would be worse than a listing in the wrong aisle."""
    mens = Product(sku="TEST-001", master={"Brand": "SIMONE ROCHA", "Gender": "MEN"},
                   measurements={})
    issues = run(good_row(**{"Category name": "Women's Shoes > Trainers"}), product=mens, size="8")
    check("the crossing is reported",
          any("listed in" in m for m in messages(issues, "REVIEW")), True)

    ok = run(good_row(**{"Category name": "Men's Shoes > Trainers"}), product=mens, size="8")
    check("the right category is silent", messages(ok, "REVIEW"), [])
    womens = run(good_row(**{"Category name": "Women's Shoes > Heels"}), size="8")
    check("and a women's item in a women's category is silent",
          messages(womens, "REVIEW"), [])
    # No category name, no opinion.
    check("and a row with no category name is silent",
          messages(run(good_row(), size="8"), "REVIEW"), [])


def test_a_row_ebay_would_refuse_never_reaches_the_upload_file():
    """07.09.26, the second half of it. Seven men's sneakers went out with an
    empty Department and Type. Every one was flagged REVIEW in the checks
    report, the report was scrolled past on a run that otherwise looked
    clean, and eBay refused all seven an hour later with 21919303.

    The information was there. It just was not in the way. So a row eBay will
    refuse is now kept OUT of the upload file entirely and written to a
    "NEEDS ATTENTION" file with the reason beside it, which makes the file
    the app hands over a file that uploads clean.

    Run through pipeline.run rather than validation, because the check
    marking a row blocking is worth nothing if the pipeline still writes it.
    That is the exact mutation that got strip_blocked_brand on 06.09.26."""
    import csv as _csv
    import tempfile
    from src import ai_client, brand_blurb, data_loader, pipeline

    templates_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "templates"
    if not (templates_dir / "menswear_shoes.json").exists():
        print("  (skipped: data/templates not present in this checkout)")
        return

    def make(sku, images):
        return Product(
            sku=sku,
            master={"Brand": "ROA", "Gender": "MEN", "Colour": "Brown",
                    "Category": "Footwear", "SubCat2": "Boots", "Rounded RRP": 395,
                    "Country of Origin": "ITA", "Clean Title Description": "ROA HIKING BOOT"},
            measurements={"Size": "EU 45", "Description": "Good condition.",
                          "Images 2D link": images},
        )

    good = make("GOOD-001", "http://a|http://b|http://c")
    bad = make("BAD-001", "")   # no photos: eBay refuses this outright

    def fake_ai(system, user, tool_name, input_schema, **kwargs):
        if tool_name == "pick_category":
            return {"category_id": "11498", "reasoning": "stub"}
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
            "title": "ROA Hiking Boots Brown Suede EU 45 RRP 395",
            "condition_id": 3000,
            "condition_description": "Good condition.",
            "material_summary": "Suede",
            "item_specifics": specifics,
        }

    originals = (ai_client.call_structured, data_loader.load_products, brand_blurb.build_blurbs)
    ai_client.call_structured = fake_ai
    data_loader.load_products = lambda *a, **k: [good, bad]
    brand_blurb.build_blurbs = lambda brands, cache_dir: {b: "" for b in brands}
    try:
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "Upload.csv"
            results, considered, uncovered, failed, held = pipeline.run(
                master_path="unused", measurements_path="unused",
                template_path=[str(templates_dir / "menswear_shoes.json")],
                output_path=out, cache_dir=d, force_regenerate=True,
            )

            check("the bad row is held back", [s for s in held.skus], ["BAD-001"])
            check("with a reason a person can act on",
                  any("photo" in r for _sku, rs in held.reasons for r in rs), True)

            rows = [tr.rows for tr in results]
            written = [r["Custom label (SKU)"] for group in rows for r in group]
            check("only the good row is in the upload file", written, ["GOOD-001"])

            # Not just the in-memory rows — the file on disk, which is what
            # actually gets uploaded.
            lines = out.read_text(encoding="utf-8-sig").splitlines()
            hi = next(i for i, l in enumerate(lines) if l.startswith("*Action"))
            on_disk = [r["Custom label (SKU)"] for r in _csv.DictReader(lines[hi:])]
            check("and the file on disk agrees", on_disk, ["GOOD-001"])

            held_file = pathlib.Path(held.path)
            check("the needs-attention file exists", held_file.exists(), True)
            hlines = held_file.read_text(encoding="utf-8-sig").splitlines()
            hhi = next(i for i, l in enumerate(hlines) if l.startswith("*Action"))
            hrows = list(_csv.DictReader(hlines[hhi:]))
            check("it holds the refused row", [r["Custom label (SKU)"] for r in hrows], ["BAD-001"])
            check("with the reason in its own column",
                  "photo" in hrows[0]["WHY THIS ROW IS HELD BACK"], True)

            # The report has to say it too, in the counts at the top, since
            # that is the part people actually read.
            report = out.with_name(out.stem + "_checks.txt").read_text()
            check("the report names it in the counts", "1 held back" in report, True)
            check("and lists the SKU", "BAD-001" in report, True)
    finally:
        (ai_client.call_structured, data_loader.load_products,
         brand_blurb.build_blurbs) = originals


def test_only_the_problems_ebay_actually_refuses_are_blocking():
    """A guard that holds back rows for things eBay would have accepted is
    worse than no guard: it turns a clean run into a pile of files to
    reconcile, and the next real one gets ignored with the rest.

    So the blocking set is exactly the failures that have been refused, with
    the error code in each check's docstring. Everything else is still
    reported and still ships."""
    aspects = {"C:Brand": AspectSpec("C:Brand", "REQUIRED", None)}

    # Blocking: eBay refuses each of these outright.
    for label, row, aspect_set in [
        ("missing REQUIRED aspect (21919303)", good_row(), aspects),
        ("no title", good_row(Title=""), None),
        ("no photos", good_row(**{"Item photo URL": ""}), None),
        ("zero price", good_row(**{"Start price": 0}), None),
    ]:
        issues = run(row, aspects=aspect_set, size="8")
        check(f"{label} blocks the row", bool(validation.blocking_reasons(issues)), True)

    # Reported, not blocked. Each of these has shipped and listed fine.
    for label, row, product in [
        ("only two photos", good_row(**{"Item photo URL": "http://a|http://b"}), None),
        ("title missing the brand", good_row(Title="Tiered Mini Skirt White 8 RRP 695"), None),
        ("a men's item in a women's category",
         good_row(**{"Category name": "Women's Shoes > Trainers"}),
         Product(sku="TEST-001", master={"Brand": "SIMONE ROCHA", "Gender": "MEN"}, measurements={})),
        ("a contradiction between Style and Length",
         good_row(**{"C:Skirt Length": "Maxi"}), None),
    ]:
        issues = run(row, product=product, size="8")
        check(f"{label} does not block the row", validation.blocking_reasons(issues), [])

    # And a clean row blocks nothing, which is the case that matters most.
    check("a clean listing blocks nothing", validation.blocking_reasons(run(good_row(), size="8")), [])


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
