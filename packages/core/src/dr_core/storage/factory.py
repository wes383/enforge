from functools import lru_cache

from dr_core.config import get_settings
from dr_core.storage.base import FileStorage
from dr_core.storage.local import LocalFileStorage


@lru_cache
def get_file_storage() -> FileStorage:
    return LocalFileStorage(get_settings().file_storage_root)
