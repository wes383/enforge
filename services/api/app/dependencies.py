from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from dr_core.db import get_database


def get_session() -> Iterator[Session]:
    with get_database().session_scope() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
