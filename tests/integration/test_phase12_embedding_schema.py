import pytest

from dr_core.config import get_settings
from dr_core.db.schema import align_embedding_column, embedding_column_dimension

pytestmark = pytest.mark.integration


def test_the_vector_column_follows_the_configured_dimension(session):
    settings = get_settings()

    assert embedding_column_dimension(session) == settings.embedding_dimension


def test_the_column_can_be_retyped_and_restored(session):
    settings = get_settings()
    target = 1024 if settings.embedding_dimension != 1024 else 768
    try:
        report = align_embedding_column(session, target)

        assert report["action"] == "altered"
        assert embedding_column_dimension(session) == target
    finally:
        align_embedding_column(session, settings.embedding_dimension)

    assert embedding_column_dimension(session) == settings.embedding_dimension
