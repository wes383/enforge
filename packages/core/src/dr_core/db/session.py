from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from dr_core.config import get_settings


def sqlalchemy_url(database_url: str) -> str:
    """Return a SQLAlchemy URL that uses the installed psycopg 3 driver."""
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    return database_url


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: Engine = create_engine(
            sqlalchemy_url(database_url),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


@lru_cache
def get_database() -> Database:
    return Database(get_settings().database_url)
