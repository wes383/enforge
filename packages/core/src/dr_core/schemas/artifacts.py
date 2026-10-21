from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator

from dr_core.db.enums import ArtifactStatus, ArtifactType
from dr_core.schemas.base import SchemaModel
from dr_core.storage.keys import validate_metadata_storage_key

ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ArtifactCreate(SchemaModel):
    artifact_type: ArtifactType
    title: str | None = Field(default=None, max_length=1000)
    version: int = Field(default=1, ge=1)
    storage_key: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    content_hash: ContentHash
    status: ArtifactStatus = ArtifactStatus.READY
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_metadata_storage_key(value)
