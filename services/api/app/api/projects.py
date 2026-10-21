from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status


from app.api.schemas import (
    ProjectListResponse,
    ProjectResponse,
)
from app.dependencies import SessionDep
from dr_core.db.repositories import ProjectRepository
from dr_core.schemas import ProjectCreate, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(data: ProjectCreate, session: SessionDep) -> ProjectResponse:
    project = ProjectRepository(session).create(data)
    session.commit()
    return ProjectResponse.model_validate(project)


@router.get("", response_model=ProjectListResponse)
def list_projects(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectListResponse:
    repository = ProjectRepository(session)
    return ProjectListResponse(
        items=[
            ProjectResponse.model_validate(project)
            for project in repository.list(limit=limit, offset=offset)
        ],
        total=repository.count(),
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID, session: SessionDep) -> ProjectResponse:
    project = ProjectRepository(session).get(project_id)
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    session: SessionDep,
) -> ProjectResponse:
    try:
        project = ProjectRepository(session).update(project_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: UUID, session: SessionDep) -> Response:
    ProjectRepository(session).soft_delete(project_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
