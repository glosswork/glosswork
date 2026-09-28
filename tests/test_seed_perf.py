"""The performance seed harness (`scripts/seed_perf.py`). Run at a small `--scale` so
the suite stays fast; the shape asserted here -- object type count, scaled record
totals, the skew, and the reported throughput -- is exactly what a 200,000-record run at
scale 1.0 must also satisfy.

Imported as a module, not shelled out to, so the test drives the same `run()` a real
invocation drives and can inspect the database and the report dict directly.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from glosswork.actor import bootstrap_actor
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.migrations import run_migrations
from glosswork.services import ServiceBundle, build_services
from scripts import seed_perf
from tests.conftest import make_actor

SCALE = 0.01
SEED = 20260825


def _open_services(data_dir: Path) -> tuple[Database, ServiceBundle]:
    db = Database.connect(data_dir / seed_perf.DATABASE_FILENAME)
    run_migrations(db)
    settings = Settings(data_dir=data_dir, embedding_enabled=False)
    return db, build_services(db, data_dir, settings)


def _reopen_object_type_keys(data_dir: Path) -> set[str]:
    db, services = _open_services(data_dir)
    try:
        return {ot.key for ot in services.schema.list_object_types(make_actor())}
    finally:
        db.close()


def test_object_types_table_sums_to_200_000_at_scale_1() -> None:
    assert sum(spec.records for spec in seed_perf.OBJECT_TYPES) == 200_000
    assert len(seed_perf.OBJECT_TYPES) == 15


def test_seed_creates_fifteen_object_types(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)
    keys = _reopen_object_type_keys(data_dir)
    assert keys == {spec.key for spec in seed_perf.OBJECT_TYPES}
    assert len(keys) == 15


def test_seed_writes_the_scaled_record_total(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    expected_total = sum(int(round(spec.records * SCALE)) for spec in seed_perf.OBJECT_TYPES)
    assert expected_total == 2000  # the brief's own worked example for --scale 0.01
    assert report["records_created"] == expected_total
    assert sum(report["counts_by_type"].values()) == expected_total


def test_distribution_is_skewed_not_even(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    total = report["records_created"]
    largest = max(report["counts_by_type"].values())
    assert largest / total >= 0.40


def test_one_percent_type_share(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    total = report["records_created"]
    one_percent_count = report["counts_by_type"][seed_perf.ONE_PERCENT_TYPE_KEY]
    share = one_percent_count / total
    assert 0.005 <= share <= 0.02


def test_seed_report_json_is_written_with_throughput(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    report_path = data_dir / "seed_report.json"
    assert report_path.exists()
    import json

    on_disk = json.loads(report_path.read_text())
    assert on_disk == report
    assert on_disk["records_per_second"] > 0
    assert on_disk["records_seconds"] > 0
    assert on_disk["links_created"] >= 0
    assert on_disk["comments_created"] >= 0


def test_report_carries_measurement_harness_sample_keys(tmp_path: Path) -> None:
    """`scripts/measure_perf.py` drives the unindexed-shape latency measurements off
    these keys rather than hardcoding values, so a sample that matches nothing would
    silently turn that measurement into a measurement of the empty case -- exactly
    what the skewed distribution exists to prevent. Assert the keys are present and
    that both samples actually occur in `largest_type_key`'s own records."""
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    for key in (
        "largest_type_key",
        "sample_vendor",
        "sample_notes_substring",
        "sample_notes_substring_hit_rate",
        "owner_pool_size",
    ):
        assert key in report, key

    largest_type_key = report["largest_type_key"]
    assert largest_type_key == max(
        report["counts_by_type"], key=lambda k: report["counts_by_type"][k]
    )
    assert 0.0 < report["sample_notes_substring_hit_rate"] < 1.0

    db, services = _open_services(data_dir)
    try:
        actor = bootstrap_actor("test-sample-keys")
        largest_count = report["counts_by_type"][largest_type_key]

        vendor_hits = services.records.query_records(
            actor,
            largest_type_key,
            filter={"field": "vendor", "op": "eq", "value": report["sample_vendor"]},
            limit=largest_count + 10,
        )
        assert vendor_hits.total_count >= 1

        substring_hits = services.records.query_records(
            actor,
            largest_type_key,
            filter={
                "field": "notes",
                "op": "contains",
                "value": report["sample_notes_substring"],
            },
            limit=largest_count + 10,
        )
        assert substring_hits.total_count >= 1
        # Not every record: a substring that matches everything would not exercise
        # the same selectivity a real unindexed-substring query has.
        assert substring_hits.total_count < largest_count
    finally:
        db.close()


def test_run_through_record_service_not_raw_sql(tmp_path: Path) -> None:
    """The audit trail is a service-layer side effect raw `executemany` would never
    produce (DD-4): every record write goes through `RecordService.create_record`
    and leaves an audit row attributed to the bootstrap actor. Record keys are
    allocated in order, so the first `initiative` record is deterministically
    `INIT-001`."""
    data_dir = tmp_path / "data"
    seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    db, services = _open_services(data_dir)
    try:
        history = services.records.get_record_history(make_actor(), "INIT-001")
        assert any(event.action == "create" for event in history)
    finally:
        db.close()


def test_owner_pool_is_forty_active_service_account_principals(tmp_path: Path) -> None:
    """`owner` is `user_ref`, which `RecordService` enforces must name an active
    principal (see the OWNER_POOL_SIZE comment in `scripts/seed_perf.py`): the pool
    is real service accounts, not synthetic ids, created before any object type."""
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)
    assert report["owner_pool_size"] == seed_perf.OWNER_POOL_SIZE

    db, services = _open_services(data_dir)
    try:
        all_service_accounts = services.principals.list_principals(
            "service_account", include_inactive=False
        )
        # Excludes the pre-seeded bootstrap principal (also a service account): the
        # pool is the ones this run created, named "Seed Owner NN".
        pool_accounts = [
            a for a in all_service_accounts if a.display_name.startswith("Seed Owner ")
        ]
        assert len(pool_accounts) == seed_perf.OWNER_POOL_SIZE
        assert all(a.is_active for a in pool_accounts)
        assert all(a.description and a.description.strip() for a in pool_accounts)
    finally:
        db.close()


def test_owner_selectivity_is_a_realistic_slice_not_everything(tmp_path: Path) -> None:
    """No single owner should hold most of the largest type's records: `owner` is
    auto-indexed, so `owner eq <id>` is a candidate shape for the latency measurement,
    and a pool of one (or a near-degenerate distribution) would make that filter
    measure a full scan while claiming to measure an indexed lookup."""
    data_dir = tmp_path / "data"
    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)
    largest_key = max(report["counts_by_type"], key=lambda k: report["counts_by_type"][k])
    largest_count = report["counts_by_type"][largest_key]

    db, services = _open_services(data_dir)
    try:
        actor = bootstrap_actor("test-owner-selectivity")
        result = services.records.query_records(
            actor, largest_key, fields=["owner"], limit=largest_count + 10
        )
        owners = [record["data"]["owner"] for record in result.records]
        assert len(owners) == largest_count
        tallies = Counter(owners)
        assert len(tallies) > 1
        assert max(tallies.values()) <= 0.10 * largest_count
    finally:
        db.close()


def test_rerun_without_force_refuses(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    with pytest.raises(RuntimeError, match="Refusing to seed"):
        seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)


def test_rerun_with_force_wipes_and_reseeds(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=False)

    report = seed_perf.run(data_dir, seed=SEED, scale=SCALE, force=True)
    expected_total = sum(int(round(spec.records * SCALE)) for spec in seed_perf.OBJECT_TYPES)
    assert report["records_created"] == expected_total


def test_cli_main_reports_error_on_bad_scale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = seed_perf.main(["--data-dir", str(tmp_path / "data"), "--scale", "0"])
    assert exit_code == 1
    assert "scale" in capsys.readouterr().err
