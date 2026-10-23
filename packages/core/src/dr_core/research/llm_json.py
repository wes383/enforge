from __future__ import annotations

import json
import re
from typing import Any


def parse_llm_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating a code fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("LLM output must be a JSON object")
    return value


def looks_truncated(text: str, exc: json.JSONDecodeError) -> bool:
    """Return True when the parse error comes from a cut-off answer.

    A reasoning model that spends its whole budget thinking returns an answer
    that stops mid-string. That is worth one retry with a larger budget, while a
    reply that never opened a JSON object is a format problem a retry will not
    fix.
    """
    if "Unterminated string" in exc.msg:
        return True
    stripped = (text or "").strip()
    return bool(stripped) and exc.pos >= len(stripped) - 1
