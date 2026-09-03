"""Thin wrapper around the Anthropic API for structured (tool-forced) JSON output."""
from __future__ import annotations

import os
import re
import time

import anthropic

from . import config

_client = None

# Occasionally a tool_use response comes back with a stray fragment of
# tool-call markup leaked into a string field (e.g. "</description>\n
# <parameter name=...>"). Treat any string value containing this as a bad
# response worth retrying rather than shipping it.
_LEAKED_MARKUP_RE = re.compile(r"</?parameter[ >]|</[a-z_]+>\s*$|^<[a-z_]+>", re.IGNORECASE)


def _contains_leaked_markup(value) -> bool:
    if isinstance(value, str):
        return bool(_LEAKED_MARKUP_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_leaked_markup(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_leaked_markup(v) for v in value)
    return False


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it in your shell before running, e.g.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            )
        _client = anthropic.Anthropic(api_key=api_key)
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
        except Exception as e:  # noqa: BLE001 - broad retry on any API hiccup or bad response
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AI call failed after {max_retries} attempts: {last_err}")
