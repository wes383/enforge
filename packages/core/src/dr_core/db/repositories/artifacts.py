from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from dr_core.db.models import Artifact
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import DuplicateEntity, EntityNotFound
from dr_core.schemas import ArtifactCreate


class ArtifactRepository(Repository):
    def register(
        self,
        project_id: UUID,
        run_id: UUID,
        data: ArtifactCreate,
    ) -> Artifact:
        self._require_run(run_id, project_id)

        existing = self.find_by_version(
            project_id,
            run_id,
            data.artifact_type,
            data.version,
        )
        if existing is not None:
            if existing.storage_key != data.storage_key:
                raise DuplicateEntity(
                    "Artifact version already registered with a different storage key"
                )
            return existing

        occupied = self.find_by_storage_key(data.storage_key)
        if occupied is not None:
            raise DuplicateEntity("Storage key is already registered to an artifact")

        artifact = Artifact(
            project_id=project_id,
            run_id=run_id,
            artifact_type=data.artifact_type,
            title=data.title,
            version=data.version,
            storage_key=data.storage_key,
            mime_type=data.mime_type,
            size_bytes=data.size_bytes,
            content_hash=data.content_hash,
            status=data.status,
            artifact_metadata=data.metadata,
        )

        def find_existing() -> Artifact | None:
            by_version = self.find_by_version(
                project_id,
                run_id,
                data.artifact_type,
                data.version,
            )
            if by_version is not None:
                if by_version.storage_key != data.storage_key:
                    raise DuplicateEntity(
                        "Artifact version already registered with a different storage key"
                    )
                return by_version
            by_key = self.find_by_storage_key(data.storage_key)
            if by_key is not None:
                raise DuplicateEntity(
                    "Storage key is already registered to an artifact"
                )
            return None

        return self._insert_or_get(artifact, find_existing)

    def find_by_version(
        self,
        project_id: UUID,
        run_id: UUID,
        artifact_type,
        version: int,
    ) -> Artifact | None:
        return self.session.scalar(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.run_id == run_id,
                Artifact.artifact_type == artifact_type,
                Artifact.version == version,
            )
        )

    def find_by_storage_key(self, storage_key: str) -> Artifact | None:
        return self.session.scalar(
            select(Artifact).where(Artifact.storage_key == storage_key)
        )

    def get(self, artifact_id: UUID, *, project_id: UUID) -> Artifact:
        artifact = self.session.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != project_id:
            raise EntityNotFound("Artifact not found")
        return artifact

    def list_for_run(self, project_id: UUID, run_id: UUID) -> list[Artifact]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(Artifact)
                .where(
                    Artifact.project_id == project_id,
                    Artifact.run_id == run_id,
                )
                .order_by(Artifact.created_at.asc(), Artifact.id.asc())
            )
        )
