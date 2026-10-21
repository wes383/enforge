class RepositoryError(Exception):
    """Base class for repository failures."""


class EntityNotFound(RepositoryError):
    """Raised when a record does not exist or is outside the requested project."""


class DuplicateEntity(RepositoryError):
    """Raised when a retry would create a conflicting business record."""
