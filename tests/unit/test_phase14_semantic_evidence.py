from __future__ import annotations

from types import SimpleNamespace

import pytest

from dr_core.db.enums import SectionRole
from dr_core.research.matching import (
    SemanticSentenceMatcher,
    cosine_similarity,
)
from dr_core.research.models import OutlineSection
from dr_core.research.nodes import _match_sentence
from dr_core.research.text import best_sentence_span_for_queries
from dr_core.research.writing import _generate_section_payload

_CHINESE_QUERY = "片尾字幕的历史"
_ENGLISH_SENTENCE = "End credits have grown steadily since the studio era."


class _FakeEmbedding:
    """Deterministic two-dimensional embedding keyed by the exact text."""

    model_name = "fake-embedding"
    dimension = 2

    def __init__(self, mapping=None, error=None):
        self.mapping = mapping or {}
        self.error = error
        self.calls = 0

    def embed_texts(self, texts):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [list(self.mapping.get(text, [0.0, 1.0])) for text in texts]


class _TruncatingLLM:
    name = "fake-llm"

    def __init__(self, responses):
        self.responses = list(responses)
        self.budgets: list[int] = []

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        self.budgets.append(max_tokens)
        return SimpleNamespace(text=self.responses.pop(0))


def _section(target_words: int = 200) -> OutlineSection:
    return OutlineSection(
        index=0,
        role=SectionRole.BODY,
        title="片尾字幕的历史",
        purpose="解释片尾字幕如何变长。",
        search_terms=["end credits history"],
        target_words=target_words,
    )


def test_url_fragments_never_become_sentences():
    from dr_core.research.text import is_low_value_sentence, sentence_spans

    fragment = "com/questions/60046/why-did-the-end-credits-in-movies-become-a-lot-lon"
    assert is_low_value_sentence(fragment) is True
    assert sentence_spans(fragment) == []
    # A markdown link cut on the dots inside its URL is not a sentence either.
    for link in (
        "title=片尾字幕&oldid=93159888](https://zh.",
        "- [电影术语](https://zh.",
        "- 在片尾演职员表中向原创作者致谢 片尾播放前的 [反应视频](https://clipchamp.",
    ):
        assert is_low_value_sentence(link) is True
        assert sentence_spans(link) == []
    # Prose that happens to mention a domain keeps its place.
    prose = "The studio published its rules at example.com/rules in 1970 for all members."
    assert is_low_value_sentence(prose) is False
    assert sentence_spans(prose) != []


def test_cosine_similarity_handles_zero_and_mismatched_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0


def test_chinese_query_never_matches_an_english_sentence_lexically():
    assert best_sentence_span_for_queries(_ENGLISH_SENTENCE, [_CHINESE_QUERY]) is None


def test_semantic_matcher_recovers_a_cross_lingual_sentence():
    embedding = _FakeEmbedding(
        mapping={_CHINESE_QUERY: [1.0, 0.0], _ENGLISH_SENTENCE: [1.0, 0.0]}
    )
    matcher = SemanticSentenceMatcher(embedding, min_similarity=0.5)

    match = matcher.match(_ENGLISH_SENTENCE, [_CHINESE_QUERY])

    assert match is not None
    sentence, similarity, start, end = match
    assert sentence == _ENGLISH_SENTENCE
    assert similarity == pytest.approx(1.0)
    assert (start, end) == (0, len(_ENGLISH_SENTENCE))
    assert matcher.matches == 1


def test_semantic_matcher_rejects_a_sentence_below_the_floor():
    embedding = _FakeEmbedding(
        mapping={_CHINESE_QUERY: [1.0, 0.0], _ENGLISH_SENTENCE: [0.6, 0.8]}
    )
    matcher = SemanticSentenceMatcher(embedding, min_similarity=0.9)

    assert matcher.match(_ENGLISH_SENTENCE, [_CHINESE_QUERY]) is None


def test_semantic_matcher_disables_itself_when_the_provider_fails():
    embedding = _FakeEmbedding(error=RuntimeError("embedding endpoint down"))
    matcher = SemanticSentenceMatcher(embedding, min_similarity=0.5)

    assert matcher.match(_ENGLISH_SENTENCE, [_CHINESE_QUERY]) is None
    assert matcher.enabled is False
    # A disabled matcher never calls the provider again and never raises.
    assert matcher.match(_ENGLISH_SENTENCE, [_CHINESE_QUERY]) is None
    assert embedding.calls == 1


def test_the_matcher_stops_scoring_texts_after_its_budget():
    embedding = _FakeEmbedding(mapping={_CHINESE_QUERY: [1.0, 0.0]})
    matcher = SemanticSentenceMatcher(embedding, min_similarity=0.5, max_texts=1)
    first = "End credits have grown steadily since the studio era."
    second = "Closing scrolls lengthened as production crews unionised."

    assert matcher.match(first, [_CHINESE_QUERY]) is None
    calls_after_first = embedding.calls
    assert matcher.match(second, [_CHINESE_QUERY]) is None
    assert embedding.calls == calls_after_first


def test_match_sentence_prefers_lexical_and_falls_back_to_semantic():
    shared = "End credits history shows steady growth in the studio era."
    embedding = _FakeEmbedding(
        mapping={_CHINESE_QUERY: [1.0, 0.0], _ENGLISH_SENTENCE: [1.0, 0.0]}
    )
    matcher = SemanticSentenceMatcher(embedding, min_similarity=0.5)

    lexical, kind, similarity = _match_sentence(shared, ["credits history"], matcher)
    assert lexical is not None
    assert kind == "lexical"
    assert similarity is None

    semantic, kind, similarity = _match_sentence(
        _ENGLISH_SENTENCE,
        [_CHINESE_QUERY],
        matcher,
    )
    assert semantic is not None
    assert kind == "semantic"
    assert similarity == pytest.approx(1.0)


def test_match_sentence_without_a_matcher_stays_lexical():
    match, kind, similarity = _match_sentence(
        _ENGLISH_SENTENCE,
        [_CHINESE_QUERY],
        None,
    )
    assert match is None
    assert kind == "lexical"
    assert similarity is None


def test_truncated_section_json_is_retried_with_a_doubled_budget():
    llm = _TruncatingLLM(['{"markdown": "cut off', '{"markdown": "Text [S1]."}'])

    payload = _generate_section_payload(llm, "prompt", _section(), max_tokens=3000)

    assert payload == {"markdown": "Text [S1]."}
    assert llm.budgets == [3000, 6000]


def test_a_second_truncation_still_falls_back():
    llm = _TruncatingLLM(['{"markdown": "first', '{"markdown": "second'])

    assert _generate_section_payload(llm, "prompt", _section(), max_tokens=3000) is None
    assert llm.budgets == [3000, 6000]


def test_the_retry_respects_the_token_ceiling():
    llm = _TruncatingLLM(['{"markdown": "cut off'])

    assert _generate_section_payload(llm, "prompt", _section(), max_tokens=16000) is None
    assert llm.budgets == [16000]


def test_a_non_json_answer_is_not_retried():
    llm = _TruncatingLLM(["this is prose, not JSON"])

    assert _generate_section_payload(llm, "prompt", _section(), max_tokens=3000) is None
    assert llm.budgets == [3000]
