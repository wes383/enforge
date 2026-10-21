from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.dispatch import (
    dispatch_research_run,
    dispatch_simulated_failure,
    revoke_research_run,
)
from app.services.uploads import (
    UploadValidationError,
    sanitize_original_filename,
    validate_upload,
)
from dr_core.config import get_settings
from dr_core.db.enums import RunStatus
from dr_core.services import (
    InvalidRunTransition,
    ensure_run_transition,
    is_terminal_status,
)
from dr_core.tasks import create_celery_app
from dr_core.tasks.names import EXECUTE_RESEARCH_RUN_TASK
from dr_core.tasks.retry import retry_countdown_seconds


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.QUEUED, RunStatus.PLANNING),
        (RunStatus.PLANNING, RunStatus.AWAITING_APPROVAL),
        (RunStatus.AWAITING_APPROVAL, RunStatus.SEARCHING),
        (RunStatus.SEARCHING, RunStatus.PROCESSING_SOURCES),
        (RunStatus.PROCESSING_SOURCES, RunStatus.BUILDING_EVIDENCE),
        (RunStatus.BUILDING_EVIDENCE, RunStatus.WRITING),
        (RunStatus.WRITING, RunStatus.VERIFYING),
        (RunStatus.VERIFYING, RunStatus.COMPLETED),
        (RunStatus.PLANNING, RunStatus.CANCELLED),
    ],
)
def test_legal_run_transitions(current, target):
    ensure_run_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.QUEUED, RunStatus.COMPLETED),
        (RunStatus.QUEUED, RunStatus.WRITING),
        (RunStatus.SEARCHING, RunStatus.WRITING),
        (RunStatus.VERIFYING, RunStatus.PLANNING),
        (RunStatus.COMPLETED, RunStatus.FAILED),
        (RunStatus.FAILED, RunStatus.CANCELLED),
        (RunStatus.CANCELLED, RunStatus.PLANNING),
    ],
)
def test_illegal_run_transitions(current, target):
    with pytest.raises(InvalidRunTransition):
        ensure_run_transition(current, target)


def test_terminal_statuses():
    for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        assert is_terminal_status(status)
    for status in (
        RunStatus.QUEUED,
        RunStatus.PLANNING,
        RunStatus.AWAITING_APPROVAL,
    ):
        assert not is_terminal_status(status)


def test_retry_countdown_is_exponential_and_capped():
    assert [retry_countdown_seconds(attempt) for attempt in range(1, 8)] == [
        1,
        2,
        4,
        8,
        16,
        30,
        30,
    ]
    with pytest.raises(ValueError):
        retry_countdown_seconds(0)


def test_pdf_upload_requires_magic_signature():
    kind = validate_upload("application/pdf", b"%PDF-1.7\nbody")
    assert kind.extension == ".pdf"
    assert kind.kind == "pdf"

    with pytest.raises(UploadValidationError) as error:
        validate_upload("application/pdf", b"<html>not a pdf</html>")
    assert error.value.status_code == 400


def test_text_uploads_must_be_utf8_without_nul_bytes():
    validate_upload("text/markdown", "标题\n正文".encode())
    validate_upload("text/html", b"<html><body>ok</body></html>")

    with pytest.raises(UploadValidationError):
        validate_upload("text/plain", b"\xff\xfe\x00bad")
    with pytest.raises(UploadValidationError):
        validate_upload("text/plain", b"text\x00with nul")
    with pytest.raises(UploadValidationError):
        validate_upload("text/plain", b"")


def test_unsupported_upload_types_are_rejected():
    with pytest.raises(UploadValidationError) as error:
        validate_upload("image/png", b"\x89PNG\r\n")
    assert error.value.status_code == 415

    with pytest.raises(UploadValidationError):
        validate_upload(None, b"content")


def test_filename_sanitization_strips_paths_and_control_characters():
    assert sanitize_original_filename(None) == "upload"
    assert sanitize_original_filename("../../etc/passwd") == "passwd"
    assert sanitize_original_filename("..\\..\\secret.txt") == "secret.txt"
    assert sanitize_original_filename('report "final"\r\n.txt') == "report final.txt"
    assert sanitize_original_filename("...") == "upload"
    assert len(sanitize_original_filename("a" * 300 + ".md")) == 255


def test_dispatch_uses_run_id_as_task_id_and_ids_only(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("app.services.dispatch.get_celery_client", lambda: client)
    run_id = uuid4()
    project_id = uuid4()

    dispatch_research_run(run_id, project_id)

    _, kwargs = client.send_task.call_args
    assert client.send_task.call_args.args[0] == EXECUTE_RESEARCH_RUN_TASK
    assert kwargs["args"] == [str(run_id), str(project_id)]
    assert kwargs["task_id"] == str(run_id)
    assert kwargs["queue"] == get_settings().celery_task_default_queue
    assert "topic" not in str(kwargs["args"])


def test_simulated_failure_dispatch_is_explicit(monkeypatch):
    client = MagicMock()
    client.send_task.return_value.id = "simulated-task-id"
    monkeypatch.setattr("app.services.dispatch.get_celery_client", lambda: client)
    run_id = uuid4()
    project_id = uuid4()

    task_id = dispatch_simulated_failure(run_id, project_id)

    assert task_id == "simulated-task-id"
    _, kwargs = client.send_task.call_args
    assert kwargs["args"] == [str(run_id), str(project_id), True]


def test_celery_isolates_queues_and_enforces_limits():
    settings = get_settings()
    app = create_celery_app()
    routes = app.conf.task_routes

    assert routes[EXECUTE_RESEARCH_RUN_TASK]["queue"] == (
        settings.celery_task_default_queue
    )
    assert routes["documents.parse"]["queue"] == settings.celery_parse_queue
    assert app.conf.task_soft_time_limit == (
        settings.run_task_soft_time_limit_seconds
    )
    assert app.conf.task_time_limit == settings.run_task_hard_time_limit_seconds
    assert app.conf.task_time_limit > app.conf.task_soft_time_limit
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_transport_options["visibility_timeout"] == 43200


def test_revoke_is_best_effort_without_terminating_workers(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("app.services.dispatch.get_celery_client", lambda: client)
    run_id = uuid4()

    revoke_research_run(run_id)

    client.control.revoke.assert_called_once_with(str(run_id), terminate=False)
