from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    pass


class EmbeddingDimensionMismatch(EmbeddingError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Embedding dimension mismatch: expected {expected}, received {actual}"
        )


def ensure_embedding_dimension(
    embedding: Sequence[float],
    expected_dimension: int,
) -> list[float]:
    actual = len(embedding)
    if actual != expected_dimension:
        raise EmbeddingDimensionMismatch(expected_dimension, actual)
    return [float(value) for value in embedding]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Provider contract kept separate from the configured embedding model."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...
