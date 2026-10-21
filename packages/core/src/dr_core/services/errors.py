class TransientRunError(Exception):
    """A retryable failure such as a temporary upstream or database outage."""


class PermanentRunError(Exception):
    """A non-retryable failure such as invalid persisted input."""
