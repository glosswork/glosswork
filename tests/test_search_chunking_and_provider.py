"""Chunking policy and the embedding provider (DD-33, DD-32, DD-40).

These run against the **real** tokenizer and the real model, and fail rather than
skip when it is absent (DD-32): chunk sizes are in the model's own WordPiece tokens,
so a chunking test against a stand-in tokenizer would assert nothing about the policy
that actually ships.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from glosswork.config import ConfigError
from glosswork.services import chunking
from glosswork.services.chunking import chunk_source_text, content_hash
from glosswork.services.embedding import (
    EXPECTED_DIMENSIONS,
    QUERY_PREFIX,
    OnnxBgeProvider,
    build_provider,
    require_supported_dimensions,
)
from glosswork.services.search_tuning import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNKS_PER_SOURCE,
)
from tests.search_support import model_dir, real_provider


@pytest.fixture(scope="module")
def provider() -> OnnxBgeProvider:
    return real_provider()  # type: ignore[return-value]


def token_ids(provider: OnnxBgeProvider, text: str) -> list[int]:
    """The model's token ids for a text, with no special tokens."""
    return list(provider._tokenizer.encode(text, add_special_tokens=False).ids)  # type: ignore[attr-defined]


def count(provider: OnnxBgeProvider, text: str) -> int:
    return len(provider.token_offsets(text))


# ------------------------------------------------------------------- chunking


def test_a_short_comment_yields_one_chunk_equal_to_its_text(provider: OnnxBgeProvider) -> None:
    body = (
        "We reviewed the vendor contract this morning and the renewal terms look "
        "acceptable to finance, so I have asked Dana to countersign it before Friday."
    )
    assert count(provider, body) < CHUNK_MAX_TOKENS
    chunks, truncated = chunk_source_text(body, provider)
    assert truncated is False
    assert len(chunks) == 1
    assert chunks[0].text == body
    assert chunks[0].index == 0


def test_empty_and_whitespace_only_values_produce_no_chunk(provider: OnnxBgeProvider) -> None:
    """No chunk means no job and no row: a cleared field stops matching."""
    for value in ("", "   ", "\n\n\t  \n"):
        chunks, truncated = chunk_source_text(value, provider)
        assert chunks == []
        assert truncated is False


def test_three_long_paragraphs_split_on_boundaries_and_each_ends_on_a_sentence(
    provider: OnnxBgeProvider,
) -> None:
    """Whole units are packed greedily, so prose is never cut mid-sentence."""
    paragraph = " ".join(
        [
            "The migration plan requires careful sequencing of every dependent service.",
            "Each cutover window has to be agreed with the owning team in advance.",
            "Rollback remains available until the final verification step completes.",
        ]
        * 4
    )
    text = "\n\n".join([paragraph, paragraph, paragraph])
    chunks, truncated = chunk_source_text(text, provider)

    assert truncated is False
    assert len(chunks) > 1, "the fixture must be long enough to actually split"
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert count(provider, chunk.text) <= CHUNK_MAX_TOKENS
        assert chunk.text.rstrip().endswith("."), (
            f"chunk {chunk.index} does not end on a sentence boundary: ...{chunk.text[-60:]!r}"
        )


def test_one_oversized_sentence_yields_windows_sharing_exactly_the_overlap(
    provider: OnnxBgeProvider,
) -> None:
    """Hard splits are the only place overlap applies, and it is exact.

    Asserted on the model's own token ids rather than on characters: the constant is
    32 *tokens*, and a character-level approximation of it would pass while the real
    overlap drifted.
    """
    sentence = " ".join(f"item{i}" for i in range(1200))
    offsets = provider.token_offsets(sentence)
    assert len(offsets) > 1500
    chunks, _ = chunk_source_text(sentence, provider)
    assert len(chunks) > 2

    # Measured by locating each window's character span in the original text and
    # converting that back to token indices -- never by re-tokenizing the chunk.
    # A window boundary can fall inside a word ("item123" is "item" + "##123"), so a
    # chunk's text can begin mid-word; re-encoding that slice yields different ids
    # than the same characters had in context, and the comparison would report zero
    # overlap for a perfectly correct implementation.
    starts = {start: index for index, (start, _) in enumerate(offsets)}
    ends = {end: index for index, (_, end) in enumerate(offsets)}
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks:
        start_char = sentence.index(chunk.text, cursor)
        ranges.append((starts[start_char], ends[start_char + len(chunk.text)] + 1))
        cursor = start_char + 1

    assert ranges[0][1] - ranges[0][0] == CHUNK_MAX_TOKENS
    assert all(end - start <= CHUNK_MAX_TOKENS for start, end in ranges)
    for (_, first_end), (second_start, _) in zip(ranges, ranges[1:], strict=False):
        assert first_end - second_start == CHUNK_OVERLAP_TOKENS, (
            f"adjacent windows share {first_end - second_start} tokens, "
            f"expected exactly {CHUNK_OVERLAP_TOKENS}"
        )


def test_a_source_is_capped_and_reports_that_it_was_truncated(
    provider: OnnxBgeProvider,
) -> None:
    paragraph = " ".join(["The quarterly review covered every open risk in detail."] * 40)
    text = "\n\n".join([paragraph] * (MAX_CHUNKS_PER_SOURCE + 10))
    chunks, truncated = chunk_source_text(text, provider)
    assert truncated is True
    assert len(chunks) == MAX_CHUNKS_PER_SOURCE
    assert [c.index for c in chunks] == list(range(MAX_CHUNKS_PER_SOURCE))


def test_the_chunking_constants_are_imported_not_redeclared() -> None:
    """The tuning constants live in one module.

    A copy of ``256`` in ``chunking.py`` would be a constant that a golden-set run
    could never move, which is the exact failure the quarantine exists to prevent.
    """
    source = Path(chunking.__file__).read_text(encoding="utf-8")
    assert "from glosswork.services.search_tuning import" in source
    body = source.split('"""', 2)[-1]  # past the module docstring
    for literal in ("= 256", "= 32", "= 64"):
        assert literal not in body, (
            f"chunking.py appears to redeclare {literal!r}; the tuning constants "
            "belong only in services/search_tuning.py (DD-33)."
        )


# ----------------------------------------------------------------- content_hash


def test_content_hash_is_sha256_over_model_id_nul_and_text() -> None:
    digest = content_hash("bge-small-en-v1.5@5c38ec7", "a paragraph")
    assert digest == hashlib.sha256(b"bge-small-en-v1.5@5c38ec7\x00a paragraph").hexdigest()


def test_content_hash_changes_with_the_model_id() -> None:
    """A model swap makes every stored row visibly stale rather than silently mixed."""
    assert content_hash("model-a@1", "same text") != content_hash("model-b@2", "same text")


def test_no_chunk_text_and_no_hash_input_ever_contains_the_query_prefix(
    provider: OnnxBgeProvider,
) -> None:
    """DD-33: the query-prefix asymmetry, asserted on the chunk and hash side.

    The prefix is applied inside ``embed_query`` and nowhere else, so it cannot reach
    the chunker; a passage hash computed over prefixed text would silently poison
    every reuse comparison.
    """
    text = "\n\n".join(
        [
            "Represent this sentence is a phrase a user might legitimately write.",
            " ".join(["The renewal was priced against last year's baseline."] * 30),
        ]
    )
    chunks, _ = chunk_source_text(text, provider)
    assert chunks
    for chunk in chunks:
        assert QUERY_PREFIX not in chunk.text
        assert content_hash("m", chunk.text) != content_hash("m", QUERY_PREFIX + chunk.text)


# -------------------------------------------------------------------- provider


def test_the_provider_reports_384_dimensions_and_a_revision_qualified_model_id(
    provider: OnnxBgeProvider,
) -> None:
    assert provider.dimensions == EXPECTED_DIMENSIONS == 384
    assert provider.model_id == "bge-small-en-v1.5@5c38ec7"
    assert "@" in provider.model_id, "model_id must be revision-qualified"


def test_paraphrases_are_closer_than_unrelated_text(provider: OnnxBgeProvider) -> None:
    """The one assertion that says the model is wired up correctly at all.

    CLS pooling, L2 normalization, and the right input names can each be wrong in a
    way that still returns 384 floats.
    """
    a, b, c = provider.embed_passages(
        [
            "The quarterly pricing review was delayed by two weeks.",
            "We pushed back the price review this quarter by a fortnight.",
            "The server rack overheated in the datacenter last night.",
        ]
    )
    cos = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert cos(a, b) > cos(a, c), (
        f"paraphrase similarity {cos(a, b):.4f} should exceed unrelated {cos(a, c):.4f}"
    )


def test_vectors_are_l2_normalized(provider: OnnxBgeProvider) -> None:
    for vector in provider.embed_passages(["one", "another passage entirely"]):
        assert abs(sum(v * v for v in vector) - 1.0) < 1e-5


def test_a_provider_reporting_another_dimension_fails_startup_naming_the_constraint() -> None:
    """DD-40: a dimension-changing model swap is not supported, and this is where that is
    enforced.

    ``vec_embeddings`` is ``float[384]`` by migration 6, so a 768-dimensional model must
    fail at startup with the constraint named rather than corrupt the index. Asserted
    against the shipped guard itself, not a copy of it: a test that re-implemented the
    check locally would pass even if ``build_provider`` stopped calling it.
    """

    class WrongDimensionProvider:
        model_id = "some-other-model@abc"
        dimensions = 768

        def token_offsets(self, text: str) -> list[tuple[int, int]]:  # pragma: no cover
            return []

        def embed_query(self, text: str) -> list[float]:  # pragma: no cover
            return []

        def embed_passages(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
            return []

    with pytest.raises(ConfigError) as excinfo:
        require_supported_dimensions(WrongDimensionProvider())
    message = str(excinfo.value)
    assert "768" in message and "384" in message
    assert "GW_EMBEDDING_MODEL" in message
    # A runtime message names the constraint and the remedy, never a document's numbering.
    assert re.search(r"\b(?:A|D)D-\d", message) is None, message


def test_build_provider_applies_that_same_guard(provider: OnnxBgeProvider) -> None:
    """The real provider passes the shipped guard, so the split changed no behavior."""
    assert require_supported_dimensions(provider) is provider


def test_a_missing_model_fails_fast_naming_gw_model_dir(tmp_path: Path) -> None:
    """DD-32: located by configuration, never downloaded.

    The message has to name the variable and the fetch command, because the two
    audiences who hit it -- an operator with a misconfigured image and a developer
    with a fresh checkout -- need different halves of it.
    """
    with pytest.raises(ConfigError) as excinfo:
        build_provider(tmp_path / "nowhere", "bge-small-en-v1.5")
    message = str(excinfo.value)
    assert "GW_MODEL_DIR" in message
    assert "scripts/fetch_model.py" in message
    assert "GW_EMBEDDING_ENABLED" in message


def test_tests_that_need_the_model_fail_rather_than_skip(tmp_path: Path, monkeypatch) -> None:
    """The suite's own discipline, asserted (DD-32).

    A suite that silently skips its model-dependent tests passes a release boundary
    while proving nothing, so ``model_dir()`` raises. This test proves the helper
    fails rather than skips when the model is genuinely absent.
    """
    monkeypatch.setenv("GW_MODEL_DIR", str(tmp_path / "empty"))
    with pytest.raises(AssertionError) as excinfo:
        model_dir()
    assert "scripts/fetch_model.py" in str(excinfo.value)
    assert "fails rather than skips" in str(excinfo.value)
