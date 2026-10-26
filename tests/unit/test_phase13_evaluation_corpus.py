from __future__ import annotations

import json

import pytest

from dr_core.evaluation.corpus import (
    CorpusContentProvider,
    CorpusSearchProvider,
    corpus_root,
    load_corpus,
)
from dr_core.providers.errors import ProviderResourceNotFound
from dr_core.providers.models import FetchStatus


def test_every_declared_source_has_a_full_document():
    bundle = load_corpus("real_remote_work")

    assert len(bundle.sources) == 5
    assert len(bundle.web_sources) == 5
    assert bundle.academic_sources == ()
    # A corpus document has to be long enough to be chunked and quoted; a
    # two-sentence stub would not exercise the parser or the extractor.
    assert all(len(source.body) > 1200 for source in bundle.sources)
    assert all(source.url.startswith("https://") for source in bundle.sources)


def test_the_corpus_is_marked_as_authored_rather_than_real_literature():
    manifest = json.loads(
        (corpus_root() / "real_remote_work" / "sources.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["synthetic_content"] is True
    assert manifest["notes"]


def test_search_ranks_the_document_that_matches_the_query():
    bundle = load_corpus("real_remote_work")

    results = CorpusSearchProvider(bundle.web_sources).search(
        "meta-analysis pooled studies",
        limit=3,
    )

    assert results
    assert results[0].title.startswith("A Meta-Analysis")
    assert [item.rank for item in results] == list(range(len(results)))


def test_content_provider_serves_the_body_and_rejects_unknown_urls():
    bundle = load_corpus("real_remote_work")
    provider = CorpusContentProvider(bundle)
    source = bundle.sources[0]

    result = provider.fetch(source.url)

    assert result.status is FetchStatus.SUCCESS
    assert result.text == source.body
    assert result.title == source.title
    with pytest.raises(ProviderResourceNotFound):
        provider.fetch("https://corpus.evaluation.invalid/remote-work/missing")


def test_the_microplastics_corpus_separates_academic_from_web_sources():
    bundle = load_corpus("real_microplastics")

    assert len(bundle.academic_sources) == 2
    assert len(bundle.web_sources) == 2
    assert all(source.doi for source in bundle.academic_sources)
    assert all(source.authors for source in bundle.sources)


def test_a_missing_corpus_fails_loudly():
    with pytest.raises(FileNotFoundError):
        load_corpus("no_such_case")
