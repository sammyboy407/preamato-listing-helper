"""Reading a garment's colour off its own photograph.

Sammy, 08.09.26: "how do we get the correct colour - can you pull it from the
imagery?"

Colour is REQUIRED on every clothing category and was not on shoes, so the
first clothing batch is the first time it has mattered. The Master File
records colour *families* rather than colours — Neutrals on 118 products,
Metallic on 133, Burgundy on 28 — and eBay's list has an entry for none of
them. Matched on the word alone, "Neutrals" scored closest to Purple and
"Metallic" closest to Yellow.

Every product in this catalogue is photographed on a white sweep before it is
listed. The answer is in the picture, and nowhere else in the data.

Deliberately narrow:

- It only runs when the recorded colour is NOT already one of eBay's own
  values. 1,300 of 1,752 products say Black, White, Brown, Blue and so on;
  those cost nothing and behave exactly as they did for the 295 shoes that
  are already live. Roughly one product in six reaches this.
- It picks from the category's own closed list, as an enum. The model cannot
  answer "Oatmeal"; it answers Beige or Ivory or nothing.
- It answers about the garment, not the photograph — a white sweep, a hanger,
  a shadow and a colour-checker card are all in shot and none of them are the
  item.
- It may answer NONE. A genuinely two-tone garment should be Multicoloured,
  and something it cannot call should fall through to the family map rather
  than be guessed at.
- Any failure at all — no photo, a CDN hiccup, an API error — falls back to
  the family map from fix 28. A colour is never left worse than it was.
"""
from __future__ import annotations

from . import ai_client, config

SYSTEM = (
    "You are looking at one studio photograph of a single preloved designer "
    "garment or accessory, shot on a plain white background for an eBay listing. "
    "Name the colour of the ITEM ITSELF, choosing only from the list of eBay "
    "colours given to you.\n\n"
    "Ignore everything that is not the item: the white background sweep, any "
    "hanger, mannequin, stand, shadow, reflection, colour-reference card, tag or "
    "packaging.\n\n"
    "Judge the item by its main body — the outer shell of a coat, the body of a "
    "knit — not by its trims, buttons, zips, lining or logo. A black coat with "
    "gold buttons is Black.\n\n"
    "Answer Multicoloured only when the item genuinely has no single dominant "
    "colour, such as a print, a check or a panelled design. Two shades of the "
    "same colour is not multicoloured.\n\n"
    "For a metallic item, choose Gold or Silver by which it actually looks like.\n\n"
    "For anything in the neutral band — cream, ecru, oatmeal, sand, stone, taupe, "
    "camel, off-white, undyed or natural — answer Beige. Do not answer Ivory. "
    "Beige is the answer for all of them. Only answer White for a clean, "
    "unmistakable white, and only answer Brown for a distinctly brown shade "
    "darker than the neutral band.\n\n"
    "If the photograph does not let you tell — it is too dark, the item is barely "
    "in frame, or you genuinely cannot choose — answer NONE. A wrong colour on a "
    "listing is worse than a missing one."
)


def _schema(valid_values: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "colour": {
                "type": "string",
                "enum": list(valid_values) + ["NONE"],
                "description": "The item's colour, copied exactly from the list, or NONE.",
            },
            "reasoning": {"type": "string", "description": "One short sentence."},
        },
        "required": ["colour", "reasoning"],
    }


def colour_from_image(
    image_url: str | None,
    valid_values: list[str] | None,
    recorded_colour=None,
    title=None,
    model: str = config.MODEL,
) -> str | None:
    """One eBay colour read off the photograph, or None.

    Never raises. The caller has a working fallback and a run of 300 products
    must not die because one CDN link went stale."""
    if not image_url or not valid_values:
        return None

    described = " ".join(str(title or "").split())
    recorded = " ".join(str(recorded_colour or "").strip().split())
    user = "Which of the listed eBay colours is this item?"
    if described:
        user += f"\n\nThe item is described internally as: {described}"
    if recorded:
        # Given as context, not as the answer. "Neutrals" and "Metallic" are
        # families, so this narrows the choice without settling it.
        user += (
            f"\nOur own records call its colour {recorded!r}, which may be a broad "
            f"family rather than a colour. Treat it as a hint only; the photograph "
            f"is what decides."
        )

    try:
        result = ai_client.call_structured(
            system=SYSTEM,
            user=user,
            tool_name="pick_colour",
            input_schema=_schema(valid_values),
            image_url=image_url,
            max_retries=2,
            model=model,
        )
    except Exception:  # noqa: BLE001 - a colour is never worth failing a batch over
        return None

    answer = str(result.get("colour") or "").strip()
    if not answer or answer == "NONE":
        return None
    # Only ever a value eBay actually offers for this category.
    if answer not in valid_values:
        return None
    return _prefer_beige(answer, valid_values)


# Sammy, 08.09.26, on beige against ivory against off-white being genuinely
# hard to tell apart in a photograph: "i would say always go with beige".
#
# Her call, and it is the right one: the two are a shade apart to a buyer,
# beige is the far commoner search term, and a consistent answer across a
# catalogue reads better than a coin flip taken separately on every item.
#
# It is in code as well as in the prompt on purpose. The prompt asks the
# model not to choose Ivory; this makes sure it cannot, whatever a future
# edit to the wording does. The Master File has no Ivory in it — the only
# way that value can ever appear is from the photograph — so collapsing it
# here can never overwrite something a person recorded.
NEUTRALS_ALWAYS = {"Ivory": "Beige"}


def _prefer_beige(answer: str, valid_values: list[str]) -> str:
    preferred = NEUTRALS_ALWAYS.get(answer)
    if preferred and preferred in valid_values:
        return preferred
    return answer


# ---------------------------------------------------------------------------
# Item specifics read off the photograph
# ---------------------------------------------------------------------------
#
# Sammy, 16.09.26, looking at eBay's own suggestion panel on a live Saint
# Laurent listing: "look at all the item specs we missed out on ... these
# will get our listings views and clicks."
#
# She is right, and the gap is large: the app knows 40 aspects for Bags
# (169291) and fills 9. Every one eBay suggested was blank -- Closure,
# Finish, Handle Style, Shape, Accessories, Size.
#
# They were blank for a good reason. The content generator works from TEXT:
# title, composition, colour, size, measurements. Nothing in that data says
# whether a clasp is magnetic or a bag is quilted. eBay could suggest them
# because eBay.ai looked at the photographs. So could we -- every item here
# is shot on a white sweep before it is listed, and that is where these
# answers live.
#
# The same three rules the colour pass earned apply:
#   - it only ever runs on aspects that came back BLANK. It cannot overwrite
#     something the data, or a person, already decided.
#   - it answers from the category's own closed list, as a schema enum, so
#     it structurally cannot invent "Oatmeal" or "Character: Aladdin".
#   - it may answer UNSURE, and usually should. A blank Preferred field
#     costs nothing; a wrong one misleads a buyer and eBay's search.

ASPECTS_SYSTEM = (
    "You are looking at one studio photograph of a single preloved designer item, "
    "shot on a plain white background for an eBay listing. Fill in the eBay item "
    "specifics you can see, choosing only from the options given for each one.\n\n"
    "Ignore everything that is not the item: the white background sweep, hangers, "
    "mannequins, stands, shadows, reflections and colour-reference cards. A dust "
    "bag, box or authenticity card shown alongside the item IS part of what is "
    "being sold and may be reported under Accessories.\n\n"
    "Answer UNSURE for anything this photograph does not actually show you. That "
    "is the expected answer for a great many of these and it is never the wrong "
    "one. Do not infer from the brand, do not reason from what such an item "
    "usually has, and do not guess at a detail hidden by the angle, the fold or "
    "the crop. If you cannot see a closure, it is UNSURE, not the closure that "
    "bag usually has.\n\n"
    "A blank field on a listing costs nothing. A confident wrong one misleads a "
    "buyer and eBay's own search, and is worse than leaving it out."
)

# Deliberately never asked of a photograph, whatever the category offers.
# Some of these a picture genuinely cannot settle; the rest are the fields
# that produced "Character: Aladdin" the last time something was forced to
# pick (see content_generator.NEVER_FILL_ASPECTS).
ASPECTS_NEVER_FROM_IMAGE = {
    "C:Brand", "C:Department", "C:Country of Origin", "C:MPN", "C:Size",
    "C:Model", "C:Product Line", "C:Character", "C:Theme", "C:Vintage",
    "C:Handmade", "C:Personalise", "C:Customised", "C:Year Manufactured",
    "C:Bag Depth", "C:Bag Height", "C:Bag Width", "C:Handle Drop", "C:Strap Drop",
}

UNSURE = "UNSURE"
MAX_ASPECTS_PER_CALL = 18
MAX_VALUES_PER_ASPECT = 40


def aspects_from_image(
    image_url: str | None,
    blank_specs: dict,
    title=None,
    model: str = config.MODEL,
) -> dict:
    """Values for blank item specifics, read off the item's own photograph.

    `blank_specs` maps aspect name -> AspectSpec, and must already be only
    the ones with no value. Returns only confident answers, only ones on the
    aspect's own list. Returns {} on any failure at all -- a batch of 75 does
    not die because one CDN link went stale."""
    if not image_url or not blank_specs:
        return {}
    askable = {
        name: spec for name, spec in blank_specs.items()
        if name not in ASPECTS_NEVER_FROM_IMAGE
        and spec.values and 1 < len(spec.values) <= MAX_VALUES_PER_ASPECT
    }
    if not askable:
        return {}
    # A long prompt is a worse prompt. Fewest options first, because those
    # are the ones a photograph most reliably settles.
    ordered = sorted(askable.items(), key=lambda kv: len(kv[1].values))[:MAX_ASPECTS_PER_CALL]

    properties = {}
    for name, spec in ordered:
        properties[name] = {
            "type": "string",
            "enum": list(spec.values) + [UNSURE],
            "description": f"{name[2:]}, or {UNSURE} if the photograph does not show it.",
        }
    schema = {"type": "object", "properties": properties,
              "required": [n for n, _ in ordered]}

    described = " ".join(str(title or "").split())
    user = "Fill in what you can actually see. UNSURE is expected for most of these."
    if described:
        user += f"\n\nThe item is listed as: {described}"

    try:
        result = ai_client.call_structured(
            system=ASPECTS_SYSTEM,
            user=user,
            tool_name="read_item_specifics",
            input_schema=schema,
            image_url=image_url,
            max_retries=2,
            model=model,
        )
    except Exception:  # noqa: BLE001 - item specifics never fail a batch
        return {}

    filled = {}
    for name, spec in ordered:
        answer = str(result.get(name) or "").strip()
        if not answer or answer == UNSURE:
            continue
        if answer in spec.values:
            filled[name] = answer
    return filled
