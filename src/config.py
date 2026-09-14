"""Fixed workflow constants.

Edit these if your account's business policies / listing settings differ.
"""

# Must be exactly "Add" — confirmed against the real template's
# ListingStaticData sheet, which lists "Add" as the only valid Action value
# for this bulk-listing format. ("VerifyAdd" was carried over from an older,
# different eBay upload format and is not valid here.)
ACTION = "Add"
FORMAT = "FixedPrice"
DURATION = "GTC"
CURRENCY = "GBP"
VAT_PERCENT = 20
LOCATION = "EC2A 4NE"
SHIPPING_PROFILE = "PAF Postage Policy - 48 Hours £5 | 24 Hours £10"
RETURN_PROFILE = "PAF Returns Policy - 30 Day Returns"
PAYMENT_PROFILE = "eBay Payments (Immediate Payment Required)"
BEST_OFFER_ENABLED = True

# Schedule Time is opt-in, not a fixed default — leaving it blank means the
# listing starts immediately (eBay's own guidance: it's used only to start a
# listing at a specific future time, and can't be in the past). Set via
# --schedule-time on the CLI or the UI's scheduling option; only applied to
# a template that actually has a "Schedule Time" column. Format eBay expects:
SCHEDULE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # e.g. "2026-08-28 13:30:00", 24-hour, GMT

# Default selling price as a fraction of RRP (matches every example row seen
# so far) — overridable per run via --price-percent on the CLI or the UI's
# pricing slider. See build.compute_start_price.
START_PRICE_RATIO = 0.5

# What a listing prices at when the Master File has no RRP for it.
#
# Sammy's call, 14.09.26, after BRK02-001-026 was held out of three separate
# runs for the same missing RRP: "please can this stop happening set start
# price of whatver to 150".
#
# Before this, a missing RRP meant a £0 start price, which validation blocks
# outright (eBay refuses it), so the product simply did not list. That is
# safe but it stops a batch dead over one empty cell.
#
# The trade is real and worth stating plainly: this price owes nothing to
# what the item is worth. A £140 top and a £2,000 Fendi bag both land on
# £150 if neither has an RRP. So every listing priced this way is named in
# the checks report under its own heading, and the description says the RRP
# is not recorded rather than inventing one. On this account listings are
# normally scheduled a fortnight out, which is the window for correcting
# one before a buyer ever sees it.
#
# Set to None to go back to blocking a row with no RRP instead.
# Sammy, 14.09.26, having seen it work: "i dont like this - i want all
# items to have RRP". Switched OFF the same day it went in, and rightly:
# £150 on an item worth £2,000 is a worse outcome than a listing that does
# not go out, and 26 unlisted Brook St items had no RRP at the time. The
# missing RRP is a data problem and belongs fixed in the Master File.
#
# The mechanism stays because it costs nothing switched off, and because
# the decision is now a one-line change rather than an argument. Set it to
# a number to price no-RRP listings at that figure (every one of them is
# then named in the checks report); leave it None to block them, which is
# the behaviour this account runs on.
#
# What replaced it: pipeline.run now names every SKU with no RRP within
# seconds of a run starting, BEFORE the AI is called — so the fix happens
# before twenty minutes of processing rather than after.
FALLBACK_START_PRICE = None

QUANTITY_DEFAULT = 1

MODEL = "claude-sonnet-5"

# Fixed closing line every Description ends with.
SHIPPING_LINE = "Ships within 24 to 48 hours, fully insured tracked delivery"

# Used in the Description's "Condition:" line when the measurements file has
# no condition/defect notes for this SKU at all — flags the gap rather than
# inventing a condition.
CONDITION_PLACEHOLDER = (
    "[add from scan sheet defect column, no defect info was in this data row, "
    "please confirm against the physical item before listing]"
)
