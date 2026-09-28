"""Semantic, keyword, and hybrid search (FR-Q1, FR-Q2, FR-Q4, FR-Q5; DD-33, DD-34;
docs/DATA_MODEL.md section 10, "Hybrid ranking"; docs/MCP_TOOLS.md section 5.1).

Retrieval, in one service both surfaces call (DD-3). Read top to bottom
it is the pipeline docs/DATA_MODEL.md section 10 decides:

1. **Two arms, each scoped before its limit.** The keyword arm runs the constructed
   FTS5 query (:func:`fts_query`; raw user text never reaches ``MATCH``) over
   ``CANDIDATE_MULTIPLIER * limit`` rows; the vector arm embeds the query -- the
   **only** caller of ``embed_query`` under ``src/``, which a grep-backed test holds --
   and runs a partition-constrained KNN over the same number of chunk rows. Both are
   repository methods; this module never names an index primitive.
2. **Collapse to records** (:func:`collapse_keyword`, :func:`collapse_vector`): a
   record's rank in an arm is the position of its best row among distinct records.
3. **Fuse** (:func:`fuse`): reciprocal rank fusion at record grain, normalized by the
   arms that ran so rank 1 scores 1.0 in every mode, ties broken deterministically.
4. **Post-filter through the compiler**: the caller's structured filter compiles
   through ``compile_candidate_filter`` with a ``FilterContext`` built exactly as
   ``RecordService.query_records`` builds it, so ``json_extract``, ``@me``, and date
   tokens stay where DD-2, FR-R8, and FR-R9 put them. **Starvation** widens both
   pools once to ``WIDEN_MULTIPLIER * limit``.
5. **Hit source, snippet, title, other_matches**, keyword-first.

Everything tunable is a named constant in :mod:`search_tuning`, and nothing in it
changes without a golden-set run (DD-33). The floors that judge this module are not
here and not tunable.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.compiler import compile_candidate_filter
from glosswork.db import Database
from glosswork.errors import (
    FeatureDisabledError,
    NotFoundError,
    UnknownObjectTypeError,
    ValidationFailedError,
)
from glosswork.fieldtypes import PSEUDO_FIELDS
from glosswork.filters import MAX_FILTER_DEPTH, FilterContext, FilterNode, parse_filter, too_deep
from glosswork.repositories.interfaces import (
    CommentRepository,
    PrincipalRepository,
    RecordRepository,
    SchemaRepository,
    SearchRepository,
)
from glosswork.repositories.models import (
    FieldDef,
    IndexSource,
    KeywordHit,
    ObjectType,
    RecordRow,
    VectorHit,
)
from glosswork.services.access import AccessService
from glosswork.services.base import display_field
from glosswork.services.embedding import EmbeddingProvider
from glosswork.services.principals import resolve_principal_ref
from glosswork.services.search_tuning import (
    ARM_WEIGHTS,
    CANDIDATE_MULTIPLIER,
    QUERY_STOPWORDS,
    RRF_K,
    SEMANTIC_SNIPPET_CHARS,
    WIDEN_MULTIPLIER,
)
from glosswork.timeutil import utc_now

_COMBINATORS = frozenset({"and", "or", "not"})

MODES: tuple[str, ...] = ("hybrid", "semantic", "keyword")
DEFAULT_MODE = "hybrid"
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
MAX_QUERY_CHARS = 1000

# The filter rule both surfaces document and ``describe_capabilities`` reports.
FILTER_RULE = (
    "When object_types names exactly one type, filter may reference that type's fields "
    "as well as the system pseudo-fields; with several or no object_types, filter may "
    "reference only the system pseudo-fields (key, created_at, updated_at, created_by, "
    "updated_by, comment_count, last_comment_at, deleted_at). Those eight names are "
    "reserved as field keys, so they never name a user field."
)

SEMANTIC_DISABLED_MESSAGE = (
    "Semantic search is disabled on this deployment (GW_EMBEDDING_ENABLED is false), so "
    "mode 'semantic' cannot run. Use mode 'keyword' instead; an administrator can enable "
    "embedding and run a re-index from Settings to turn semantic search on."
)


# ------------------------------------------------------------- query construction

# One alphanumeric run; underscores are separators here, exactly as FTS5's unicode61
# tokenizer treats them, so a query token is never wider than an index token.
_RUN = re.compile(r"[^\W_]+", re.UNICODE)
_EDGE_PUNCTUATION = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def keyword_phrases(query: str) -> list[str]:
    """One FTS5 phrase per whitespace word, built from its alphanumeric runs
    (docs/DATA_MODEL.md section 10).

    ``PO-88213`` becomes the phrase ``"PO 88213"``, which matches only text carrying
    that identifier rather than any text that says ``PO``; ``CTR-2024-117`` becomes
    ``"CTR 2024 117"``. A word that is a single run on ``QUERY_STOPWORDS`` is dropped.
    Everything the user typed that is not an alphanumeric run -- quotes,
    parentheses, ``NOT``, ``NEAR``, ``*``, a lone hyphen -- never appears outside a
    phrase's runs and is therefore data, not FTS5 syntax. Porter still applies inside
    a phrase, so ``"pricing"`` finds ``priced`` and ``prices``.
    """
    phrases: list[str] = []
    for word in query.split():
        runs = _RUN.findall(word)
        if not runs:
            continue
        if len(runs) == 1 and runs[0].lower() in QUERY_STOPWORDS:
            continue
        phrases.append('"' + " ".join(runs) + '"')
    return phrases


def fts_query(query: str) -> str | None:
    """The OR-joined phrase query, or ``None`` when every word was dropped (a
    stopword-only query runs no keyword arm at all)."""
    phrases = keyword_phrases(query)
    return " OR ".join(phrases) if phrases else None


def content_terms(query: str) -> list[str]:
    """The query's literal content words, for ``<em>``-wrapping a semantic snippet:
    each whitespace word with its non-alphanumeric edges stripped, single-run
    stopwords dropped, order preserved, duplicates removed."""
    terms: list[str] = []
    for word in query.split():
        stripped = _EDGE_PUNCTUATION.sub("", word)
        if not stripped:
            continue
        runs = _RUN.findall(stripped)
        if len(runs) == 1 and runs[0].lower() in QUERY_STOPWORDS:
            continue
        if stripped.lower() not in {t.lower() for t in terms}:
            terms.append(stripped)
    return terms


def semantic_snippet(chunk_text: str, terms: Sequence[str]) -> str:
    """The leading ``SEMANTIC_SNIPPET_CHARS`` characters of the nearest chunk, with the
    query's literal content terms ``<em>``-wrapped where they occur (case-insensitive,
    longest term first so a shorter term never splits a longer match)."""
    snippet = chunk_text[:SEMANTIC_SNIPPET_CHARS]
    if not terms:
        return snippet
    ordered = sorted({t for t in terms if t}, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(t) for t in ordered), re.IGNORECASE)
    return pattern.sub(lambda m: f"<em>{m.group(0)}</em>", snippet)


# ------------------------------------------------------------------ collapse


@dataclass(slots=True)
class KeywordCandidate:
    """One record's standing in the keyword arm: its rank among distinct records,
    its best (first) row, and every distinct source that matched (a field or a
    comment each count once)."""

    rank: int
    object_type_id: str
    best: KeywordHit
    sources: set[IndexSource]


@dataclass(slots=True)
class VectorCandidate:
    """One record's standing in the vector arm: its rank among distinct records and
    its nearest chunk."""

    rank: int
    object_type_id: str
    best: VectorHit


def collapse_keyword(hits: Sequence[KeywordHit]) -> dict[str, KeywordCandidate]:
    """Pools are rows, ranks are records: the first row of a record
    fixes its rank and its hit source; later rows only add to ``sources``."""
    out: dict[str, KeywordCandidate] = {}
    for hit in hits:
        candidate = out.get(hit.source.record_id)
        if candidate is None:
            out[hit.source.record_id] = KeywordCandidate(
                rank=len(out) + 1,
                object_type_id=hit.source.object_type_id,
                best=hit,
                sources={hit.source},
            )
        else:
            candidate.sources.add(hit.source)
    return out


def collapse_vector(hits: Sequence[VectorHit]) -> dict[str, VectorCandidate]:
    """Same rule for chunk rows: a 64-chunk record occupies one rank, its nearest
    chunk is its hit source, and it cannot fill the pool by itself."""
    out: dict[str, VectorCandidate] = {}
    for hit in hits:
        if hit.source.record_id not in out:
            out[hit.source.record_id] = VectorCandidate(
                rank=len(out) + 1, object_type_id=hit.source.object_type_id, best=hit
            )
    return out


# -------------------------------------------------------------------- fusion


@dataclass(frozen=True, slots=True)
class ArmRanks:
    """A record's rank in each arm (``None`` where it did not appear)."""

    keyword: int | None = None
    semantic: int | None = None


@dataclass(frozen=True, slots=True)
class FusedRecord:
    record_id: str
    score: float


def fuse(ranks: dict[str, ArmRanks], arms_run: Sequence[str]) -> list[FusedRecord]:
    """Reciprocal rank fusion at record grain, a pure function over ranked lists
    (DD-33; docs/DATA_MODEL.md section 10, rule 3).

    ``raw = sum(weight / (RRF_K + rank))`` over the arms a record appears in, then
    ``score = raw / (sum(weights of arms_run) / (RRF_K + 1))``, so a record ranked
    first in every arm that ran scores exactly 1.0 -- in ``hybrid`` with both arms,
    in ``keyword`` and ``semantic`` alone, and in degraded ``hybrid`` alike
    Exact ties break by better best-arm rank, then better keyword rank
    (an exact word match over a near neighbour), then ``records.id``;
    that is an ordering of ties, not an arm weight, which DD-33 defers.
    """
    if not arms_run:
        return []
    denominator = sum(ARM_WEIGHTS[arm] for arm in arms_run) / (RRF_K + 1)
    keyed: list[tuple[float, int, float, str, float]] = []
    for record_id, arm_ranks in ranks.items():
        raw = 0.0
        best: int | None = None
        for arm in arms_run:
            rank: int | None = getattr(arm_ranks, arm)
            if rank is None:
                continue
            raw += ARM_WEIGHTS[arm] / (RRF_K + rank)
            best = rank if best is None else min(best, rank)
        if best is None:
            continue
        score = raw / denominator
        keyword_rank = arm_ranks.keyword if arm_ranks.keyword is not None else math.inf
        # An exact tie is an equality of rationals (ranks (3, 24) and (12, 12) both
        # sum to 1/36); comparing the floats rounded past any rank arithmetic keeps
        # it a tie rather than an accident of summation order.
        keyed.append((-round(score, 12), best, keyword_rank, record_id, score))
    keyed.sort()
    return [FusedRecord(record_id=record_id, score=score) for _, _, _, record_id, score in keyed]


# ------------------------------------------------------------------- results


@dataclass(slots=True)
class SearchHit:
    record_id: str
    record_key: str
    object_type: str
    title: str
    score: float
    hit_source: dict[str, Any]
    snippet: str
    other_matches: int


@dataclass(slots=True)
class SearchResult:
    """What ``search`` returns on both surfaces, shaped by
    ``envelopes.search_result_doc``. ``widened`` is service-only: tests assert it,
    no envelope carries it."""

    results: list[SearchHit]
    pending_jobs: int
    failed_jobs: int
    mode_applied: str
    widened: bool


TypeScope = list[tuple[ObjectType, dict[str, FieldDef]]]


class SearchService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        comment_repo: CommentRepository,
        principal_repo: PrincipalRepository,
        search_repo: SearchRepository,
        access: AccessService,
        *,
        embedding_enabled: bool,
        provider: EmbeddingProvider | None,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._records = record_repo
        self._comments = comment_repo
        self._principals = principal_repo
        self._search = search_repo
        # Intersected inside ``_resolve_scope``, before either arm runs.
        self._access = access
        self._provider = provider if embedding_enabled else None

    @property
    def semantic_enabled(self) -> bool:
        """What ``describe_capabilities.search.semantic_enabled`` reports, so an agent
        can know before it asks (DD-34)."""
        return self._provider is not None

    # ------------------------------------------------------------------ search

    def search(
        self,
        actor: ActorContext,
        query: str,
        object_types: list[str] | None = None,
        filter: dict[str, Any] | None = None,
        mode: str = DEFAULT_MODE,
        limit: int = DEFAULT_SEARCH_LIMIT,
        now: datetime | None = None,
    ) -> SearchResult:
        query = _validate_query(query)
        _validate_limit(limit)
        arms, mode_applied = self._arms(mode)
        with self._db.read() as conn:
            scope, named_one = self._resolve_scope(conn, actor, object_types)
            node = self._parse_filter(conn, actor, scope, named_one, filter, now)
            type_ids = [object_type.id for object_type, _ in scope]
            scope_by_id = {object_type.id: (object_type, fields) for object_type, fields in scope}

            phrases = fts_query(query)
            run_keyword = "keyword" in arms and phrases is not None
            run_semantic = "semantic" in arms
            query_vector: list[float] | None = None
            model_id = ""
            if run_semantic:
                assert self._provider is not None
                query_vector = self._provider.embed_query(query)
                model_id = self._provider.model_id
            arms_run = [
                arm for arm, ran in (("keyword", run_keyword), ("semantic", run_semantic)) if ran
            ]

            pool = CANDIDATE_MULTIPLIER * limit
            widened = False
            while True:
                keyword_hits = (
                    self._search.keyword_search(conn, phrases, type_ids, pool)
                    if run_keyword and phrases is not None
                    else []
                )
                vector_pool = (
                    self._search.vector_search(conn, query_vector, type_ids, model_id, pool)
                    if run_semantic and query_vector is not None
                    else None
                )
                keyword = collapse_keyword(keyword_hits)
                vector = collapse_vector(vector_pool.hits if vector_pool is not None else [])
                ranks = {
                    record_id: ArmRanks(
                        keyword=keyword[record_id].rank if record_id in keyword else None,
                        semantic=vector[record_id].rank if record_id in vector else None,
                    )
                    for record_id in set(keyword) | set(vector)
                }
                fused = fuse(ranks, arms_run)
                type_of = {
                    record_id: (keyword.get(record_id) or vector[record_id]).object_type_id
                    for record_id in ranks
                }
                survivors = self._survivors(conn, scope_by_id, node, fused, type_of)
                ordered = [f for f in fused if f.record_id in survivors]
                # Starved: fewer than ``limit`` distinct records survived while at least
                # one arm's raw pool was full, so a wider pool may hold more.
                # The vector arm's fullness is its pre-join KNN count: a soft-deleted
                # record's chunks occupy KNN slots and are dropped by the live join.
                full = len(keyword_hits) >= pool or (
                    vector_pool is not None and vector_pool.knn_rows >= pool
                )
                if len(ordered) < limit and full and not widened:
                    widened = True
                    pool = WIDEN_MULTIPLIER * limit
                    continue
                break

            terms = content_terms(query)
            results = []
            for fused_record in ordered[:limit]:
                record = survivors[fused_record.record_id]
                object_type, fields = scope_by_id[record.object_type_id]
                results.append(
                    self._hit(
                        conn,
                        record,
                        object_type,
                        fields,
                        fused_record.score,
                        keyword.get(record.id),
                        vector.get(record.id),
                        terms,
                    )
                )
            counts = self._search.counts(conn)
        return SearchResult(
            results=results,
            pending_jobs=counts.pending_jobs,
            failed_jobs=counts.failed_jobs,
            mode_applied=mode_applied,
            widened=widened,
        )

    # ------------------------------------------------------------- validation

    def _arms(self, mode: str) -> tuple[tuple[str, ...], str]:
        """Which arms a mode runs on this deployment, and the ``mode_applied`` the
        response reports (DD-34 as settled: ``semantic`` on a disabled deployment is
        ``feature_disabled``; ``hybrid`` degrades to keyword and says so)."""
        if mode not in MODES:
            raise ValidationFailedError(
                f"mode must be one of {', '.join(MODES)}; got {mode!r}.",
                "mode",
                valid_options=list(MODES),
            )
        if mode == "keyword":
            return ("keyword",), "keyword"
        if mode == "semantic":
            if not self.semantic_enabled:
                raise FeatureDisabledError(
                    SEMANTIC_DISABLED_MESSAGE,
                    feature="semantic_search",
                    setting="GW_EMBEDDING_ENABLED",
                    use_instead={"mode": "keyword"},
                )
            return ("semantic",), "semantic"
        if not self.semantic_enabled:
            return ("keyword",), "keyword"
        return ("keyword", "semantic"), "hybrid"

    def _resolve_scope(
        self, conn: Connection, actor: ActorContext, object_types: list[str] | None
    ) -> tuple[TypeScope, bool]:
        """Every live type when ``object_types`` is omitted or ``[]``; otherwise the
        named types in order, duplicates dropped, each key resolved or
        ``unknown_object_type``. The flag says whether the caller **named** exactly
        one type, which is what the one-type filter rule keys off: a deployment that
        happens to have a single type does not make an omitted ``object_types`` mean
        that type, because the rule has to read the same to an agent everywhere.

        **Access.** The grant restriction is intersected **here**, before either arm
        runs, which is what makes it a scope narrowing rather than a post-filter: both
        arms receive ``type_ids`` derived from this result, and ``vector_search``
        constrains the KNN by ``object_type_id IN (...)`` *inside* its MATERIALIZED CTE,
        with ``k`` per matched partition. The starvation mode of a global KNN pool
        filtered down to nothing afterwards therefore does not apply.

        Naming an inaccessible type is ``forbidden``, because the caller named it.
        Omitting ``object_types`` silently searches only what the caller may read, which
        is the same "omitted, not refused" rule ``list_object_types`` follows. Note the
        interaction V3 flagged as correct-but-incidental and worth keeping: narrowing an
        *omitted* ``object_types`` down to a single type leaves ``named_one`` false, so
        single-type filter-field resolution does not silently switch on.
        """
        if object_types is None:
            object_types = []
        if not isinstance(object_types, list) or not all(
            isinstance(key, str) for key in object_types
        ):
            raise ValidationFailedError(
                "object_types must be a list of object type keys.", "object_types"
            )
        types: list[ObjectType] = []
        readable = self._access.accessible_type_ids(conn, actor, "read")
        if not object_types:
            types = [t for t in self._schema.list_object_types(conn) if t.id in readable]
        else:
            seen: set[str] = set()
            for key in object_types:
                if key in seen:
                    continue
                seen.add(key)
                object_type = self._schema.get_object_type_by_key(conn, key)
                if object_type is None:
                    valid = [t.key for t in self._schema.list_object_types(conn)]
                    raise UnknownObjectTypeError(key, valid)
                self._access.require_level(conn, actor, object_type, "read")
                types.append(object_type)
        scope = [
            (object_type, {f.key: f for f in self._schema.list_fields(conn, object_type.id)})
            for object_type in types
        ]
        return scope, bool(object_types) and len(types) == 1

    def _parse_filter(
        self,
        conn: Connection,
        actor: ActorContext,
        scope: TypeScope,
        named_one: bool,
        raw: dict[str, Any] | None,
        now: datetime | None,
    ) -> FilterNode | None:
        """The one-type rule, then the same parse ``query_records`` performs.

        With exactly one in-scope type the filter may name that type's fields; with
        several or none, a user-field reference is ``validation_failed`` naming the
        rule, detected **before** parsing, because ``parse_filter`` over an empty
        field map would report ``unknown_field`` and send the agent to
        ``describe_object_type`` for a field it named correctly.
        """
        if raw is None or raw == {}:
            return None
        if not named_one:
            user_keys = sorted(k for k in _filter_field_keys(raw) if k not in PSEUDO_FIELDS)
            if user_keys:
                named = len(scope) if len(scope) != 1 else 0
                raise ValidationFailedError(
                    f"filter references field {user_keys[0]!r}, but object_types names "
                    f"{named if named else 'no'} type{'' if named == 1 else 's'}. {FILTER_RULE}",
                    user_keys[0],
                    rule="one_type",
                    object_types=[object_type.key for object_type, _ in scope],
                    system_fields=list(PSEUDO_FIELDS),
                )
            object_type_key = "*"
            fields_by_key: dict[str, FieldDef] = {}
        else:
            object_type_key = scope[0][0].key
            fields_by_key = scope[0][1]
        ctx = FilterContext(
            object_type_key=object_type_key,
            fields_by_key=fields_by_key,
            now=(now or utc_now()),
            resolve_record_ref=lambda ref: self._resolve_record_ref(conn, ref),
            # The third and last construction site. ``allow_inactive=True`` for
            # the same reason ``query_records`` passes it -- a search filter is a read.
            resolve_principal_ref=lambda value: resolve_principal_ref(
                conn, self._principals, value, me=actor.principal_id, allow_inactive=True
            ),
        )
        return parse_filter(raw, ctx)

    def _resolve_record_ref(self, conn: Connection, ref: str) -> str:
        record = self._records.get_record(conn, ref, include_deleted=True)
        if record is None:
            raise NotFoundError("record", ref)
        return record.id

    # ------------------------------------------------------------ post-filter

    def _survivors(
        self,
        conn: Connection,
        scope_by_id: dict[str, tuple[ObjectType, dict[str, FieldDef]]],
        node: FilterNode | None,
        fused: Sequence[FusedRecord],
        type_of: dict[str, str],
    ) -> dict[str, RecordRow]:
        """``records.id IN (<candidates>) AND <compiled>`` per in-scope type
        (docs/DATA_MODEL.md section 10, rule 4), through the compiler and the record
        repository like any other query. Returns the surviving rows by id; the rows
        themselves carry the key, the display value, and the version a hit needs."""
        by_type: dict[str, list[str]] = {}
        for fused_record in fused:
            by_type.setdefault(type_of[fused_record.record_id], []).append(fused_record.record_id)
        survivors: dict[str, RecordRow] = {}
        for type_id, candidate_ids in by_type.items():
            object_type, _ = scope_by_id[type_id]
            sql, params = compile_candidate_filter(object_type, node, candidate_ids)
            for record, _extras in self._records.run_query(conn, sql, params):
                survivors[record.id] = record
        return survivors

    # ------------------------------------------------------------------- hits

    def _hit(
        self,
        conn: Connection,
        record: RecordRow,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        score: float,
        keyword: KeywordCandidate | None,
        vector: VectorCandidate | None,
        terms: Sequence[str],
    ) -> SearchHit:
        """Keyword-first: the best keyword source with its FTS5 snippet
        when the record has one, otherwise the nearest chunk's source with a literal
        snippet; ``other_matches`` counts additional keyword sources and is 0 for a
        semantic-only hit."""
        if keyword is not None:
            source = keyword.best.source
            snippet = keyword.best.snippet
            other_matches = len(keyword.sources) - 1
        else:
            assert vector is not None
            source = vector.best.source
            snippet = semantic_snippet(vector.best.chunk_text, terms)
            other_matches = 0
        return SearchHit(
            record_id=record.id,
            record_key=record.key,
            object_type=object_type.key,
            title=_title(record, fields_by_key, object_type.display_field_key),
            score=score,
            hit_source=self._hit_source(conn, source),
            snippet=snippet,
            other_matches=other_matches,
        )

    def _hit_source(self, conn: Connection, source: IndexSource) -> dict[str, Any]:
        if source.source_type == "field":
            return {"type": "field", "field_key": source.field_key}
        assert source.comment_id is not None
        comment = self._comments.get_comment(conn, source.comment_id)
        if comment is None:  # pragma: no cover - the live join already excluded it
            return {"type": "comment", "comment_id": source.comment_id}
        author = self._principals.get(conn, comment.author_id)
        return {
            "type": "comment",
            "comment_id": comment.id,
            "author": author.display_name if author is not None else comment.author_id,
            "created_at": comment.created_at,
        }


# ------------------------------------------------------------------- helpers


def _validate_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValidationFailedError("query must be a non-empty string.", "query")
    stripped = query.strip()
    if len(stripped) > MAX_QUERY_CHARS:
        raise ValidationFailedError(
            f"query is limited to {MAX_QUERY_CHARS} characters; got {len(stripped)}.",
            "query",
            max_chars=MAX_QUERY_CHARS,
        )
    return stripped


def _validate_limit(limit: Any) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= MAX_SEARCH_LIMIT)
    ):
        raise ValidationFailedError(
            f"limit must be an integer between 1 and {MAX_SEARCH_LIMIT}; got {limit!r}.",
            "limit",
            max_limit=MAX_SEARCH_LIMIT,
        )


def _filter_field_keys(raw: Any, depth: int = 1, value_depth: int = 0) -> set[str]:
    """Every ``field`` a raw filter tree names, tolerant of malformed shapes (which
    ``parse_filter`` reports precisely afterwards).

    Depth-capped (DD-18), and it counts **filter nodes**, so it refuses
    exactly what ``filters._parse_node`` refuses. The two walkers have different
    recursion shapes and a naive ``> MAX_FILTER_DEPTH`` in both would not have agreed:

    - this one descends through ``raw.values()`` and then through list items, so an
      ``and``/``or`` node costs it two levels of recursion where ``_parse_node`` costs
      one. ``depth`` is therefore incremented only when descending from a filter node
      into a child, and the intervening list hop carries the count through unchanged;
    - scalar leaves cost nothing at all. Counting them was this function's own first
      bug: a condition's ``"field": "title"`` is a string, and charging a level for it
      refused, on the search path, a tree the query path accepted at exactly the cap;
    - this one also descends into a condition's ``value``, which ``_parse_node`` never
      does. A deeply nested **value** is not a deeply nested filter, so it is bounded on
      its own budget: otherwise a two-element ``between`` would count toward nesting,
      and a shallow filter carrying a 10,000-deep literal would recurse here and nowhere
      else.

    This runs only on the multi-type search path (``not named_one``); the single-type
    path is covered by ``_parse_node`` alone.
    """
    if depth > MAX_FILTER_DEPTH or value_depth > MAX_FILTER_DEPTH:
        raise too_deep(max(depth, value_depth))
    keys: set[str] = set()
    if isinstance(raw, dict):
        field = raw.get("field")
        if isinstance(field, str):
            keys.add(field)
        # Inside a value nothing is a filter node, however node-shaped it looks: a
        # caller can put ``{"and": [...]}`` in a value and it is data there.
        is_node = value_depth == 0 and (bool(_COMBINATORS & set(raw)) or "field" in raw)
        for key, value in raw.items():
            if not isinstance(value, dict | list):
                continue
            if is_node and key == "value":
                keys |= _filter_field_keys(value, depth, 1)
            elif is_node:
                keys |= _filter_field_keys(value, depth + 1, 0)
            else:
                keys |= _filter_field_keys(value, depth, value_depth + 1)
    elif isinstance(raw, list):
        # A list between a node and its children is the node's own child list and costs
        # nothing; a list nested inside a value is another level of that value.
        child_value_depth = value_depth + 1 if value_depth else 0
        for item in raw:
            keys |= _filter_field_keys(item, depth, child_value_depth)
    return keys


def _title(
    record: RecordRow, fields_by_key: dict[str, FieldDef], display_field_key: str | None = None
) -> str:
    """The display field's value, or the record key when it is empty.

    Told the object type's chosen key; the rule itself lives in
    ``services/base.display_field`` and nowhere else."""
    field = display_field(fields_by_key, display_field_key)
    if field is not None:
        value = record.data.get(field.key)
        if isinstance(value, str):
            if value.strip():
                return value
        elif value is not None and not isinstance(value, list | dict):
            return str(value)
    return record.key
