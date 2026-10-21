import re

MAX_STORAGE_KEY_LENGTH = 512

# Metadata storage keys are server-generated and intentionally restricted to
# ASCII path segments so they remain portable across storage backends.
METADATA_STORAGE_KEY_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
_METADATA_STORAGE_KEY_RE = re.compile(METADATA_STORAGE_KEY_PATTERN)


def validate_metadata_storage_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("Storage key must be a non-empty string")
    if len(key) > MAX_STORAGE_KEY_LENGTH:
        raise ValueError("Storage key is too long")
    if not _METADATA_STORAGE_KEY_RE.fullmatch(key):
        raise ValueError(
            "Storage key must be a relative ASCII path without traversal segments"
        )
    return key
