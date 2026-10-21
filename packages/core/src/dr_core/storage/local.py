import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from dr_core.storage.base import (
    FileStorage,
    InvalidStorageKey,
    StorageError,
    StorageFileNotFound,
    StoredObject,
)
from dr_core.storage.keys import MAX_STORAGE_KEY_LENGTH


class LocalFileStorage(FileStorage):
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise StorageError("File storage root is not a directory")

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key:
            raise InvalidStorageKey("Storage key must be a non-empty string")
        if len(key) > MAX_STORAGE_KEY_LENGTH:
            raise InvalidStorageKey("Storage key is too long")
        if key.startswith("/") or "\\" in key or "\x00" in key or ":" in key:
            raise InvalidStorageKey("Storage key contains forbidden characters")
        parts = key.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidStorageKey("Storage key contains an invalid path segment")

    def _resolve(self, key: str) -> Path:
        self._validate_key(key)
        candidate = (self._root / key).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise InvalidStorageKey("Storage key escapes the configured root") from exc
        if candidate == self._root:
            raise InvalidStorageKey("Storage key must point to a file")
        return candidate

    def save(self, key: str, data: bytes | BinaryIO) -> StoredObject:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as handle:
                if isinstance(data, (bytes, bytearray, memoryview)):
                    handle.write(data)
                else:
                    shutil.copyfileobj(data, handle, length=1024 * 1024)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            try:
                os.chmod(target, 0o640)
            except OSError:
                pass
        except OSError as exc:
            raise StorageError(f"Failed to write storage key: {key}") from exc
        finally:
            temp_path.unlink(missing_ok=True)
        return self.stat(key)

    def open(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        try:
            if not path.is_file():
                raise StorageFileNotFound(key)
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageFileNotFound(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to read storage key: {key}") from exc

    def delete(self, key: str) -> bool:
        path = self._resolve(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError(f"Failed to delete storage key: {key}") from exc
        self._prune_empty_directories(path.parent)
        return True

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except InvalidStorageKey:
            return False

    def stat(self, key: str) -> StoredObject:
        path = self._resolve(key)
        try:
            file_stat = path.stat()
        except FileNotFoundError as exc:
            raise StorageFileNotFound(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to stat storage key: {key}") from exc
        if not path.is_file():
            raise StorageFileNotFound(key)
        return StoredObject(
            key=key,
            size_bytes=file_stat.st_size,
            modified_at=datetime.fromtimestamp(file_stat.st_mtime, tz=UTC),
        )

    def list_prefix(self, prefix: str) -> list[str]:
        normalized = prefix.rstrip("/")
        if normalized:
            self._validate_key(normalized)
            base = self._resolve(normalized)
        else:
            base = self._root
        if not base.exists():
            return []
        keys: list[str] = []
        for path in base.rglob("*"):
            if path.is_file():
                keys.append(path.relative_to(self._root).as_posix())
        return sorted(keys)

    def _prune_empty_directories(self, start: Path) -> None:
        current = start
        while current != self._root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
        if not self._root.exists():
            self._root.mkdir(parents=True, exist_ok=True)
