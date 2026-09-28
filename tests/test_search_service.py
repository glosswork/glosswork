"""The search service (FR-Q1, FR-Q2, FR-Q4, FR-Q5; DD-33, DD-34;
docs/DATA_MODEL.md section 10, "Hybrid ranking").

Most of this module runs over the deterministic ``FakeEmbeddingProvider``: its vectors
are hash-seeded, so semantic *order* is meaningless there and what is under test is
validation, query construction, fusion arithmetic, collapse, the filter intersection,
starvation, hit sources, and the disabled-mode clauses. The few tests that need real
semantics -- a paraphrase found, chunk crowding in the vector arm -- use the real
bundled model and fail rather than skip without it (DD-32).
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import (
    FeatureDisabledError,
    UnknownFieldError,
    UnknownObjectTypeError,
    ValidationFailedError,
)
from glosswork.repositories.models import CommentRow, RecordRow
from glosswork.services import ServiceBundle
from glosswork.services.search import (
    ArmRanks,
    SearchResult,
    content_terms,
    fts_query,
    fuse,
    keyword_phrases,
    semantic_snippet,
)
from glosswork.services.search_tuning import (
    CANDIDATE_MULTIPLIER,
    QUERY_STOPWORDS,
    RRF_K,
    SEMANTIC_SNIPPET_CHARS,
    WIDEN_MULTIPLIER,
)
from tests.conftest import make_actor, make_worker, select_options
from tests.search_support import FakeClock, drain, real_provider

NOTE_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "The note's title; opted in to search.",
        "required": True,
        "embed": True,
    },
    {
        "key": "body",
        "name": "Body",
        "type": "long_text",
        "description": "The note's narrative; the main searchable text.",
    },
    {
        "key": "region",
        "name": "Region",
        "type": "single_select",
        "description": "Which region the note concerns.",
        "config": {"options": select_options("north", "south")},
    },
    {
        "key": "code",
        "name": "Code",
        "type": "short_text",
        "description": "An internal code, deliberately not indexed.",
        "embed": False,
    },
]

MEMO_FIELDS: list[dict[str, Any]] = [
    {
        "key": "subject",
        "name": "Subject",
        "type": "short_text",
        "description": "The memo's subject line; opted in to search.",
        "required": True,
        "embed": True,
    },
    {
        "key": "text",
        "name": "Text",
        "type": "long_text",
        "description": "The memo's text.",
    },
]


def _create_types(services: ServiceBundle) -> None:
    services.schema.create_object_type(
        make_actor(),
        key="note",
        name="Note",
        name_plural="Notes",
        description="A note used by the search service tests.",
        key_prefix="NOTE",
        fields=NOTE_FIELDS,
    )
    services.schema.create_object_type(
        make_actor(),
        key="memo",
        name="Memo",
        name_plural="Memos",
        description="A memo used by the search service tests; a second type for scoping.",
        key_prefix="MEMO",
        fields=MEMO_FIELDS,
    )


@pytest.fixture
def corpus(search_services: ServiceBundle) -> ServiceBundle:
    """The two types over the fake provider, nothing seeded."""
    _create_types(search_services)
    return search_services


@pytest.fixture
def real_corpus(real_search_services: ServiceBundle) -> ServiceBundle:
    _create_types(real_search_services)
    return real_search_services


def note(services: ServiceBundle, actor: ActorContext | None = None, **values: Any) -> RecordRow:
    return services.records.create_record(actor or make_actor(), "note", values)


def memo(services: ServiceBundle, **values: Any) -> RecordRow:
    return services.records.create_record(make_actor(), "memo", values)


def comment(services: ServiceBundle, record: RecordRow, body: str) -> CommentRow:
    return services.comments.add_comment(make_actor(), record.key, body)


def drain_all(db: Database, services: ServiceBundle) -> None:
    drain(make_worker(db, services, FakeClock()))


def keys(result: SearchResult) -> list[str]:
    return [hit.record_key for hit in result.results]


def search(services: ServiceBundle, query: str, **kwargs: Any) -> SearchResult:
    return services.search.search(make_actor(), query, **kwargs)


# ---------------------------------------------------------------- validation


def test_an_empty_or_whitespace_query_is_validation_failed(corpus: ServiceBundle) -> None:
    for query in ("", "   ", "\n\t"):
        with pytest.raises(ValidationFailedError) as excinfo:
            search(corpus, query)
        assert excinfo.value.details["field_key"] == "query"


def test_a_query_over_one_thousand_characters_is_validation_failed(
    corpus: ServiceBundle,
) -> None:
    assert search(corpus, "x" * 1000, mode="keyword").results == []
    with pytest.raises(ValidationFailedError) as excinfo:
        search(corpus, "x" * 1001)
    assert excinfo.value.details["max_chars"] == 1000


@pytest.mark.parametrize("limit", [0, 51, -1, True, "10"])
def test_limit_outside_one_to_fifty_is_validation_failed(corpus: ServiceBundle, limit: Any) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        search(corpus, "anything", limit=limit)
    assert excinfo.value.details["field_key"] == "limit"
    assert excinfo.value.details["max_limit"] == 50


def test_an_unknown_mode_is_validation_failed(corpus: ServiceBundle) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        search(corpus, "anything", mode="fuzzy")
    assert excinfo.value.details["valid_options"] == ["hybrid", "semantic", "keyword"]


def test_an_unknown_object_type_key_is_unknown_object_type(corpus: ServiceBundle) -> None:
    with pytest.raises(UnknownObjectTypeError) as excinfo:
        search(corpus, "anything", object_types=["note", "nope"])
    assert excinfo.value.details["valid_keys"] == ["memo", "note"]


def test_duplicate_object_types_are_dropped_and_an_empty_list_means_omitted(
    corpus: ServiceBundle, db: Database
) -> None:
    note(corpus, title="Zebra north", body="A zebra note.")
    memo(corpus, subject="Zebra memo", text="A zebra memo.")
    everything = search(corpus, "zebra", mode="keyword")
    assert set(keys(everything)) == set(
        keys(search(corpus, "zebra", mode="keyword", object_types=[]))
    )
    assert len(keys(everything)) == 2
    scoped = search(corpus, "zebra", mode="keyword", object_types=["note", "note", "note"])
    assert [hit.object_type for hit in scoped.results] == ["note"]


# ------------------------------------------------------- query construction


def test_an_identifier_becomes_one_phrase_of_its_alphanumeric_runs() -> None:
    assert keyword_phrases("PO-88213") == ['"PO 88213"']
    assert keyword_phrases("contract CTR-2024-117") == ['"contract"', '"CTR 2024 117"']
    assert fts_query("PO-88213 stuck") == '"PO 88213" OR "stuck"'


def test_single_run_stopwords_are_dropped_and_a_stopword_only_query_has_no_phrases() -> None:
    assert keyword_phrases("the dashcam of the union") == ['"dashcam"', '"union"']
    assert fts_query("the and of") is None
    assert "the" in QUERY_STOPWORDS and "dashcam" not in QUERY_STOPWORDS


@pytest.mark.parametrize(
    "hostile",
    [
        '"quoted"',
        "(paren) NOT this",
        "NEAR(a b)",
        "wild*",
        "-",
        'a"b',
        "спутник",
        "東京",
        "* OR *",
        '"""',
    ],
)
def test_hostile_syntax_never_errors_and_never_escapes_a_phrase(
    corpus: ServiceBundle, hostile: str
) -> None:
    """Raw user text never reaches ``MATCH``: every phrase is a quoted string of
    alphanumeric runs, so FTS5 operators typed by a user are data, not syntax."""
    for phrase in keyword_phrases(hostile):
        assert phrase.startswith('"') and phrase.endswith('"')
        inner = phrase[1:-1]
        assert '"' not in inner and "(" not in inner and "*" not in inner
        assert all(part.isalnum() for part in inner.split(" "))
    note(corpus, title="Plain note", body="Nothing hostile here.")
    result = search(corpus, hostile, mode="keyword")  # must not raise
    assert isinstance(result.results, list)


def test_an_identifier_phrase_matches_only_the_source_holding_it(corpus: ServiceBundle) -> None:
    target = note(corpus, title="Chase the server", body="Purchase order PO-88213 is stuck.")
    note(corpus, title="Second batch", body="The PO for the second batch is awaiting approval.")
    result = search(corpus, "PO-88213", mode="keyword")
    assert keys(result) == [target.key]


def test_a_stopword_only_query_returns_nothing_in_keyword_mode_without_error(
    corpus: ServiceBundle,
) -> None:
    note(corpus, title="The note", body="Of the and.")
    result = search(corpus, "the and of", mode="keyword")
    assert result.results == [] and result.mode_applied == "keyword"


def test_a_stopword_only_query_runs_the_semantic_arm_alone_in_hybrid(
    corpus: ServiceBundle, db: Database
) -> None:
    record = note(corpus, title="Only note", body="Some body text about nothing.")
    drain_all(db, corpus)
    result = search(corpus, "the and of", mode="hybrid")
    assert result.mode_applied == "hybrid"
    assert keys(result) == [record.key]
    # One arm ran, so its rank 1 scores 1.0 (decision 9), and it is a semantic-only hit.
    assert result.results[0].score == pytest.approx(1.0)
    assert result.results[0].other_matches == 0


# --------------------------------------------------------------------- fusion


def test_fusion_first_in_both_arms_scores_exactly_one() -> None:
    fused = fuse({"a": ArmRanks(keyword=1, semantic=1)}, ("keyword", "semantic"))
    assert fused[0].record_id == "a" and fused[0].score == pytest.approx(1.0)


def test_fusion_keyword_only_rank_one_scores_one_in_keyword_mode() -> None:
    fused = fuse({"a": ArmRanks(keyword=1), "b": ArmRanks(keyword=2)}, ("keyword",))
    assert [f.record_id for f in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(1.0)
    assert fused[1].score == pytest.approx((1 / (RRF_K + 2)) / (1 / (RRF_K + 1)))


def test_fusion_the_one_two_versus_two_one_tie_resolves_to_the_keyword_first_record() -> None:
    """The class (C) shape: the exact match is keyword 1 / semantic 2 and its
    same-topic decoy is keyword 2 / semantic 1. Equal raw scores, equal best rank,
    so the keyword rank decides (decision 8)."""
    fused = fuse(
        {"decoy": ArmRanks(keyword=2, semantic=1), "exact": ArmRanks(keyword=1, semantic=2)},
        ("keyword", "semantic"),
    )
    assert [f.record_id for f in fused] == ["exact", "decoy"]
    assert fused[0].score == pytest.approx(fused[1].score)


def test_fusion_a_better_best_arm_rank_beats_an_equal_score_with_a_worse_one() -> None:
    """1/63 + 1/84 and 1/72 + 1/72 are both exactly 1/36: an exact tie in raw score
    between a record whose best rank is 3 and one whose best rank is 12."""
    fused = fuse(
        {"even": ArmRanks(keyword=12, semantic=12), "peaked": ArmRanks(keyword=3, semantic=24)},
        ("keyword", "semantic"),
    )
    assert fused[0].score == pytest.approx(fused[1].score)
    assert [f.record_id for f in fused] == ["peaked", "even"]


def test_fusion_falls_through_to_record_id_on_a_complete_tie_and_ignores_arms_not_run() -> None:
    fused = fuse(
        {"b": ArmRanks(keyword=1, semantic=9), "a": ArmRanks(keyword=1, semantic=3)}, ("keyword",)
    )
    assert [f.record_id for f in fused] == ["a", "b"]
    assert fuse({"a": ArmRanks(keyword=1)}, ()) == []
    assert fuse({"a": ArmRanks(semantic=1)}, ("keyword",)) == []


# ---------------------------------------------- pools, collapse, starvation


def test_a_record_with_twenty_matching_rows_does_not_crowd_out_other_records(
    corpus: ServiceBundle, db: Database
) -> None:
    """Pools are rows, ranks are records (decision 6): the crowded record occupies
    one rank; every other matching record still appears."""
    crowded = note(corpus, title="Crowded", body="zebra")
    for index in range(20):
        comment(corpus, crowded, f"zebra sighting number {index}")
    others = [note(corpus, title=f"Other {index}", body="a zebra too") for index in range(3)]
    result = search(corpus, "zebra", mode="keyword", limit=5)
    assert set(keys(result)) == {crowded.key, *[o.key for o in others]}
    assert keys(result).count(crowded.key) == 1


def test_a_soft_deleted_records_rows_consume_no_keyword_pool_slot(
    corpus: ServiceBundle, db: Database
) -> None:
    """The keyword arm joins ``records`` and ``comments`` on ``deleted_at IS NULL``
    *before* ``LIMIT k``: eight deleted rows ahead of two live ones leave the pool
    holding the live ones with no widening at all."""
    deleted = note(corpus, title="Gone", body="zebra")
    for index in range(8):
        comment(corpus, deleted, f"zebra {index}")
    live = [note(corpus, title=f"Live {index}", body="zebra") for index in range(2)]
    corpus.records.delete_record(make_actor(), deleted.key)
    result = search(corpus, "zebra", mode="keyword", limit=2)
    assert set(keys(result)) == {record.key for record in live}
    assert result.widened is False


def test_a_soft_deleted_comments_rows_consume_no_keyword_pool_slot(
    corpus: ServiceBundle, db: Database
) -> None:
    holder = note(corpus, title="Holder", body="nothing relevant")
    doomed = [comment(corpus, holder, f"zebra {index}") for index in range(8)]
    live = [note(corpus, title=f"Live {index}", body="zebra") for index in range(2)]
    for row in doomed:
        corpus.comments.delete_comment(make_actor(), row.id)
    result = search(corpus, "zebra", mode="keyword", limit=2)
    assert set(keys(result)) == {record.key for record in live}
    assert holder.key not in keys(result)
    assert result.widened is False


def test_soft_deleted_chunks_in_the_vector_pool_trigger_one_widen_not_an_empty_answer(
    corpus: ServiceBundle, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``records.deleted_at`` is invisible inside vec0, so a deleted record's chunks
    occupy KNN slots. The pre-join count says the pool was full, the join empties it,
    and the service widens once rather than reading zero live rows as "nothing more to
    find" (decision 6: widening covers soft deletion for the vector arm)."""
    deleted = note(corpus, title="Gone", body="zebra")
    for index in range(8):
        comment(corpus, deleted, f"zebra {index}")
    live = note(corpus, title="Live", body="zebra")
    drain_all(db, corpus)
    corpus.records.delete_record(make_actor(), deleted.key)
    repo = corpus.search._search  # type: ignore[attr-defined]
    calls: list[int] = []
    original = repo.vector_search

    def counting(conn: Any, vector: Any, type_ids: Any, model_id: Any, k: int) -> Any:
        calls.append(k)
        return original(conn, vector, type_ids, model_id, k)

    monkeypatch.setattr(repo, "vector_search", counting)
    result = search(corpus, "zebra", mode="semantic", limit=2)
    assert keys(result) == [live.key]
    # The live record's rows are the only survivors; whether the first pool held any
    # of them depends on hash-seeded distances, so what is asserted is the bound: at
    # most one widen, to exactly WIDEN_MULTIPLIER x limit, never a third pass.
    assert calls in ([CANDIDATE_MULTIPLIER * 2], [CANDIDATE_MULTIPLIER * 2, WIDEN_MULTIPLIER * 2])


def test_a_selective_filter_with_a_full_pool_widens_once_and_returns_the_survivors(
    corpus: ServiceBundle, db: Database
) -> None:
    """Ten identical bodies rank by source id, so the two ``north`` notes created last
    are outside the first pool of 4 x limit = 8 rows; the filter leaves no survivor,
    the pool was full, and the widened 16 x limit pool finds them."""
    for index in range(8):
        note(corpus, title=f"South {index}", body="zebra", region="south")
    north = [
        note(corpus, title=f"North {index}", body="zebra", region="north") for index in range(2)
    ]
    result = search(
        corpus,
        "zebra",
        mode="keyword",
        limit=2,
        object_types=["note"],
        filter={"field": "region", "op": "eq", "value": "north"},
    )
    assert set(keys(result)) == {record.key for record in north}
    assert result.widened is True


def test_a_second_widen_never_happens(
    corpus: ServiceBundle, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(10):
        note(corpus, title=f"South {index}", body="zebra", region="south")
    repo = corpus.search._search  # type: ignore[attr-defined]
    calls: list[int] = []
    original = repo.keyword_search

    def counting(conn: Any, query: Any, type_ids: Any, k: int) -> Any:
        calls.append(k)
        return original(conn, query, type_ids, k)

    monkeypatch.setattr(repo, "keyword_search", counting)
    result = search(
        corpus,
        "zebra",
        mode="keyword",
        limit=2,
        object_types=["note"],
        filter={"field": "region", "op": "eq", "value": "north"},
    )
    assert result.results == [] and result.widened is True
    assert calls == [CANDIDATE_MULTIPLIER * 2, WIDEN_MULTIPLIER * 2]


def test_an_unfilled_pool_does_not_widen(corpus: ServiceBundle) -> None:
    note(corpus, title="Only", body="zebra", region="south")
    result = search(
        corpus,
        "zebra",
        mode="keyword",
        limit=5,
        object_types=["note"],
        filter={"field": "region", "op": "eq", "value": "north"},
    )
    assert result.results == [] and result.widened is False


# --------------------------------------------------- the structured filter


def test_the_one_type_rule_is_enforced_before_parsing(corpus: ServiceBundle) -> None:
    """With several or no object_types a user-field reference is validation_failed
    naming the rule -- not unknown_field, which would send an agent to
    describe_object_type for a field it named correctly."""
    for scope in (None, [], ["note", "memo"]):
        with pytest.raises(ValidationFailedError) as excinfo:
            search(
                corpus,
                "zebra",
                object_types=scope,
                filter={"field": "region", "op": "eq", "value": "north"},
            )
        details = excinfo.value.details
        assert details["rule"] == "one_type" and details["field_key"] == "region"
        assert "exactly one type" in excinfo.value.message
    # Exactly one type: the type's own fields resolve, and a typo is unknown_field.
    with pytest.raises(UnknownFieldError):
        search(
            corpus,
            "zebra",
            object_types=["note"],
            filter={"field": "regio", "op": "eq", "value": "north"},
        )


def test_pseudo_fields_are_allowed_across_types_and_at_me_resolves_to_the_caller(
    corpus: ServiceBundle,
) -> None:
    mine = make_actor()
    theirs = corpus.principals.create_user(
        make_actor(),
        email="second@example.com",
        display_name="Second Admin",
        role="admin",
        auth_provider="local",
        password="a-long-enough-password",
    )
    other_actor = ActorContext(
        principal_id=theirs.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-other",
        scope="admin",
    )
    own = note(corpus, mine, title="Mine", body="zebra")
    note(corpus, other_actor, title="Theirs", body="zebra")
    memo(corpus, subject="Memo", text="zebra")
    result = corpus.search.search(
        mine,
        "zebra",
        mode="keyword",
        filter={"field": "created_by", "op": "eq", "value": "@me"},
    )
    assert own.key in keys(result) and "Theirs" not in [h.title for h in result.results]
    as_other = corpus.search.search(
        other_actor,
        "zebra",
        mode="keyword",
        filter={"field": "created_by", "op": "eq", "value": "@me"},
    )
    assert [hit.title for hit in as_other.results] == ["Theirs"]


def test_a_comment_count_filter_intersects_with_relevance(corpus: ServiceBundle) -> None:
    quiet = note(corpus, title="Quiet", body="zebra")
    noisy = note(corpus, title="Noisy", body="zebra")
    comment(corpus, noisy, "a remark")
    result = search(
        corpus, "zebra", mode="keyword", filter={"field": "comment_count", "op": "eq", "value": 0}
    )
    assert keys(result) == [quiet.key]


def test_a_filter_on_the_one_named_types_own_field_uses_date_tokens_like_query_records(
    corpus: ServiceBundle,
) -> None:
    note(corpus, title="Recent", body="zebra")
    result = search(
        corpus,
        "zebra",
        mode="keyword",
        object_types=["note"],
        filter={"field": "created_at", "op": "gte", "value": "@today-1d"},
    )
    assert len(result.results) == 1


# ------------------------------------------- collapse, hit source, snippet


def test_a_record_matching_in_a_field_and_two_comments_returns_once_with_other_matches_two(
    corpus: ServiceBundle,
) -> None:
    record = note(corpus, title="Regional pricing", body="The deal desk go-live is June.")
    comment(corpus, record, "Deal desk staffing is two analysts.")
    comment(corpus, record, "Until the deal desk is staffed, exceptions come by email.")
    result = search(corpus, "deal desk", mode="keyword")
    assert keys(result) == [record.key]
    assert result.results[0].other_matches == 2
    assert result.results[0].hit_source["type"] in ("field", "comment")


def test_a_keyword_hit_carries_the_fts_snippet_with_em_markers(corpus: ServiceBundle) -> None:
    note(corpus, title="Telematics", body="The telematics vendor contract expires in September.")
    result = search(corpus, "telematics contract", mode="keyword")
    assert "<em>telematics</em>" in result.results[0].snippet
    assert "<em>contract</em>" in result.results[0].snippet


def test_a_semantic_only_hit_carries_the_nearest_chunks_source_and_a_literal_snippet(
    corpus: ServiceBundle, db: Database
) -> None:
    long_body = "Zebra crossings. " * 40  # well over 240 characters, one chunk
    record = note(corpus, title="Crossing", body=long_body)
    drain_all(db, corpus)
    result = search(corpus, "zebra", mode="semantic")
    hit = result.results[0]
    assert hit.record_key == record.key and hit.other_matches == 0
    assert hit.hit_source["type"] == "field" and hit.hit_source["field_key"] in ("title", "body")
    plain = hit.snippet.replace("<em>", "").replace("</em>", "")
    assert len(plain) <= SEMANTIC_SNIPPET_CHARS
    if hit.hit_source["field_key"] == "body":
        assert plain == long_body[:SEMANTIC_SNIPPET_CHARS]
    assert "<em>Zebra</em>" in hit.snippet


def test_semantic_snippet_wraps_literal_terms_case_insensitively_longest_first() -> None:
    assert semantic_snippet("The Deal Desk and the desk.", ["deal desk", "desk"]) == (
        "The <em>Deal Desk</em> and the <em>desk</em>."
    )
    assert semantic_snippet("x" * 300, []) == "x" * SEMANTIC_SNIPPET_CHARS
    assert content_terms('the PO-88213, of "quoted" terms') == ["PO-88213", "quoted", "terms"]


def test_title_is_the_display_field_value_or_the_record_key_when_empty(
    corpus: ServiceBundle,
) -> None:
    titled = note(corpus, title="A real title", body="zebra one")
    result = search(corpus, "zebra one", mode="keyword")
    assert result.results[0].title == "A real title"
    # ``memo`` requires ``subject`` (its display field), so an empty display value
    # needs a type whose first field is optional: seed one.
    corpus.schema.create_object_type(
        make_actor(),
        key="scrap",
        name="Scrap",
        name_plural="Scraps",
        description="A type whose display field may be empty.",
        key_prefix="SCR",
        fields=[
            {
                "key": "label",
                "name": "Label",
                "type": "short_text",
                "description": "Optional label.",
            },
            {"key": "text", "name": "Text", "type": "long_text", "description": "Body."},
        ],
    )
    untitled = corpus.records.create_record(make_actor(), "scrap", {"text": "zebra two"})
    result = search(corpus, "zebra two", mode="keyword", object_types=["scrap"])
    assert result.results[0].title == untitled.key
    assert titled.key != untitled.key


def test_a_comment_hit_source_carries_the_comment_id_author_display_name_and_created_at(
    corpus: ServiceBundle,
) -> None:
    record = note(corpus, title="Risk", body="Certificate renewal has no owner.")
    row = comment(corpus, record, "Priya knows the bank file format.")
    result = search(corpus, "bank file format", mode="keyword")
    source = result.results[0].hit_source
    assert source["type"] == "comment" and source["comment_id"] == row.id
    assert source["created_at"] == row.created_at
    author = corpus.principals.get_principal(row.author_id)
    assert source["author"] == author.display_name


# ---------------------------------------------------------------- index lag


def test_index_lag_reports_pending_jobs_before_a_drain(corpus: ServiceBundle, db: Database) -> None:
    note(corpus, title="Pending", body="zebra")
    before = search(corpus, "zebra", mode="keyword")
    assert before.pending_jobs == 2 and before.failed_jobs == 0  # title and body
    drain_all(db, corpus)
    after = search(corpus, "zebra", mode="keyword")
    assert after.pending_jobs == 0


# ---------------------------------------------- GW_EMBEDDING_ENABLED=false


@pytest.fixture
def disabled(services: ServiceBundle) -> ServiceBundle:
    """The shared ``services`` fixture already runs with embedding disabled."""
    _create_types(services)
    note(services, title="Zebra", body="A zebra note for the disabled deployment.")
    return services


def test_semantic_mode_is_feature_disabled_with_use_instead(disabled: ServiceBundle) -> None:
    with pytest.raises(FeatureDisabledError) as excinfo:
        search(disabled, "zebra", mode="semantic")
    details = excinfo.value.details
    assert details == {
        "feature": "semantic_search",
        "setting": "GW_EMBEDDING_ENABLED",
        "use_instead": {"mode": "keyword"},
    }
    assert "re-index" in excinfo.value.message
    assert disabled.search.semantic_enabled is False


def test_hybrid_degrades_to_keyword_and_says_so_with_rank_one_scoring_one(
    disabled: ServiceBundle,
) -> None:
    result = search(disabled, "zebra", mode="hybrid")
    assert result.mode_applied == "keyword"
    assert result.results[0].score == pytest.approx(1.0)


def test_keyword_mode_is_unaffected_by_embedding_being_disabled(disabled: ServiceBundle) -> None:
    result = search(disabled, "zebra", mode="keyword")
    assert result.mode_applied == "keyword" and len(result.results) == 1


# ---------------------------------------------- the real model, where it matters


def test_a_paraphrase_is_found_by_the_semantic_arm_and_not_by_keyword(
    real_corpus: ServiceBundle, db: Database
) -> None:
    target = note(
        real_corpus,
        title="Vendor lock-in on the analytics estate",
        body=(
            "Our analytics estate depends on one cloud provider's proprietary storage "
            "format; moving to another supplier would mean rewriting every pipeline."
        ),
    )
    note(real_corpus, title="Warehouse slotting", body="Re-plan where fast-moving stock sits.")
    drain_all(db, real_corpus)
    query = "trapped with a single hosting company because of their custom file layout"
    assert target.key not in keys(search(real_corpus, query, mode="keyword"))
    semantic = search(real_corpus, query, mode="semantic")
    # Found by meaning alone. Rank 1 is *not* asserted here: on a two-record corpus
    # the short opted-in title "Warehouse slotting" sits close to everything (measured
    # cos 0.598 against 0.581 for the lock-in title), which is a model property the
    # golden set judges on its full corpus with a top-3 floor (DD-33, class D).
    assert target.key in keys(semantic)
    assert semantic.results[0].score == pytest.approx(1.0)
    assert all(hit.other_matches == 0 for hit in semantic.results)


def test_twenty_near_chunks_of_one_record_do_not_crowd_the_vector_pool(
    real_corpus: ServiceBundle, db: Database
) -> None:
    """A 20-chunk record occupies one rank in the vector arm; two other records that
    paraphrase the query still appear (decision 6), via the widen when they must."""
    sentence = (
        "The customer portal certificate expired and the renewal step has no owner, "
        "so the outage will repeat unless someone automates the renewal."
    )
    crowded = note(real_corpus, title="Repeat outage", body="\n\n".join([sentence] * 20))
    others = [
        note(
            real_corpus,
            title="Certificate renewal risk",
            body="Nobody owns the manual certificate renewal on the load balancer.",
        ),
        note(
            real_corpus,
            title="Portal downtime",
            body="The portal went down when its TLS certificate lapsed.",
        ),
    ]
    drain_all(db, real_corpus)
    result = search(
        real_corpus, "expired certificate takes the portal down", mode="semantic", limit=3
    )
    assert set(keys(result)) == {crowded.key, *[o.key for o in others]}


def test_a_600_token_query_embeds_while_token_offsets_runs_on_another_thread() -> None:
    """``token_offsets`` must not toggle truncation on the tokenizer
    ``embed_query`` shares. With one tokenizer, a query encoded during a chunking call
    reached the model untruncated and failed; with two, every result is a vector."""
    provider = real_provider()
    long_query = " ".join(f"word{i}" for i in range(600))
    long_text = "A sentence that keeps the tokenizer busy. " * 400
    stop = threading.Event()
    errors: list[BaseException] = []

    def churn() -> None:
        try:
            while not stop.is_set():
                provider.token_offsets(long_text)  # type: ignore[attr-defined]
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    thread = threading.Thread(target=churn)
    thread.start()
    try:
        for _ in range(20):
            vector = provider.embed_query(long_query)  # type: ignore[attr-defined]
            assert len(vector) == 384
    finally:
        stop.set()
        thread.join(timeout=30)
    assert errors == []
    assert len(provider.token_offsets(long_query)) > 512  # type: ignore[attr-defined]
