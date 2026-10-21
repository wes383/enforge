import io
import os

import pytest

from dr_core.storage import (
    InvalidStorageKey,
    LocalFileStorage,
    StorageFileNotFound,
)
from dr_core.storage.smoke_keys import normalize_file_id, smoke_original_key


@pytest.fixture
def storage(tmp_path):
    return LocalFileStorage(tmp_path / "files")


def test_save_read_stat_list_and_delete(storage):
    key = "projects/p1/sources/s1/original.txt"
    content = b"traceable evidence"

    stored = storage.save(key, content)

    assert stored.key == key
    assert stored.size_bytes == len(content)
    assert storage.read(key) == content
    assert storage.exists(key) is True
    assert storage.stat(key).size_bytes == len(content)
    assert storage.list_prefix("projects/p1") == [key]

    assert storage.delete(key) is True
    assert storage.exists(key) is False
    assert storage.delete(key) is False
    assert storage.list_prefix("projects/p1") == []


def test_save_accepts_binary_stream_and_overwrites(storage):
    key = "artifacts/report.md"
    storage.save(key, io.BytesIO(b"v1"))
    storage.save(key, b"version two")

    assert storage.read(key) == b"version two"


def test_list_prefix_is_scoped_and_sorted(storage):
    storage.save("projects/p1/b.txt", b"b")
    storage.save("projects/p1/a.txt", b"a")
    storage.save("projects/p2/c.txt", b"c")

    assert storage.list_prefix("projects/p1") == [
        "projects/p1/a.txt",
        "projects/p1/b.txt",
    ]
    assert len(storage.list_prefix("")) == 3


def test_missing_file_raises_storage_file_not_found(storage):
    with pytest.raises(StorageFileNotFound):
        storage.read("missing/file.txt")
    with pytest.raises(StorageFileNotFound):
        storage.stat("missing/file.txt")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute",
        "../escape",
        "a/../../b",
        "a/./b",
        "a//b",
        "a\\b",
        "C:/windows",
        "a:b",
        "nul\x00byte",
    ],
)
def test_unsafe_keys_are_rejected(storage, key):
    with pytest.raises(InvalidStorageKey):
        storage.save(key, b"blocked")


def test_exists_hides_invalid_keys(storage):
    assert storage.exists("../escape") is False


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs extra privileges on Windows")
def test_symlink_escape_is_rejected(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, storage.root / "linked")

    with pytest.raises(InvalidStorageKey):
        storage.save("linked/escape.txt", b"blocked")


def test_smoke_key_normalizes_uuid():
    raw = "A4A2D2C8-5D5E-4E86-9C2A-B3F6C8BD0E55"
    expected = raw.lower()
    assert smoke_original_key(raw) == f"_dev/storage-smoke/{expected}/original.bin"
    with pytest.raises(ValueError):
        normalize_file_id("not-a-uuid")
