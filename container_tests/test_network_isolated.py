"""The network-isolated container proof (FR-Q6, FR-P1, FR-Q7).

Deliberately outside ``pyproject.toml``'s ``testpaths`` (``["tests"]``), so
``uv run pytest -q`` never needs Docker; run this module explicitly with
``uv run pytest -q container_tests``.

Builds the image named by ``GW_IMAGE``, or ``docker build``s one, and starts it with
``docker run --network none``: the ``none`` driver leaves the container with only a
loopback interface, so no port can be published and every API call below travels
through ``docker exec <cid> python3 /tmp/gw_client.py ...`` against
``http://localhost:8000`` from inside the container's own network namespace
(``docker_support.py``). Each container bootstraps its own admin and PAT through
``docker exec ... python -m glosswork.admin`` (FR-P3's operator CLI), the same
credential-only bootstrap path a real egress-blocked deployment needs.

Two containers back the five clauses:

- ``indexing_container`` (module-scoped) backs the offline-indexing clause, the
  network positive control, and the two retrieval clauses (FR-Q6): the
  comment-only keyword search and the filtered semantic search. They deliberately
  share one container: the positive control's entire point is to prove that the
  environment the indexing and retrieval clauses ran in truly had no network, and
  proving that in a second, unrelated container would prove nothing about the first.
  The retrieval clauses cost one ``docker exec`` per REST call and no new container.
- ``fresh_container`` (function-scoped) backs the restart-resumption clause, which
  needs to ``docker kill`` and ``docker start`` a container of its own.

The golden relevance set (DD-33) deliberately does **not** run here. The image pins the
model's bytes, so all an in-container run could add is a rank flip between the
arm64 checkout and the amd64 image, which this suite does not measure.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 90
INDEX_TIMEOUT_S = 120

# The revision-qualified model id every embedding row carries (DD-32,
# services/embedding.py: ``f"{model_name}@{MODEL_REVISION}"``).
EXPECTED_MODEL_ID = "bge-small-en-v1.5@5c38ec7"

NOTE_OBJECT_TYPE: dict[str, Any] = {
    "key": "note",
    "name": "Note",
    "name_plural": "Notes",
    "description": "A test note for the search index container proof.",
    "key_prefix": "NOTE",
    "fields": [
        {
            "key": "body",
            "name": "Body",
            "type": "long_text",
            "description": "Free text body (embed defaults to true for long_text).",
        }
    ],
}

# Two records whose summaries paraphrase each other, in different regions, so a
# semantic search scoped to the type and filtered on the region has exactly one right
# answer and the filter, not the ranking, is what excludes the other (clause 2).
BRIEF_OBJECT_TYPE: dict[str, Any] = {
    "key": "brief",
    "name": "Brief",
    "name_plural": "Briefs",
    "description": "A regional brief for the filtered semantic search container proof.",
    "key_prefix": "BRIEF",
    "fields": [
        {
            "key": "summary",
            "name": "Summary",
            "type": "long_text",
            "description": "What the brief says, in prose (embed defaults to true).",
        },
        {
            "key": "region",
            "name": "Region",
            "type": "single_select",
            "description": "Which region the brief concerns.",
            "config": {
                "options": [
                    {"value": "north", "label": "North", "description": "The northern region."},
                    {"value": "south", "label": "South", "description": "The southern region."},
                ]
            },
        },
    ],
}

RESTART_OBJECT_TYPE: dict[str, Any] = {
    "key": "rnote",
    "name": "Restart Note",
    "name_plural": "Restart Notes",
    "description": "A test object type for the restart-resumption container proof.",
    "key_prefix": "RNOTE",
    "fields": [
        {
            "key": "body",
            "name": "Body",
            "type": "long_text",
            "description": "Free text body, left empty so only seeded comments are indexed.",
        }
    ],
}


@pytest.fixture(scope="module")
def indexing_container(image_tag: str) -> Iterator[tuple[str, str]]:
    cid = ds.start_container(image_tag)
    try:
        ds.wait_ready(cid, timeout=READY_TIMEOUT_S)
        token = ds.bootstrap_admin(cid)
        yield cid, token
    finally:
        ds.remove_container(cid)


@pytest.fixture()
def fresh_container(image_tag: str) -> Iterator[tuple[str, str]]:
    cid = ds.start_container(image_tag)
    try:
        ds.wait_ready(cid, timeout=READY_TIMEOUT_S)
        token = ds.bootstrap_admin(cid)
        yield cid, token
    finally:
        ds.remove_container(cid)


def _create_object_type(cid: str, token: str, spec: dict[str, Any]) -> None:
    result = ds.api_call(cid, "POST", "/api/v1/object-types", token=token, body=spec)
    assert result["status"] == 200, result


def _create_record(cid: str, token: str, object_type_key: str, values: dict[str, Any]) -> Any:
    result = ds.api_call(
        cid, "POST", f"/api/v1/object-types/{object_type_key}/records", token=token, body=values
    )
    assert result["status"] == 200, result
    return result["body"]


def _add_comment(cid: str, token: str, ref: str, body_text: str) -> Any:
    result = ds.api_call(
        cid, "POST", f"/api/v1/records/{ref}/comments", token=token, body={"body": body_text}
    )
    assert result["status"] == 200, result
    return result["body"]


@pytest.fixture(scope="module")
def indexed_note(indexing_container: tuple[str, str]) -> dict[str, Any]:
    """The indexing seed, drained: one long_text record and one comment, each one chunk.
    Shared by the indexing clause (which asserts the drain) and retrieval clause 1 (which
    searches the comment-only phrase), so neither re-seeds."""
    cid, token = indexing_container
    _create_object_type(cid, token, NOTE_OBJECT_TYPE)
    record = _create_record(
        cid, token, "note", {"body": "The quarterly onboarding checklist mentions a wombat."}
    )
    comment = _add_comment(
        cid, token, record["key"], "Follow up needed about the wombat migration timeline."
    )
    status = ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)
    return {"record": record, "comment": comment, "status": status}


def test_offline_indexing_produces_the_exact_expected_chunk_count(
    indexed_note: dict[str, Any],
) -> None:
    """FR-Q6, FR-Q7: a long_text field and a comment each embed with no network, and
    each is exactly one chunk because both texts are well under the 256-token chunk
    size (DD-33), so the expected total is arithmetic, not a guess."""
    status = indexed_note["status"]
    assert status["indexed_chunks"] == 2, status
    assert status["failed_jobs"] == []
    assert status["embedding_model"] == EXPECTED_MODEL_ID


def test_a_comment_only_phrase_is_found_with_the_comment_as_hit_source(
    indexing_container: tuple[str, str], indexed_note: dict[str, Any]
) -> None:
    """Retrieval clause 1 (FR-Q6; PRD section 9 criterion 3): inside the network-isolated
    container, a phrase that appears only in the comment body returns the record,
    and the hit source names that comment. One ``docker exec``; no new container."""
    cid, token = indexing_container
    result = ds.api_call(
        cid,
        "POST",
        "/api/v1/search",
        token=token,
        body={"query": "migration timeline", "mode": "hybrid"},
    )
    assert result["status"] == 200, result
    body = result["body"]
    assert body["mode_applied"] == "hybrid"
    assert body["index_lag"]["pending_jobs"] == 0
    keys = [hit["record_key"] for hit in body["results"]]
    assert keys[:1] == [indexed_note["record"]["key"]], body
    hit = body["results"][0]
    assert hit["hit_source"]["type"] == "comment", hit
    assert hit["hit_source"]["comment_id"] == indexed_note["comment"]["id"], hit
    assert "<em>migration</em>" in hit["snippet"] and "<em>timeline</em>" in hit["snippet"]


def test_a_filtered_semantic_search_intersects_relevance_with_the_structured_filter(
    indexing_container: tuple[str, str], indexed_note: dict[str, Any]
) -> None:
    """Retrieval clause 2 (FR-Q6, FR-Q5): two briefs paraphrase each other in different
    regions; a ``mode=semantic`` search scoped to the type with a filter on the region
    returns exactly the in-region one, so the filter and the semantic arm intersect
    rather than either standing alone. About four seconds of the run."""
    cid, token = indexing_container
    _create_object_type(cid, token, BRIEF_OBJECT_TYPE)
    north = _create_record(
        cid,
        token,
        "brief",
        {
            "summary": (
                "Depot managers want driver scorecards that ignore a new joiner's first two weeks."
            ),
            "region": "north",
        },
    )
    south = _create_record(
        cid,
        token,
        "brief",
        {
            "summary": (
                "Site leads asked for the driver scorecard to skip a recruit's first fortnight."
            ),
            "region": "south",
        },
    )
    ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)

    result = ds.api_call(
        cid,
        "POST",
        "/api/v1/search",
        token=token,
        body={
            "query": "scorecards should exclude the first weeks after someone joins",
            "mode": "semantic",
            "object_types": ["brief"],
            "filter": {"field": "region", "op": "eq", "value": "north"},
        },
    )
    assert result["status"] == 200, result
    body = result["body"]
    assert body["mode_applied"] == "semantic"
    assert [hit["record_key"] for hit in body["results"]] == [north["key"]], body
    assert south["key"] not in [hit["record_key"] for hit in body["results"]]

    unfiltered = ds.api_call(
        cid,
        "POST",
        "/api/v1/search",
        token=token,
        body={
            "query": "scorecards should exclude the first weeks",
            "mode": "semantic",
            "object_types": ["brief"],
        },
    )
    assert {hit["record_key"] for hit in unfiltered["body"]["results"]} == {
        north["key"],
        south["key"],
    }


def test_no_network_egress_from_inside_the_indexed_container(
    indexing_container: tuple[str, str],
) -> None:
    """Positive control (FR-Q6, FR-P1): from inside the *same* container the indexing
    clause just ran in, a fetch to the model's own host must raise. If this test ever
    passes without raising, the indexing clause above proves nothing about isolation,
    only that the model happens to already be on disk."""
    cid, _token = indexing_container
    result = ds.exec_in(
        cid,
        [
            "python3",
            "-c",
            "import urllib.request; urllib.request.urlopen('https://huggingface.co', timeout=5)",
        ],
        timeout=30,
    )
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    assert result.returncode != 0, (
        "urlopen('https://huggingface.co') unexpectedly succeeded inside the "
        f"network-isolated container; the offline-indexing proof above is void.\n"
        f"stdout={stdout}\nstderr={stderr}"
    )
    assert "URLError" in stderr, (
        "urlopen failed, but not with the network error the isolation should produce "
        f"(a bug in the check itself, not proof of isolation):\nstderr={stderr}"
    )


def test_restart_resumption_drains_to_the_exact_expected_total(
    fresh_container: tuple[str, str],
) -> None:
    """FR-Q7: a container killed mid-queue resumes and finishes with no re-embedding
    and nothing stranded, proven with a real ``docker kill``/``docker start`` rather
    than an in-process restart.

    ``docker kill`` fires only once the status endpoint shows the freshly seeded
    backlog sitting entirely ``pending`` (``running_jobs == 0``). That state holds for
    close to a full second after a drain: the worker's idle poll sleeps that long
    once it finds nothing to claim (``IDLE_POLL_SECONDS`` in
    ``services/embedding_worker.py``), which is the window seeding lands in. This is
    what keeps the proof fast and deterministic without touching worker internals.

    Catching a row genuinely ``running`` at kill time does not require waiting out the
    real 10-minute ``RUNNING_TIMEOUT_SECONDS``: under DD-35 a restart reclaims every
    ``running`` row whatever its age, and
    ``container_tests/test_clean_shutdown.py::test_a_kill_strands_running_rows_and_the_next_start_reclaims_them_all``
    is the arm that covers it. This test is kept as it is, because it covers what that
    one does not: a kill against a backlog that is entirely ``pending``, where nothing
    is reclaimed and resumption is purely the queue's durability.

    Every comment seeded, across every attempt, is counted in ``expected_total``, so
    the final assertion is exact regardless of which attempt actually lands the kill.
    """
    cid, token = fresh_container
    _create_object_type(cid, token, RESTART_OBJECT_TYPE)
    record = _create_record(cid, token, "rnote", {})
    ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)

    batch_size = 20
    max_attempts = 10
    expected_total = 0
    caught = False
    for _attempt in range(max_attempts):
        seeded = ds.seed_comments(cid, record["key"], token, batch_size, start=expected_total)
        expected_total += seeded["seeded"]
        status = ds.api_call(cid, "GET", "/api/v1/admin/search-index", token=token)["body"]
        if status["pending_jobs"] > 0 and status["running_jobs"] == 0:
            ds.kill_container(cid)
            caught = True
            break
        ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)
    if not caught:
        pytest.fail(
            "could not observe an all-pending, none-running backlog to kill against "
            f"after {max_attempts} attempts ({expected_total} comments seeded).\n\n"
            f"{ds.logs(cid)}"
        )

    ds.start_stopped_container(cid)
    ds.wait_ready(cid, timeout=READY_TIMEOUT_S)
    final = ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)

    assert final["indexed_chunks"] == expected_total, final
    assert final["running_jobs"] == 0, final
    assert final["pending_jobs"] == 0, final
    assert final["failed_jobs"] == [], final
