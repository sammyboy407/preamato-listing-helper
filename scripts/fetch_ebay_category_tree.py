#!/usr/bin/env python3
"""One-off / occasional maintenance script: pulls eBay's REAL, CURRENT, COMPLETE
category tree for a marketplace (default: the UK site, ebay.co.uk) straight from
eBay's own Taxonomy API, and writes it to data/Ebay Category Codes.csv in the
L1..L6 + Category ID format that src/category_codes.py reads.

Why this exists
----------------
There is no trustworthy static download of eBay's full category list anymore —
the old pics.ebay.com/.../CategoryIDs-UK.csv File Exchange links are dead, and
third-party copies floating around the web are not reliably current. Getting
this wrong means listing into a category ID that doesn't exist or has been
retired, which can silently break or suppress a listing. So this script goes
straight to eBay's own API instead of trusting a scraped or stale file.

Requirements
------------
A FREE eBay developer account (no cost, a few minutes, uses your existing eBay
login):
  1. Go to https://developer.ebay.com and sign in / register as a developer.
  2. Go to "My Account" -> "Application Keys" and create a PRODUCTION keyset.
  3. Copy the "App ID (Client ID)" and "Cert ID (Client Secret)".

This script only ever calls read-only category endpoints using an application
(client-credentials) token — it never touches your eBay account's listings,
orders, or seller data, and no seller login/consent step is involved.

Usage
-----
    export EBAY_APP_ID=your-app-id
    export EBAY_CERT_ID=your-cert-id
    python3 scripts/fetch_ebay_category_tree.py

    # Optional: a different marketplace (default EBAY_GB):
    python3 scripts/fetch_ebay_category_tree.py --marketplace EBAY_US

Re-run this occasionally (eBay's category tree changes rarely, but does
change) to refresh data/Ebay Category Codes.csv.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
API_BASE = "https://api.ebay.com/commerce/taxonomy/v1"
SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "Ebay Category Codes.csv"
MAX_LEVELS = 6  # matches src/category_codes.py's L1..L6


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
    url = f"{API_BASE}/get_default_category_tree_id?marketplace_id={marketplace_id}"
    result = _http_json(url, headers={"Authorization": f"Bearer {token}"})
    tree_id = result.get("categoryTreeId")
    if not tree_id:
        raise SystemExit(f"No categoryTreeId in response: {result}")
    return tree_id


def get_category_tree(token: str, category_tree_id: str) -> dict:
    url = f"{API_BASE}/category_tree/{category_tree_id}"
    return _http_json(url, headers={"Authorization": f"Bearer {token}"})


def flatten_tree(tree: dict) -> list[list[str]]:
    """Walks the nested rootCategoryNode -> childCategoryTreeNodes structure
    depth-first, pre-order, and emits one row per category in the same
    "name in the column matching its depth" layout as eBay's own classic
    category-list export — which is exactly what
    src/category_codes.py.load_categories() parses (it detects a leaf by
    checking whether the next row is at the same depth or shallower).
    """
    rows: list[list[str]] = []
    overflow_warnings: list[str] = []

    def walk(node: dict, depth: int) -> None:
        category = node.get("category", {})
        name = category.get("categoryName", "")
        cat_id = category.get("categoryId", "")
        if depth >= MAX_LEVELS:
            # Deeper than L6 — extremely rare (a handful of branches like
            # Business & Industrial go this deep). Fold the overflow into the
            # last column rather than silently dropping the category.
            row = [""] * MAX_LEVELS
            row[MAX_LEVELS - 1] = f"{rows[-1][MAX_LEVELS - 1]} > {name}" if rows and rows[-1][MAX_LEVELS - 1] else name
            overflow_warnings.append(f"{cat_id} ({name}) exceeds L{MAX_LEVELS} depth — folded into last column")
        else:
            row = [""] * MAX_LEVELS
            row[depth] = name
        rows.append(row + [cat_id])
        for child in node.get("childCategoryTreeNodes", []) or []:
            walk(child, depth + 1)

    root = tree.get("rootCategoryNode", {})
    for child in root.get("childCategoryTreeNodes", []) or []:
        walk(child, 0)

    if overflow_warnings:
        print(f"Warning: {len(overflow_warnings)} categories deeper than L{MAX_LEVELS} — see folded names.", file=sys.stderr)

    return rows


def write_csv(rows: list[list[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([f"L{i + 1}" for i in range(MAX_LEVELS)] + ["Category ID"])
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--marketplace", default="EBAY_GB", help="eBay marketplace ID (default: EBAY_GB, i.e. ebay.co.uk)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output CSV path")
    args = parser.parse_args()

    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        raise SystemExit(
            "Set EBAY_APP_ID and EBAY_CERT_ID first (free keys from developer.ebay.com "
            "-> My Account -> Application Keys -> create a Production keyset). "
            "See the docstring at the top of this script for the full steps."
        )

    print("Requesting application token...")
    token = get_application_token(app_id, cert_id)

    print(f"Looking up default category tree for {args.marketplace}...")
    tree_id = get_default_category_tree_id(token, args.marketplace)
    print(f"  Tree ID: {tree_id}")

    print("Downloading full category tree (this can take a few seconds)...")
    tree = get_category_tree(token, tree_id)

    print("Flattening to L1..L6 + Category ID rows...")
    rows = flatten_tree(tree)
    print(f"  {len(rows)} categories total.")

    output_path = Path(args.output)
    write_csv(rows, output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
