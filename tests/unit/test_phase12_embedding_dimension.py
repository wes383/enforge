from types import SimpleNamespace

import pytest

from dr_core.db.schema import (
    align_embedding_column,
    embedding_dimension_mismatch,
    parse_vector_dimension,
)
from dr_core.providers.embeddings import OpenAICompatibleEmbeddingProvider
from dr_core.providers.errors import ProviderResponseError
from dr_core.providers.http import JsonHttpClient


class _StubSession:
    def __init__(self, column_type: str | None) -> None:
        self.column_type = column_type
        self.statements: list[str] = []

    def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))
        return SimpleNamespace(scalar=lambda: self.column_type)


def _capture(monkeypatch, payload: dict) -> list[dict]:
    calls: list[dict] = []

    def fake_request_json(self, method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        return payload

    monkeypatch.setattr(JsonHttpClient, "request_json", fake_request_json)
    return calls


def test_the_dimension_parser_reads_the_column_type():
    assert parse_vector_dimension("vector(1536)") == 1536
    assert parse_vector_dimension("vector(1024)") == 1024
    assert parse_vector_dimension("vector") is None
    assert parse_vector_dimension(None) is None
    assert parse_vector_dimension("text") is None


def test_the_provider_sends_the_configured_dimension_for_v3_models(monkeypatch):
    calls = _capture(
        monkeypatch,
        {"data": [{"embedding": [0.1] * 1024}]},
    )
    provider = OpenAICompatibleEmbeddingProvider(
        "key",
        model="text-embedding-3-small",
        dimension=1024,
    )

    vectors = provider.embed_texts(["hello"])

    assert calls[0]["json"]["dimensions"] == 1024
    assert len(vectors[0]) == 1024
    assert provider.dimension == 1024


def test_the_provider_omits_the_dimension_for_other_models(monkeypatch):
    calls = _capture(
        monkeypatch,
        {"data": [{"embedding": [0.1] * 1024}]},
    )
    provider = OpenAICompatibleEmbeddingProvider(
        "key",
        model="bge-m3",
        dimension=1024,
    )

    vectors = provider.embed_texts(["hello"])

    assert "dimensions" not in calls[0]["json"]
    assert len(vectors[0]) == 1024


def test_the_provider_reports_a_vector_of_an_unexpected_size(monkeypatch):
    _capture(monkeypatch, {"data": [{"embedding": [0.1] * 512}]})
    provider = OpenAICompatibleEmbeddingProvider(
        "key",
        model="text-embedding-3-small",
        dimension=1024,
    )

    with pytest.raises(ProviderResponseError) as excinfo:
        provider.embed_texts(["hello"])

    assert "512 dimensions" in str(excinfo.value)
    assert "expected 1024" in str(excinfo.value)


def test_a_dimension_mismatch_names_the_column_and_the_setting():
    mismatch = embedding_dimension_mismatch(_StubSession("vector(1536)"), 1024)

    assert mismatch is not None
    assert "vector(1536)" in mismatch
    assert "EMBEDDING_DIMENSION is 1024" in mismatch
    assert "dr_core.maintenance.alignment" in mismatch


def test_a_matching_column_reports_no_mismatch():
    assert embedding_dimension_mismatch(_StubSession("vector(1024)"), 1024) is None
    assert embedding_dimension_mismatch(_StubSession("vector"), 1024) is None


def test_alignment_leaves_a_matching_column_untouched():
    session = _StubSession("vector(1024)")

    report = align_embedding_column(session, 1024)

    assert report["action"] == "unchanged"
    assert not any(
        "ALTER" in statement or "UPDATE" in statement
        for statement in session.statements
    )


def test_alignment_retypes_the_column_and_clears_stale_vectors():
    session = _StubSession("vector(1536)")

    report = align_embedding_column(session, 1024)

    assert report["action"] == "altered"
    assert report["previous"] == 1536
    joined = " ".join(session.statements)
    assert "ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1024)" in joined
    assert "SET embedding = NULL" in joined
    assert "ix_document_chunks_embedding_hnsw" in joined
