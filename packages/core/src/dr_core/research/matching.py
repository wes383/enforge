"""Cross-lingual sentence selection for evidence extraction.

Lexical matching pairs a section query with a source sentence through shared
tokens. That works inside one language and fails completely across two: a
Chinese query and an English abstract share no token at all, so a scholarly
source that the hybrid retriever returned on its vector score is dropped before
it can ever become evidence.

This module keeps the deterministic lexical match first and only fills the gap
it cannot cover. When the retriever still ranks a source, the embedding space is
the remaining signal, so the query is compared with the candidate sentences and
the closest one is accepted only above a similarity floor. A provider failure
disables the matcher for the rest of the run instead of failing extraction.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Iterable, Sequence

from dr_core.embeddings import EmbeddingProvider
from dr_core.research.text import sentence_spans

logger = logging.getLogger(__name__)

# Candidate sentences per text. A snippet or an abstract rarely holds more, and
# the cap keeps one cross-lingual fallback to a single embedding request.
_MAX_SENTENCES = 12
# Query vectors are reused across every retrieved chunk of a section, so a run
# embeds each distinct query once.
_MAX_CACHED_QUERIES = 256
# Sentence vectors are only reused when the same text is scored for several
# sections; the cache is small and bounded.
_MAX_CACHED_TEXTS = 256
# Each scored text costs one embedding request. The cap keeps the fallback from
# turning a retrieval round into hundreds of calls, and the lexical pass still
# covers every text that never reaches it.
_MAX_SCORED_TEXTS = 60


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine similarity of two equal-length vectors."""
    if not left or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class SemanticSentenceMatcher:
    """Match a query to the closest sentence of a text by embedding similarity.

    The matcher is a fallback, never the primary path: callers try the lexical
    match first and only call :meth:`match` when it returns nothing.
    """

    def __init__(
        self,
        embedding: EmbeddingProvider | None,
        *,
        min_similarity: float,
        max_sentences: int = _MAX_SENTENCES,
        max_texts: int = _MAX_SCORED_TEXTS,
    ) -> None:
        self._embedding = embedding
        self._min_similarity = float(min_similarity)
        self._max_sentences = max(1, int(max_sentences))
        self._max_texts = max(1, int(max_texts))
        self._query_vectors: dict[str, list[float] | None] = {}
        self._text_vectors: dict[str, list[list[float]] | None] = {}
        self._texts_scored = 0
        self._disabled = embedding is None
        self.matches = 0

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def match(
        self,
        text: str,
        queries: Iterable[str],
    ) -> tuple[str, float, int, int] | None:
        """Return ``(sentence, similarity, start, end)`` for the closest sentence.

        ``None`` means no sentence cleared the similarity floor, the provider is
        unavailable, or the text holds no usable sentence.
        """
        if self._disabled or not text:
            return None
        spans = sentence_spans(text)[: self._max_sentences]
        if not spans:
            return None
        query_vectors = self._query_vectors_for(queries)
        if not query_vectors:
            return None
        sentence_vectors = self._sentence_vectors(text, [span[0] for span in spans])
        if sentence_vectors is None:
            return None

        best: tuple[str, float, int, int] | None = None
        for query_vector in query_vectors:
            for (sentence, start, end), vector in zip(
                spans, sentence_vectors, strict=True
            ):
                similarity = cosine_similarity(query_vector, vector)
                if similarity < self._min_similarity:
                    continue
                if best is None or similarity > best[1]:
                    best = (sentence, similarity, start, end)
        if best is not None:
            self.matches += 1
        return best

    def _query_vectors_for(self, queries: Iterable[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        missing: list[tuple[str, str]] = []
        for query in queries:
            normalized = " ".join(str(query or "").split())
            if not normalized:
                continue
            key = normalized.casefold()
            if key in self._query_vectors:
                cached = self._query_vectors[key]
                if cached:
                    vectors.append(cached)
                continue
            if len(self._query_vectors) + len(missing) >= _MAX_CACHED_QUERIES:
                continue
            missing.append((key, normalized))
        if missing:
            embedded = self._embed([text for _, text in missing])
            if embedded is not None:
                for (key, _), vector in zip(missing, embedded, strict=True):
                    self._query_vectors[key] = vector
                    vectors.append(vector)
        return vectors

    def _sentence_vectors(
        self,
        text: str,
        sentences: list[str],
    ) -> list[list[float]] | None:
        key = hashlib.sha256(
            "\x1f".join([text, *sentences]).encode("utf-8", "replace")
        ).hexdigest()
        cached = self._text_vectors.get(key)
        if cached is not None:
            return cached
        if self._texts_scored >= self._max_texts:
            return None
        embedded = self._embed(sentences)
        if embedded is None:
            return None
        self._texts_scored += 1
        if len(self._text_vectors) < _MAX_CACHED_TEXTS:
            self._text_vectors[key] = embedded
        return embedded

    def _embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        if self._disabled or self._embedding is None or not texts:
            return None
        try:
            vectors = self._embedding.embed_texts(list(texts))
        except Exception as exc:
            # One provider failure ends the fallback for this run; evidence
            # extraction continues with lexical matching only.
            self._disabled = True
            logger.warning(
                "Semantic evidence matching disabled after %s: %s",
                type(exc).__name__,
                exc,
            )
            return None
        if len(vectors) != len(texts):
            self._disabled = True
            logger.warning(
                "Semantic evidence matching disabled: embedding batch returned "
                "%s vectors for %s texts",
                len(vectors),
                len(texts),
            )
            return None
        return [[float(value) for value in vector] for vector in vectors]
