"""Loads eBay's category tree CSV (L1..L6 outline + Category ID) and turns it
into a flat list of leaf categories with full breadcrumb paths, searchable by
keyword so we can hand the AI a short candidate shortlist instead of the
whole ~16k-row tree.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

LEVELS = ["L1", "L2", "L3", "L4", "L5", "L6"]


@dataclass
class Category:
    category_id: str
    path: tuple[str, ...]  # e.g. ("Clothes, Shoes & Accessories", "Women", "Dresses")

    @property
    def full_path(self) -> str:
        return " > ".join(self.path)

    @property
    def keywords(self) -> set[str]:
        text = " ".join(self.path).lower()
        return set(re.findall(r"[a-z0-9]+", text))


def load_categories(path: str | Path) -> list[Category]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    # Find the header row (contains "L1" and "Category ID").
    header_idx = next(i for i, r in enumerate(rows) if r[:1] == ["L1"])
    data_rows = rows[header_idx + 1:]

    stack: list[str] = []  # current path, indexed by depth
    parsed = []  # (depth, path_tuple, category_id)

    for row in data_rows:
        if not row or not any(row):
            continue
        levels = row[:6]
        cat_id = row[6].strip() if len(row) > 6 else ""
        if not cat_id:
            continue
        depth = next((i for i, v in enumerate(levels) if v.strip()), None)
        if depth is None:
            continue
        name = levels[depth].strip()
        stack = stack[:depth] + [name]
        parsed.append((depth, tuple(stack), cat_id))

    categories = []
    for i, (depth, path, cat_id) in enumerate(parsed):
        is_leaf = (i == len(parsed) - 1) or (parsed[i + 1][0] <= depth)
        if is_leaf:
            categories.append(Category(category_id=cat_id, path=path))

    return categories


def top_level_categories(categories: list[Category]) -> list[str]:
    """The ~35 department names at the root of the tree (e.g. 'Clothes, Shoes
    & Accessories', 'Jewellery & Watches'). Picking one of these first, before
    keyword search, keeps unrelated branches (Baby, Sporting Goods, Wholesale
    & Job Lots, Business & Industrial hydraulics...) out of the candidate
    list entirely — critical, since generic query terms like "footwear" or
    "women" otherwise match thousands of irrelevant leaves across those
    branches and drown out the real match."""
    return sorted({c.path[0] for c in categories})


def filter_by_l1(categories: list[Category], l1_name: str) -> list[Category]:
    return [c for c in categories if c.path[0] == l1_name]


def search(categories: list[Category], query_terms: list[str], top_n: int = 25) -> list[Category]:
    """Rank leaf categories by IDF-weighted keyword overlap with query_terms,
    best first. Intended to run over an already department-narrowed list
    (see filter_by_l1) — run over the full ~14k-category tree, generic terms
    still dominate badly even with IDF weighting, since e.g. "women" appears
    in categories across dozens of unrelated departments."""
    terms = set()
    for t in query_terms:
        terms.update(re.findall(r"[a-z0-9]+", t.lower()))
    terms = {t for t in terms if len(t) > 2}
    if not terms or not categories:
        return []

    doc_count = len(categories)
    df: dict[str, int] = {}
    for cat in categories:
        for kw in cat.keywords & terms:
            df[kw] = df.get(kw, 0) + 1

    import math
    idf = {t: math.log((doc_count + 1) / (df.get(t, 0) + 1)) + 1 for t in terms}

    scored = []
    for cat in categories:
        overlap = cat.keywords & terms
        if overlap:
            score = sum(idf[t] for t in overlap)
            scored.append((score, cat))
    scored.sort(key=lambda x: (-x[0], len(x[1].path)))
    return [c for _, c in scored[:top_n]]
