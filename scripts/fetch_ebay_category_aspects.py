#!/usr/bin/env python3
"""Pulls real, per-category eBay data straight from eBay's own APIs — the same
data src/ebay_template.py currently gets by parsing a "Create listings in
bulk" template downloaded by hand from Seller Hub — for a specific list of
category IDs, and writes it out as one JSON file that ebay_template.py can
load directly (see ebay_template.load_json_template). This is the
API-driven alternative to the manual "Get template" download-and-tick-boxes
flow in Seller Hub: same underlying eBay data, no category-picker UI involved
at all, and it can cover every department (and be re-run any time the catalog
changes, e.g. once Makeup gets added) with one command instead of repeating
the Seller Hub flow by hand each time.

Two eBay APIs are used, both read-only, both using the same free application
(client-credentials) token as scripts/fetch_ebay_category_tree.py already
does — no seller login/consent step, nothing that can touch a live listing:

  - Taxonomy API's get_item_aspects_for_category: the Required/Recommended/
    Optional item-specific fields for a category, whether each one accepts a
    single value or multiple (eBay calls this itemToAspectCardinality —
    SINGLE or MULTI), and its closed list of valid values where it has one.
    This is strictly MORE precise than the Seller Hub xlsx download for one
    specific thing: the xlsx has no column that says whether a field is
    multi-select (see content_generator.MULTI_SELECT_ASPECTS — that set is
    hand-maintained today purely from outside knowledge, e.g. "Occasion is
    well known to be multi-select on eBay"). The API says so directly.
  - Metadata API's get_item_condition_policies: the valid Condition IDs and
    their category-specific display text (e.g. Shoes' "New with box" vs.
    Clothing's "New with tags") — the same data ebay_template.py currently
    reads from the xlsx Categories sheet.

IMPORTANT — schema not yet verified against a live response
-------------------------------------------------------------
This script was written from eBay's published API documentation, not by
testing against a real account (no API keys were available while writing
it). The JSON field names below (aspectConstraint.itemToAspectCardinality,
aspectConstraint.aspectRequired, aspectValues[].localizedValue,
itemConditionPolicies[].itemConditions[].conditionDescription, etc.) are my
best understanding of the real shape, but eBay APIs do shift field names and
nesting between versions. The FIRST time this runs with real keys, run it
with --category-ids for just one or two categories and --dump-raw first,
and check the printed raw JSON actually matches what _parse_aspects_response
/ _parse_condition_response below expect, before trusting a full run across
every category. I flagged this rather than silently guessing — a wrong
parse here would either crash loudly (safe) or, worse, silently produce an
empty aspect set for a category (unsafe — see the aspects.get(...) fallback
warnings this script prints).

Requirements
------------
The same free eBay developer account as scripts/fetch_ebay_category_tree.py:
  1. https://developer.ebay.com -> sign in / register as a developer.
  2. My Account -> Application Keys -> create a PRODUCTION keyset.
  3. Copy the App ID (Client ID) and Cert ID (Client Secret).

Usage
-----
    export EBAY_APP_ID=your-app-id
    export EBAY_CERT_ID=your-cert-id

    # Verify the parsing against ONE real category first:
    python3 scripts/fetch_ebay_category_aspects.py --category-ids 53557 --dump-raw

    # Once that looks right, run for every category a department needs, e.g.
    # Women's Shoes. For more than a couple of categories, put names in a
    # JSON file instead of --category-names — real eBay category names often
    # contain commas ("Coats, Jackets & Waistcoats"), which breaks
    # --category-names's own "id=Name,id=Name" splitting:
    #   data/category_lists/womenswear_shoes_names.json:
    #     {"53557": "Women's Shoes > Boots", "45333": "Women's Shoes > Flats", ...}
    python3 scripts/fetch_ebay_category_aspects.py \\
        --category-ids 53557,45333,55793,62107 \\
        --category-names-file data/category_lists/womenswear_shoes_names.json \\
        --output data/templates/womenswear_shoes.json

The resulting .json file can be passed anywhere src/pipeline.py currently
takes an uploaded .xlsx template path — see ebay_template.load_template
(dispatches on file suffix).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TAXONOMY_API_BASE = "https://api.ebay.com/commerce/taxonomy/v1"
METADATA_API_BASE = "https://api.ebay.com/sell/metadata/v1"
SCOPE = "https://api.ebay.com/oauth/api_scope"

# The fixed prefix/suffix listing columns and #INFO preamble are identical
# across every real per-category template eBay generates — see
# builtin_catalog.py's module docstring, which documents this same fact and
# hardcodes the same values from several real downloaded templates. Reused
# here rather than re-derived, since there's nothing category-specific about
# them to get from an API.
FIXED_LISTING_HEADERS_PREFIX = [
    "*Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)",
    "Custom label (SKU)",
    "Category ID",
    "Category name",
    "Title",
    "Start price",
    "Quantity",
    "Item photo URL",
    "Condition ID",
    "Description",
    "Format",
    "Duration",
    "Best Offer Enabled",
    "VAT%",
    "Immediate pay required",
    "Location",
    "Shipping profile name",
    "Return profile name",
    "Payment profile name",
]
FIXED_INFO_ROWS = [
    ["#INFO", "Version=1.0", "Template=fx_category_template_EBAY_GB"],
    ["#INFO Action and Category templates can be found on the Listing Templates tab of the Seller Hub Reports tab."],
    ["#INFO"],
]


def _http_json(url: str, *, method: str = "GET", headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"eBay API call failed ({e.code}) for {url}:\n{body}") from e


def get_application_token(app_id: str, cert_id: str) -> str:
    basic = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    body = f"grant_type=client_credentials&scope={SCOPE}".encode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    result = _http_json(TOKEN_URL, method="POST", headers=headers, data=body)
    token = result.get("access_token")
    if not token:
        raise SystemExit(f"No access_token in response: {result}")
    return token


def get_default_category_tree_id(token: str, marketplace_id: str) -> str:
    url = f"{TAXONOMY_API_BASE}/get_default_category_tree_id?marketplace_id={marketplace_id}"
    result = _http_json(url, headers={"Authorization": f"Bearer {token}"})
    tree_id = result.get("categoryTreeId")
    if not tree_id:
        raise SystemExit(f"No categoryTreeId in response: {result}")
    return tree_id


def fetch_aspects_raw(token: str, tree_id: str, category_id: str) -> dict:
    url = f"{TAXONOMY_API_BASE}/category_tree/{tree_id}/get_item_aspects_for_category?category_id={category_id}"
    return _http_json(url, headers={"Authorization": f"Bearer {token}"})


def fetch_condition_policy_raw(token: str, marketplace_id: str, category_id: str) -> dict:
    url = f"{METADATA_API_BASE}/marketplace/{marketplace_id}/get_item_condition_policies?filter=categoryIds:{{{category_id}}}"
    return _http_json(url, headers={"Authorization": f"Bearer {token}"})


def _parse_aspects_response(raw: dict, category_id: str) -> dict:
    """-> {aspect_name: {"level": "REQUIRED"|"PREFERRED"|"OPTIONAL", "multi": bool, "values": [...] | None}}
    See the module docstring's schema-not-verified warning — this is the
    part most likely to need a small key-name fix against a real response."""
    out: dict[str, dict] = {}
    aspects = raw.get("aspects") or []
    if not aspects:
        print(f"  WARNING: category {category_id} — 0 aspects in the response. Either this "
              f"category genuinely has none (rare), or the response shape didn't match what "
              f"this script expects — check with --dump-raw before trusting an empty result.",
              file=sys.stderr)
    for a in aspects:
        name = a.get("localizedAspectName")
        if not name:
            continue
        constraint = a.get("aspectConstraint") or {}
        required = bool(constraint.get("aspectRequired"))
        usage = str(constraint.get("aspectUsage") or "").upper()
        level = "REQUIRED" if required else ("PREFERRED" if usage == "RECOMMENDED" else "OPTIONAL")
        cardinality = str(constraint.get("itemToAspectCardinality") or "SINGLE").upper()
        values_raw = a.get("aspectValues") or []
        values = [v.get("localizedValue") for v in values_raw if v.get("localizedValue")] or None
        out[f"C:{name}"] = {"level": level, "multi": cardinality == "MULTI", "values": values}
    return out


def _parse_condition_response(raw: dict, category_id: str) -> list[tuple[int, str]]:
    policies = raw.get("itemConditionPolicies") or []
    for p in policies:
        if str(p.get("categoryId")) == str(category_id):
            conditions = p.get("itemConditions") or []
            out = []
            for c in conditions:
                cid = c.get("conditionId")
                label = c.get("conditionDescription") or c.get("conditionDisplayName")
                if cid and label:
                    try:
                        out.append((int(cid), str(label)))
                    except ValueError:
                        continue
            return out
    print(f"  WARNING: category {category_id} — no matching entry in the condition policy "
          f"response. Check with --dump-raw.", file=sys.stderr)
    return []


def build_json_template(
    token: str,
    tree_id: str,
    marketplace_id: str,
    category_ids: list[str],
    category_names: dict[str, str],
    dump_raw: bool = False,
    sleep_between: float = 0.2,
) -> dict:
    categories = []
    aspects_by_category: dict[str, dict] = {}

    for cat_id in category_ids:
        print(f"Fetching aspects for category {cat_id}...")
        aspects_raw = fetch_aspects_raw(token, tree_id, cat_id)
        if dump_raw:
            print(json.dumps(aspects_raw, indent=2)[:4000])
        aspects_by_category[cat_id] = _parse_aspects_response(aspects_raw, cat_id)

        print(f"Fetching condition policy for category {cat_id}...")
        cond_raw = fetch_condition_policy_raw(token, marketplace_id, cat_id)
        if dump_raw:
            print(json.dumps(cond_raw, indent=2)[:4000])
        conditions = _parse_condition_response(cond_raw, cat_id)

        categories.append({
            "category_id": cat_id,
            "category_name": category_names.get(cat_id, f"(unnamed category {cat_id})"),
            "conditions": conditions,
        })
        time.sleep(sleep_between)  # gentle on eBay's rate limits across many categories

    return {
        "listing_headers": FIXED_LISTING_HEADERS_PREFIX,
        "info_rows": FIXED_INFO_ROWS,
        "categories": categories,
        "aspects": aspects_by_category,
    }


def _parse_category_names_arg(raw: str | None) -> dict[str, str]:
    """--category-names "id1=Name One,id2=Name Two" -> {"id1": "Name One", ...}"""
    out = {}
    if not raw:
        return out
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        cid, name = pair.split("=", 1)
        out[cid.strip()] = name.strip()
    return out


def _load_category_names_file(path: str | None) -> dict[str, str]:
    """--category-names-file path/to/names.json -> {"id": "Name", ...}, loaded
    straight from a JSON object. This is the option to actually use once
    you're passing more than a couple of categories: --category-names splits
    on "," between pairs, which silently breaks on any real eBay category
    name that itself contains a comma (there are plenty — "Coats, Jackets &
    Waistcoats", "Cookware, Dining & Bar", "Curtains, Blinds & Accessories",
    etc. — the pair after the comma would get treated as a separate, broken
    "pair" and dropped). A JSON file has no such ambiguity."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"--category-names-file {path} must contain a JSON object of {{id: name}}")
    return {str(k): str(v) for k, v in data.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category-ids", required=True, help="Comma-separated eBay category IDs, e.g. 53557,45333,55793")
    parser.add_argument("--category-names", default="", help='Optional "id=Name,id=Name" — fine for a couple of categories, but breaks on names containing a comma. Prefer --category-names-file for anything bigger.')
    parser.add_argument("--category-names-file", default="", help="Optional path to a JSON file of {id: name} — the reliable way to pass names for more than a couple of categories (see --category-names). These names are shown to the app's AI category-matcher, so real, descriptive names matter for match quality, they are not just cosmetic.")
    parser.add_argument("--marketplace", default="EBAY_GB")
    parser.add_argument("--output", required=True, help="Output .json path, e.g. data/templates/womenswear_shoes.json")
    parser.add_argument("--dump-raw", action="store_true", help="Print each raw API response (truncated) — use for the first-ever run to verify the parsing before trusting a full batch")
    args = parser.parse_args()

    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        raise SystemExit(
            "Set EBAY_APP_ID and EBAY_CERT_ID first (free keys from developer.ebay.com "
            "-> My Account -> Application Keys -> create a Production keyset). "
            "Same keys as scripts/fetch_ebay_category_tree.py — reuse them if already set up."
        )

    category_ids = [c.strip() for c in args.category_ids.split(",") if c.strip()]
    category_names = _load_category_names_file(args.category_names_file)
    category_names.update(_parse_category_names_arg(args.category_names))

    print("Requesting application token...")
    token = get_application_token(app_id, cert_id)

    print(f"Looking up default category tree for {args.marketplace}...")
    tree_id = get_default_category_tree_id(token, args.marketplace)
    print(f"  Tree ID: {tree_id}")

    result = build_json_template(
        token, tree_id, args.marketplace, category_ids, category_names, dump_raw=args.dump_raw
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2))
    print(f"Wrote {output_path} — {len(category_ids)} categories.")


if __name__ == "__main__":
    main()
