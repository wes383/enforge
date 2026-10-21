from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


class StorageError(Exception):
    """Base class for storage failures."""


class InvalidStorageKey(StorageError):
    """Raised when a storage key could escape the configured root."""


class StorageFileNotFound(StorageError):
    """Raised when a storage key does not resolve to a readable file."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    modified_at: datetime


class FileStorage(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes | BinaryIO) -> StoredObject:
        """Persist bytes or a readable stream at a server-generated key."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open a stored object for binary reading."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete an object, returning False when it did not exist."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether the key resolves to a regular file."""

    @abstractmethod
    def stat(self, key: str) -> StoredObject:
        """Return metadata for a stored object."""

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """List object keys under a validated key prefix."""

    def read(self, key: str) -> bytes:
        with self.open(key) as handle:
            return handle.read()
