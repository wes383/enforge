from __future__ import annotations

import json
import re
from typing import Any


def parse_llm_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating a code fence.

    A model that thinks before it answers often appends a closing remark after
    the object, so a strict parse falls back to the first decodable object. A
    cut-off answer re-raises its original error so callers can still tell a
    truncation worth retrying from a reply that never opened an object.
    """
    cleaned = _strip_fence(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        value = _first_object(cleaned, exc)
    if not isinstance(value, dict):
        raise ValueError("LLM output must be a JSON object")
    return value


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


def _first_object(text: str, exc: json.JSONDecodeError) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            return decoder.raw_decode(text, index)[0]
        except ValueError:
            continue
    raise exc


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
