"""Embedding for codebook text (Requirement 13.3).

The same model and the same normalization must be used here and at query
time. If they diverge, retrieval does not fail — it silently returns
plausible-looking nonsense, which is far worse. The model name lives in one
place (services.api.config) precisely so both sides read the identical value.
"""

from __future__ import annotations

from collections.abc import Sequence

from services.api.config import settings


class Embedder:
    """Lazily-loaded sentence-transformers wrapper producing unit vectors.

    Loading pulls ~420 MB of weights on first use and takes a few seconds, so
    construction is cheap and the model is materialized on first encode.
    """

    def __init__(self, model_name: str | None = None, dimensions: int | None = None):
        self.model_name = model_name or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self._model = None

    @property
    def model(self):
        if self._model is None:
            # Imported here so that merely importing this module does not pay
            # the torch import cost.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: Sequence[str], batch_size: int = 32) -> list[list[float]]:
        """Embed texts as L2-normalized vectors.

        Normalization matters: the pgvector indexes use vector_cosine_ops, and
        normalized vectors make cosine distance equivalent to inner product.
        """
        if not texts:
            return []

        vectors = self.model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        if vectors.shape[1] != self.dimensions:
            raise ValueError(
                f"model {self.model_name!r} produced {vectors.shape[1]}-dim "
                f"vectors but configuration expects {self.dimensions}. "
                "Fix EMBEDDING_MODEL/EMBEDDING_DIMENSIONS and re-run the "
                "migration if the column width must change."
            )

        return [vector.tolist() for vector in vectors]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


def to_pgvector(vector: Sequence[float]) -> str:
    """Render a vector in the literal form pgvector accepts via a text cast."""
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"
