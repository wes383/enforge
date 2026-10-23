import json

from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.schema import align_embedding_column


def main() -> int:
    settings = get_settings()
    database = get_database()
    with database.session_scope() as session:
        report = align_embedding_column(session, settings.embedding_dimension)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
