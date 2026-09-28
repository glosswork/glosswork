# Performance at 200,000 records

The first full measurement and its re-measurements, in the shape the golden retrieval report already
uses, so a later run is comparable rather than merely reassuring. Every number here was produced by
a script in `scripts/`, on the platform and corpus named below. Nothing is estimated.

**Re-measured in full on 2026-09-02 after per-type access control and the attachment index**,
because that change amended migration 1 in place and so invalidated the first full measurement's
database. Every section below carries both the first full measurement's figure and the re-measured
one where they differ, rather than overwriting the first with the second: the point of this document
is that a later run is comparable. One number moved the wrong way — the schema fan-out's worst
concurrent write — and is reported as a finding in that section rather than absorbed.

## Platform and corpus

| | |
| --- | --- |
| Platform | Darwin 25.6.0, arm64, 12 cores |
| Python | 3.13.7 |
| Embedding model | `bge-small-en-v1.5@5c38ec7` |
| Corpus | `scripts/seed_perf.py --data-dir ./perf-data`, seed `20260825`, scale 1.0 |
| Records | 200,000 across 15 object types |
| Distribution | **Skewed.** `initiative` holds 90,000 (45%); the smallest, `lesson`, holds 1,000 |
| Relations / comments | 39,608 links, 19,807 comments |
| Embedding during seeding | Disabled (`GW_EMBEDDING_ENABLED=false`) |
| Constants in force | `RRF_K=60`, `CANDIDATE_MULTIPLIER=4`, `WIDEN_MULTIPLIER=16`, chunking 256/32, `FAN_OUT_BATCH_SIZE=500`, `FAN_OUT_BATCH_PAUSE_S=0.02`, `busy_timeout=5000 ms` |

**The distribution is skewed on purpose and the number depends on it.** An unindexed
filter is a full scan of the *object type's* partition, so an even 15-way split would
have understated that path by roughly 7x. The unindexed figures below are measured
against `initiative`, the largest type, which is the honest case.

## Seed throughput (success criterion 5)

The seed runs through `RecordService`, not through raw `executemany`, because that
number *is* the CSV import throughput an office migrating its data experiences.

| | |
| --- | --- |
| Records created | 200,000 in 432.3 s = **463 records/second** |
| Links | 39,608 in 59.7 s |
| Comments | 19,807 in 17.3 s |
| Whole run | 509.4 s |

**Re-measured 2026-09-02 after per-type access control and the attachment index**, on a corpus
reseeded from scratch because that change amended migration 1 in place. The first full measurement
measured 494 records/second on the same harness; this run measured 463. (The corpus was seeded twice
that day -- the destructive fan-out measurement below needs a clean one -- and the first run, with
the backend suite running concurrently, measured 449. The figures above are the second, quieter
run.) Every record write also maintains `record_attachments` — one delete and, for a record with no
attachment field, no insert — so a small cost is expected there, and it is well inside the
run-to-run variance this section already records rather than distinguishable from it.

For comparison, the same harness on the same corpus before the sort-composite indexes of
section 3 existed measured 567 records/second, and a controlled micro-benchmark isolating
one object type measured 621 against 614. Run-to-run variance on a shared laptop is wider
than the effect being measured, so the honest statement is that the composites cost
somewhere in the low single-digit to low double-digit percent of write throughput, and
the read path they buy is 16x.

## Query latency (the latency gate)

Measured **server-side, end to end through HTTP**, taken from the access log's
`duration_ms` and correlated to each request by the `x-request-id` the response carries,
never from raw SQL timing. 200 requests per shape after 25 warmup requests
(`scripts/measure_perf.py`).

The gate: indexed path **p50 <= 50 ms, p95 <= 150 ms**. Unindexed path p50 <= 250 ms /
p95 <= 600 ms, which is reported and not gated, because it scales with the largest object type
rather than with this code.

| Shape | p50 | p95 | Budget | |
| --- | --- | --- | --- | --- |
| Indexed filter, sorted, first page of 50 | **2.33 ms** | **2.41 ms** | 50 / 150 | pass |
| Same, 200 rows deep by keyset cursor | **2.47 ms** | **2.62 ms** | 50 / 150 | pass |
| Same, with the embedding worker mid-drain | **2.98 ms** | **4.05 ms** | 50 / 150 | pass |
| Equality filter on an unindexed field | 141.03 ms | 146.74 ms | 250 / 600 | reported |
| Substring match on unindexed long text | 200.06 ms | 216.22 ms | 250 / 600 | reported |

**Re-measured 2026-09-02 after per-type access control and the attachment index**, against a corpus
reseeded from scratch: that change amended migration 1 in place (`object_types.default_level`, the
`principals.role` CHECK) and added migration 7, which invalidated the first full measurement's
database exactly as migration 6's own comment says an earlier database was invalidated. The three
gated shapes previously measured 2.79 / 2.86 / 3.42 ms p50; the numbers above are the same shapes on
the same hardware after the change. They moved slightly *down*, which is run-to-run variance on a
shared laptop rather than an improvement — the read path is untouched by that change, and the grant
restriction never reaches it, because a per-type read resolves its level once at the service entry
point and compiles nothing into the filter AST.

> **Not re-measured after the principals sidecar alone, and the read path did change.** A
> `principals` sidecar is composed onto every response document that carries a record, which adds
> **one** `SELECT * FROM principals WHERE id IN (...)` per response — one per document, not one per
> record, asserted by a repository call count in `tests/test_principal_sidecar.py`. That read is on
> a primary key over a table with one row per human, and `scripts/measure_perf.py` drives the HTTP
> `/query` route end to end, so it is inside the measured path and the figures above predate it.
> Nothing else on the read path moved: the filter compiler gained a resolver callable that is
> invoked only for a `user_ref` comparison value, the stored shape is unchanged, and no index,
> cursor or compiled SQL differs. The numbers are therefore expected to be within run-to-run
> variance rather than wrong, but that is a prediction, not a measurement, and it is recorded here
> as one. Re-seed and re-run before the next change that touches this path.

**Re-measured 2026-09-04 after the index rename**, on a corpus reseeded from scratch, and this run
also settles the sidecar's open prediction above. The rename changes the name of every per-type
index on `records` (docs/DATA_MODEL.md section 5, DD-12) and changes no query text and no index
*expression*, so the read path should not have moved; the point of measuring was that "should not
have moved" is a prediction until it is a number.

| Shape | p50, after per-type access control and the attachment index | p50, after the index rename | Gate |
| --- | --- | --- | --- |
| Indexed filter, sorted, first page of 50 | 2.33 ms | **3.00 ms** | 50 ms, pass |
| Same, 200 rows deep by keyset cursor | 2.47 ms | **3.02 ms** | 50 ms, pass |
| Same, with the embedding worker mid-drain | 2.98 ms | **3.74 ms** | 50 ms, pass |
| Equality filter on an unindexed field | 141.03 ms | 149.24 ms | reported |
| Substring match on unindexed long text | 200.06 ms | 206.17 ms | reported |

Roughly 17x headroom on the gate, and about 0.6 ms slower across every shape including the two
ungated ones — which is what a slower machine-day looks like, not what one changed code path looks
like. The one real candidate for a share of it is the `principals` sidecar, one primary-key
`SELECT` per response document, which is inside this measured path and predates these numbers; it
is not separable from the variance at this size, and it is recorded as unseparated rather than
dismissed. The first full measurement's figures, which the earlier rows are compared against,
were 2.79 / 2.86 / 3.42.

**The retirement pass the index rename adds at startup is not in these numbers and is not a query
cost.** First start: 6.82 s, dropping 105 indexes under the old index names and creating 105 under
the new names, in one write transaction. Second start: 0.07 s, `(0, 0)`. `docs/DEPLOYMENT.md`
carries the operator note.

**Migration 9's backfill is likewise a startup cost and not a query cost.**
Measured 2026-09-09 on a corpus seeded at migration 8 and then upgraded: **0.58 s** over 337.3 MB,
50,000 records and 375,055 audit events, for the `ALTER TABLE` plus the one correlated-subquery
`UPDATE` that reads the newest field-writing audit event per record (`ix_audit_record` covers it).
It runs once; a second start applies nothing.

It was measured rather than assumed because DD-26 *rejected* the read-path alternative on measured
grounds, and shipping an unmeasured one-shot pass over the table this document names as 58.2% of
the corpus would have been the same error in the other direction. At a quarter scale it is an
order of magnitude under the index rename's pass, so no operator note is warranted.

**What this measurement does not cover, stated so it is not mistaken for coverage:**
`scripts/seed_perf.py` writes no agent labels at all, so the run marked **zero** rows. It is a
measurement of the backfill's *scan*, which is the part that scales, and not of its attribution.
That correctness lives in `tests/test_record_agent_label.py`, which builds a database at
migration 8, upgrades it, and pins which audit events count.

**The corpus this run used was reseeded, and had to be.** The corpus on disk predates
`object_types.display_field_key`, which was added by amending migration 1 in place rather than by
adding a migration — so the runner considers such a database fully migrated
and the current code cannot read its object types at all. That is the same failure mode migration 6
has its own note about in `docs/DEPLOYMENT.md`, in a second place, and it means a perf corpus does
not survive a change that amends migration 1. Reseed before measuring, rather than trusting a
directory that is present.

Every gated shape passes with roughly 17x headroom. The mid-drain shape ran against a
genuinely busy worker: 20,000 jobs were queued before it started and 19,868 were still
pending when it finished, so the worker held CPU throughout.

Deep paging costs essentially nothing over the first page (2.86 ms against 2.79 ms),
which is the keyset cursor doing what it was built for; an `OFFSET` implementation would
have grown with depth.

### The finding the first full measurement produced, and what was done about it

**The first measurement missed.** Before the change described below, the same three
indexed shapes measured 45.04 / 49.28, 48.34 / 52.84, and **54.96 / 70.82** ms. The
mid-drain shape missed the 50 ms p50 gate by 5 ms. Under the gate's rule that is "a
finding to report, not a budget to adjust", and the remedy is index or query work.

**Diagnosis.** The cost was not the row count and not the `total_count`, which measured
1.08 ms. The compiled query ends `ORDER BY <sort expr>, records.id ASC`, because keyset
pagination needs a total order, and `records.id` is a UUID unrelated to rowid. No
single-column index can supply that ordering, so SQLite found all 35,961 rows matching
the filter and sorted every one of them in a temporary B-tree to return 50. It did this
with `ANALYZE` run and `sqlite_stat4` populated (1,906 rows), so it was not a missing
statistics problem. Cost was linear in rows *matched*, about 1.1 microseconds each: a
`user_ref` filter matching 2,375 rows already returned in 4.04 ms.

**Two remedies were measured before either was chosen.** Forcing the sort field's index
with `INDEXED BY` gave 0.40 ms but is unconditionally wrong for a selective filter, where
it would turn a fast lookup into a full index scan. A composite `(filter, sort, id)`
partial index gave 0.60 ms, and 0.16 ms once built the way section 3 describes.

The composite was built. The gate line was unchanged by the measurement.

## Sort composite indexes

Described normatively in docs/DATA_MODEL.md section 5. What they cost and buy, measured:

| | |
| --- | --- |
| Indexes created | 45 (three per object type: the `single_select` field paired with `name`, `due_date`, `owner`) |
| Build time | 0.6 s for three over 90,000 records |
| Disk | +59 MB on a 1.29 GB database (4.6%) |
| Read path | 45.04 ms to 2.79 ms at p50 (16x) |
| Write path | see the seed throughput caveat above |

One index covers both sort directions. On this corpus SQLite read straight out of the
index in both; on a small table it takes a partial sort, reported as `USE TEMP B-TREE FOR
LAST TERM OF ORDER BY`, which sorts only within one group of tied values. A bare `USE
TEMP B-TREE FOR ORDER BY` is the regression, and `tests/test_sort_composite_indexes.py`
asserts that distinction rather than the weaker "an index was used".

## Retrieval at scale

**The claim a 36-record search corpus could not test.** Migration 6 gained `object_type_id`
and `model_id` partition keys on the argument that a global KNN starves on a scoped search
once the target type is about 1% of the corpus, and nothing in that corpus could exercise
it. `tests/test_search_at_scale.py` now does, on a corpus with the same
1% ratio the seed produces: a scoped semantic search returns a full `limit` of in-scope
records, while the same `k` drawn without the partition constraint returns fewer in-scope
chunks than one page needs. The ratio, not the absolute size, is what makes a KNN starve,
which is why the mechanism is asserted in a test rather than observed once here.

## Indexing throughput, and the re-index cost

`scripts/measure_indexing.py`, real `OnnxBgeProvider`, bounded slice rather than a full
drain (extrapolated, not paid).

| | p50 | chunks/second |
| --- | --- | --- |
| Short chunk (a title, 9 tokens) | 3.39 ms | 294.9 |
| Full chunk (264 tokens) | 43.62 ms | 22.9 |

End to end through the real worker, which batches: **23,472 sources drained in 208.8 s =
113.2 sources/second**. The gap between that and the per-chunk figures is batching, and
it is large enough that the batched number is the one an operator should be quoted.

The corpus holds **423,439 indexable sources**, so a **full re-index is about an hour**
at the measured batched rate, single threaded, with the worker holding up to four cores.
That is the number `docs/DEPLOYMENT.md` gives an administrator before they press the
FR-Q9 re-index button, which offered no indication of its cost.

**Re-measured 2026-09-02 after per-type access control and the attachment index** on a reseeded
corpus. The batched rate came out lower than the first full measurement's 164.4/s on a larger drain
(23,472 sources against 10,013) with the suite and a second measurement running on the same laptop;
the per-chunk figures, which are the ones the extrapolation is anchored to and the ones least
sensitive to what else is running, are within 5% of the first full measurement's. Nothing in that
change touches the indexing path.

## Backup at scale (FR-P8, DD-36)

`scripts/measure_backup.py`, with four writer threads running for the duration.

| | |
| --- | --- |
| Artifact | 1,234.8 MB |
| Total | 3.74 s (snapshot 3.6 s, blob walk 0.14 s) |
| Throughput | 330.4 MB/s |
| Concurrent writes | 3,632, **all succeeded** |
| Concurrent write latency | p50 1.06 ms, p95 1.51 ms, **max 4,129 ms** |

FR-P8's "without stopping writes" holds: nothing failed. But the maximum is the number
that matters. `VACUUM INTO` holds the writer lock for effectively the whole snapshot, about
**3 seconds per gigabyte** here, and a write that arrives at the wrong moment waits that
long against a 5,000 ms `busy_timeout`. At 1.23 GB that leaves under 20% of headroom.

**Re-measured 2026-09-02 after per-type access control and the attachment index** on a reseeded
corpus. The snapshot ran faster than the first full measurement's (3.74 s against 6.43 s, 330 MB/s
against 191) and the worst concurrent write correspondingly *worse* relative to the timeout —
4,129 ms against 3,592 ms, on a warmer page cache and a busier machine. Read that pair the way the
first full measurement asked it to be read: the maximum is what matters, it is within 900 ms of the
`busy_timeout`, and the ceiling below is unchanged. The first full measurement's earlier runs on
databases of 1.19 GB and 1.24 GB observed maxima of 4,118 ms and 4,421 ms, which brackets this one.

So `VACUUM INTO` stands at this scale and the ceiling is recorded rather than discovered
later: past roughly 1.5 GB, expect writes concurrent with a backup to fail.
`sqlite3.Connection.backup()` with a step size remains DD-36's documented fallback.

## Schema-change fan-out (DD-34)

`scripts/measure_fanout.py`, four writer threads writing to a *different* object type,
because SQLite's writer lock is database-wide and the realistic victim is an unrelated
colleague saving an unrelated record.

| Operation | Elapsed | Writes completed | Max wait | Failed |
| --- | --- | --- | --- | --- |
| `embed` on, before the fix (first full measurement) | 22.1 s | 597 | 1,615 ms | **16** |
| `embed` on, after (first full measurement) | 37.0 s | 9,437 | 4,472 ms | **0** |
| `embed` on (re-measured 2026-09-02, after per-type access control and the attachment index) | 60.6 s | 20,175 | **7,264 ms** | **0** |
| `delete_field` approval (first full measurement) | 8.6 s | 541 | 3,395 ms | 4 |
| `delete_field` approval (re-measured, after per-type access control and the attachment index) | 8.9 s | 473 | 1,652 ms | 4 |

**The re-measurement after per-type access control and the attachment index moved the `embed` on
maximum past the busy timeout, and that is reported rather than absorbed.** Two of the 20,175
concurrent writes took longer than 5,000 ms end to end. None *failed* — the script counts a write at
or over `BUSY_TIMEOUT_MS` separately from one that raised, and here the count is 2 against 0
failures — so its own comment ("a write that waits longer than the busy timeout does not wait, it
fails") over-states what this counter proves. But the maximum is the number the DD-34 decision turns
on, and it went from 4,472 ms to 7,264 ms.

Two things changed and **one run each cannot separate them**, which is the honest
statement:

- **The attachment index adds one indexed `DELETE FROM record_attachments` inside every record
  write** (the join-table sync, which runs from the record's stored data on every write path).
  The writer threads here write to `task`, which has no attachment field, so the delete
  matches nothing — but it is still a statement inside the transaction holding the
  database-wide writer lock. A cheaper design exists and is deliberately not used: skip the
  delete when the record's type declares no attachment field, which
  needs the schema delete-field path to purge join rows so a removed field cannot leave
  a stale one behind. Correctness first, then the optimization.
- **The run was 64% longer and completed 2.1x more writes** (20,175 against 9,437), so
  the tail had more than twice as many chances to be sampled. Writer *throughput* rose,
  333/s against 255/s, which is the opposite of what a per-write regression alone would
  do.

DD-34 holds unchanged, since nothing failed, but the margin against the busy timeout is now thin enough that it is a
number to watch rather than a number to quote. `delete_field` moved the other way, its
maximum falling from 3,395 ms to 1,652 ms with the same four failures.

**Batching alone did not fix it, which is the part worth not rediscovering.** With
2,000-record batches and no pause between them the fan-out still failed ten writes:
SQLite's busy handler retries a blocked `BEGIN IMMEDIATE` on its own schedule, and a
writer that releases the lock and immediately re-takes it leaves almost no window for a
retry to land in. A 20 ms pause between batches is what actually worked; 10 ms still
failed one write.

`delete_field` is a recorded residual rather than a fix. About 3.2 s of its 8.6 s is
`UPDATE records SET data = json_remove(...)` across all 90,000 rows, a data mutation that
is atomic with the schema change that authorised it. DD-34 carries the reasoning.

## Cold start (scale to zero)

`scripts/measure_cold_start.py`, against `glosswork:container-test` built from this repository.
A hosted workspace scales to zero, so it is stopped and started many times a
day and somebody waits for each start. Five stop/start cycles on an already-migrated
named volume:

| | min | p50 | max |
| --- | --- | --- | --- |
| `docker start` **returning** to the first `200` from `/readyz` | 2.003 s | 2.077 s | 2.125 s |
| The `docker start` **call** to the first `200` | 2.119 s | 2.188 s | 2.236 s |
| The `docker start` call itself | 0.111 s | 0.114 s | 0.125 s |

First boot on an **empty** volume, with the migrations running inside the measurement:
2.029 s from `docker start` returning, and 2.119 s from the call.

**Which instant, and what the probe was.** The middle row is the number a person waiting
on a scaled-to-zero workspace experiences; the first row is the one to compare a later
run against, because it excludes the container runtime's own latency. They differ by the
third row, which is small here and is not nothing: an earlier pass measured this same
start 10 percent apart under two different probes, with the `docker start` call's cost
outside both numbers. So the report carries the probe and so does this section. It is
`urllib.request.urlopen` against `GET /readyz`, one request per poll, polled every
20 ms, over a published loopback port, with the volume already migrated except where the
table says otherwise. A number recorded without its probe measures the probe.

**The embedding model is not the cost.** Measured separately: the same start with
`GW_EMBEDDING_ENABLED=false` came in roughly a twentieth lower, so building the ONNX session is a
small part of this. Lazy-loading the model, the obvious way to make cold start faster, is not the
lever it looks like. The rest is interpreter start, imports, `check_search_extensions`, the
migration runner and uvicorn binding. That comparison is not in the committed report, which measures
the shipping configuration only.

**This is a local number and it is not a hosted one.** It is a Docker VM on the platform
in "Platform and corpus", with the image layers already on the host, a local volume and a
warm page cache. That is the right environment for catching a regression in this
repository. The same start on a real platform also pays machine creation or resume,
pulling the image onto a host that may not have it, attaching the volume, and the round
trip to the region, on a shared-CPU machine with fewer and slower cores than this laptop.
The hosted figure is measured separately against the real platform, is expected to be
larger, and nothing here should be quoted as it.

There is a generous ceiling rather than a gate: `container_tests/test_clean_shutdown.py`
asserts under 30 seconds on each of the two arms that already restart a container, and
`scripts/measure_cold_start.py`'s `start_returned_p50_s` is asserted under 30 in the same
place. A catastrophic regression fails a test; a loaded laptop does not.

## Storage

Taken with `dbstat` after the measurement sequence above (so it includes the writes the
backup and fan-out harnesses made).

| | Size | Share |
| --- | --- | --- |
| Total | 1.31 GB | |
| `audit_events` and its five indexes | 782.6 MB | **58.2%** |
| `records` | 151.3 MB | 11.2% |
| Keyword index (`fts_content*`, all five FTS5 shadow tables) | 126.7 MB | 9.4% |
| `search_sources` and its indexes | ~90 MB | 6.9% |

**The audit trail is the largest object in the database, by a factor of five over the
records themselves.** So the audit table is not "small relative to `records`". The decision
DD-40 records (audit retention is indefinite) rests on cursor monotonicity rather than on size,
so it stands, but an operator sizing a volume should budget for the audit trail first.
See DD-40 and `docs/DEPLOYMENT.md` section 8.

## Reproducing this

```bash
uv run python scripts/seed_perf.py    --data-dir ./perf-data
uv run python scripts/measure_perf.py --data-dir ./perf-data --out ./perf-data/latency.json
uv run python scripts/measure_backup.py   --data-dir ./perf-data --out ./perf-data/backup.json
uv run python scripts/measure_indexing.py --data-dir ./perf-data --out ./perf-data/indexing.json
uv run python scripts/measure_fanout.py   --data-dir ./perf-data --out ./perf-data/fanout.json

docker build -t glosswork:container-test .
uv run python scripts/measure_cold_start.py \
  --image glosswork:container-test --runs 5 --out ./perf-data/cold-start.json
```

`measure_cold_start.py` is the one that needs no corpus: it creates its own volume and
container, measures, and removes both. It needs Docker rather than the 1.3 GB database,
so it can be run on its own.

**The reports these produced are committed**, in `perf-data/`, so every number above can
be checked against the run that produced it rather than taken on trust: `seed_report.json`
backs the throughput table, `latency.json` the five shapes, `backup.json`, `indexing.json`
`fanout.json` and `cold-start.json` their own sections, and `latency-index-rename.json` the same
five shapes after the index rename. The 1.3 GB database itself is not committed, which
is why `.gitignore` ignores `perf-data/*` and re-includes `*.json`.

A re-measurement is written to a **new** file named for the change that made it or for what
it measured rather than over `latency.json`. The first full measurement is what the latency
gate was set against, and a document that keeps only the newest number cannot show that a shape
moved.

**That pattern is written the way it is on purpose.** It must be `perf-data/*`, not
`perf-data/`: git cannot re-include a file whose parent *directory* is excluded, so the
directory form would make the `!perf-data/*.json` negation silently do nothing and the
reports would quietly stop being committed. There is no error when this is wrong, only
missing files, which is why it is recorded here rather than left to be rediscovered.

Run them in that order. `measure_indexing.py` needs the model
(`uv run python scripts/fetch_model.py`, then `GW_MODEL_DIR=$PWD/models`), and
`measure_fanout.py` is **destructive**: it turns `embed` on for a field and then deletes
it, so re-seed before measuring anything else. `perf-data/` is gitignored; it is roughly
1.3 GB.
