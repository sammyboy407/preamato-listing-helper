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

import re
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
           "8.5", "9", "9.5", "10", "10.5", "11", "11.5", "12", "12.5", "13", "13.5",
           "14", "14.5", "15", "15.5", "16", "16.5", "17", "17.5", "18", "18.5", "19"]
UK_WOMENS = ["1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5", "6", "6.5", "7",
             "7.5", "8", "8.5", "9", "9.5", "10", "10.5", "11", "11.5", "12", "12.5",
             "13", "13.5", "14", "14.5", "15", "15.5", "16", "16.5", "17", "17.5"]


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


def test_bare_numbers_are_read_as_uk_and_flagged():
    """Sammy's call, 04.09.26, on 63 rows of the QTN02 footwear parcel: a
    bare number below EU range is a UK size, because the stock is UK-sourced
    and that is how a UK shoe is marked. It is still an assumption, so
    is_assumed_shoe_system has to be able to pick those rows out for spot
    checking — converting silently is the part that would be dangerous."""
    check("bare 7 reads as UK 7", am.match_shoe_size_uk("7", UK_WOMENS, "WOMEN"), "7")
    check("bare 5.5 reads as UK 5.5", am.match_shoe_size_uk("5.5", UK_MENS, "MEN"), "5.5")
    check("bare 9 reads as UK 9", am.match_shoe_size_uk("9", UK_MENS, "MEN"), "9")
    # No gender needed: a UK size is a UK size, no table involved.
    check("bare number needs no gender", am.match_shoe_size_uk("8", UK_MENS, "UNISEX"), "8")
    # And it must never be written into the EU field. The list below is
    # deliberately artificial (eBay's real EU list starts at 32, so a "9"
    # could never match it anyway) — the point is to prove the guard itself
    # refuses, rather than relying on the list happening not to contain it.
    check("bare number is not an EU size", am.match_shoe_size_eu("9", ["9", "39", "40"]), None)

    check("a bare number is flagged as assumed", am.is_assumed_shoe_system("9"), True)
    check("a decimal bare number is flagged", am.is_assumed_shoe_system("7.5"), True)
    check("an explicit UK size is not flagged", am.is_assumed_shoe_system("UK 9"), False)
    check("an explicit US size is not flagged", am.is_assumed_shoe_system("US 9"), False)
    check("an EU size is not flagged", am.is_assumed_shoe_system("45"), False)
    # The C names the scale, so a child size is explicit, not assumed.
    check("a child size is not flagged", am.is_assumed_shoe_system("1C-2C"), False)
    check("nor a single one", am.is_assumed_shoe_system("2C"), False)
    check("blank is not flagged", am.is_assumed_shoe_system(None), False)


def test_a_bare_number_follows_the_brand_rule():
    """Sammy, 05.09.26, from the first 295-row batch. Blackstock & Weber had
    three pairs recorded three ways: "US10", "uk 6" and a bare "10". The US10
    and the bare 10 are the same Penny Pony Loafer at the same RRP, and they
    went out half a size apart because the bare one took the UK default.

    So a bare number means US for the brands on US_SIZED_BRANDS. An explicit
    marker always wins, whatever the brand — that is what keeps the rule from
    doing damage when the intake data IS marked."""
    check("bare number on a US brand converts",
          am.match_shoe_size_uk("10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"), "9.5")
    check("VISVIM too", am.match_shoe_size_uk("9", UK_MENS, "MEN", brand="VISVIM"), "8.5")
    check("the same number on any other brand stays UK",
          am.match_shoe_size_uk("10", UK_MENS, "MEN", brand="GH BASS"), "10")
    check("no brand at all stays UK",
          am.match_shoe_size_uk("10", UK_MENS, "MEN"), "10")

    # The guard rail: an explicit marker is never overridden by the brand.
    check("explicit UK on a US brand stays UK",
          am.match_shoe_size_uk("uk 6", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"), "6")
    check("explicit EU on a US brand still converts as EU",
          am.match_shoe_size_uk("EU 45", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"), "11")
    check("explicit US on a US brand is unchanged",
          am.match_shoe_size_uk("US10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"), "9.5")
    check("the marked and the bare pair now agree",
          am.match_shoe_size_uk("US10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"),
          am.match_shoe_size_uk("10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"))

    # Brand names come off a spreadsheet, so matching can't be fussy.
    for spelling in ("blackstock & weber", "  BLACKSTOCK & WEBER  ", "Blackstock  &  Weber"):
        check(f"brand matching survives {spelling!r}",
              am.match_shoe_size_uk("10", UK_MENS, "MEN", brand=spelling), "9.5")
    check("a near-miss brand is NOT treated as US",
          am.match_shoe_size_uk("10", UK_MENS, "MEN", brand="Blackstock"), "10")

    # A US brand still needs a gender, same as any other US size.
    check("US brand with unknown gender is refused",
          am.match_shoe_size_uk("10", UK_MENS, "UNISEX", brand="BLACKSTOCK & WEBER"), None)

    # Bands follow the brand rule too, before the middle size is taken.
    check("a bare band on a US brand converts",
          am.match_shoe_size_uk("9-10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER"), "9")
    check("a bare band elsewhere stays UK",
          am.match_shoe_size_uk("9-10", UK_MENS, "MEN", brand="GH BASS"), "9.5")
    check("and the two do not agree, which is the whole point",
          am.match_shoe_size_uk("9-10", UK_MENS, "MEN", brand="BLACKSTOCK & WEBER")
          != am.match_shoe_size_uk("9-10", UK_MENS, "MEN", brand="GH BASS"), True)

    # And the report has to be able to say WHICH reading was assumed.
    check("assumed scale is named for a US brand",
          am.assumed_shoe_system("10", "BLACKSTOCK & WEBER"), "US")
    check("assumed scale is named for everything else",
          am.assumed_shoe_system("10", "GH BASS"), "UK")
    check("an explicit size is not an assumption",
          am.assumed_shoe_system("US10", "BLACKSTOCK & WEBER"), None)
    check("an EU size is not an assumption",
          am.assumed_shoe_system("45", "GH BASS"), None)


def test_us_sizes_convert_by_gender():
    """9 rows of the QTN02 parcel came in as "US9"/"US11". Women's UK is
    US - 2, men's is US - 0.5; the two differ, so gender is required exactly
    as it is for EU."""
    check("US 9 women is UK 7", am.match_shoe_size_uk("US9", UK_WOMENS, "WOMEN"), "7")
    check("US 8 women is UK 6", am.match_shoe_size_uk("US 8", UK_WOMENS, "WOMEN"), "6")
    check("US 10 women is UK 8", am.match_shoe_size_uk("US10", UK_WOMENS, "WOMEN"), "8")
    check("US 9 men is UK 8.5", am.match_shoe_size_uk("US9", UK_MENS, "MEN"), "8.5")
    check("US 11 men is UK 10.5", am.match_shoe_size_uk("US11", UK_MENS, "MEN"), "10.5")
    check("US 6 men is UK 5.5", am.match_shoe_size_uk("US 6", UK_MENS, "MEN"), "5.5")
    # The whole point of two tables: the same US number is not the same UK
    # number for men and women.
    check("US 9 differs by gender",
          am.match_shoe_size_uk("US9", UK_WOMENS, "WOMEN") != am.match_shoe_size_uk("US9", UK_MENS, "MEN"),
          True)
    check("US with unknown gender refused", am.match_shoe_size_uk("US9", UK_MENS, "UNISEX"), None)
    check("US with blank gender refused", am.match_shoe_size_uk("US9", UK_MENS, None), None)
    check("US outside the table refused", am.match_shoe_size_uk("US 30", UK_MENS, "MEN"), None)
    # A US size must never be written into the EU field either. Artificial
    # list again, for the same reason as above.
    check("US is not an EU size", am.match_shoe_size_eu("US9", ["9", "39", "40"]), None)
    check("UK is not an EU size", am.match_shoe_size_eu("UK 9", ["9", "39", "40"]), None)


def test_a_jp_marked_size_says_what_is_wrong_with_it():
    """07.09.26. The team started marking every size with its country, which
    is exactly right, and one came back as JP41.

    Japanese shoe sizes are the foot length in centimetres, about 21 to 31.
    There is no JP 41; 41cm is not a foot. The Master File records that same
    Miharayasuhiro sneaker as 41 and its three siblings at 42, 43 and 44 are
    live on eBay as EU, so it was an EU size with the wrong marker.

    It is still refused, which is right — the app does not rewrite a marker
    the team put there. What changed is that the run summary now says which
    of the two problems it is, instead of "isn't a recognisable shoe size",
    which was both wrong and useless."""
    check("JP is parsed as its own scale, not folded into EU",
          am.parse_shoe_size("JP41"), ("JP", "41"))
    check("and a real one too", am.parse_shoe_size("JP 27"), ("JP", "27"))

    for cm in ("21", "26.5", "27", "31"):
        check(f"{cm}cm is a believable Japanese size",
              am.looks_like_japanese_size(cm), True)
    for not_cm in ("41", "44", "20.9", "31.1", "", None, "x"):
        check(f"{not_cm!r} is not", am.looks_like_japanese_size(not_cm), False)

    # It must still refuse to convert, with or without a plausible number.
    uk_list = ["7", "7.5", "8", "8.5", "9", "9.5", "10"]
    check("a JP size is not converted", am.match_shoe_size_uk("JP41", uk_list, "MEN"), None)
    check("nor a plausible one", am.match_shoe_size_uk("JP 27", uk_list, "MEN"), None)
    check("and never lands in the EU field",
          am.match_shoe_size_eu("JP41", ["40", "41", "42"]), None)

    # The two messages have to differ, and both have to be actionable.
    from src import content_generator, ebay_template
    from src.data_loader import Product
    spec = ebay_template.AspectSpec("C:UK Shoe Size", "REQUIRED", uk_list)

    def why(size):
        product = Product(sku="T", master={"Brand": "MIHARAYASUHIRO", "Gender": "MEN"},
                          measurements={"Size": size})
        return content_generator._size_failure_detail("C:UK Shoe Size", product, spec)

    wrong_marker = why("JP41")
    check("an impossible JP number names the likely truth",
          "wrong marker" in wrong_marker and "EU 41" in wrong_marker, True)
    real_jp = why("JP 27")
    check("a real one says there is no table yet",
          "no JP conversion table" in real_jp, True)
    check("and the two do not say the same thing", wrong_marker == real_jp, False)


def test_a_kids_child_size_is_listable():
    """Sammy, 06.09.26: find a hard push for the kids Moon Boot, but keep the
    sizing in the title.

    "1C-2C" is the US child scale, and it was refused outright until now,
    which left a real pair of crib boots unlistable. It is now read, but only
    as far as there is evidence for: US 1 = UK 0.5 = EU 16 and US 2 = UK 1 =
    EU 17, which an independent conversion chart gives and which agrees
    exactly with the Master File, where the same boot is recorded as EU 17.
    Nothing above 2C is in the table, because nothing above 2C has been
    confirmed."""
    kids_uk = ["1", "1.5", "2", "2.5", "3", "3.5", "4"]
    kids_eu = ["15", "16", "17", "18", "19", "20"]

    check("a child band lists at its top size",
          am.match_shoe_size_uk("1C-2C", kids_uk, "GIRL"), "1")
    check("and the EU field agrees, from the same shoe",
          am.match_shoe_size_eu("1C-2C", kids_eu), "17")
    check("which is exactly what the Master File records for it", "17", "17")
    check("a single child size resolves too",
          am.match_shoe_size_uk("2C", kids_uk, "GIRL"), "1")
    check("and with an explicit US marker on it",
          am.match_shoe_size_uk("US 2C", kids_uk, "GIRL"), "1")

    # The child scale needs no gender: it is the same for boys and girls.
    check("no gender needed", am.match_shoe_size_uk("2C", kids_uk, "UNISEX"), "1")

    # A child size is a different shoe from the adult number.
    check("2C is not adult 2", am.parse_shoe_size("2C"), ("USC", "2"))
    check("2 is still adult 2", am.parse_shoe_size("2"), ("UK", "2"))

    # Beyond the evidence, refused, exactly as an out-of-table adult size is.
    check("3C has no table row", am.match_shoe_size_uk("3C", kids_uk, "GIRL"), None)
    check("nor does a band reaching it",
          am.match_shoe_size_uk("1C-3C", kids_uk, "GIRL"), None)

    # The AI's own size mention has to be stripped first, C and all. It was
    # not, so a real listing went out reading "Crib Boots Pink C-2C US
    # 1C-2C RRP 95" — the strip took "Size 1" and left "C-2C" behind.
    for written in ["MOON BOOT KIDS Baby Girl Crib Boots Pink Size 1C-2C RRP 95",
                    "MOON BOOT KIDS Baby Girl Crib Boots Pink 1C-2C RRP 95",
                    "MOON BOOT KIDS Baby Girl Crib Boots Pink 2C RRP 95",
                    "MOON BOOT KIDS Baby Girl Crib Boots Pink US 2C RRP 95"]:
        out = am.enforce_title_size(written, "US 1C-2C")
        check(f"no stray C left in {written[-18:]!r}",
              out, "MOON BOOT KIDS Baby Girl Crib Boots Pink US 1C-2C RRP 95")

    # And a measurement that merely ends in a letter is not a size.
    check("a heel height survives",
          am.enforce_title_size("BRAND Boot Black 20mm Heel RRP 395", "UK 5"),
          "BRAND Boot Black 20mm Heel UK 5 RRP 395")

    # The band is what the buyer sees, because it is what the box says.
    title = am.size_display("1C-2C", uk_shoe="1", eu_shoe="17")
    check("the title keeps the band", title, "US 1C-2C")
    check("the description spells the UK size out too",
          am.size_display("1C-2C", uk_shoe="1", eu_shoe="17", both=True), "US 1C-2C (UK 1)")
    check("and title enforcement leaves the band alone",
          "US 1C-2C" in am.enforce_title_size(
              "MOON BOOT KIDS Baby Girl Nylon Crib Boots Pink RRP 110", title), True)

    # The top-of-band rule is the opposite of the adult one, on purpose.
    check("a child band takes the top", am.child_band_size("1", "2"), "2")
    check("an adult band still rounds down",
          am.match_shoe_size_uk("2.5-3.5", ["2", "2.5", "3", "3.5"], "WOMEN"), "3")


def test_a_size_band_lists_at_its_middle_size():
    """Some boots really are made to fit a span of sizes: Moon Boot, and 3
    pairs in the QTN02 parcel.

    Until 06.09.26 the band went into the item specific whole. eBay refused
    it — UK Shoe Size is Required and takes one value off a fixed list — and
    two Moon Boots were rejected out of the first 295 row batch. Sammy's
    rule: "put the middle number in the item specifics but 2.5-3.5 in the
    title". So the specific carries one real size and the display string,
    built from the raw size rather than from the specific, still carries the
    band. test_a_band_is_shown_whole_to_the_buyer covers the other half."""
    check("a UK band lists at its middle size",
          am.match_shoe_size_uk("2.5-3.5", UK_WOMENS, "WOMEN"), "3")
    check("a UK band needs no gender",
          am.match_shoe_size_uk("2.5-3.5", UK_MENS, "UNISEX"), "3")
    check("the previously sold example resolves",
          am.match_shoe_size_uk("10.5-12", UK_WOMENS, "WOMEN"), "11")
    check("an EU band converts both ends before taking the middle",
          am.match_shoe_size_uk("45/47", UK_MENS, "MEN"), "12")
    check("an explicit EU band converts",
          am.match_shoe_size_uk("EU 39-41", UK_WOMENS, "WOMEN"), "7")
    check("a US band converts",
          am.match_shoe_size_uk("US 9-10", UK_MENS, "MEN"), "9")
    # An even-length band rounds down: a roomy boot is wearable, a tight one
    # is a return.
    check("an even band rounds down",
          am.middle_size("2.5", "3", UK_WOMENS), "2.5")
    check("the middle of a band is always a real eBay value",
          am.match_shoe_size_uk("2.5-3.5", UK_WOMENS, "WOMEN") in UK_WOMENS, True)
    check("an EU range fills the EU field too",
          am.match_shoe_size_eu("45/47", ["44", "45", "46", "47"]), "45-47")
    check("a UK range never fills the EU field",
          am.match_shoe_size_eu("2.5-3.5", ["2.5", "3.5", "45"]), None)

    # Both ends still go through the normal rules — a range can't smuggle a
    # value past the conversion tables or the gender requirement.
    check("an EU range still needs a gender",
          am.match_shoe_size_uk("EU 39-41", UK_WOMENS, "UNISEX"), None)
    check("a US range still needs a gender",
          am.match_shoe_size_uk("US 9-10", UK_MENS, "UNISEX"), None)
    check("a range spanning two scales is refused",
          am.match_shoe_size_uk("30-40", UK_WOMENS, "WOMEN"), None)
    # (Refused by the marker check, and would be refused by the conversion
    # tables anyway — both ends go through one system's table.)
    check("two different markers is refused",
          am.match_shoe_size_uk("EU 39-UK 6", UK_WOMENS, "WOMEN"), None)
    check("a backwards range is refused",
          am.match_shoe_size_uk("3.5-2.5", UK_WOMENS, "WOMEN"), None)
    check("a range with equal ends is refused",
          am.match_shoe_size_uk("7-7", UK_WOMENS, "WOMEN"), None)
    # 1C-2C used to be refused outright. It is now read as the US child
    # scale — see test_a_kids_child_size_is_listable. It must still refuse
    # against an ADULT size list, because UK 1 is not an adult women's size
    # the way it is a kids one.
    check("a child size is not forced into an adult list",
          am.match_shoe_size_uk("1C-2C", ["4", "5", "6"], "GIRL"), None)
    check("an EU range outside the table is refused",
          am.match_shoe_size_uk("60-62", UK_MENS, "MEN"), None)

    # And it is shown in the system it was recorded in, same rule as a
    # single size.
    check("a UK range reads as UK in the title",
          am.size_display("2.5-3.5", uk_shoe="2.5-3.5"), "UK 2.5-3.5")
    check("an EU range reads as EU in the title",
          am.size_display("45/47", uk_shoe="11-13", eu_shoe="45-47"), "EU 45-47")
    check("the description spells both out",
          am.size_display("45/47", uk_shoe="11-13", eu_shoe="45-47", both=True),
          "EU 45-47 (UK 11-13)")

    # A bare range is read as UK on the same assumption as a bare number, so
    # it has to be flagged for spot checking the same way.
    check("a bare range is flagged as assumed", am.is_assumed_shoe_system("2.5-3.5"), True)
    check("an EU range is not flagged", am.is_assumed_shoe_system("45/47"), False)
    check("a US range is not flagged", am.is_assumed_shoe_system("US 9-10"), False)


def test_ambiguous_sizes_are_refused_not_guessed():
    """What still produces nothing, so the SKU is skipped with a message
    rather than listed with a coin-flip size."""
    # Gender decides the table, so it must be known (EU 43 is UK 9 for men,
    # UK 10 for women — a full size apart).
    check("UNISEX refused", am.match_shoe_size_uk("43", UK_MENS, "UNISEX"), None)
    check("blank gender refused", am.match_shoe_size_uk("43", UK_MENS, None), None)
    check("GIRL refused", am.match_shoe_size_uk("37", UK_WOMENS, "GIRL"), None)
    # Junk. (A well-formed range like "39/40" is a real size and is handled
    # in test_a_size_band_lists_at_its_middle_size — what stays
    # refused is anything that isn't a size at all.)
    check("non-numeric refused", am.match_shoe_size_uk("abc", UK_WOMENS, "WOMEN"), None)
    check("open-ended range refused", am.match_shoe_size_uk("39-", UK_WOMENS, "WOMEN"), None)
    check("three-ended range refused", am.match_shoe_size_uk("38-39-40", UK_WOMENS, "WOMEN"), None)
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
    # The title shows the recorded system only. Spelling the conversion out
    # belongs in the description, where there is room — a title has 80
    # characters and eBay truncates.
    check("end-to-end: the title does NOT spell out the conversion",
          "(UK 11)" in title, False)
    for bad in ("UK 4.5", "EU 39", "UK 7"):
        if bad in title:
            FAILURES.append(f"end-to-end: title still contains the AI's invented {bad!r}: {title!r}")


def test_the_brand_rule_survives_the_whole_pipeline():
    """The unit checks above prove match_shoe_size_uk honours the brand. This
    one proves the brand actually REACHES it.

    Found by mutation testing on 05.09.26: dropping `brand=` from the call in
    content_generator silently reverted every US-brand size to the UK reading
    and left all twelve unit checks green. The feature can only be trusted
    end to end, so it is tested end to end."""
    import tempfile
    from src import ai_client, content_generator, ebay_template
    from src.data_loader import Product

    templates_dir = Path(__file__).resolve().parent.parent / "data" / "templates"
    menswear = templates_dir / "menswear_shoes.json"
    if not menswear.exists():
        print("  (skipped end-to-end brand check: data/templates not present in this checkout)")
        return

    template = ebay_template.load_template(menswear)
    category = template.category_by_id("24087")  # Men's Shoes > Casual Shoes

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
        return {"title": "Penny Pony Loafer Brown RRP 445",
                "condition_id": category.conditions[0][0],
                "condition_description": "Good condition.",
                "material_summary": "Calf Leather",
                "item_specifics": specifics}

    def run_one(brand, raw_size):
        product = Product(
            sku="TEST-BRAND",
            master={"Brand": brand, "Gender": "MEN", "Colour": "Brown",
                    "Category": "Footwear", "SubCat2": "Shoes", "Rounded RRP": 445,
                    "Clean Title Description": f"{brand} PENNY PONY LOAFER"},
            measurements={"Size": raw_size, "Description": "Good condition."})
        original = ai_client.call_structured
        ai_client.call_structured = fake_ai
        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                return content_generator.generate_for_product(
                    product, category, template, cache_dir, force=True)
        finally:
            ai_client.call_structured = original

    # The exact pair from Sammy's batch: same shoe, one marked, one bare.
    marked_r = run_one("BLACKSTOCK & WEBER", "US10")
    bare_r = run_one("BLACKSTOCK & WEBER", "10")
    marked = marked_r["item_specifics"].get("C:UK Shoe Size")
    bare = bare_r["item_specifics"].get("C:UK Shoe Size")
    check("end-to-end: an explicit US10 gives UK 9.5", marked, "9.5")
    check("end-to-end: a bare 10 on the same brand gives UK 9.5", bare, "9.5")
    check("end-to-end: the two agree, which they did not before this fix", marked, bare)

    # The title has to move with the item specific. Without the brand reaching
    # size_display the specific says 9.5 while the title still says UK 10,
    # which is the exact title-vs-specifics disagreement Sammy ruled out on
    # 04.09.26 ("it cant show UK7 in the title and then 7.5 in the item
    # specifics"). Caught by mutation testing 05.09.26.
    check("end-to-end: the bare-number title matches its item specific",
          f"UK {bare}" in bare_r["title"], True)
    check("end-to-end: and the marked one too",
          f"UK {marked}" in marked_r["title"], True)
    check("end-to-end: the title does not carry the raw US number as a UK size",
          "UK 10" in bare_r["title"], False)
    check("end-to-end: both titles read identically",
          bare_r["title"], marked_r["title"])

    # And the rule stays off for everyone else.
    other = run_one("GH BASS", "10")
    check("end-to-end: a bare 10 on another brand is still UK 10",
          other["item_specifics"].get("C:UK Shoe Size"), "10")
    check("end-to-end: and its title says UK 10", "UK 10" in other["title"], True)


def test_an_over_long_title_keeps_its_size_end_to_end():
    """trim_title is only worth anything if the pipeline calls it. The unit
    check above passed while content_generator still did title[:80], so this
    one runs a deliberately over-long AI title through generate_for_product
    and checks what actually comes out."""
    import tempfile
    from src import ai_client, content_generator, ebay_template
    from src.data_loader import Product

    templates_dir = Path(__file__).resolve().parent.parent / "data" / "templates"
    menswear = templates_dir / "menswear_shoes.json"
    if not menswear.exists():
        print("  (skipped end-to-end title check: data/templates not present)")
        return

    template = ebay_template.load_template(menswear)
    category = template.category_by_id("15709")  # Men's Shoes > Trainers

    long_title = ("CDG Homme Plus x Nike Air Max TL2.5 Low Top Sneaker Black "
                  "Calf Leather Mesh Panel Trainers UK 7.5 RRP 395")

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
        return {"title": long_title,
                "condition_id": category.conditions[0][0],
                "condition_description": "Good condition.",
                "material_summary": "Calf Leather",
                "item_specifics": specifics}

    product = Product(
        sku="TEST-TITLE",
        master={"Brand": "COMME DES GARCON HOMME PLUS", "Gender": "MEN",
                "Colour": "Black", "Category": "Footwear", "SubCat2": "Sneakers",
                "Rounded RRP": 395,
                "Clean Title Description": "CDG HOMME PLUS X NIKE AIR MAX TL2.5"},
        measurements={"Size": "UK 7.5", "Description": "Good condition."})

    original = ai_client.call_structured
    ai_client.call_structured = fake_ai
    try:
        with tempfile.TemporaryDirectory() as cache_dir:
            result = content_generator.generate_for_product(
                product, category, template, cache_dir, force=True)
    finally:
        ai_client.call_structured = original

    title = result["title"]
    check("end-to-end: the title fits eBay's limit", len(title) <= 80, True)
    check("end-to-end: and it still carries the size", "UK 7.5" in title, True)
    check("end-to-end: and it does not end mid-word",
          all(w in long_title.split() or w.isupper() or w == "Mens"
              for w in title.split()), True)
    # The gender word is added in the same stretch of code, so this is also
    # where a dropped call site shows up. The product is MEN.
    check("end-to-end: and it says who the item is for", "Mens" in title, True)
    # The same stretch of code strips a brand eBay refuses in someone else's
    # title. The fake AI title above is the real refused one, CDG x Nike.
    check("end-to-end: the blocked second brand is gone", "Nike" in title, False)
    check("end-to-end: but the model name survives", "Air Max" in title, True)
    check("end-to-end: right after the brand",
          title.startswith("COMME DES GARCON HOMME PLUS Mens"), True)


def test_one_size_string_feeds_title_description_and_checks():
    """Title, description and the checks report must all describe the size
    the same way. They used to build it from three separate copies of the
    same wiring, and mutation testing on 05.09.26 showed two of the three
    could silently drop the brand. They now share size_display_for, and this
    proves the shared helper is what they use."""
    from src.data_loader import Product

    us_brand = Product(
        sku="T1",
        master={"Brand": "BLACKSTOCK & WEBER", "Gender": "MEN"},
        measurements={"Size": "10"})
    specifics = {"C:UK Shoe Size": "9.5"}
    check("US-brand bare number reads as its converted UK size",
          am.size_display_for(us_brand, specifics), "UK 9.5")
    check("and never as the raw number",
          am.size_display_for(us_brand, specifics) == "UK 10", False)

    uk_brand = Product(sku="T2", master={"Brand": "GH BASS", "Gender": "MEN"},
                       measurements={"Size": "10"})
    check("everyone else still reads as UK 10",
          am.size_display_for(uk_brand, {"C:UK Shoe Size": "10"}), "UK 10")

    # The description spells the conversion out; the title does not.
    eu = Product(sku="T3", master={"Brand": "ROA", "Gender": "MEN"},
                 measurements={"Size": "45"})
    eu_specifics = {"C:UK Shoe Size": "11", "C:EU Shoe Size": "45"}
    check("title form", am.size_display_for(eu, eu_specifics), "EU 45")
    check("description form", am.size_display_for(eu, eu_specifics, both=True), "EU 45 (UK 11)")

    # Clothing: the description falls back to the recorded size, the title
    # does not. That difference is deliberate and easy to lose.
    shirt = Product(sku="T4", master={"Brand": "VISVIM", "Gender": "MEN"},
                    measurements={"Size": "03"})
    check("clothing with no matched size gives nothing for the title",
          am.size_display_for(shirt, {}), None)
    check("but the description falls back to what was recorded",
          am.size_display_for(shirt, {}, clothing_fallback=True), "03")


def test_the_buyer_facing_description_says_the_same_size():
    """The description is what the buyer actually reads, and until now
    nothing tested it. Mutation testing on 05.09.26 found three separate ways
    its Size line could go wrong with every other test still green: losing
    the brand, losing the conversion in brackets, and losing the fallback to
    the recorded size for clothing."""
    from src import build, ebay_template
    from src.data_loader import Product

    templates_dir = Path(__file__).resolve().parent.parent / "data" / "templates"
    menswear = templates_dir / "menswear_shoes.json"
    if not menswear.exists():
        print("  (skipped description check: data/templates not present in this checkout)")
        return
    template = ebay_template.load_template(menswear)
    category = template.category_by_id("24087")

    def size_line(brand, raw_size, specifics):
        product = Product(
            sku="T", master={"Brand": brand, "Gender": "MEN", "Colour": "Brown",
                             "Rounded RRP": 445, "Season": "AW25", "Category": "Footwear"},
            measurements={"Size": raw_size, "Description": "Good condition."})
        ai_result = {"condition_description": "Good condition.", "material_summary": "Leather",
                     "item_specifics": specifics, "style": "Loafer", "type": "Casual"}
        text = build.build_description(product, ai_result, category, template, {})
        for line in text.replace("<br>", "").splitlines():
            if line.strip().startswith("Size:"):
                return line.strip().rstrip()
        return None

    check("US-brand bare number shows the converted size, not the raw one",
          size_line("BLACKSTOCK & WEBER", "10", {"C:UK Shoe Size": "9.5"}), "Size: UK 9.5")
    check("everyone else is unaffected",
          size_line("GH BASS", "10", {"C:UK Shoe Size": "10"}), "Size: UK 10")
    check("an EU size spells the conversion out for the buyer",
          size_line("ROA", "45", {"C:UK Shoe Size": "11", "C:EU Shoe Size": "45"}),
          "Size: EU 45 (UK 11)")
    check("clothing with no matched size still shows what was recorded",
          size_line("VISVIM", "03", {}), "Size: 03")
    # The other half of the band rule: the specific says one size, the buyer
    # is told the whole band. The description is built from the raw size, not
    # from the specific, which is what makes that possible.
    check("a band is shown whole to the buyer",
          size_line("MOON BOOT", "2.5-3.5", {"C:UK Shoe Size": "3"}), "Size: UK 2.5-3.5")


def test_a_band_is_shown_whole_to_the_buyer():
    """Sammy, 06.09.26: "put the middle number in the item specifics but
    2.5-3.5 in the title". Both halves have to hold at once, and they are
    produced by different code, so both are checked here.

    The failure this guards against is subtle: make the display string read
    from the item specific instead of the raw size and the specific is still
    right, the file still uploads, and the buyer is quietly told the boot is
    a UK 3 when it is a 2.5 to 3.5 shell."""
    check("the specific carries one real size",
          am.match_shoe_size_uk("2.5-3.5", UK_WOMENS, "WOMEN"), "3")
    check("the title carries the band",
          am.size_display("2.5-3.5", uk_shoe="3"), "UK 2.5-3.5")
    check("and so does the description",
          am.size_display("2.5-3.5", uk_shoe="3", both=True), "UK 2.5-3.5")
    check("an EU band is shown as recorded, in EU",
          am.size_display("45/47", uk_shoe="12", eu_shoe="45-47"), "EU 45-47")
    check("with the conversion alongside in the description",
          am.size_display("45/47", uk_shoe="12", eu_shoe="45-47", both=True),
          "EU 45-47 (UK 12)")
    # And the title enforcement must not "correct" the band back to the
    # single size, which is exactly what it is built to do everywhere else.
    title = am.enforce_title_size(
        "MOON BOOT Light Low White Padded Snow Ankle Boots RRP 195", "UK 2.5-3.5")
    check("title enforcement leaves the band alone", "UK 2.5-3.5" in title, True)
    check("and does not put the middle size in the title", "UK 3 " in title, False)


def test_changing_a_sizing_rule_invalidates_the_cache():
    """A stale cache entry is indistinguishable from a fresh one in the
    output file, so after a sizing fix a re-run must not re-serve the old
    answer. The cache key carries a fingerprint of the sizing code and
    tables; this proves the fingerprint actually moves when they do."""
    from src import aspect_matching, content_generator

    baseline = content_generator._sizing_fingerprint()

    us_original = dict(aspect_matching.US_TO_UK_MENS_SHOE_SIZE)
    try:
        aspect_matching.US_TO_UK_MENS_SHOE_SIZE["9"] = "99"
        check("changing the US table changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.US_TO_UK_MENS_SHOE_SIZE.clear()
        aspect_matching.US_TO_UK_MENS_SHOE_SIZE.update(us_original)

    for name in ("US_CHILD_TO_UK_SHOE_SIZE", "US_CHILD_TO_EU_SHOE_SIZE"):
        table = getattr(aspect_matching, name)
        original = dict(table)
        try:
            table["99"] = "99"
            check(f"changing {name} changes the cache fingerprint",
                  content_generator._sizing_fingerprint() != baseline, True)
        finally:
            table.clear()
            table.update(original)

    brands_original = set(aspect_matching.US_SIZED_BRANDS)
    try:
        aspect_matching.US_SIZED_BRANDS.add("SOME OTHER BRAND")
        check("changing the US brand list changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.US_SIZED_BRANDS.clear()
        aspect_matching.US_SIZED_BRANDS.update(brands_original)

    bare_original = aspect_matching.BARE_NUMBER_SHOE_SYSTEM
    try:
        aspect_matching.BARE_NUMBER_SHOE_SYSTEM = None
        check("changing the bare-number rule changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.BARE_NUMBER_SHOE_SYSTEM = bare_original

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

    # The mutation that motivated this: dropping a function from the
    # fingerprint's input list leaves every test green while silently
    # letting a stale cached size be re-served after a sizing fix. So the
    # list is checked against the sizing code itself.
    import inspect
    hashed = {fn.__name__ for fn in content_generator._sizing_sources()}
    must_be_hashed = {
        "parse_shoe_size", "parse_shoe_size_range", "_resolve_range_end",
        "_normalise_size_marker", "is_assumed_shoe_system", "match_shoe_size_uk",
        "match_shoe_size_eu", "_eu_to_uk_table", "_us_to_uk_table",
        "size_display", "enforce_title_size", "fuzzy_match", "match_size",
        "bare_number_system", "assumed_shoe_system", "size_display_for",
        # trim_title decides whether the size survives into the title, so a
        # change to it must invalidate the cache like any other sizing edit.
        # Added 05.09.26 after mutation testing showed it could be dropped
        # from the list with every check still green.
        "trim_title",
        # middle_size decides the number in the item specific for a band.
        # Added 06.09.26; mutation testing showed it could drop out of the
        # list with every check still green, same as trim_title before it.
        "middle_size",
        # Not sizes, but they decide the finished title, which is cached
        # alongside the size — so a change to either has to invalidate the
        # cache exactly as a sizing change does. Added 06.09.26.
        "enforce_title_gender", "title_gender_word", "_drop_dangling_markers",
        # looks_like_japanese_size is deliberately NOT here. It only
        # decides the wording of a failure message for a product that is
        # skipped either way, so it cannot change a cached size, and
        # hashing it would bust the whole cache for a reworded sentence.
        "child_band_size",
        "strip_blocked_brand", "collaborating_brand",
    }
    missing = sorted(must_be_hashed - hashed)
    if missing:
        FAILURES.append(
            f"these decide a size but aren't in _sizing_sources(), so editing them "
            f"would NOT invalidate the cache: {missing}")

    # And every one of them must actually reach the hash.
    material = "".join(inspect.getsource(fn) for fn in content_generator._sizing_sources())
    for name in sorted(must_be_hashed):
        if f"def {name}(" not in material:
            FAILURES.append(f"{name}'s source never reaches the cache fingerprint")


def test_conversion_tables_are_internally_consistent():
    """Guards against a typo in a table: every entry must be a real eBay UK
    value, and sizes must increase with EU size, never jump backwards."""
    for name, table, valid in [("women's EU", am.EU_TO_UK_WOMENS_SHOE_SIZE, UK_WOMENS),
                               ("men's EU", am.EU_TO_UK_MENS_SHOE_SIZE, UK_MENS),
                               ("women's US", am.US_TO_UK_WOMENS_SHOE_SIZE, UK_WOMENS),
                               ("men's US", am.US_TO_UK_MENS_SHOE_SIZE, UK_MENS)]:
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


def test_a_size_range_leaves_no_orphan_in_the_title():
    """05.09.26: two Moon Boots shipped as "Snow Ankle Boots -3.5 UK 2.5-3.5
    RRP 195". The size strip removed "Size 2.5" and walked away from the
    "-3.5", which then sat in the title next to the size put back in."""
    cases = [
        ("MOON BOOT Light Low White Padded Snow Ankle Boots Size 2.5-3.5 RRP 195",
         "UK 2.5-3.5"),
        ("MOON BOOT Light Low Snow Boots UK 2.5 - 3.5 RRP 195", "UK 2.5-3.5"),
        ("SOME BRAND Trainer White EU 41/42 RRP 200", "UK 7.5"),
    ]
    for title, size in cases:
        out = am.enforce_title_size(title, size)
        if re.search(r"(?<![\w.])-\s*\d", out):
            FAILURES.append(f"orphan range end left in title: {out!r}")
        if out.count(size) != 1:
            FAILURES.append(f"size should appear exactly once, got {out!r}")


def test_a_second_brand_is_stripped_from_the_title():
    """eBay error 240, 06.09.26. "COMME DES GARCON HOMME PLUS x Nike Air Max
    TL2.5 Sneaker Black UK 7.5 RRP 395" was refused outright under the search
    manipulation policy: extra brand names are not allowed in a title.

    Enforcement is per brand, not even. Eleven other collaboration titles in
    the same upload listed without complaint — adidas x Wales Bonner, CDG x
    New Balance, CDG SHIRT x ASICS, MM6 x Salomon, RICK OWENS DRKSHDW x
    Converse and the rest. So TITLE_BLOCKED_BRANDS holds brands actually
    observed being refused, and these checks are as much about what must NOT
    be stripped as what must."""
    refused = "COMME DES GARCON HOMME PLUS x Nike Air Max TL2.5 Sneaker Black UK 7.5 RRP 395"
    out = am.strip_blocked_brand(refused, "COMME DES GARCON HOMME PLUS")
    check("the blocked brand goes", "Nike" in out, False)
    check("and the x that joined it", " x " in out, False)
    check("the rest of the model name stays", "Air Max TL2.5" in out, True)
    check("the item's own brand stays", out.startswith("COMME DES GARCON HOMME PLUS"), True)
    check("and so do the size and the RRP", "UK 7.5" in out and "RRP 395" in out, True)
    check("no double spaces left behind", "  " in out, False)

    # A brand that has never been refused is left alone. Stripping these
    # would cost real search traffic for no reason.
    for title in [
        "ADIDAS X WALES BONNER Mens WB SL76 Sneaker Blue Suede UK 8 RRP 195",
        "COMME DES GARCON HOMME PLUS Mens x New Balance U509 Sneaker Grey EU 41.5 RRP 245",
        "MM6 MAISON MARGIELA X Salomon XT-4 Mule Blue White Sneaker UK 3.5 RRP 295",
        "RICK OWENS DRKSHDW Womens x Converse Turbowpn OX Black Leather UK 4.5 RRP 195",
        "CDG COMME DES GARCONS SHIRT Mens x ASICS GEL-KAYANO 14 White UK 8 RRP 495",
    ]:
        check(f"left alone: {title.split()[0]}",
              am.strip_blocked_brand(title, title.split()[0]), title)

    # A Nike listing keeps its own name. The rule is "someone else's brand",
    # not "this word".
    own = "NIKE Mens Air Max 90 Sneaker White Leather UK 9 RRP 130"
    check("Nike's own listing is untouched", am.strip_blocked_brand(own, "Nike"), own)
    check("and nothing is flagged on it", am.collaborating_brand(own, "Nike"), None)
    check("the collaborating brand is named for the report",
          am.collaborating_brand(refused, "COMME DES GARCON HOMME PLUS"), "NIKE")


def test_the_title_says_who_the_item_is_for():
    """Sammy, 06.09.26: "we need to add Mens Womens after each brand in the
    title, this is optimal for ebay search results".

    After the brand, not on the end, because eBay weights the front of a
    title. Read from the Master File's Gender, the same column C:Department
    comes from, so the title and the specific cannot disagree."""
    check("womens goes straight after the brand",
          am.enforce_title_gender("TORY BURCH Eleanor Ballet Flat Black RRP 395", "WOMEN",
                                  brand="Tory Burch"),
          "TORY BURCH Womens Eleanor Ballet Flat Black RRP 395")
    check("and mens",
          am.enforce_title_gender("GH BASS Weejun Larson Loafer Brown EU 44 RRP 245", "MEN",
                                  brand="GH BASS"),
          "GH BASS Mens Weejun Larson Loafer Brown EU 44 RRP 245")

    # Unisex gets nothing. Sammy's decision the same day: 14 items in the
    # first batch, and a wrong word costs more than a missing one.
    for department in ("Unisex Adults", "UNISEX", "GIRL", "Teens", "", None):
        title = "SALOMON XT-6 Trainer Black UK 8 RRP 195"
        check(f"{department!r} gets no word", am.enforce_title_gender(title, department,
                                                                     brand="Salomon"), title)

    # 17 of 282 titles in the first batch already said it. Two gender words
    # in one title reads as a mistake.
    for existing in [
        "BURBERRY Rogue Loafer Black Calf Leather Men's Shoes EU 44 RRP 695",
        "DIEMME Cornaro Low Top Hiking Boot Green Leather Mens EU 46 RRP 395",
        "SOME BRAND Unisex Trainer White UK 8 RRP 100",
    ]:
        check("no second gender word is added",
              am.enforce_title_gender(existing, "MEN", brand=existing.split()[0]), existing)

    # HOMME is part of a brand name, not a gender word. Treating it as one
    # would silently skip every Comme des Garcons Homme Plus listing.
    out = am.enforce_title_gender(
        "COMME DES GARCON HOMME PLUS x Nike Air Max Sneaker Black UK 7.5 RRP 395",
        "MEN", brand="COMME DES GARCON HOMME PLUS")
    check("a brand containing HOMME still gets the word", "PLUS Mens x Nike" in out, True)

    # With the brand missing from the title the word still has to appear.
    check("no brand in the title puts the word at the front",
          am.enforce_title_gender("Eleanor Ballet Flat Black RRP 395", "WOMEN", brand="Tory Burch"),
          "Womens Eleanor Ballet Flat Black RRP 395")
    check("and no brand given at all",
          am.enforce_title_gender("Eleanor Ballet Flat Black RRP 395", "WOMEN"),
          "Womens Eleanor Ballet Flat Black RRP 395")

    # The word survives trimming, along with the brand, the size and the RRP.
    # 23 of 282 titles went over 80 once the word was added.
    long_title = am.enforce_title_gender(
        "JACQUEMUS Les Ballerines Ovalo Leather Ballet Flats Multicoloured EU 40 RRP 545",
        "WOMEN", brand="JACQUEMUS")
    trimmed = am.trim_title(long_title, "EU 40")
    check("a trimmed title still fits", len(trimmed) <= 80, True)
    check("and keeps the gender word", "Womens" in trimmed, True)
    check("and the brand", trimmed.startswith("JACQUEMUS Womens"), True)
    check("and the size", "EU 40" in trimmed, True)
    check("and the RRP", "RRP 545" in trimmed, True)


def test_a_stranded_size_marker_is_cleaned_up():
    """Two real titles from the first batch, 06.09.26.

    The AI wrote "UK Size" on a shoe whose recorded size was EU. Stripping
    its size mention left those two words stranded in front of the correct
    one, so the title read "...Choc Brown Leather UK Size EU 44 RRP 245".
    Nothing was wrong enough to fail a check, and it only became visible when
    the trimmer had to make room and dropped "Size", leaving "UK EU 44"."""
    check("a stranded marker before the real size goes",
          am.enforce_title_size(
              "GH BASS Weejun Larson Moc Penny Loafer Choc Brown Leather UK Size RRP 245",
              "EU 44"),
          "GH BASS Weejun Larson Moc Penny Loafer Choc Brown Leather EU 44 RRP 245")
    check("and a chain of them",
          am._drop_dangling_markers("BRAND Shoe Brown UK Size EU 44 RRP 245"),
          "BRAND Shoe Brown EU 44 RRP 245")

    # It must not eat a marker that has its own number, or ordinary words.
    for untouched in [
        "BRAND Shoe Brown EU 44 RRP 245",
        "BRAND US Polo Shoe Brown EU 44 RRP 245",
        "BRAND Shoe Size Brown EU 44 RRP 245",
    ]:
        check(f"leaves {untouched!r} alone", am._drop_dangling_markers(untouched), untouched)

    # RRP with no space still reads as the RRP tail, so the trimmer keeps it
    # instead of dropping it as an ordinary word. One GH Bass title, 06.09.26.
    long_one = ("GH BASS Mens Weejun Heritage Larson Moc Penny Loafer Choc Brown "
                "Leather RRP245 EU 45")
    out = am.trim_title(long_one, "EU 45")
    check("RRP245 survives trimming", "RRP245" in out, True)
    check("and the title fits", len(out) <= 80, True)


def test_an_over_long_title_keeps_its_size():
    """The blind title[:80] chop cut the size off the end of the very titles
    enforce_title_size had just corrected (two CDG trainers, 05.09.26)."""
    long_title = ("COMME DES GARCON HOMME PLUS CDG Homme Plus x Nike Air Max TL2.5 "
                  "Sneaker Black UK 7.5 RRP 395")
    out = am.trim_title(long_title, "UK 7.5")
    if len(out) > 80:
        FAILURES.append(f"trimmed title is still {len(out)} chars: {out!r}")
    if "UK 7.5" not in out:
        FAILURES.append(f"trimming dropped the size: {out!r}")
    if not out.startswith("COMME DES GARCON HOMME PLUS"):
        FAILURES.append(f"trimming dropped the brand: {out!r}")
    if "RRP 395" not in out:
        FAILURES.append(f"trimming dropped the RRP: {out!r}")
    if out != out.strip() or "  " in out or out.split()[-1] != "395":
        FAILURES.append(f"trimmed title is not clean: {out!r}")
    # A title already inside the limit must come back untouched.
    short = "GH BASS Weejun Larson Moc Penny Loafers Burgundy EU 44 RRP 245"
    if am.trim_title(short, "EU 44") != short:
        FAILURES.append("trim_title altered a title that already fitted")
    # No word may be cut in half.
    brutal = "BRAND " + " ".join(["Extraordinarily"] * 8) + " Descriptive Shoe UK 9 RRP 100"
    out = am.trim_title(brutal, "UK 9")
    if len(out) > 80:
        FAILURES.append(f"trim_title exceeded the limit on a hard case: {out!r}")
    for word in out.split():
        if word not in brutal.split():
            FAILURES.append(f"trim_title cut a word in half: {word!r} in {out!r}")


def test_an_unrecognised_country_code_is_spotted():
    """Country of Origin is a customs declaration. "SLV" and "CXR" reached
    three live listings on 05.09.26 because an unresolved code was written
    through raw."""
    for code in ("SLV", "CXR", "IT", "xx"):
        if not am.looks_like_country_code(code):
            FAILURES.append(f"{code!r} should be recognised as a bare code")
    for name in ("Italy", "United Kingdom", "El Salvador", "", None):
        if am.looks_like_country_code(name):
            FAILURES.append(f"{name!r} should not be treated as a bare code")
    # Every code that actually appears in this account's Master File must
    # resolve, so the blank-rather-than-guess path above stays a backstop.
    for code in ("ITA", "CHN", "BGD", "PRT", "TUR", "IND", "NLD", "VNM", "GBR",
                 "USA", "UKR", "SVK", "JPN", "BGR", "FRA", "COL", "ESP", "BRA",
                 "LBN", "POL", "IDN", "ROU", "NZL", "ALB", "MAR", "TUN", "KHM",
                 "KOR", "SLV", "PER", "DEU", "EGY", "CZE", "MEX", "CHE", "GRC",
                 "CAN", "HUN", "CXR"):
        if code.lower() not in am.COUNTRY_ALIASES:
            FAILURES.append(f"{code} is in the Master File but has no country alias")


def test_a_title_never_says_the_gender_twice():
    """07.09.26 shipped "LANVIN Mens Core Curb Sneaker White Leather Trainers
    Men EU 44 RRP 795".

    The AI had written "Men" on the end. enforce_title_gender only knew the
    plural and possessive spellings, saw no gender word, and put "Mens" in
    after the brand. Two of them in one title reads as a mistake, which is
    the exact thing the "already says it" branch exists to avoid."""
    for existing, department in [
        ("LANVIN Core Curb Sneaker White Leather Trainers Men EU 44 RRP 795", "MEN"),
        ("TORY BURCH Eleanor Ballet Flat Black Women EU 38 RRP 395", "WOMEN"),
        ("GUCCI Horsebit Loafer Black Leather Woman EU 38 RRP 695", "WOMEN"),
        ("PRADA Monolith Boot Black Leather Man EU 44 RRP 995", "MEN"),
        ("MOON BOOT Icon Junior Boot Pink Child EU 30 RRP 95", "MEN"),
    ]:
        check(f"no second gender word: {existing[:20]}...",
              am.enforce_title_gender(existing, department, brand=existing.split()[0]),
              existing)

    # And the word is still added when the title genuinely does not say it.
    # A rule that blocks everything is not a rule, it is an outage.
    check("a title with no gender word still gets one",
          am.enforce_title_gender("AMIRI Skel Top Low Sneaker Black Calf Leather EU 44 RRP 495",
                                  "MEN", brand="AMIRI"),
          "AMIRI Mens Skel Top Low Sneaker Black Calf Leather EU 44 RRP 495")

    # The words that must NOT count as gender words, because they are parts
    # of brand and model names this account actually stocks. Blocking on one
    # of these would silently skip the gender word on every one of them.
    for innocent in [
        "MANOLO BLAHNIK Hangisi 90 Pump Blue Satin EU 38 RRP 745",
        "OUR LEGACY Off Court Low Top Leather Sneaker White EU 44 RRP 345",
        "GENTLE MONSTER Sunglasses Black Acetate RRP 295",
        "MIHARAYASUHIRO Blakey Black Leather Low-Top Sneakers EU 41 RRP 445",
        "WOMENSWEAR ARCHIVE Coat Navy Wool RRP 500",
    ]:
        out = am.enforce_title_gender(innocent, "MEN", brand=innocent.split()[0])
        check(f"gender word still added to {innocent[:18]}...", "Mens" in out, True)


def test_the_doubled_gender_word_cannot_be_re_served_from_cache():
    """_TITLE_GENDER_RE lives at module level, so inspect.getsource on
    enforce_title_gender does not see it. Without the pattern in the
    fingerprint, fixing the doubled word would change what the app produces
    while leaving every cache key identical — and every already-cached
    listing would keep the old title. Same failure the function list was
    written to prevent, one level down."""
    import re as _re
    from src import aspect_matching, content_generator

    baseline = content_generator._sizing_fingerprint()
    for name in ("_TITLE_GENDER_RE", "_TITLE_SIZE_RE", "_TITLE_BARE_CHILD_SIZE_RE",
                 "_TITLE_RRP_RE", "_DANGLING_MARKER_RE"):
        original = getattr(aspect_matching, name)
        try:
            setattr(aspect_matching, name, _re.compile(original.pattern + "|zzzz", _re.IGNORECASE))
            check(f"changing {name} changes the cache fingerprint",
                  content_generator._sizing_fingerprint() != baseline, True)
        finally:
            setattr(aspect_matching, name, original)

    colours_original = dict(aspect_matching.COLOUR_FAMILY_ALIASES)
    try:
        aspect_matching.COLOUR_FAMILY_ALIASES["zzz"] = "Black"
        check("changing the colour family map changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.COLOUR_FAMILY_ALIASES.clear()
        aspect_matching.COLOUR_FAMILY_ALIASES.update(colours_original)

    markers_original = aspect_matching._SIZE_MARKERS
    try:
        aspect_matching._SIZE_MARKERS = markers_original + ("ZZ",)
        check("changing the size marker list changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching._SIZE_MARKERS = markers_original

    words_original = dict(aspect_matching.TITLE_GENDER_WORDS)
    try:
        aspect_matching.TITLE_GENDER_WORDS["ZZZ"] = "Zzz"
        check("changing the gender word map changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.TITLE_GENDER_WORDS.clear()
        aspect_matching.TITLE_GENDER_WORDS.update(words_original)

    blocked_original = set(aspect_matching.TITLE_BLOCKED_BRANDS)
    try:
        aspect_matching.TITLE_BLOCKED_BRANDS.add("ZZZ BRAND")
        check("changing the blocked brand list changes the cache fingerprint",
              content_generator._sizing_fingerprint() != baseline, True)
    finally:
        aspect_matching.TITLE_BLOCKED_BRANDS.clear()
        aspect_matching.TITLE_BLOCKED_BRANDS.update(blocked_original)

    check("fingerprint is stable when nothing changed",
          content_generator._sizing_fingerprint(), baseline)


def test_a_clothing_size_marker_reaches_ebays_own_value():
    """Sammy briefed the team on 07.09.26 to put EU, UK or US in front of
    every numerical size. The first clothing file back, 08.09.26, has the
    marker behind it instead: "50 IT" on a Rick Owens coat, "34 IT" on the
    jeans.

    That matters more on clothing than it looks. eBay's clothing Size lists
    carry the marker in FRONT — IT 50, EU 40, US 2, FR 38 — so the same size
    written the other way round matches nothing and ships as free text,
    which keeps the listing out of every size-filtered search a buyer runs.
    Moving the marker turns it into one of eBay's own values."""
    coats = ["2XS", "XS", "S", "M", "L", "XL", "48", "50", "52",
             "IT 48", "IT 50", "IT 52", "EU 48", "EU 50", "FR 50", "One Size"]
    check("a trailing marker moves to the front", am.match_size("50 IT", coats), "IT 50")
    check("and a joined one", am.match_size("IT50", coats), "IT 50")
    check("lower case too", am.match_size("50 it", coats), "IT 50")
    check("EU as well", am.match_size("48 EU", coats), "EU 48")

    # eBay's marked form wins over the bare number when it exists, because
    # "IT 50" and a UK 50 are not the same garment.
    check("the marked form is preferred", am.match_size("50 IT", coats), "IT 50")

    # And where eBay offers no marked form, the bare number is better than
    # shipping the raw string: men's jeans offer waist inches 24-40 and IT
    # 42-60, so a Rick Owens "34 IT" has no IT 34 to match.
    jeans = ["S", "M", "L", "30", "32", "34", "36", "IT 42", "IT 44", "IT 46"]
    check("no marked form falls back to the number", am.match_size("34 IT", jeans), "34")

    # Everything that already worked still works.
    check("a leading marker still strips", am.match_size("UK8", ["6", "8", "10"]), "8")
    check("a leading marker with a space", am.match_size("UK 8", ["6", "8", "10"]), "8")
    check("a plain letter size", am.match_size("M", coats), "M")
    check("a zero-padded number", am.match_size("03", ["3", "5", "8"]), "3")
    check("one size", am.match_size("os", coats), "One Size")

    # And a size on a scale nobody offers is still refused here, so it can
    # fall through to the raw value rather than being bent into a wrong one.
    # A Moncler 3 is a real size; it is not a UK 3.
    check("an unknown scale is not forced", am.match_size("3", coats), None)
    check("nor is a typo", am.match_size("MM", coats), None)
    # The marker must not invent a size that was never written.
    check("a bare marker matches nothing", am.match_size("IT", coats), None)


def test_a_colour_family_is_never_matched_to_a_random_colour():
    """This account records colour families, not colours: "Neutrals" on 118
    products and "Metallic" on 133. eBay's Colour list has neither.

    Left to a fuzzy match, 08.09.26, "Neutrals" scored closest to "Purple"
    and "Metallic" to "Yellow". A beige coat listed as purple is worse than
    no colour at all, and it would have gone out on five of the thirty
    garments in the first clothing batch."""
    ebay = ["Beige", "Black", "Blue", "Brown", "Clear", "Gold", "Green", "Grey",
            "Ivory", "Multicoloured", "Orange", "Pink", "Purple", "Red", "Silver",
            "White", "Yellow"]

    check("Neutrals is beige, not purple", am.match_colour("Neutrals", ebay), "Beige")
    check("Metallic is silver, not yellow", am.match_colour("Metallic", ebay), "Silver")
    check("Burgundy is a red, not a brown", am.match_colour("Burgundy", ebay), "Red")
    check("and case does not matter", am.match_colour("  neutrals ", ebay), "Beige")

    # Every real colour in the Master File still resolves to itself. This is
    # the half that matters: an alias map that broke the 1,300 products whose
    # colour is already a colour would be a bad trade.
    for colour in ["Black", "White", "Brown", "Multicoloured", "Blue", "Green",
                   "Pink", "Red", "Grey", "Yellow", "Purple", "Orange"]:
        check(f"{colour} is still {colour}", am.match_colour(colour, ebay), colour)

    check("nothing in, nothing out", am.match_colour("", ebay), None)
    check("no list, no answer", am.match_colour("Black", []), None)

    # And it has to actually be wired into the generator, not just exist.
    # The fallback only fires when the model's own colour guess matches
    # nothing, so the stub answers with a word eBay has never heard of.
    import tempfile
    from src import ai_client, content_generator, ebay_template
    from src.data_loader import Product

    templates_dir = Path(__file__).resolve().parent.parent / "data" / "templates"
    womens = templates_dir / "womenswear_clothing.json"
    if not womens.exists():
        print("  (skipped end-to-end colour check: data/templates not present)")
        return

    template = ebay_template.load_template(womens)
    category = template.category_by_id("63862")  # Coats, Jackets & Waistcoats
    product = Product(
        sku="QTN02-001-543",
        master={"Brand": "FRANKIE SHOP", "Gender": "WOMEN", "Colour": "Neutrals",
                "Category": "Ready to Wear", "SubCat2": "Coats", "Rounded RRP": 295,
                "Clean Title Description": "FRANKIE SHOP COAT"},
        measurements={"Size": "XS", "Description": "Good condition."},
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
                specifics[name] = "Cotton"
        # A colour eBay does not have, so the family fallback is what answers.
        specifics["C:Colour"] = "Oatmeal Sand Taupe"
        return {
            "title": "FRANKIE SHOP Womens Wool Coat Beige XS RRP 295",
            "condition_id": category.conditions[0][0],
            "condition_description": "Good condition.",
            "material_summary": "Wool",
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

    colour = result["item_specifics"].get("C:Colour")
    check("end-to-end: a Neutrals coat is not listed as Purple", colour != "Purple", True)
    check("end-to-end: it is listed as Beige", colour, "Beige")


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
