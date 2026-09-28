"""The embedding provider seam and its ONNX implementation (FR-Q6, FR-Q8, DD-32).

Two things in this module are load-bearing beyond "it returns vectors":

**The query/passage asymmetry.** bge models are asymmetric: a search query must be
embedded with a prefix that documents are embedded without. The prefix is applied
*inside* :meth:`OnnxBgeProvider.embed_query` and nowhere else, so no caller can pick
the wrong one by accident and no ``chunk_text`` or ``content_hash`` can ever be
computed over prefixed text. Reversing the prefix passes every functional test and
silently degrades retrieval, which is why DD-33 asserts the asymmetry three
separate ways rather than trusting this docstring.

**Nothing here fetches.** ``Tokenizer.from_file`` on the bundled ``tokenizer.json`` is
the only tokenizer entry point used; ``from_pretrained`` (the one path in the
``tokenizers`` library that reaches ``huggingface_hub``) is never called, and a
grep-backed test plus a fresh-subprocess ``sys.modules`` assertion hold that true over
time (DD-32). The model artifact is located by configuration, never downloaded
(DD-32).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from glosswork.config import ConfigError

# The prefix the bge model card requires on search queries. Defined once, applied in
# exactly one method.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# The bundled artifact's repository revision, abbreviated. ``model_id`` is
# revision-qualified so a model swap makes every stored row visibly stale
# (docs/DATA_MODEL.md section 10) rather than silently mixing vector spaces.
MODEL_REVISION = "5c38ec7"

# bge-small-en-v1.5 emits 384-dimensional vectors, which is what migration 6's
# ``vec0(embedding float[384])`` declares. A provider reporting anything else fails
# fast at startup (DD-40: a dimension-changing swap is not supported).
EXPECTED_DIMENSIONS = 384

# The model's own maximum sequence length. Chunks are capped at 256 tokens plus
# specials, so this only ever bites on a pathologically long query.
MAX_SEQUENCE_TOKENS = 512


class EmbeddingProvider(Protocol):
    """What the indexing worker and the search service depend on."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def token_offsets(self, text: str) -> list[tuple[int, int]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


def _bounded_intra_op_threads() -> int:
    """How many cores one embedding batch may use.

    The contention this bounds is CPU and the GIL against the request handlers in the
    same process, not anyio's threadpool -- the worker thread never enters it. ONNX
    Runtime keeps its own pool and releases the GIL during inference, while
    tokenization holds it, so an unbounded intra-op pool lets a drain saturate every
    core and starve the API. Half the machine, at least one, at most four.
    """
    cores = os.cpu_count() or 2
    return max(1, min(4, cores // 2))


class OnnxBgeProvider:
    """bge-small-en-v1.5 over onnxruntime, with CLS pooling and L2 normalization."""

    def __init__(
        self,
        model_dir: Path,
        model_name: str,
        intra_op_num_threads: int | None = None,
    ) -> None:
        # Imported here rather than at module import time so that merely importing the
        # service layer does not pay onnxruntime's import cost in the 806 tests that
        # run with embedding disabled.
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = model_dir / model_name
        onnx_path = directory / "model.onnx"
        tokenizer_path = directory / "tokenizer.json"
        missing = [p.name for p in (onnx_path, tokenizer_path) if not p.is_file()]
        if missing:
            raise ConfigError(
                f"GW_MODEL_DIR: {', '.join(missing)} not found in {directory}. "
                "The embedding model is baked into the image at build time and "
                "is never downloaded at runtime. In a source checkout, fetch it with "
                "'uv run python scripts/fetch_model.py'; in a container, check that "
                "GW_MODEL_DIR points at the directory the image copied the model into, "
                "or set GW_EMBEDDING_ENABLED=false to run without semantic search."
            )

        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_op_num_threads or _bounded_intra_op_threads()
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), options, providers=["CPUExecutionProvider"]
        )
        self._input_names = [i.name for i in self._session.get_inputs()]

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=MAX_SEQUENCE_TOKENS)
        # A second instance for :meth:`token_offsets`, **never truncated and never
        # shared with** :meth:`_embed`. Toggling truncation off and back on around
        # each offsets call on one shared tokenizer is not safe: ``embed_query`` runs on
        # request threads against this same provider instance, so a query encoded during
        # a chunking call would be encoded with truncation off, and a query over 512
        # tokens would reach the model untruncated and fail. Two instances make the two
        # callers independent.
        self._offsets_tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._offsets_tokenizer.no_truncation()
        self._model_id = f"{model_name}@{MODEL_REVISION}"

        dims = self._session.get_outputs()[0].shape[-1]
        self._dimensions = int(dims) if isinstance(dims, int) else EXPECTED_DIMENSIONS

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        """Character spans of the model's own tokens, without ``[CLS]``/``[SEP]``.

        This is the seam :mod:`glosswork.services.chunking` measures against, and
        the reason chunk sizes are in the model's tokens rather than in characters.
        Truncation is off on the tokenizer this method uses: chunking has to see how
        long a unit really is in order to decide to window it, and a truncated count
        would report every oversized sentence as exactly the limit. That tokenizer is
        a separate instance from the one :meth:`_embed` uses, so a query embedded on
        a request thread while the worker is chunking is still truncated.
        """
        encoding = self._offsets_tokenizer.encode(text, add_special_tokens=False)
        return [(int(start), int(end)) for start, end in encoding.offsets]

    def embed_query(self, text: str) -> list[float]:
        """Embed one search query. The **only** place the bge query prefix is applied."""
        return self._embed([QUERY_PREFIX + text])[0]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed document chunks. No prefix, ever."""
        if not texts:
            return []
        return self._embed(list(texts))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        encodings = self._tokenizer.encode_batch(texts)
        width = max(len(e.ids) for e in encodings)
        input_ids = np.zeros((len(encodings), width), dtype=np.int64)
        attention = np.zeros((len(encodings), width), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[row, :length] = encoding.ids
            attention[row, :length] = encoding.attention_mask

        feed: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attention}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        hidden = self._session.run(None, feed)[0]
        # CLS pooling: bge-small's sentence representation is the first token's
        # hidden state, not a mean over the sequence. Mean pooling here would produce
        # plausible vectors with measurably worse retrieval -- another failure this
        # module cannot detect on its own and DD-33's golden set exists to catch.
        pooled = hidden[:, 0, :]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        # Guard the degenerate all-zero row rather than emitting NaNs into the index.
        normalized = pooled / np.maximum(norms, 1e-12)
        return [[float(v) for v in row] for row in normalized]


def require_supported_dimensions(
    provider: EmbeddingProvider, expected_dimensions: int = EXPECTED_DIMENSIONS
) -> EmbeddingProvider:
    """Refuse a provider whose vectors the storage cannot hold (DD-40).

    ``vec_embeddings`` is ``float[384]`` by migration 6. A same-dimension model swap is
    supported by configuration plus a re-index (FR-Q8); a different dimension needs a
    drop-and-recreate of the vector table, which this version does not do (DD-40).
    Failing here, at startup, with the constraint named is the difference between that
    limit being known and being a corrupted index.

    Split out from :func:`build_provider` so the guard can be exercised against a
    provider that reports the wrong dimension without loading a second 133 MB graph --
    and, more to the point, so the test asserts *this* message rather than a copy of it.
    """
    if provider.dimensions != expected_dimensions:
        raise ConfigError(
            f"GW_EMBEDDING_MODEL: model {provider.model_id!r} produces "
            f"{provider.dimensions}-dimensional vectors, but the vector index is "
            f"float[{expected_dimensions}] (migration 6). Supporting a different "
            "dimension requires rebuilding the vector table, which this version does "
            "not do. Use a 384-dimensional model, or restore the bundled one."
        )
    return provider


def build_provider(
    model_dir: Path, model_name: str, expected_dimensions: int = EXPECTED_DIMENSIONS
) -> EmbeddingProvider:
    """Construct the real provider and refuse a dimension the storage cannot hold."""
    return require_supported_dimensions(OnnxBgeProvider(model_dir, model_name), expected_dimensions)
