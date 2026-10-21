import re
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum

from dr_core.storage.keys import METADATA_STORAGE_KEY_PATTERN

# Generated keys must be relative, ASCII-safe, and free of traversal segments.
RELATIVE_STORAGE_KEY_SQL = f"storage_key ~ '{METADATA_STORAGE_KEY_PATTERN}'"


def str_enum(enum_class: type[StrEnum], name: str, *, length: int = 32) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        validate_strings=True,
        # Persist StrEnum values (for example "queued"), not member names.
        values_callable=lambda enum_type: [member.value for member in enum_type],
    )


def is_relative_storage_key(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(METADATA_STORAGE_KEY_PATTERN, value)
    )
