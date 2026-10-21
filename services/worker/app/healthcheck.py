import sys

from app.celery_app import celery_app


def main() -> int:
    try:
        replies = celery_app.control.ping(timeout=2.0)
    except Exception as exc:
        print(f"Celery ping failed: {type(exc).__name__}")
        return 1
    if not replies:
        print("No Celery workers responded")
        return 1
    print(f"{len(replies)} Celery worker(s) responded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
