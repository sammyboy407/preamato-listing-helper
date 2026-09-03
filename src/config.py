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
