MAX_RETRY_COUNTDOWN_SECONDS = 30


def retry_countdown_seconds(attempt: int) -> int:
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    return min(2 ** (attempt - 1), MAX_RETRY_COUNTDOWN_SECONDS)
