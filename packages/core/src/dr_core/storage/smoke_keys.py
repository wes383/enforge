from uuid import UUID

_SMOKE_PREFIX = "_dev/storage-smoke"


def normalize_file_id(file_id: str | UUID) -> str:
    try:
        return str(UUID(str(file_id)))
    except (TypeError, ValueError) as exc:
        raise ValueError("file_id must be a valid UUID") from exc


def smoke_original_key(file_id: str | UUID) -> str:
    return f"{_SMOKE_PREFIX}/{normalize_file_id(file_id)}/original.bin"


def smoke_result_key(file_id: str | UUID) -> str:
    return f"{_SMOKE_PREFIX}/{normalize_file_id(file_id)}/worker-result.json"
