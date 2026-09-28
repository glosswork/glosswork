"""Retrieval and chunking constants, quarantined in one module (DD-33).

Chunk size, overlap, and the chunk cap, and ``k``, arm weights, candidate multipliers,
and the widen factor live here and nowhere else. Changing any value in this file without
a recorded golden-set run is the wrong diff, and a change to the chunking constants
requires a full re-index because every ``content_hash`` is computed over the text the
policy produced.

The floors the golden set asserts are **not** in this file, deliberately: DD-33
makes them non-editable, and a threshold sitting next to the knobs it judges is a
threshold that gets adjusted to make a run pass.
"""

from __future__ import annotations

# Chunk size in the model's own WordPiece tokens, excluding [CLS] and [SEP]
# (DD-33). bge-small was trained on short passages: 512 dilutes the vector with
# unrelated sentences and 128 fragments a paragraph, so 256 is the midpoint, and the
# golden set confirms or moves it.
CHUNK_MAX_TOKENS = 256

# Overlap applies **only** to hard splits, where one unit (a single sentence) exceeds
# CHUNK_MAX_TOKENS and has to be windowed. Unit-packed chunks share no text at all,
# because their boundaries fall on sentence ends by construction.
CHUNK_OVERLAP_TOKENS = 32

# Upper bound on chunks per source, about 16,000 tokens. Truncation is logged so an
# operator can see that a source was cut rather than silently half-indexed.
MAX_CHUNKS_PER_SOURCE = 64

# --------------------------------------------------------------------- retrieval
#
# Every value below was set by DD-33 and confirmed or moved by a golden-set run. None
# changes without a golden run recorded alongside it.

# Reciprocal rank fusion constant: ``raw = sum(weight / (RRF_K + rank))`` over the
# arms a record appears in (docs/DATA_MODEL.md section 10, "Hybrid ranking").
RRF_K = 60

# Both arms weighted equally. Unequal weights are an index-tuning candidate (DD-33),
# accepted or rejected by a golden-set run, never by inspection.
ARM_WEIGHTS: dict[str, float] = {"keyword": 1.0, "semantic": 1.0}

# Each arm's pool is ``CANDIDATE_MULTIPLIER * limit`` **rows** (FTS rows, chunk rows),
# scoped and live-joined before the limit, then collapsed to records.
CANDIDATE_MULTIPLIER = 4

# When fewer than ``limit`` distinct records survive the structured filter while an
# arm's raw pool was full, both pools widen **once** to ``WIDEN_MULTIPLIER * limit``.
WIDEN_MULTIPLIER = 16

# Leading characters of the nearest chunk shown for a semantic-only hit, with the
# query's literal content terms ``<em>``-wrapped where they occur.
SEMANTIC_SNIPPET_CHARS = 240

# Query-side stopwords for the keyword arm. FTS5 has no stopword list
# of its own and matches a term present in every row at bm25 -1e-06, which on a small
# corpus fills the tail of every result list with rows that share only "the". A
# whitespace word that is a single alphanumeric run on this list is dropped before
# phrase construction; the **index** keeps every word, so a multi-run phrase such as
# ``"to do"`` inside an identifier still matches. Articles, conjunctions, prepositions,
# pronouns, and auxiliaries; about a hundred tokens.
QUERY_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if of to in on at for by with from as into onto over under after before
    between about through during without is are was were be been being am do does did has have
    had having will would shall should can could may might must it its this that these those
    there here i me my we our ours you your they them their he him his she her who whom whose
    which what when where why how not no nor so than then too very just also only own same such
    each any all some up down out off again further once because while until
    """.split()
)
