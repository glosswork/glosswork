"""Chunking policy for embedding sources (DD-33, docs/DATA_MODEL.md section 10).

A pure function over a tokenizer seam. It has no database, no provider, and no
configuration of its own: the constants come from :mod:`search_tuning`, and the only
thing it needs from the model is where that model's own WordPiece token boundaries
fall, because the 256-token limit is in tokens and characters per token vary by
content.

The policy, restated so the code below can be read against it:

- split points are preferred in the order paragraph break, sentence end, token window;
- whole units are packed greedily until the next would exceed the limit, so ordinary
  prose is never cut mid-sentence;
- overlap applies only to hard splits, where one sentence alone exceeds the limit;
- a value shorter than the limit is one chunk, and empty or whitespace-only text
  produces no chunk at all (and therefore no job).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from glosswork.services.search_tuning import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNKS_PER_SOURCE,
)


class TokenOffsets(Protocol):
    """The one thing chunking needs from an embedding provider.

    ``token_offsets`` returns one ``(start, end)`` character span per model token, with
    no special tokens added. Character spans rather than token strings are what make a
    hard split exact: a window over tokens ``[i:j]`` maps back to
    ``text[offsets[i][0]:offsets[j - 1][1]]``, so the windowed chunk is a real slice of
    the caller's text and never a detokenized approximation of it.
    """

    def token_offsets(self, text: str) -> list[tuple[int, int]]: ...


# A blank line (optionally carrying whitespace) separates paragraphs.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n\s*")
# Sentence end: terminal punctuation followed by whitespace. Deliberately simple --
# an abbreviation splits one sentence in two, which costs a boundary, not a token.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class _Unit:
    """One indivisible-by-preference span of the source text.

    ``standalone`` marks a window that came out of a hard split. Such a window is
    already a whole chunk and is never packed with a neighbour, even when it is short:
    packing the tail of a windowed sentence together with the sentence after it would
    put overlap text and fresh text in one chunk, which is exactly the mixing the
    boundary policy exists to avoid.
    """

    text: str
    token_count: int
    starts_paragraph: bool
    standalone: bool = False


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str


def chunk_source_text(
    text: str,
    tokenizer: TokenOffsets,
    *,
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    max_chunks: int = MAX_CHUNKS_PER_SOURCE,
) -> tuple[list[Chunk], bool]:
    """Chunk one field value or comment body.

    Returns the chunks in document order with contiguous 0-based ``index``, and whether
    the ``max_chunks`` cap truncated the source. The caller logs the truncation; this
    function stays pure so it is testable without a logger or a database.
    """
    if not text or not text.strip():
        return [], False

    units = _units(text, tokenizer, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    texts: list[str] = []
    pending: list[_Unit] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending, pending_tokens
        if pending:
            texts.append(_join(pending))
            pending = []
            pending_tokens = 0

    for unit in units:
        # A hard-split window is already a finished chunk; so is any unit that fills
        # the limit on its own.
        if unit.standalone or unit.token_count >= max_tokens:
            flush()
            texts.append(unit.text)
            continue
        if pending and pending_tokens + unit.token_count > max_tokens:
            flush()
        pending.append(unit)
        pending_tokens += unit.token_count
    flush()

    truncated = len(texts) > max_chunks
    return [Chunk(index=i, text=t) for i, t in enumerate(texts[:max_chunks])], truncated


def _join(units: list[_Unit]) -> str:
    """Re-join packed units with the separator their own boundary implies.

    A unit that began a paragraph rejoins with a blank line; a sentence packed after
    its neighbour rejoins with a single space. The result is text a human would
    recognize as the original, which matters because ``chunk_text`` is what a semantic
    hit renders its snippet from.
    """
    out = units[0].text
    for unit in units[1:]:
        out += ("\n\n" if unit.starts_paragraph else " ") + unit.text
    return out


def _units(
    text: str,
    tokenizer: TokenOffsets,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[_Unit]:
    units: list[_Unit] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        count = len(tokenizer.token_offsets(paragraph))
        if count <= max_tokens:
            units.append(_Unit(paragraph, count, starts_paragraph=True))
            continue
        # Preference order: the paragraph did not fit, so try sentence ends.
        first_in_paragraph = True
        for sentence in _SENTENCE_END.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            offsets = tokenizer.token_offsets(sentence)
            if len(offsets) <= max_tokens:
                units.append(_Unit(sentence, len(offsets), starts_paragraph=first_in_paragraph))
                first_in_paragraph = False
                continue
            # Neither boundary helped: this one sentence must be windowed, and this is
            # the only place overlap applies.
            for window, count in _windows(
                sentence, offsets, max_tokens=max_tokens, overlap_tokens=overlap_tokens
            ):
                units.append(
                    _Unit(
                        window,
                        count,
                        starts_paragraph=first_in_paragraph,
                        standalone=True,
                    )
                )
                first_in_paragraph = False
    return units


def _windows(
    text: str,
    offsets: list[tuple[int, int]],
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[tuple[str, int]]:
    """Slide a ``max_tokens`` window over one oversized unit, stepping by the stride.

    Plain striding, so **every** adjacent pair shares exactly ``overlap_tokens``
    tokens, the tail pair included -- which is what the chunking test asserts. Anchoring
    the last window to the end of the text instead (to avoid a short tail) would make
    that final overlap larger than the constant and the assertion false, so the short
    tail is the deliberate choice.

    The one window never emitted is a tail already wholly contained in its
    predecessor: once fewer than ``overlap_tokens`` tokens remain past the previous
    window's start, they are all inside it and a further window would add nothing but
    a duplicate row to embed.
    """
    stride = max_tokens - overlap_tokens
    total = len(offsets)
    out: list[tuple[str, int]] = []
    start = 0
    while start < total:
        end = min(start + max_tokens, total)
        out.append((text[offsets[start][0] : offsets[end - 1][1]], end - start))
        if end == total:
            break
        start += stride
        if total - start <= overlap_tokens:
            break
    return out


def content_hash(model_id: str, chunk_text: str) -> str:
    """sha256 over ``model_id``, a NUL byte, and the chunk's exact text (DD-33).

    ``model_id`` is revision-qualified, so a model swap changes every hash and makes
    every stored row visibly stale rather than silently mixing two vector spaces in
    one index. The NUL separator keeps a model whose name ends in the first characters
    of a chunk from colliding with a shorter name and a longer chunk.

    The text hashed is the text embedded. The bge **query** prefix is applied inside
    the provider's ``embed_query`` and nowhere else, so it can never reach this
    function -- which is one of the three ways DD-33 asserts the asymmetry.
    """
    return hashlib.sha256(
        model_id.encode("utf-8") + b"\x00" + chunk_text.encode("utf-8")
    ).hexdigest()
