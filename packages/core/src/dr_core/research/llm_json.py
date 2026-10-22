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
