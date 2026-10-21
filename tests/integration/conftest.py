import os
import uuid

import pytest
from sqlalchemy import delete

from dr_core.db import Database
from dr_core.db.models import ResearchProject
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.schemas import ProjectCreate, RunCreate


@pytest.fixture(scope="session")
def database():
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://enforge:enforge_dev_password@localhost:5432/enforge",
    )
    db = Database(url)
    yield db
    db.dispose()


@pytest.fixture
def session(database):
    with database.session_scope() as current_session:
        yield current_session


@pytest.fixture
def project_factory(session):
    created: list[uuid.UUID] = []

    def factory(title: str | None = None, **overrides):
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=title or f"Phase 2 test {uuid.uuid4().hex[:8]}",
                topic="Traceable evidence and database constraints",
                **overrides,
            )
        )
        created.append(project.id)
        return project

    yield factory

    for project_id in created:
        session.execute(
            delete(ResearchProject).where(ResearchProject.id == project_id)
        )


@pytest.fixture
def project(project_factory):
    return project_factory()


@pytest.fixture
def run(session, project):
    return RunRepository(session).create(project.id, RunCreate())
