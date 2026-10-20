# Preamato Listing Helper

A Streamlit app + CLI that turns a Master File (.xlsx) + Pictures & Measurements file (+ optional eBay bulk template) into a ready-to-upload eBay bulk CSV, using the Anthropic API to write per-product titles, descriptions, and item specifics. Deployed on Streamlit Community Cloud, redeploying automatically from `main`.

Owner: Sammy. This file is read automatically by Claude Code at the start of every session in this repo, so keep it accurate and keep it short — it's the one piece of project memory that travels with the code itself.

## Before you touch anything: run the tests

```
python3 tests/run_all.py
```

This imports every module under `src/` before running anything (catches Python-version import errors immediately, see below), then runs the sizing checks and the listing checks. Everything must pass before a commit. A red run is safe and leaves nothing committed — just fix it and rerun.

## The Python version trap

The Mac's `python3` (the one that runs the tests and that `deploy.sh` used to use) is **3.9**. Streamlit Cloud runs something newer. A fix can be correct on a newer Python and still fail here. Concretely:

- Never write a bare `str | None` / `list[dict]` style union as a runtime expression (e.g. in a `Callable[[...], ...]` alias) — Python 3.9 can't evaluate it even with `from __future__ import annotations`, since `from __future__ import annotations` only defers *annotations*, not expressions. Use `Optional[str]` / `typing.List[dict]` for anything that gets evaluated at runtime, or keep `str | None` only inside actual type-hint positions (those are fine, since they're deferred).
- `tests/run_all.py` prints the Python version it ran under and will say exactly which file failed to import if this happens — read that message, don't guess.

## Git commit messages: a real macOS bash bug

macOS ships bash 3.2.6 (frozen, will never update). The pattern

```bash
git commit -m "$(cat <<'EOF'
...multi-line message with apostrophes...
EOF
)"
```

breaks on this shell with `unexpected EOF while looking for matching ')'` when the message body has more than a couple of embedded apostrophes. It's a real parser bug in bash 3.2, not a typo.

**Always commit like this instead:**

```bash
cat > /tmp/commit_msg.txt <<'MSGEOF'
Your commit message here.
Can have multiple lines and apostrophes fine.
MSGEOF
git commit -F /tmp/commit_msg.txt
```

Keep commit messages **ASCII only** — curly quotes and the £ sign have come out mangled in this shell's locale before. Write "GBP" or "pound" instead of £, and straight quotes only.

## Deploying

```
git add -A
git commit -F /tmp/commit_msg.txt   # see above, never -m with a heredoc
git push
```

Streamlit redeploys from `main` automatically, about two minutes. **Reboot the app from Manage app before the next run** — it can otherwise keep serving the old build.

After every reboot, two settings reset and need rechecking in the app itself:
- **Test run only** goes back to on, limited to 5 products
- **Price slider** goes back to 50% of RRP

## eBay business policies

Shipping/return/payment profile names in `src/config.py` (`SHIPPING_PROFILE` etc.) must match **exactly** what's live in eBay Seller Hub. If a policy gets renamed on eBay's side (it has happened before), every row using the old name fails upload with "invalid postage policy" (or the return/payment equivalent) — this is an eBay-side rename, not a data bug, and the fix is just updating the constant to match the new name exactly.

## QC / held-back rows

Rows eBay would refuse (missing photos, contradictory data, etc.) are never written into the main upload CSV. They go to a sibling `... NEEDS ATTENTION.csv` file instead, with a `WHY THIS ROW IS HELD BACK` column naming the reason. That column must be dropped before a fixed row is re-uploaded.

## Linnworks condition notes

Every run also writes `... LINNWORKS CONDITION NOTES.csv` alongside the main upload: a Linnworks Extended Properties import (`SKU, Property Name, Property Value, Property Type`, Property Name always `ConditionNotes`, Property Type always `Attribute`). Only rows that are both (a) in the live upload file (not held back) and (b) have a non-empty `ConditionDescription` (new/condition-1000 items never get one) are included. Built in `src/build.py` (`write_linnworks_condition_notes`), wired through `src/pipeline.py` (`LinnworksNotes` dataclass, 7th element of `pipeline.run()`'s return tuple) and surfaced in both `app.py` and `src/main.py`.

## Credentials

GitHub auth lives in the macOS keychain (`credential.helper osxkeychain`) — `git push` should never prompt. If it does, see the GitHub PAT settings: Repository access must be scoped to `Only select repositories → preamato-listing-helper` *before* the Contents permission section even appears (easy to miss — this cost an hour once). Set Contents to Read and write.

**Never paste a token, password, or API key into a Claude chat, local or cloud.** If one ever gets pasted anywhere outside the keychain store command, treat it as compromised and regenerate it immediately, it doesn't matter whether it was "only used once."

## Deeper history

Business-side knowledge (eBay title/sizing/colour/category rules, batch review notes, past incidents) lives in the "ebay listing creation" Claude Project on claude.ai, not in this repo. That's reference material for *listing content* decisions; this file is for *this codebase's* mechanics. Pull a specific doc over into a conversation with Claude Code if a task needs it — there's no automatic link between the two.
