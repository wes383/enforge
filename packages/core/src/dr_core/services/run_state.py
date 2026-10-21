from dr_core.db.enums import TERMINAL_RUN_STATUSES, RunStatus
from dr_core.db.repositories.exceptions import RepositoryError


class InvalidRunTransition(RepositoryError):
    pass


class RunAlreadyFinished(RepositoryError):
    pass


ALLOWED_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.PLANNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.SEARCHING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.SEARCHING: frozenset(
        {
            RunStatus.PROCESSING_SOURCES,
            RunStatus.BUILDING_EVIDENCE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.PROCESSING_SOURCES: frozenset(
        {RunStatus.BUILDING_EVIDENCE, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.BUILDING_EVIDENCE: frozenset(
        {
            RunStatus.WRITING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WRITING: frozenset(
        {RunStatus.VERIFYING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.VERIFYING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {
            RunStatus.PLANNING,
            RunStatus.SEARCHING,
            RunStatus.BUILDING_EVIDENCE,
            RunStatus.WRITING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in ALLOWED_RUN_TRANSITIONS[current]:
        raise InvalidRunTransition(
            f"Illegal run transition from {current.value} to {target.value}"
        )


def is_terminal_status(status: RunStatus) -> bool:
    return status in TERMINAL_RUN_STATUSES
