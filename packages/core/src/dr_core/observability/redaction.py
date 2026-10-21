from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|access[_-]?token|secret[_-]?key|client[_-]?secret|"
    r"password|passwd|authorization|cookie|credential|"
    r"(?<![a-z])token(?![a-z]))",
    re.IGNORECASE,
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bfc-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|client[_-]?secret|"
        r"token|password|passwd|authorization|cookie|credential)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)
_LOG_FILTER_MARKER = "_enforge_secret_redaction"


def redact_text(value: str) -> str:
    redacted = _SECRET_TEXT_PATTERNS[0].sub(
        lambda match: f"{match.group(1)} {REDACTED}",
        value,
    )
    for pattern in _SECRET_TEXT_PATTERNS[1:4]:
        redacted = pattern.sub(REDACTED, redacted)
    return _SECRET_TEXT_PATTERNS[4].sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        redacted,
    )


def sanitize_metadata(
    value: Any,
    *,
    key: str | None = None,
    max_string: int = 500,
    max_items: int = 50,
    depth: int = 0,
) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key):
        return REDACTED
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, str):
        truncated = value if len(value) <= max_string else value[:max_string] + "…"
        return redact_text(truncated)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_metadata(
                item_value,
                key=str(item_key),
                max_string=max_string,
                max_items=max_items,
                depth=depth + 1,
            )
            for item_key, item_value in list(value.items())[:max_items]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_metadata(
                item,
                max_string=max_string,
                max_items=max_items,
                depth=depth + 1,
            )
            for item in list(value)[:max_items]
        ]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))[:max_string]


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = ()
        return True


def install_log_redaction(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger()
    handlers = target.handlers or logging.getLogger().handlers
    for handler in handlers:
        if any(getattr(item, _LOG_FILTER_MARKER, False) for item in handler.filters):
            continue
        flt = SecretRedactingFilter()
        setattr(flt, _LOG_FILTER_MARKER, True)
        handler.addFilter(flt)
