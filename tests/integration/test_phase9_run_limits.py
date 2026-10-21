from __future__ import annotations

import uuid

import pytest

from dr_core.db.enums import RunStatus
from dr_core.db.repositories import RunRepository
from dr_core.schemas import RunCreate
from dr_core.services import ActiveRunLimitExceeded, create_run_with_event

pytestmark = pytest.mark.integration


def test_active_run_limit_is_enforced_per_project(
    database,
    session,
    project_factory,
):
    project = project_factory()
    session.commit()
    existing_key = f"limit-{uuid.uuid4()}"
    with database.session_scope() as session:
        repository = RunRepository(session)
        first = repository.create(
            project.id,
            RunCreate(idempotency_key=existing_key),
        )
        repository.create(project.id, RunCreate())
        repository.create(project.id, RunCreate())

    with pytest.raises(ActiveRunLimitExceeded):
        create_run_with_event(
            database,
            project.id,
            RunCreate(),
            max_active_runs=3,
        )

    reused, created = create_run_with_event(
        database,
        project.id,
        RunCreate(idempotency_key=existing_key),
        max_active_runs=3,
    )
    assert created is False
    assert reused.id == first.id


def test_completed_runs_do_not_count_toward_limit(
    database,
    session,
    project_factory,
):
    project = project_factory()
    session.commit()
    with database.session_scope() as session:
        repository = RunRepository(session)
        for _ in range(3):
            run = repository.create(project.id, RunCreate())
            run.status = RunStatus.COMPLETED
        session.flush()
        assert repository.count_active_for_project(project.id) == 0

    run, created = create_run_with_event(
        database,
        project.id,
        RunCreate(),
        max_active_runs=3,
    )
    assert created is True
    assert run.project_id == project.id
