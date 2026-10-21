import pytest

from dr_core.db.session import sqlalchemy_url
from dr_core.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingProvider,
    ensure_embedding_dimension,
)
from dr_core.normalization import (
    InvalidDOI,
    InvalidURL,
    claim_fingerprint,
    evidence_fingerprint,
    normalize_doi,
    normalize_url,
    query_fingerprint,
    question_fingerprint,
    url_fingerprint,
)
from dr_core.storage.keys import validate_metadata_storage_key


@pytest.mark.parametrize(
    "raw",
    [
        "10.1000/ABC.123",
        "https://doi.org/10.1000/ABC.123",
        "http://dx.doi.org/10.1000/ABC.123",
        "doi: 10.1000/ABC.123",
        "  DOI:10.1000/ABC.123  ",
    ],
)
def test_normalize_doi_accepts_common_forms(raw):
    assert normalize_doi(raw) == "10.1000/abc.123"


@pytest.mark.parametrize(
    "raw",
    ["", "not-a-doi", "10.12/abc", "10.1234/has space", "https://example.com/10.1234/x"],
)
def test_normalize_doi_rejects_invalid_values(raw):
    with pytest.raises(InvalidDOI):
        normalize_doi(raw)


def test_normalize_url_canonicalizes_tracking_fragment_and_path():
    raw = (
        "HTTPS://Example.COM:443/a//b/?utm_source=newsletter&b=2&a=1"
        "&fbclid=abc#section"
    )
    assert normalize_url(raw) == "https://example.com/a/b?a=1&b=2"


def test_normalize_url_keeps_non_default_port_and_ipv6():
    assert (
        normalize_url("https://Example.com:8443/a")
        == "https://example.com:8443/a"
    )
    assert (
        normalize_url("https://[2001:DB8::1]:443/a#x")
        == "https://[2001:db8::1]/a"
    )


def test_normalize_url_handles_international_domains():
    assert normalize_url("https://例子.测试/文章") == "https://xn--fsqu00a.xn--0zwm56d/文章"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://example.com/file",
        "https://example.com/path with space",
        "https://user:secret@example.com/",
        "https:///missing-host",
        "not a url",
    ],
)
def test_normalize_url_rejects_unsafe_values(raw):
    with pytest.raises(InvalidURL):
        normalize_url(raw)


def test_url_fingerprint_is_stable_across_tracking_parameters():
    first = url_fingerprint(normalize_url("https://example.com/a?utm_source=x"))
    second = url_fingerprint(normalize_url("https://EXAMPLE.com/a/#fragment"))
    assert first == second
    assert len(first) == 64


def test_text_fingerprints_normalize_whitespace_and_case():
    assert claim_fingerprint("  The\nclaim  ") == claim_fingerprint("the claim")
    assert query_fingerprint(" AI   Research ") == query_fingerprint("ai research")
    assert question_fingerprint("What\nis it?") == question_fingerprint("what is it?")
    assert evidence_fingerprint("version-1", "Quoted\ntext") == evidence_fingerprint(
        "version-1",
        "quoted text",
    )
    assert evidence_fingerprint(
        "version-1",
        "quoted text",
        "question-1",
    ) != evidence_fingerprint("version-1", "quoted text", "question-2")


@pytest.mark.parametrize(
    "key",
    [
        "projects/123/sources/456/original.pdf",
        "artifacts/run-1/report.md",
        "a/b-c_d.e",
    ],
)
def test_metadata_storage_keys_accept_relative_paths(key):
    assert validate_metadata_storage_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute/path.pdf",
        "C:/drive/path.pdf",
        "a\\b.pdf",
        "a/../b.pdf",
        "a/./b.pdf",
        "a//b.pdf",
        ".hidden/file.pdf",
        "projects/ümlaut.pdf",
        "a" * 513,
    ],
)
def test_metadata_storage_keys_reject_unsafe_paths(key):
    with pytest.raises(ValueError):
        validate_metadata_storage_key(key)


def test_embedding_dimension_validation():
    assert ensure_embedding_dimension([0.1, 0.2], 2) == [0.1, 0.2]
    with pytest.raises(EmbeddingDimensionMismatch) as exc_info:
        ensure_embedding_dimension([0.1, 0.2], 3)
    assert exc_info.value.expected == 3
    assert exc_info.value.actual == 2


def test_embedding_protocol_is_provider_agnostic():
    class FakeProvider:
        model_name = "fake-model"
        dimension = 2

        def embed_texts(self, texts):
            return [[1.0, 0.0] for _ in texts]

    provider = FakeProvider()
    assert isinstance(provider, EmbeddingProvider)
    assert provider.embed_texts(["a"]) == [[1.0, 0.0]]


def test_sqlalchemy_url_uses_psycopg_driver():
    assert (
        sqlalchemy_url("postgresql://user:pass@db:5432/app")
        == "postgresql+psycopg://user:pass@db:5432/app"
    )
    assert (
        sqlalchemy_url("postgresql+psycopg://user:pass@db:5432/app")
        == "postgresql+psycopg://user:pass@db:5432/app"
    )
    assert sqlalchemy_url("sqlite:///local.db") == "sqlite:///local.db"
