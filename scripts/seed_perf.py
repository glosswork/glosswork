#!/usr/bin/env python3
"""Seed a large, skewed corpus through the service layer for performance work.

This is the seed harness the performance measurement uses:
fifteen object types, a **skewed** distribution (one type holds close to half the
corpus), realistic field values from a seeded RNG, and a throughput number for
records created **through ``RecordService``**, not through raw ``executemany`` --
that number is the CSV import throughput an office migrating its data experiences.

The skew is deliberate, not an oversight: an unindexed filter is a full scan of the
*type's* partition, so an even fifteen-way split would understate that path by
roughly 7x (measured) and let a real regression through.

Usage::

    uv run python scripts/seed_perf.py --data-dir /path/to/data
    uv run python scripts/seed_perf.py --data-dir /tmp/smoke --scale 0.01 --force

``--scale`` multiplies every per-type record count; the default (1.0) writes exactly
200,000 records. The run is deterministic for a given ``--seed``/``--scale`` pair:
same inputs, same values written, byte for byte. Embedding is off throughout
(``Settings(embedding_enabled=False)``, never ``GW_EMBEDDING_ENABLED``): FTS rows are
still maintained on every write, but nothing is enqueued and no model is required.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import shutil
import sys
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from glosswork.actor import bootstrap_actor
from glosswork.config import Settings
from glosswork.db import Database, check_search_extensions
from glosswork.migrations import run_migrations
from glosswork.services import ServiceBundle, build_services

# Deliberately not imported from glosswork.app (see admin.py's own copy of this
# constant for why): that module builds a module-level FastAPI app from live process
# environment variables as a side effect of import. The filename is duplicated here
# rather than pulled in through an import with that side effect.
DATABASE_FILENAME = "glosswork.sqlite3"
ATTACHMENTS_DIRNAME = "attachments"

# Report progress to stderr this often so a 200k run is observable, not silent.
PROGRESS_INTERVAL = 5000


@dataclass(frozen=True, slots=True)
class ObjectTypeSpec:
    key: str
    key_prefix: str
    name: str
    name_plural: str
    description: str
    records: int


# Fifteen object types, in the exact order and at the exact scale-1.0 record counts
# the performance measurement uses. The distribution is skewed, not even: `initiative` alone
# holds 45% of the corpus.
OBJECT_TYPES: tuple[ObjectTypeSpec, ...] = (
    ObjectTypeSpec(
        "initiative",
        "INIT",
        "Initiative",
        "Initiatives",
        "A funded program of work pursuing a specific business outcome for the operations team.",
        90_000,
    ),
    ObjectTypeSpec(
        "task",
        "TASK",
        "Task",
        "Tasks",
        "A discrete unit of work assigned to an owner and tracked to completion.",
        24_000,
    ),
    ObjectTypeSpec(
        "risk",
        "RISK",
        "Risk",
        "Risks",
        "A potential event that could negatively affect an initiative's cost, "
        "schedule, or outcome.",
        18_000,
    ),
    ObjectTypeSpec(
        "decision",
        "DEC",
        "Decision",
        "Decisions",
        "A recorded choice made by the operations team, including its "
        "rationale and the alternatives considered.",
        14_000,
    ),
    ObjectTypeSpec(
        "process",
        "PROC",
        "Process",
        "Processes",
        "A documented business process being analyzed, redesigned, or retired.",
        11_000,
    ),
    ObjectTypeSpec(
        "capability",
        "CAP",
        "Capability",
        "Capabilities",
        "An organizational capability the operations team is building, maturing, or consolidating.",
        9_000,
    ),
    ObjectTypeSpec(
        "system",
        "SYS",
        "System",
        "Systems",
        "A software system or platform in the enterprise's application landscape.",
        7_000,
    ),
    ObjectTypeSpec(
        "vendor_contract",
        "VC",
        "Vendor Contract",
        "Vendor Contracts",
        "An agreement with an external vendor supporting a system, process, or initiative.",
        6_000,
    ),
    ObjectTypeSpec(
        "milestone",
        "MS",
        "Milestone",
        "Milestones",
        "A significant checkpoint an initiative or workstream is tracked against.",
        5_000,
    ),
    ObjectTypeSpec(
        "dependency",
        "DEP",
        "Dependency",
        "Dependencies",
        "A cross-team or cross-system dependency that could block delivery if left unresolved.",
        4_000,
    ),
    ObjectTypeSpec(
        "issue",
        "ISS",
        "Issue",
        "Issues",
        "An active problem blocking progress that needs resolution, distinct from a "
        "risk that has not yet materialized.",
        3_500,
    ),
    ObjectTypeSpec(
        "stakeholder",
        "STK",
        "Stakeholder",
        "Stakeholders",
        "A person or group with an interest in, or influence over, the operations team's work.",
        3_000,
    ),
    ObjectTypeSpec(
        "workstream",
        "WS",
        "Workstream",
        "Workstreams",
        "A grouping of related tasks and milestones organized under an initiative.",
        2_500,
    ),
    ObjectTypeSpec(
        "benefit_case",
        "BEN",
        "Benefit Case",
        "Benefit Cases",
        "A quantified business case describing the expected value an initiative will deliver.",
        2_000,
    ),
    ObjectTypeSpec(
        "lesson",
        "LES",
        "Lesson",
        "Lessons",
        "A lesson learned captured from a completed initiative, kept for reuse by future work.",
        1_000,
    ),
)

# benefit_case sits at exactly 1% of the 200,000-record corpus (2,000 records). The
# scoped-search assertion uses it to prove the vec0 partition constraint (object_type_id,
# model_id) does real work rather than starving on a global KNN scan.
ONE_PERCENT_TYPE_KEY = "benefit_case"

_SCALE_1_TOTAL = 200_000
if sum(spec.records for spec in OBJECT_TYPES) != _SCALE_1_TOTAL:  # pragma: no cover
    raise AssertionError("OBJECT_TYPES record counts no longer sum to 200,000; fix the table.")


# ---------------------------------------------------------------------------
# The seven shared fields (FR-S4 field types: short_text, long_text, single_select,
# date, user_ref, relation, plus comments). Every object type gets the identical
# spec, so the measurement harness needs no per-type knowledge of the schema.
# ---------------------------------------------------------------------------

STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": "not_started", "label": "Not Started", "description": "Work has not begun."},
    {
        "value": "in_progress",
        "label": "In Progress",
        "description": "Work is actively underway.",
    },
    {
        "value": "blocked",
        "label": "Blocked",
        "description": "Work has stalled on an external dependency or decision.",
    },
    {"value": "done", "label": "Done", "description": "Work is complete."},
    {
        "value": "cancelled",
        "label": "Cancelled",
        "description": "Work was abandoned before completion.",
    },
]
STATUS_VALUES: tuple[str, ...] = tuple(o["value"] for o in STATUS_OPTIONS)
# Roughly 15/40/10/30/5, so a `status eq 'in_progress'` filter selects a realistic
# fraction of the corpus rather than an even (and unrealistic) one-in-five.
STATUS_WEIGHTS: tuple[int, ...] = (15, 40, 10, 30, 5)

FIELD_SPECS: list[dict[str, Any]] = [
    {
        "key": "name",
        "name": "Name",
        "type": "short_text",
        "description": "Short human-readable title identifying this record at a glance.",
        "required": True,
        "indexed": True,
        "embed": True,
    },
    {
        "key": "notes",
        "name": "Notes",
        "type": "long_text",
        "description": (
            "Free-form narrative detail about this record, read by keyword and "
            "semantic search alike."
        ),
        "embed": True,
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Current delivery state of this record.",
        "config": {"options": STATUS_OPTIONS},
    },
    {
        "key": "due_date",
        "name": "Due Date",
        "type": "date",
        "description": "Date by which this record is expected to be resolved or delivered.",
    },
    {
        "key": "owner",
        "name": "Owner",
        "type": "user_ref",
        "description": "Principal accountable for this record's progress.",
    },
    {
        "key": "vendor",
        "name": "Vendor",
        "type": "short_text",
        "description": (
            "Name of the external vendor associated with this record, if any. "
            "Deliberately left unindexed: the latency harness filters on this "
            "field to measure the unindexed-field query path."
        ),
        "indexed": False,
        "embed": False,
    },
]

RELATED_FIELD_DESCRIPTION = (
    "Records of the next object type in the seed's rotation that this record relates to."
)


def _related_field_spec(target_type_key: str) -> dict[str, Any]:
    return {
        "key": "related",
        "name": "Related",
        "type": "relation",
        "description": RELATED_FIELD_DESCRIPTION,
        "config": {"target_type_key": target_type_key, "cardinality": "many"},
    }


# ---------------------------------------------------------------------------
# Realistic-shaped value generation, all driven off one seeded random.Random so a
# run is byte-for-byte reproducible for a given --seed/--scale pair.
# ---------------------------------------------------------------------------

NAME_VERBS: tuple[str, ...] = (
    "Consolidate",
    "Modernize",
    "Streamline",
    "Automate",
    "Rationalize",
    "Migrate",
    "Standardize",
    "Simplify",
    "Accelerate",
    "Redesign",
    "Retire",
    "Integrate",
    "Optimize",
    "Scale",
    "Harden",
)
NAME_TOPICS: tuple[str, ...] = (
    "vendor onboarding",
    "invoice processing",
    "customer intake",
    "the data pipeline",
    "the reporting stack",
    "the approval workflow",
    "access provisioning",
    "incident response",
    "change management",
    "budget forecasting",
    "contract renewal",
    "employee onboarding",
    "the release process",
    "the backup strategy",
    "the monitoring stack",
)
NAME_QUALIFIERS: tuple[str, ...] = (
    "in finance",
    "for the payroll run",
    "across regions",
    "for procurement",
    "in customer support",
    "for the platform team",
    "ahead of Q3",
    "for the EMEA rollout",
    "in legal",
    "for compliance",
    "for the data team",
    "in HR",
    "for the mobile team",
    "for security",
    "for the migration",
)

NOTES_FRAGMENTS: tuple[str, ...] = (
    "The team flagged this after a routine review surfaced inconsistent ownership.",
    "Stakeholders from finance and operations both requested visibility into progress.",
    "A prior attempt at this stalled when a vendor contract renewal slipped by a quarter.",
    "Current tooling does not support the volume this workstream is expected to generate.",
    "Leadership asked for a status update ahead of the next steering committee meeting.",
    "The dependency on the platform team's migration timeline remains the largest open risk.",
    "Early estimates suggest the effort will span at least two fiscal quarters.",
    "A pilot in one region validated the approach before it was rolled out more broadly.",
    "Several downstream systems consume this data and will need coordinated testing.",
    "The original owner moved teams, and accountability has since been reassigned.",
    "Audit findings from last year's review are still being tracked against this item.",
    "A related incident earlier this quarter raised the priority of this work.",
    "The business case assumes savings will materialize within twelve months of go-live.",
    "Legal has not yet signed off on the revised terms, which is blocking closure.",
    "This was originally scoped more narrowly before the steering committee expanded it.",
    "Feedback from the last retrospective suggested tightening the intake process.",
    "A change freeze during the holiday period pushed the original timeline back.",
    "The vendor's support contract expires before this work is expected to complete.",
    "Cross-functional alignment took longer than expected given competing priorities.",
    "A follow-up session is scheduled to walk through the remaining open questions.",
    "Data quality issues in the source system were discovered partway through analysis.",
    "The rollout plan now includes a staged cutover to reduce operational risk.",
    "Budget approval came through later than planned, compressing the delivery window.",
    "A dependency on an external API's rate limits surfaced during load testing.",
    "The working group agreed to revisit scope once the pilot results are in.",
)

# Unique to the "Audit findings ..." fragment above among all 25 NOTES_FRAGMENTS.
# Measured against a full-scale draw: ~13.8% of a type's records contain it (rng.sample
# draws 2-5 of 25 fragments per record, occasionally padded toward the 150-character
# floor), comfortably inside the 5-30% window the latency harness wants for the
# `notes contains <value>` unindexed-substring shape. Matched case-insensitively, both
# here and by the `contains` operator's SQL LIKE.
SAMPLE_NOTES_SUBSTRING = "audit findings"

COMMENT_FRAGMENTS: tuple[str, ...] = (
    "Can we get an update on this by end of week?",
    "Marking this as blocked until the vendor responds.",
    "Confirmed with the owner; moving forward as planned.",
    "This looks resolved from where I'm sitting, closing it out.",
    "Following up after the steering committee meeting.",
    "Flagging for visibility ahead of the quarterly review.",
    "Reassigned to the platform team per this morning's sync.",
    "Linked the related risk so we don't lose track of it.",
    "Still waiting on legal sign-off before we can proceed.",
    "Nice work getting the pilot region live ahead of schedule.",
    "Reopening; the fix didn't hold in production.",
    "Adding context from the incident review for future reference.",
)

VENDOR_ADJECTIVES: tuple[str, ...] = (
    "Northpoint",
    "Silverline",
    "Bluecrest",
    "Ashgrove",
    "Ironwood",
    "Meridian",
    "Solstice",
    "Harborview",
    "Crestline",
    "Foundry",
    "Lighthouse",
    "Anchor",
    "Cobalt",
    "Granite",
    "Vantage",
    "Pinegate",
    "Fernwood",
    "Redshift",
    "Brightline",
    "Cascade",
)
VENDOR_SUFFIXES: tuple[str, ...] = (
    "Systems",
    "Solutions",
    "Partners",
    "Consulting",
    "Technologies",
    "Group",
    "Labs",
    "Analytics",
    "Networks",
    "Dynamics",
)
# 20 x 10 = 200 synthetic vendor names, fixed (no RNG needed to build the pool).
VENDOR_POOL: tuple[str, ...] = tuple(
    f"{adjective} {suffix}" for adjective in VENDOR_ADJECTIVES for suffix in VENDOR_SUFFIXES
)

# Roughly a two-year window centered on the date below.
DUE_DATE_CENTER = date(2026, 1, 1)
DUE_DATE_SPREAD_DAYS = 365

OWNER_POOL_SIZE = 40
LINK_FRACTION = 0.2
LINK_MIN_TARGETS = 1
LINK_MAX_TARGETS = 3
COMMENT_FRACTION = 0.05
COMMENT_MIN_COUNT = 1
COMMENT_MAX_COUNT = 3

# `owner` is `user_ref`, and `RecordService._validate_values` enforces -- correctly,
# by design -- that a `user_ref` value names an active row in `principals`
# (`repositories/sqlite.py`'s `principal_exists`). A synthetic, non-existent uuid4
# string fails that check on the very first write. Three
# decisions follow, recorded here so the next reader does not re-derive them:
#
# 1. The FK-style enforcement is not weakened for a seed script's convenience. A
#    pool of real principals is created instead (`_make_owner_pool`, below).
# 2. The pool is ~40 accounts, not one shared owner. `owner` is auto-indexed, so
#    `owner eq <id>` is a candidate shape for the pass-1 latency work; a pool of one
#    would make that filter match the whole corpus, measuring a full scan while
#    claiming to measure an indexed lookup. Selectivity is the point.
# 3. "Reproducible given the same seed" governs the *distribution and assignment* --
#    which pool slot a record draws, which status, which vendor, which date -- not
#    the principal ids themselves. `create_service_account` mints its own uuid4 id
#    with no injection point, and does not need one: every run seeds a fresh
#    database, so there is nothing for a stable id to be compared against across
#    runs. The seeded RNG picks the pool *index*; the id at that index is whatever
#    was just created.


def _make_owner_pool(
    services: ServiceBundle, request_ids: Iterator[int], size: int = OWNER_POOL_SIZE
) -> list[str]:
    """Create ``size`` real service-account principals, before any object type
    exists, and return their ids in creation order. ``_random_record_values`` then
    draws a pool *index* from the seeded RNG for each record's ``owner``."""
    pool: list[str] = []
    for i in range(size):
        actor = bootstrap_actor(f"seed-owner-{next(request_ids):08d}")
        principal = services.principals.create_service_account(
            actor,
            display_name=f"Seed Owner {i:02d}",
            description=(
                f"Synthetic owner (pool slot {i:02d} of {size}) used by the "
                "performance seed corpus; assigned to records only, never used to "
                "log in or hold a token."
            ),
        )
        pool.append(principal.id)
    return pool


def _random_name(rng: random.Random) -> str:
    return f"{rng.choice(NAME_VERBS)} {rng.choice(NAME_TOPICS)} {rng.choice(NAME_QUALIFIERS)}"


def _random_notes(rng: random.Random) -> str:
    """2-5 sentences, nudged toward 150-600 characters. This is seed data for
    realistic query shapes, not a value any test asserts an exact length on."""
    sentences = list(rng.sample(NOTES_FRAGMENTS, k=rng.randint(2, 5)))
    text = " ".join(sentences)
    while len(text) < 150 and len(sentences) < len(NOTES_FRAGMENTS):
        candidate = rng.choice(NOTES_FRAGMENTS)
        if candidate not in sentences:
            sentences.append(candidate)
            text = " ".join(sentences)
    while len(text) > 600 and len(sentences) > 2:
        sentences.pop()
        text = " ".join(sentences)
    return text


def _random_comment(rng: random.Random) -> str:
    text = rng.choice(COMMENT_FRAGMENTS)
    while len(text) < 40:
        text = f"{text} {rng.choice(COMMENT_FRAGMENTS)}"
    return text[:200]


def _random_due_date(rng: random.Random) -> str:
    offset = rng.randint(-DUE_DATE_SPREAD_DAYS, DUE_DATE_SPREAD_DAYS)
    return (DUE_DATE_CENTER + timedelta(days=offset)).isoformat()


def _random_record_values(rng: random.Random, owner_pool: list[str]) -> dict[str, Any]:
    return {
        "name": _random_name(rng),
        "notes": _random_notes(rng),
        "status": rng.choices(STATUS_VALUES, weights=STATUS_WEIGHTS, k=1)[0],
        "due_date": _random_due_date(rng),
        "owner": rng.choice(owner_pool),
        "vendor": rng.choice(VENDOR_POOL),
    }


def _scaled_count(spec: ObjectTypeSpec, scale: float) -> int:
    return max(0, int(round(spec.records * scale)))


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Progress:
    label: str
    start: float
    count: int = 0

    def tick(self) -> None:
        self.count += 1
        if self.count % PROGRESS_INTERVAL == 0:
            elapsed = time.perf_counter() - self.start
            rate = self.count / elapsed if elapsed > 0 else 0.0
            print(
                f"[seed_perf] {self.label}: {self.count} done, {elapsed:.1f}s elapsed, "
                f"{rate:.0f}/s",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Seeding steps, each through the service layer (DD-3, DD-4): every write takes a
# fresh ActorContext.
# ---------------------------------------------------------------------------


def _create_object_types(services: ServiceBundle, request_ids: Iterator[int]) -> None:
    for spec in OBJECT_TYPES:
        actor = bootstrap_actor(f"seed-schema-{next(request_ids):08d}")
        services.schema.create_object_type(
            actor,
            key=spec.key,
            name=spec.name,
            name_plural=spec.name_plural,
            description=spec.description,
            key_prefix=spec.key_prefix,
            fields=list(FIELD_SPECS),
        )


def _add_relation_fields(services: ServiceBundle, request_ids: Iterator[int]) -> None:
    """Second pass: a relation's target_type_key must already exist (FR-L1)."""
    for i, spec in enumerate(OBJECT_TYPES):
        target = OBJECT_TYPES[(i + 1) % len(OBJECT_TYPES)]
        actor = bootstrap_actor(f"seed-schema-{next(request_ids):08d}")
        services.schema.add_field(actor, spec.key, _related_field_spec(target.key))


@dataclass(slots=True)
class _SampleStats:
    """Value-level stats for one object type (the largest, by construction), so
    `run()` can report a `sample_vendor` and `sample_notes_substring` that are
    guaranteed to actually occur, rather than a value picked blind that might match
    nothing (see the `run()` docstring on why that matters to the latency harness)."""

    vendor_counts: Counter[str]
    notes_hits: int = 0
    notes_total: int = 0


def _seed_records(
    services: ServiceBundle,
    rng: random.Random,
    owner_pool: list[str],
    counts: dict[str, int],
    request_ids: Iterator[int],
    progress: _Progress,
    sample_type_key: str,
) -> tuple[dict[str, list[str]], _SampleStats]:
    keys_by_type: dict[str, list[str]] = {}
    stats = _SampleStats(vendor_counts=Counter())
    for spec in OBJECT_TYPES:
        track = spec.key == sample_type_key
        type_keys: list[str] = []
        for _ in range(counts[spec.key]):
            values = _random_record_values(rng, owner_pool)
            actor = bootstrap_actor(f"seed-record-{next(request_ids):08d}")
            record = services.records.create_record(actor, spec.key, values)
            type_keys.append(record.key)
            if track:
                stats.vendor_counts[values["vendor"]] += 1
                stats.notes_total += 1
                if SAMPLE_NOTES_SUBSTRING in values["notes"].lower():
                    stats.notes_hits += 1
            progress.tick()
        keys_by_type[spec.key] = type_keys
    return keys_by_type, stats


def _seed_links(
    services: ServiceBundle,
    rng: random.Random,
    keys_by_type: dict[str, list[str]],
    request_ids: Iterator[int],
) -> int:
    """Link roughly 20% of records to 1-3 records of the next type in the rotation.
    Bounded on purpose (FR-L1): this is not an every-record fan-out."""
    created = 0
    for i, spec in enumerate(OBJECT_TYPES):
        target_spec = OBJECT_TYPES[(i + 1) % len(OBJECT_TYPES)]
        target_keys = keys_by_type[target_spec.key]
        if not target_keys:
            continue
        for record_key in keys_by_type[spec.key]:
            if rng.random() >= LINK_FRACTION:
                continue
            k = min(rng.randint(LINK_MIN_TARGETS, LINK_MAX_TARGETS), len(target_keys))
            to_refs = rng.sample(target_keys, k=k)
            actor = bootstrap_actor(f"seed-link-{next(request_ids):08d}")
            services.records.link_records(actor, record_key, "related", to_refs)
            created += 1
    return created


def _seed_comments(
    services: ServiceBundle,
    rng: random.Random,
    keys_by_type: dict[str, list[str]],
    request_ids: Iterator[int],
) -> int:
    """1-3 comments on roughly 5% of records."""
    created = 0
    for spec in OBJECT_TYPES:
        for record_key in keys_by_type[spec.key]:
            if rng.random() >= COMMENT_FRACTION:
                continue
            for _ in range(rng.randint(COMMENT_MIN_COUNT, COMMENT_MAX_COUNT)):
                actor = bootstrap_actor(f"seed-comment-{next(request_ids):08d}")
                services.comments.add_comment(actor, record_key, _random_comment(rng))
                created += 1
    return created


def _reset_data_dir(data_dir: Path, db_path: Path) -> None:
    """``--force``: delete the database file (plus its WAL/SHM siblings) and the
    attachments/ tree before anything reconnects, so the run starts clean."""
    for suffix in ("", "-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    attachments_dir = data_dir / ATTACHMENTS_DIRNAME
    if attachments_dir.exists():
        shutil.rmtree(attachments_dir)


def run(data_dir: Path, seed: int, scale: float, force: bool) -> dict[str, Any]:
    """Seed one deterministic, skewed corpus through the service layer.

    Returns the report dict that ``main`` prints and writes to
    ``<data_dir>/seed_report.json``. Raises ``ValueError``/``RuntimeError`` for
    conditions the CLI is expected to report and exit non-zero on.
    """
    if scale <= 0:
        raise ValueError("--scale must be positive.")

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DATABASE_FILENAME
    if force:
        _reset_data_dir(data_dir, db_path)

    db = Database.connect(db_path)
    try:
        check_search_extensions(db)
        run_migrations(db)
        settings = Settings(data_dir=data_dir, embedding_enabled=False)
        services = build_services(db, data_dir, settings)

        existing_keys = {
            ot.key
            for ot in services.schema.list_object_types(bootstrap_actor("seed-precheck-00000000"))
        }
        target_keys = {spec.key for spec in OBJECT_TYPES}
        overlap = sorted(existing_keys & target_keys)
        if overlap:
            raise RuntimeError(
                f"Refusing to seed: {data_dir} already has object type(s) {overlap} from a "
                "previous run. Delete the data directory, or pass --force to wipe the "
                "database and attachments/ tree and start clean."
            )

        rng = random.Random(seed)
        request_ids = itertools.count()
        counts = {spec.key: _scaled_count(spec, scale) for spec in OBJECT_TYPES}
        # `initiative` at every scale (it is always the largest by construction), but
        # computed rather than hardcoded so a re-scaled table would not silently drift.
        largest_type_key = max(counts, key=lambda key: counts[key])

        wall_start = time.perf_counter()

        # Before any object type (its target FK, `principals`, must already have
        # rows): see the OWNER_POOL_SIZE comment above for why these are real
        # principals rather than synthetic ids.
        owner_pool = _make_owner_pool(services, request_ids)

        _create_object_types(services, request_ids)
        _add_relation_fields(services, request_ids)

        records_start = time.perf_counter()
        progress = _Progress("records", records_start)
        keys_by_type, sample_stats = _seed_records(
            services, rng, owner_pool, counts, request_ids, progress, largest_type_key
        )
        records_elapsed = time.perf_counter() - records_start
        total_records = sum(len(keys) for keys in keys_by_type.values())

        links_start = time.perf_counter()
        links_created = _seed_links(services, rng, keys_by_type, request_ids)
        links_elapsed = time.perf_counter() - links_start

        comments_start = time.perf_counter()
        comments_created = _seed_comments(services, rng, keys_by_type, request_ids)
        comments_elapsed = time.perf_counter() - comments_start

        total_elapsed = time.perf_counter() - wall_start

        # A vendor and a notes-substring the measurement harness can build the
        # unindexed-equality and unindexed-substring latency shapes from, guaranteed
        # to actually occur in `largest_type_key`'s records rather than picked blind
        # (a value that matches nothing would silently measure the empty case instead
        # of the full-partition scan the skewed distribution exists to exercise).
        sample_vendor = (
            sample_stats.vendor_counts.most_common(1)[0][0]
            if sample_stats.vendor_counts
            else VENDOR_POOL[0]
        )
        sample_notes_hit_rate = (
            sample_stats.notes_hits / sample_stats.notes_total if sample_stats.notes_total else 0.0
        )

        report: dict[str, Any] = {
            "data_dir": str(data_dir),
            "seed": seed,
            "scale": scale,
            "total_seconds": total_elapsed,
            "records_created": total_records,
            "records_seconds": records_elapsed,
            "records_per_second": (total_records / records_elapsed if records_elapsed > 0 else 0.0),
            "links_created": links_created,
            "links_seconds": links_elapsed,
            "comments_created": comments_created,
            "comments_seconds": comments_elapsed,
            "counts_by_type": {key: len(keys) for key, keys in keys_by_type.items()},
            # The type the latency harness measures the unindexed shapes against
            # (FR-R5/FR-R6): an unindexed filter is a full scan of the *type's*
            # partition, so measuring against a small type would flatter the result.
            "largest_type_key": largest_type_key,
            "sample_vendor": sample_vendor,
            "sample_notes_substring": SAMPLE_NOTES_SUBSTRING,
            "sample_notes_substring_hit_rate": round(sample_notes_hit_rate, 4),
            # The selectivity `owner eq <id>` filters were measured at (see the
            # OWNER_POOL_SIZE comment above): each pool slot holds roughly
            # 1/owner_pool_size of the corpus.
            "owner_pool_size": len(owner_pool),
        }
        (data_dir / "seed_report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        db.close()


def _print_summary(report: dict[str, Any]) -> None:
    print("seed_perf: done")
    print(f"  data dir:          {report['data_dir']}")
    print(f"  seed / scale:      {report['seed']} / {report['scale']}")
    print(f"  total wall clock:  {report['total_seconds']:.1f}s")
    print(
        f"  records created:   {report['records_created']} in "
        f"{report['records_seconds']:.1f}s ({report['records_per_second']:.0f}/s "
        "through RecordService)"
    )
    print(f"  links created:     {report['links_created']} in {report['links_seconds']:.1f}s")
    print(f"  comments created:  {report['comments_created']} in {report['comments_seconds']:.1f}s")
    print(f"  owner pool size:   {report['owner_pool_size']}")
    print(f"  largest type:      {report['largest_type_key']}")
    print(f"  sample vendor:     {report['sample_vendor']!r}")
    print(
        f"  sample substring:  {report['sample_notes_substring']!r} "
        f"(hit rate {report['sample_notes_substring_hit_rate']:.2%})"
    )
    print("  per-type counts:")
    for key, count in report["counts_by_type"].items():
        print(f"    {key:<20} {count}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="GW_DATA_DIR to seed into; created if it does not exist.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260825,
        help="random.Random seed; reproducible for a given seed/scale pair (default: 20260825).",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Multiplies every per-type record count (default: 1.0, exactly 200,000 records).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing database file and attachments/ tree first, then seed clean.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """The testable entry point. Never calls ``sys.exit``; returns a process exit
    code instead (0 on success, 1 for a handled error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = run(args.data_dir, args.seed, args.scale, args.force)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_summary(report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
