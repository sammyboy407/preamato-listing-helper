"""Thin wrapper around the Anthropic API for structured (tool-forced) JSON output."""
from __future__ import annotations

import os
import re
import time

import anthropic

from . import config

_client = None
_client_cache_key = None  # (api_key, workspace_id) _client was built with

# Occasionally a tool_use response comes back with a stray fragment of
# tool-call markup leaked into a string field (e.g. "</description>\n
# <parameter name=...>"). Treat any string value containing this as a bad
# response worth retrying rather than shipping it.
_LEAKED_MARKUP_RE = re.compile(r"</?parameter[ >]|</[a-z_]+>\s*$|^<[a-z_]+>", re.IGNORECASE)

# Errors where retrying is pointless - the request is misconfigured (bad key,
# wrong key type, missing workspace id, no permission) rather than a
# transient hiccup. Anthropic's own SDK exception hierarchy separates these
# from truly retryable errors (rate limits, timeouts, 5xx) cleanly, so we
# lean on that instead of guessing from status codes. Failing fast on these
# matters a lot for a batch of 100+ products: a misconfigured key used to
# mean every single product burned 3 retries (with 1s/2s backoff) before the
# batch died anyway, turning one wrong paste into minutes of wasted waiting.
_NON_RETRYABLE_EXCEPTIONS = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
)


def _contains_leaked_markup(value) -> bool:
    if isinstance(value, str):
        return bool(_LEAKED_MARKUP_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_leaked_markup(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_leaked_markup(v) for v in value)
    return False


def _friendly_config_error(e: Exception) -> str:
    """Turns one of the _NON_RETRYABLE_EXCEPTIONS above into plain guidance,
    for the specific cases seen in practice (04.09.26) rather than every
    theoretically possible one - falls back to the raw error otherwise."""
    msg = str(e)
    if "anthropic-workspace-id is required" in msg:
        return (
            "This API key needs a workspace id, or needs to be a single-workspace key.\n"
            "Easiest fix: in console.anthropic.com, go to Settings > API Keys > Create key, "
            "and when creating it, restrict it to one workspace (e.g. \"Default\") rather than "
            "leaving it set to all workspaces. Then use that new key here instead.\n"
            "Alternative: keep the current key, but also fill in the \"Workspace ID (optional)\" "
            "field above with that workspace's id from console.anthropic.com > Settings > Workspaces."
        )
    if isinstance(e, anthropic.AuthenticationError) or "invalid" in msg.lower() and "key" in msg.lower():
        return (
            "This API key was rejected as invalid by Anthropic. Double-check: it's freshly copied "
            "with no extra spaces, it hasn't been deleted/rotated in console.anthropic.com > Settings "
            "> API Keys, and the account it belongs to has billing set up (Settings > Billing)."
        )
    if isinstance(e, anthropic.PermissionDeniedError):
        return (
            "This API key was rejected for lacking permission. Check the workspace/organization "
            "it belongs to has access to the model this app uses, and that billing is active."
        )
    return f"Request rejected as misconfigured, not a temporary error: {msg}"


def get_client() -> anthropic.Anthropic:
    """Builds (or rebuilds) the Anthropic client from the current
    ANTHROPIC_API_KEY (and optional ANTHROPIC_WORKSPACE_ID). Deliberately
    re-checks both on every call rather than caching forever: app.py (the
    Streamlit UI) sets these fresh from whatever the user typed into the
    page, and the server process it runs in is long-lived and shared across
    reruns/users - a naive "build once" cache would silently keep using the
    FIRST key it ever saw (or the first bad one, e.g. an invalid key entered
    before a valid one), ignoring everything typed in for the rest of that
    process's life. Only rebuilds when the key or workspace id actually
    changes, so this stays as cheap as a plain cached client in the normal
    case (repeated calls with the same key, e.g. the CLI's one export per
    run)."""
    global _client, _client_cache_key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it in your shell before running, e.g.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
        )
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID") or None
    cache_key = (api_key, workspace_id)
    if _client is None or _client_cache_key != cache_key:
        extra_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else {}
        _client = anthropic.Anthropic(api_key=api_key, default_headers=extra_headers)
        _client_cache_key = cache_key
    return _client


def call_structured(
    system: str,
    user: str,
    tool_name: str,
    input_schema: dict,
    max_retries: int = 3,
    model: str = config.MODEL,
) -> dict:
    """Calls Claude with a single forced tool call and returns its input dict."""
    client = get_client()
    tool = {
        "name": tool_name,
        "description": f"Submit the {tool_name} result.",
        "input_schema": input_schema,
    }

    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == tool_name:
                    if _contains_leaked_markup(block.input):
                        raise RuntimeError(
                            "Response contained leaked tool-call markup in a field value."
                        )
                    return block.input
            raise RuntimeError("Model did not return the expected tool call.")
        except _NON_RETRYABLE_EXCEPTIONS as e:
            # Config problem, not a hiccup - retrying 2 more times with
            # backoff would just waste time and repeat the same failure.
            raise RuntimeError(_friendly_config_error(e)) from e
        except Exception as e:  # noqa: BLE001 - broad retry on any other API hiccup or bad response
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AI call failed after {max_retries} attempts: {last_err}")
