"""The partition-key claim, tested at scale.

Migration 6 gives ``vec_embeddings`` ``object_type_id`` and ``model_id`` partition keys,
on the argument that a global KNN plus a post-filter starves structurally on any scoped
search once the target type is a small fraction of the corpus. A 36-record search corpus
cannot exercise that argument: a KNN of 40 simply returns everything and starvation
cannot occur. So the claim is tested here, at scale.

**What actually makes the KNN starve is the ratio, not the absolute size.** A vector
index answers "the k nearest chunks overall"; if the in-scope type holds 1% of them,
roughly 1% of those k are usable and the post-filter throws the rest away. That holds
identically at 5,000 chunks and at 500,000. So this module builds a corpus with the
ratio the performance seed produces — one type at ~1% — at a size a test suite can pay for, and
asserts the mechanism directly. The absolute figures on the real 200,000-record seeded
corpus are reported in ``docs/PERFORMANCE.md``; the mechanism is proven here, where it
can be asserted rather than observed.

The provider is the deterministic fake (the FR-Q8 seam): its vectors are hash-seeded
and therefore effectively random directions, which is exactly right for this test.
It measures partition arithmetic and pool arithmetic, never relevance, and it must not
touch the golden set — DD-33's floors keep running the real model over their own
corpus, unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.repositories.sqlite import SqliteSchemaRepository, SqliteSearchRepository
from glosswork.services import ServiceBundle
from glosswork.services.search_tuning import CANDIDATE_MULTIPLIER
from tests.conftest import make_worker
from tests.search_support import FakeClock, drain

# One "haystack" type holding the bulk of the corpus and one "needle" type holding
# about 1%, matching the shape ``scripts/seed_perf.py`` produces (``benefit_case`` is
# 2,000 of 200,000). Sized so the needle type is genuinely rarer than the candidate
# pool is wide: with limit 10 and CANDIDATE_MULTIPLIER 4 the vector arm asks for 40
# chunks, and 1% of 40 is well under one.
HAYSTACK_RECORDS = 1_000
NEEDLE_RECORDS = 10
SEARCH_LIMIT = 10


def _make_type(services: ServiceBundle, actor: ActorContext, key: str, prefix: str) -> None:
    services.schema.create_object_type(
        actor,
        key=key,
        name=key.title(),
        name_plural=f"{key.title()}s",
        description=f"Scale-test object type {key!r}.",
        key_prefix=prefix,
        fields=[
            {
                "key": "body",
                "name": "Body",
                "type": "long_text",
                "description": "Free text; embedded, so each record contributes a chunk.",
                "embed": True,
            }
        ],
    )


@pytest.fixture
def scaled_corpus(
    search_services: ServiceBundle, db: Database, actor: ActorContext
) -> dict[str, Any]:
    """A corpus with one type at ~1% of the whole, fully embedded.

    Not module-scoped: the ``db`` and ``search_services`` fixtures are function-scoped
    and sharing a database across these tests would let one test's writes change
    another's pool arithmetic.
    """
    _make_type(search_services, actor, "haystack", "HAY")
    _make_type(search_services, actor, "needle", "NDL")

    for i in range(HAYSTACK_RECORDS):
        search_services.records.create_record(
            actor, "haystack", {"body": f"Routine operational note number {i} about logistics."}
        )
    needle_keys = [
        search_services.records.create_record(
            actor, "needle", {"body": f"Quarterly benefit case {i} for the operations team."}
        ).key
        for i in range(NEEDLE_RECORDS)
    ]

    worker = make_worker(db, search_services, FakeClock())
    drain(worker, max_ticks=500)

    schema_repo = SqliteSchemaRepository()
    with db.read() as conn:
        haystack_id = schema_repo.get_object_type_by_key(conn, "haystack").id
        needle_id = schema_repo.get_object_type_by_key(conn, "needle").id
    return {
        "needle_keys": needle_keys,
        "needle_type_id": needle_id,
        "haystack_type_id": haystack_id,
    }


def _chunk_counts(db: Database, type_ids: dict[str, str]) -> dict[str, int]:
    with db.read() as conn:
        rows = conn.execute(
            text("SELECT object_type_id, count(*) FROM embeddings GROUP BY object_type_id")
        ).all()
    by_id = {str(row[0]): int(row[1]) for row in rows}
    return {name: by_id.get(type_id, 0) for name, type_id in type_ids.items()}


def test_the_needle_type_really_is_about_one_percent(
    scaled_corpus: dict[str, Any], db: Database
) -> None:
    """Guards the premise. If a future change made the fixture's ratio 20%, every
    assertion below would still pass and would prove nothing."""
    counts = _chunk_counts(
        db,
        {"needle": scaled_corpus["needle_type_id"], "haystack": scaled_corpus["haystack_type_id"]},
    )
    total = counts["needle"] + counts["haystack"]
    share = counts["needle"] / total
    assert 0.005 <= share <= 0.02, f"needle type is {share:.1%} of chunks, not roughly 1%"
    assert total > CANDIDATE_MULTIPLIER * SEARCH_LIMIT * 10


def test_a_scoped_search_returns_a_full_limit_of_in_scope_records(
    scaled_corpus: dict[str, Any], search_services: ServiceBundle, actor: ActorContext
) -> None:
    """The partition keys' purpose, end to end. Without them, a semantic search scoped to
    a 1% type would come back with one or two hits — or none — because the
    global KNN's 40 candidates were almost all out of scope and the post-filter
    discarded them."""
    result = search_services.search.search(
        actor,
        "quarterly benefit case for the operations team",
        object_types=["needle"],
        mode="semantic",
        limit=SEARCH_LIMIT,
    )
    keys = [hit.record_key for hit in result.results]
    assert len(keys) == SEARCH_LIMIT
    assert set(keys) <= set(scaled_corpus["needle_keys"])


def test_the_partition_constraint_does_the_work_not_the_post_filter(
    scaled_corpus: dict[str, Any],
    search_services: ServiceBundle,
    db: Database,
    fake_provider: Any,
) -> None:
    """The partition key's effect, asserted as a controlled comparison.

    The same query vector and the same ``k`` are put through the repository twice:
    once constrained to the needle partition, once across both. If the partition key
    were doing nothing, the two would return comparable numbers of in-scope chunks.
    They do not — which is the whole argument, and the reason migration 6 carries the
    partition keys.
    """
    repo = SqliteSearchRepository()
    k = CANDIDATE_MULTIPLIER * SEARCH_LIMIT
    vector = fake_provider.embed_query("quarterly benefit case for the operations team")
    needle_id = scaled_corpus["needle_type_id"]
    haystack_id = scaled_corpus["haystack_type_id"]

    with db.read() as conn:
        scoped = repo.vector_search(conn, vector, [needle_id], fake_provider.model_id, k)
        unscoped = repo.vector_search(
            conn, vector, [needle_id, haystack_id], fake_provider.model_id, k
        )

    # Constrained: every candidate the KNN produced is in scope, so the pool is full
    # and the post-filter has nothing to discard.
    assert all(hit.source.object_type_id == needle_id for hit in scoped.hits)
    assert len(scoped.hits) >= SEARCH_LIMIT

    # Unconstrained: the same k candidates are drawn from the whole corpus, and what
    # a post-filter to the needle type would have kept is a small fraction of a limit.
    in_scope_unscoped = [hit for hit in unscoped.hits if hit.source.object_type_id == needle_id]
    assert len(in_scope_unscoped) < SEARCH_LIMIT, (
        "a KNN across the whole corpus returned a full limit of in-scope chunks, so "
        "this corpus is not skewed enough to demonstrate starvation and the test "
        "proves nothing"
    )
    assert len(in_scope_unscoped) < len(scoped.hits)

    # The pre-join KNN count is what tells the service "the pool was full" from
    # "there was nothing more to find". Under the partition
    # constraint it reflects in-scope rows only.
    assert scoped.knn_rows >= SEARCH_LIMIT


def test_a_scoped_hybrid_search_is_not_starved_either(
    scaled_corpus: dict[str, Any], search_services: ServiceBundle, actor: ActorContext
) -> None:
    """Hybrid is the default mode, so the arm that starves would drag the fused
    ranking down even where the keyword arm was healthy."""
    result = search_services.search.search(
        actor,
        "quarterly benefit case",
        object_types=["needle"],
        mode="hybrid",
        limit=SEARCH_LIMIT,
    )
    assert len(result.results) == SEARCH_LIMIT
    assert {hit.record_key for hit in result.results} <= set(scaled_corpus["needle_keys"])
