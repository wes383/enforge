import dataclasses
import json

from dr_core.db import get_database
from dr_core.maintenance import reconcile_files
from dr_core.storage import get_file_storage


def main() -> int:
    database = get_database()
    with database.session() as session:
        report = reconcile_files(session, get_file_storage())

    payload = dataclasses.asdict(report)
    print(json.dumps(payload, default=str, ensure_ascii=False, indent=2))
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
