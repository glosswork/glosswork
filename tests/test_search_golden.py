"""The golden relevance run (DD-33).

``tests/golden/search_golden.json`` was authored before any retrieval code existed, so
that its floors measure the implementation rather than the other way round. This module
seeds it through the service layer as the two principals the file names, soft-deletes
what it marks deleted, drains a worker over the **real** bundled model (failing, not
skipping, without it -- DD-32), runs every case in every mode it names, and asserts at
three levels:

- **hard per-case assertions**, each its own failure;
- **the aggregate floor**: hybrid recall@5 over the 27 positive cases at least the
  gate (the measured 23/27; DD-33, with 0.90 a target rather than a gate);
- **the per-class floor**: no class with positive cases scores zero in hybrid.

The floors and the case expectations are **not editable to make a run pass** (DD-33).
The first measurement landed below the original 0.90 floor and was reported to the
maintainer with the per-case table this module prints under ``-s``. DD-33 sets the gate
to that measurement, restricts E2 to the mode its claim is about, asserts a hit source
only where the keyword arm ran, and marks D2, D3, and D5 as **strict expected
failures**, so the suite is green while those three stay visible and a future
improvement surfaces as an unexpected pass rather than silence. Only
``services/search_tuning.py`` and the chunking policy may be tuned toward the floors,
each change recorded with its golden run.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from glosswork.actor import ActorContext
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.migrations import run_migrations
from glosswork.repositories.models import CommentRow, RecordRow
from glosswork.services import ServiceBundle, build_services
from glosswork.services.search import SearchResult
from glosswork.services.search_tuning import (
    CANDIDATE_MULTIPLIER,
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    RRF_K,
    WIDEN_MULTIPLIER,
)
from tests.conftest import make_actor, make_worker
from tests.search_support import FakeClock, drain, real_provider

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "search_golden.json"
GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text())
CASES: list[dict[str, Any]] = GOLDEN["cases"]
POSITIVES = [case for case in CASES if case["expect"]["record"] is not None]
MODES = ("hybrid", "keyword", "semantic")

# The floors, as DD-33 states them. Not constants of the implementation and not in
# search_tuning.py, deliberately: a threshold sitting next to the knobs it judges is a
# threshold that gets adjusted to make a run pass. The gate is the measured 23/27 exactly
# (not a rounded number), so the number stays comparable to future runs.
#
# 0.90 is **a target, not a gate**, a decision recorded in DD-33: the reranking work that
# would reach it is not built, the fusion-lever ceiling is 24/27 = 0.889 so no constant
# can get there anyway, and keyword recall backstops semantic recall on every failing
# case. It stays here as an aspiration the report prints, not a floor anything is
# measured against.
AGGREGATE_RECALL_AT_5_FLOOR = 23 / 27
POST_MVP_AGGREGATE_TARGET = 0.90
RECALL_AT = 5

# D2, D3, and D5 stay exactly as written and keep contributing to the aggregate; their
# semantic top-3 assertions are strict expected failures. DD-33 keeps them strict
# deliberately: a future change that happens to fix one fails the suite with an
# unexpected pass and forces the closure to be reopened, rather than quietly
# satisfying a target nobody is measuring any more.
EXPECTED_SEMANTIC_MISSES = {
    "D2": "semantic rank 7 (measured 2026-08-25)",
    "D3": "semantic rank 6 (measured 2026-08-25)",
    "D5": "semantic rank 7 (measured 2026-08-25)",
}
SEMANTIC_MISS_REASON = (
    "DD-33: bge-small does not place this stem-free paraphrase in its semantic top 3 on "
    "the golden corpus (short opted-in titles act as hubs); cross-encoder reranking "
    "(DD-33) is what would change that, and it is not built. strict=True so an "
    "improvement surfaces as an unexpected pass."
)


@dataclass
class Golden:
    services: ServiceBundle
    db: Database
    actors: dict[str, ActorContext]
    records: dict[str, RecordRow]  # by ref
    comments: dict[str, CommentRow]  # by ref
    # (case id, mode) -> the run
    runs: dict[tuple[str, str], SearchResult] = field(default_factory=dict)

    def record_ref(self, record_id: str) -> str:
        return next(ref for ref, row in self.records.items() if row.id == record_id)

    def rank(self, case_id: str, mode: str, ref: str) -> int | None:
        result = self.runs[(case_id, mode)]
        target = self.records[ref].id
        for position, hit in enumerate(result.results, start=1):
            if hit.record_id == target:
                return position
        return None

    def hit(self, case_id: str, mode: str, ref: str) -> Any:
        target = self.records[ref].id
        return next(h for h in self.runs[(case_id, mode)].results if h.record_id == target)


def _second_actor(services: ServiceBundle) -> ActorContext:
    principal = services.principals.create_user(
        make_actor(),
        email="second-admin@golden.example",
        display_name="Second Golden Admin",
        role="admin",
        auth_provider="local",
        password="golden-second-password",
    )
    return ActorContext(
        principal_id=principal.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="golden-second",
        scope="admin",
    )


def seed_golden(root: Path) -> Golden:
    """Seed the corpus into a fresh database under ``root`` and run every case.

    A plain function rather than only a fixture so a tuning session can drive it
    from a script and inspect ranks and distances without pytest in the way.
    """
    db = Database.connect(root / "golden.sqlite3")
    run_migrations(db)
    services = build_services(
        db,
        root,
        Settings(data_dir=root, embedding_enabled=True),
        embedding_provider=real_provider(),  # type: ignore[arg-type]
    )
    actors = {"seed": make_actor(), "second": _second_actor(services)}
    for object_type in GOLDEN["object_types"]:
        services.schema.create_object_type(
            actors["seed"],
            key=object_type["key"],
            name=object_type["name"],
            name_plural=object_type["name_plural"],
            description=object_type["description"],
            key_prefix=object_type["key_prefix"],
            fields=object_type["fields"],
        )
    records: dict[str, RecordRow] = {}
    for spec in GOLDEN["records"]:
        records[spec["ref"]] = services.records.create_record(
            actors[spec["created_by"]], spec["type"], spec["values"]
        )
    comments: dict[str, CommentRow] = {}
    for spec in GOLDEN["comments"]:
        comments[spec["ref"]] = services.comments.add_comment(
            actors["seed"], records[spec["record"]].key, spec["body"]
        )
    # Index first, delete after: a soft delete touches neither index, so the
    # class (I) exclusion is proven against rows that genuinely exist.
    drain(make_worker(db, services, FakeClock()), max_ticks=400)
    for spec in GOLDEN["comments"]:
        if spec["deleted"]:
            services.comments.delete_comment(actors["seed"], comments[spec["ref"]].id)
    for spec in GOLDEN["records"]:
        if spec["deleted"]:
            services.records.delete_record(actors["seed"], records[spec["ref"]].key)

    state = Golden(services=services, db=db, actors=actors, records=records, comments=comments)
    for case in CASES:
        for mode in case["mode_runs"]:
            state.runs[(case["id"], mode)] = services.search.search(
                actors[case["as_principal"]],
                case["query"],
                object_types=case["object_types"],
                filter=case["filter"],
                mode=mode,
                limit=case["limit"],
            )
    return state


@pytest.fixture(scope="module")
def golden(tmp_path_factory: pytest.TempPathFactory) -> Golden:
    state = seed_golden(tmp_path_factory.mktemp("golden"))
    yield state
    state.db.close()


def _cases(cls: str) -> list[dict[str, Any]]:
    return [case for case in CASES if case["class"] == cls]


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [case["id"] for case in cases]


# ------------------------------------------------------------ hard assertions


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["expect"]["absent_records"]], ids=lambda c: c["id"]
)
def test_absent_records_are_absent_in_every_mode(golden: Golden, case: dict[str, Any]) -> None:
    """Every negative case (E2, I1, I2) and every scope or filter exclusion (F, G)."""
    for mode in case["mode_runs"]:
        for ref in case["expect"]["absent_records"]:
            assert golden.rank(case["id"], mode, ref) is None, (case["id"], mode, ref)


@pytest.mark.parametrize("case", _cases("C"), ids=lambda c: c["id"])
def test_class_c_identifier_ranks_first_in_keyword_and_hybrid(
    golden: Golden, case: dict[str, Any]
) -> None:
    for mode, expected in case["expect"]["hard_rank"].items():
        assert golden.rank(case["id"], mode, case["expect"]["record"]) == expected, (
            case["id"],
            mode,
        )


def _class_d_params() -> list[Any]:
    params = []
    for case in _cases("D"):
        if case["id"] in EXPECTED_SEMANTIC_MISSES:
            reason = f"{EXPECTED_SEMANTIC_MISSES[case['id']]}; {SEMANTIC_MISS_REASON}"
            params.append(
                pytest.param(
                    case, id=case["id"], marks=pytest.mark.xfail(strict=True, reason=reason)
                )
            )
        else:
            params.append(pytest.param(case, id=case["id"]))
    return params


@pytest.mark.parametrize("case", _class_d_params())
def test_class_d_paraphrase_is_in_semantic_top_three_and_absent_from_keyword(
    golden: Golden, case: dict[str, Any]
) -> None:
    """The keyword-absence half is asserted for every D case before the rank half, so an
    expected semantic miss on D2, D3, or D5 can never hide a lexical match in disguise."""
    ref = case["expect"]["record"]
    for mode in case["expect"]["absent_in"]:
        assert golden.rank(case["id"], mode, ref) is None, (case["id"], mode)
    rank = golden.rank(case["id"], "semantic", ref)
    assert rank is not None and rank <= case["expect"]["hard_rank"]["semantic"], (case["id"], rank)


@pytest.mark.parametrize(
    "case",
    [c for c in _cases("D") if c["id"] in EXPECTED_SEMANTIC_MISSES],
    ids=lambda c: c["id"],
)
def test_class_d_expected_misses_are_still_absent_from_keyword(
    golden: Golden, case: dict[str, Any]
) -> None:
    """Split out so the lexical claim of the three expected semantic misses is a plain
    passing test, not something folded into an xfail."""
    ref = case["expect"]["record"]
    for mode in case["expect"]["absent_in"]:
        assert golden.rank(case["id"], mode, ref) is None, (case["id"], mode)


@pytest.mark.parametrize("case", _cases("F") + _cases("G"), ids=lambda c: c["id"])
def test_classes_f_and_g_return_nothing_that_violates_the_filter_or_the_scope(
    golden: Golden, case: dict[str, Any]
) -> None:
    for mode in case["mode_runs"]:
        result = golden.runs[(case["id"], mode)]
        for hit in result.results:
            row = golden.records[golden.record_ref(hit.record_id)]
            if case["object_types"]:
                assert hit.object_type in case["object_types"], (case["id"], mode, hit)
            if case["filter"] is not None:
                _assert_satisfies(golden, case, row)
        assert len(result.results) <= case["limit"]


def _assert_satisfies(golden: Golden, case: dict[str, Any], row: RecordRow) -> None:
    condition = case["filter"]
    field_key, value = condition["field"], condition["value"]
    if field_key == "created_by":
        assert row.created_by == golden.actors[case["as_principal"]].principal_id
    elif field_key == "comment_count":
        assert row.comment_count == value
    else:
        assert row.data.get(field_key) == value


@pytest.mark.parametrize("case", _cases("H"), ids=lambda c: c["id"])
def test_class_h_collapses_to_one_result_with_the_expected_other_matches(
    golden: Golden, case: dict[str, Any]
) -> None:
    """``other_matches`` counts additional keyword-arm sources (decision 7), so it is
    asserted in the modes where the keyword arm ran; in ``semantic`` mode there is no
    keyword arm and every hit reports 0 by definition."""
    ref = case["expect"]["record"]
    target = golden.records[ref].id
    for mode in case["mode_runs"]:
        hits = [h for h in golden.runs[(case["id"], mode)].results if h.record_id == target]
        assert len(hits) == 1, (case["id"], mode, "must return once")
        expected = case["expect"]["other_matches"] if mode != "semantic" else 0
        assert hits[0].other_matches == expected, (case["id"], mode, hits[0])


@pytest.mark.parametrize("case", _cases("J"), ids=lambda c: c["id"])
def test_class_j_morphology_ranks_in_keyword_top_five(golden: Golden, case: dict[str, Any]) -> None:
    rank = golden.rank(case["id"], "keyword", case["expect"]["record"])
    assert rank is not None and rank <= case["expect"]["hard_rank"]["keyword"], (case["id"], rank)


def test_f3_starves_the_first_pool_and_widens(golden: Golden) -> None:
    case = next(c for c in CASES if c["id"] == "F3")
    for mode in case["mode_runs"]:
        assert golden.runs[(case["id"], mode)].widened is case["expect"]["widened"], mode


@pytest.mark.parametrize(
    "case", [c for c in POSITIVES if c["expect"]["hit_source"] is not None], ids=lambda c: c["id"]
)
def test_the_hit_source_is_the_expected_one_wherever_it_is_named(
    golden: Golden, case: dict[str, Any]
) -> None:
    """Asserted in the modes where the keyword arm ran (DD-33): in
    ``semantic`` mode the hit source is whichever chunk was nearest, a property of the
    model rather than of anything built here, and asserting it would make the suite a
    model test (on the baseline A4 and J1 surfaced their title chunks there)."""
    ref = case["expect"]["record"]
    expected = case["expect"]["hit_source"]
    for mode in case["mode_runs"]:
        if mode == "semantic":
            continue
        if golden.rank(case["id"], mode, ref) is None:
            continue  # absence is judged by the rank assertions and the floors
        source = golden.hit(case["id"], mode, ref).hit_source
        assert source["type"] == expected["type"], (case["id"], mode, source)
        if expected["type"] == "field":
            assert source["field_key"] == expected["field_key"], (case["id"], mode, source)
        else:
            assert source["comment_id"] == golden.comments[expected["comment"]].id, (
                case["id"],
                mode,
                source,
            )
            assert source["author"] and source["created_at"]


# ------------------------------------------------------------------- floors


def _hybrid_ranks(golden: Golden) -> dict[str, int | None]:
    return {
        case["id"]: golden.rank(case["id"], "hybrid", case["expect"]["record"])
        for case in POSITIVES
    }


def test_aggregate_floor_hybrid_recall_at_5_over_the_27_positives(golden: Golden) -> None:
    ranks = _hybrid_ranks(golden)
    found = sum(1 for rank in ranks.values() if rank is not None and rank <= RECALL_AT)
    recall = found / len(POSITIVES)
    print(
        f"\nGOLDEN AGGREGATE: hybrid recall@{RECALL_AT} = {found}/{len(POSITIVES)} = {recall:.3f} "
        f"(gate 23/27 = {AGGREGATE_RECALL_AT_5_FLOOR:.3f}; "
        f"{POST_MVP_AGGREGATE_TARGET:.2f} is a target, not a gate, DD-33)"
    )
    assert len(POSITIVES) == 27
    assert recall >= AGGREGATE_RECALL_AT_5_FLOOR, (
        f"hybrid recall@{RECALL_AT} {recall:.3f} is below the DD-33 gate "
        f"{AGGREGATE_RECALL_AT_5_FLOOR:.3f}; misses: "
        f"{sorted(k for k, r in ranks.items() if r is None or r > RECALL_AT)}. "
        "This is a finding for the product owner, not a constant to adjust (DD-33)."
    )


def test_per_class_floor_no_class_with_positives_scores_zero_in_hybrid(golden: Golden) -> None:
    ranks = _hybrid_ranks(golden)
    lines = []
    zero: list[str] = []
    for cls in sorted({case["class"] for case in POSITIVES}):
        members = [case["id"] for case in POSITIVES if case["class"] == cls]
        found = sum(1 for cid in members if ranks[cid] is not None and ranks[cid] <= RECALL_AT)
        lines.append(f"  class {cls}: {found}/{len(members)}")
        if found == 0:
            zero.append(cls)
    print("\nGOLDEN PER-CLASS (hybrid recall@5):\n" + "\n".join(lines))
    assert zero == [], f"classes scoring zero in hybrid: {zero} (DD-33 per-class floor)"


# ------------------------------------------------------------------- report


def test_print_the_per_case_rank_table_and_mrr(golden: Golden) -> None:
    """Printed, never asserted (DD-33), so a tuning run is readable and the verify
    agent can quote it. MRR is over the 27 positives in hybrid."""
    header = (
        f"{'case':5} {'cls':3} {'hybrid':>7} {'keyword':>8} {'semantic':>9}  hit_source (hybrid)"
    )
    rows = [header, "-" * len(header)]
    reciprocal = 0.0
    for case in CASES:
        ref = case["expect"]["record"]
        cells = []
        for mode in MODES:
            if mode not in case["mode_runs"]:
                cells.append("-")
            elif ref is None:
                absent = all(
                    golden.rank(case["id"], mode, r) is None
                    for r in case["expect"]["absent_records"]
                )
                cells.append("absent" if absent else "PRESENT")
            else:
                rank = golden.rank(case["id"], mode, ref)
                cells.append(str(rank) if rank is not None else "miss")
        source = ""
        if (
            ref is not None
            and "hybrid" in case["mode_runs"]
            and golden.rank(case["id"], "hybrid", ref)
        ):
            hs = golden.hit(case["id"], "hybrid", ref).hit_source
            source = hs.get("field_key") or "comment"
        rows.append(
            f"{case['id']:5} {case['class']:3} {cells[0]:>7} {cells[1]:>8} {cells[2]:>9}  {source}"
        )
        if ref is not None:
            rank = golden.rank(case["id"], "hybrid", ref)
            reciprocal += 1 / rank if rank else 0.0
    mrr = reciprocal / len(POSITIVES)
    print("\nGOLDEN PER-CASE RANKS:\n" + "\n".join(rows))
    print(f"GOLDEN MRR (hybrid, 27 positives) = {mrr:.3f}")
    print(
        "GOLDEN CONSTANTS: "
        f"RRF_K={RRF_K} CANDIDATE_MULTIPLIER={CANDIDATE_MULTIPLIER} "
        f"WIDEN_MULTIPLIER={WIDEN_MULTIPLIER} CHUNK_MAX_TOKENS={CHUNK_MAX_TOKENS} "
        f"CHUNK_OVERLAP_TOKENS={CHUNK_OVERLAP_TOKENS}"
    )
    print(
        f"GOLDEN PLATFORM: {platform.machine()} {platform.system()} {platform.release()} "
        f"python {platform.python_version()} model {golden.services.embedding_provider.model_id}"  # type: ignore[union-attr]
    )
