from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dr_core.db.models import Artifact, SourceVersion
from dr_core.storage.base import FileStorage

DEFAULT_IGNORED_PREFIXES = ("_dev/", "_health/", "_tmp/")


@dataclass(frozen=True, slots=True)
class TrackedFile:
    record_type: Literal["source_version", "artifact"]
    record_id: UUID
    project_id: UUID
    storage_key: str


@dataclass(frozen=True, slots=True)
class FileReconciliationReport:
    checked_at: datetime
    checked_files: int
    missing_files: tuple[TrackedFile, ...]
    orphan_keys: tuple[str, ...]
    ignored_prefixes: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.missing_files and not self.orphan_keys


def reconcile_files(
    session: Session,
    storage: FileStorage,
    *,
    ignore_prefixes: tuple[str, ...] = DEFAULT_IGNORED_PREFIXES,
) -> FileReconciliationReport:
    tracked: list[TrackedFile] = []

    version_rows = session.execute(
        select(
            SourceVersion.id,
            SourceVersion.project_id,
            SourceVersion.storage_key,
        ).where(SourceVersion.storage_key.is_not(None))
    ).all()
    for record_id, project_id, storage_key in version_rows:
        if storage_key:
            tracked.append(
                TrackedFile(
                    record_type="source_version",
                    record_id=record_id,
                    project_id=project_id,
                    storage_key=storage_key,
                )
            )

    artifact_rows = session.execute(
        select(
            Artifact.id,
            Artifact.project_id,
            Artifact.storage_key,
        )
    ).all()
    for record_id, project_id, storage_key in artifact_rows:
        tracked.append(
            TrackedFile(
                record_type="artifact",
                record_id=record_id,
                project_id=project_id,
                storage_key=storage_key,
            )
        )

    known_keys = {item.storage_key for item in tracked}
    missing_files = tuple(
        item for item in tracked if not storage.exists(item.storage_key)
    )
    orphan_keys = tuple(
        key
        for key in storage.list_prefix("")
        if key not in known_keys
        and not any(key.startswith(prefix) for prefix in ignore_prefixes)
    )

    return FileReconciliationReport(
        checked_at=datetime.now(tz=UTC),
        checked_files=len(tracked),
        missing_files=missing_files,
        orphan_keys=orphan_keys,
        ignored_prefixes=ignore_prefixes,
    )
