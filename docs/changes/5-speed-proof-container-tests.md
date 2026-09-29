# 5: container_tests size themselves to the machine they run on

| | |
| --- | --- |
| Issue | #5, https://github.com/glosswork/glosswork/issues/5 |
| Branch | `5-speed-proof-container-tests` |
| Spec | PRD FR-P8 (backup without stopping writes), FR-Q7 (clean shutdown); CONTRIBUTING "Releases" (each architecture's image passes `container_tests`) |
| Decisions | DD-35 (startup reclaim), DD-36 (backup ordering) |
| Requirements | FR-P8, FR-Q7 |
| Depends on | nothing; `main` at `8ec0cc9` |
| Approved | plan and build in one run, no adversarial pass (maintainer, 2026-09-29); the plan itself is approved at merge |

**Process for this change.** The maintainer approved a lighter run on 2026-09-29: plan and
build in one session, **no adversarial pass**, no stop for plan approval. The plan reaches
the maintainer in the pull request, and the merge is where it is approved. Verification is
the Accept block run by the session that built it, which is the weak version of step 7 and
is labelled as such below.

## Why

The first release dry run (run 36554918375 on `8ec0cc9`) built the image on both native
GitHub runners and failed at "The image passes container_tests" on both. Each failure is a
test sized to the speed of the laptop it was written on, not a product defect. `v0.1.0`
cannot publish until the gate passes there.

## Premises

- **P1. amd64 failed on a start-up race, not on backup speed.** Read from job 109361728187's
  log (downloaded with `gh api --allow-escape-sequences .../jobs/109361728187/logs`): 3
  errors, all the setup of `test_backup_restore.py` at line 346, `only 0 concurrent
  write(s) completed`. Read at `container_tests/test_backup_restore.py:318-346`: the fixture
  starts the writer thread and calls the backup in the same instant; each is its own
  `docker exec`. The writer counts every write until the stop marker, which is created by a
  third `docker exec` *after* the backup returns. So zero writes means the writer's process
  had not completed one request by the time the backup finished **and** the stop marker
  landed, which is a race between two `docker exec` start-ups. If the backup had blocked
  writes instead, the writer would still have landed writes in the gap between the backup
  returning and the marker appearing. The dispatcher's reading is **confirmed**: seeding
  more content lengthens the backup and only moves the race. Not reproduced locally: on the
  maintainer's Mac the writer won the race in 3 of 3 runs (5, 6, 6 writes inside the
  window), so the evidence for the race is the runner's log plus this reading.
- **P2. The existing count is looser than its message.** Read at the same lines: it counts
  every write the loop made, including writes after the backup returned and before the stop
  marker. It does not show writes landed *during* the backup.
- **P3. Inside the backup call, writes proceed, and the ratio does not depend on speed.**
  Measured with this change's instrumentation (every write carries `time.monotonic()` start
  and finish; `download` reports its own): writes that began and committed inside the backup
  call were 10 at full speed (backup 0.025 s), 9 at `--cpus 1` (0.027 s), 9 at `--cpus 0.5`
  (0.104 s). The floor is 3, so the margin is about 3x at every speed measured.
- **P4. arm64 failed on a fixed band, and on reading the wrong batch.** Read from job
  109361728264's log: control arm `per_source_s` 3.696 (760 chunks, 19 per source, drain
  147.8 s) against a band of 0.3 to 2.5 s. The graceful arm on the same run claimed batches
  `[3, 32]`, released `[0, 31]`, and stopped in 4.016 s with exit 0: the fixed tree did
  exactly what the arm proves, and `test_a_graceful_stop_releases_the_rest_of_its_batch`
  failed only because it asserted on `claimed_batches[0]`, the small batch the slow runner
  claimed before the rest of the corpus arrived.
- **P5. The same laptop at full speed is 6.4x faster than that runner.** Measured:
  `GW_IMAGE=glosswork:main uv run pytest -s container_tests/test_clean_shutdown.py`, exit 0,
  control `per_source_s` 0.574 (drain 22.975 s), graceful claimed `[32]`, released `[30]`,
  stop 0.771 s.
- **P6. The ceiling is a product number, the floor a derived one.** Read at
  `src/glosswork/services/embedding_worker.py:78`: `STOP_GRACE_SECONDS = 5.0`, the join a
  stop gives the source in hand. A source longer than that makes the fixed tree's stop time
  out. Read at `test_clean_shutdown.py:95-105` (on `main`): the floor's stated purpose is
  that the unfixed tree cannot finish the in-flight batch inside `docker stop -t 10`, and
  `MIN_RUNNING_AT_STOP = 12` encodes that only at about 0.83 s a source.
- **P7. The worker times itself.** Read at `embedding_worker.py:194-205`: every batch logs
  `embedding_batch` with `claimed`, `released` and `duration_ms`. So per-source worker time
  is measurable from the log without the write overhead that `drain_s / 40` includes.
- **P8. The killed arm's drain was one slow runner from failing too.** Control drain 147.8 s
  on arm64 against `DRAIN_TIMEOUT_S = 180`. Sizing the corpus to the machine brings every
  drain to about 40 sources at the target time, wherever it runs.
- **P9. The other container tests carry no speed-sized discriminator.** Read every file in
  `container_tests/`. What remains are ceilings (`READY_TIMEOUT_S` 90 to 120,
  `INDEX_TIMEOUT_S` 120, `COLD_START_CEILING_S` 30, arm64 measured 3.6 s), one product bound
  (`test_operator_usage.py` stop under 5 s, DD-39's claim, measured 0.57 s), and one bounded
  retry (`test_network_isolated.py` restart resumption, up to 10 attempts to catch an
  all-pending backlog, which the claim's one-second hold-back makes likely at any speed).
  Both arm64 and amd64 passed all three files on the dry run. None is changed.

## What changes

1. **`container_tests/test_backup_restore.py`**: the backup starts only after the writer has
   announced its first successful write (a marker file it creates in the container), and
   the FR-P8 assertion counts only writes that **began and committed inside the backup
   call**, on the container's own monotonic clock. `MIN_CONCURRENT_WRITES` stays 3. The
   assertion is stronger than before (P2), not weaker.
2. **`container_tests/_gw_client.py`**: `loop-write` takes an optional writing-marker path
   and records `started` and `finished` per write; `download` reports `started` and
   `finished`.
3. **`container_tests/docker_support.py`**: `wait_for_path`; `run_loop_write` passes the
   writing marker; `GW_CONTAINER_CPUS`, when set, adds `--cpus` to every application
   container, so a slow runner can be reproduced on one machine.
4. **`container_tests/test_clean_shutdown.py`**:
   - a **sizing arm** (its own container, removed before the others start) times a probe
     from the worker's own batch lines and picks the words per source that make one source
     take about `TARGET_SOURCE_S` (1.0 s, raised to 1.5 s by D2), bounded to 2.5 to 60
     chunks;
   - the control arm writes that corpus and measures **worker** seconds per source from its
     batch lines;
   - the fixed band becomes two derived checks: a ceiling of 2.5 s (half the worker's 5 s
     join, P6; 3.5 s by D4), and a floor that the sources which must still be claimed at the signal,
     `ceil(10 s x 1.2 / per_source) + 1`, fit in a batch of 32 (half a batch, 16, by D2);
   - the graceful and killed arms wait for that derived count instead of a fixed 12;
   - the graceful discriminator asserts on **the batch the stop interrupted** (the one with
     `released > 0`), not on the first batch claimed.
5. **`AGENTS.md`**, Traps: one bullet on container tests sized to one machine, and
   `GW_CONTAINER_CPUS`.

## What does not change

- Any product code. `src/` is untouched, and so is the image.
- `MIN_CONCURRENT_WRITES` (3), `MAX_STOP_S` (8), `STOP_GRACE_S` (10), `COLD_START_CEILING_S`
  (30), `DRAIN_TIMEOUT_S` (180), `CORPUS_SOURCES` (40), and every other assertion in both
  files.
- The four other container test files (P9), and `.github/workflows/release.yml`.

## Constraints

- Every assertion still fails against the defect it exists to catch: a backup that blocks
  writes; a worker that drains its batch on a stop; a start that does not reclaim.
- No test reads a number measured on one machine as a constant.
- `container_tests` stays outside `testpaths`.

## Checklist

1. [x] Run the new assertions against the unfixed tree and record how each failed (see
   Accept output, "Unfixed").
2. [x] Backup: writer marker, per-write times, window count (items 1 to 3 of What changes).
3. [x] Clean shutdown: sizing arm, derived thresholds, interrupted-batch assertion.
4. [x] `AGENTS.md` trap.
5. [x] Run the Accept block at full speed, at `--cpus 1` and at `--cpus 0.5`.

## Accept

- **AC1** lint: `uv run ruff check . && uv run ruff format --check .`, exit 0.
- **AC2** types for the touched modules: `uv run mypy container_tests/test_clean_shutdown.py
  container_tests/test_backup_restore.py container_tests/docker_support.py
  container_tests/_gw_client.py`, exit 0.
- **AC3** full speed: `uv run pytest -s -q container_tests`, exit 0.
- **AC4** slowed, about the arm64 runner's speed: `GW_CONTAINER_CPUS=1 uv run pytest -s -q
  container_tests/test_clean_shutdown.py container_tests/test_backup_restore.py`, exit 0.
- **AC5** slowed further: `GW_CONTAINER_CPUS=0.5`, the same two files, exit 0. (Split by
  D4 into AC5 at `0.75`, exit 0, and AC5b at `0.5`, which states the envelope.)
- **AC6** the unit suite is untouched: `uv run pytest -q`, exit 0.
- **AC7** the fixed tree still fails where it should (the negative case): with
  `embedding_worker.py`'s checkpoint disabled (the `if self._stop.is_set()` release made a
  no-op, in a scratch image never committed),
  `test_a_graceful_stop_releases_the_rest_of_its_batch` and
  `test_a_graceful_stop_takes_the_time_one_source_takes_not_one_batch` fail at full speed
  and at `--cpus 1`.

"Faster" is not reproducible on this machine: nothing here is faster than full speed. The
fast direction is covered by the sizing arm lengthening sources up to the chunk cap, and by
the floor check reporting plainly if a machine is too fast even for that.

## Adversarial pass

None, by the maintainer's decision for this change (2026-09-29).

## Deviations from the approved plan

- **D1. The corpus filler is no longer a plain rotation.** Found by the first full-speed run of
  step 3: the sizing arm aimed at 1.0 s and the control arm measured 0.543 s at 5305 words.
  Measured inside the image with the product's own chunker and model: the rotated
  fifteen-word filler repeats its chunks, the worker embeds each distinct chunk once, and a
  source stops getting more expensive at 13 distinct chunks (2800 words and 5305 words: 13
  distinct each, 0.505 s and 0.506 s). A seeded random draw from the same vocabulary gives
  every chunk its own text (19 and 35 distinct, 0.751 s and 1.416 s), so cost is linear in
  length and the sizing arm can scale it. Same vocabulary, same single-token words, same
  chunk count for every source. This also means the old corpus was cheaper than its
  comment said, on every machine.
- **D2. Target 1.5 s, and the arms may require at most half a batch.** Found by the same
  run: at 0.543 s a source the derived requirement was 24 sources claimed at the signal,
  and the killed arm's corpus arrived as batches of 24 then 16, so 23 running with a chunk
  indexed never happened and the arm timed out. The corpus is written across the claim's
  one-second hold-back and arrives in two or more batches (`[9, 31]`, `[24, 16]`, `[3, 32]`
  seen), so the floor check is now `required_running <= 16`, not 32. At 16 the floor on
  per-source time is 0.8 s, so the target moved from 1.0 s to 1.5 s: 1.7x under the 2.5 s
  ceiling and 1.9x over the floor.
- **D3. Whole shuffled cycles, and the nonce in its own paragraph.** D1's first form, a free
  random draw, failed the exact yardstick at full speed: 1419 chunks over 40 sources, so
  bodies differed by a chunk. A free draw gives bodies different token counts; the nonce's
  hex digits also vary its token count. The filler is now whole fifteen-word cycles, each
  shuffled per source (same words, same numbers, so the same tokens), and the nonce is its
  own paragraph, so it is its own first chunk and cannot move the filler's window
  boundaries. Measured in the image with the product's chunker, 120 bodies each (40 indices
  under 3 nonce stems) at 754, 2800, 5356, 5444 and 8000 words: one chunk count per size
  (6, 20, 37, 37, 54), and every chunk's text distinct.

- **D4. The ceiling is 70% of the worker's join, not half; the shortest source is 1.5
  chunks; and a machine too slow for the proof says so in the sizing arm.** Found by AC5.
  At `--cpus 0.5` the probe took 27.1 s over a 2800-word source (1.36 s a chunk, about 5x
  the arm64 runner): no corpus fits the ceiling, and the first form failed as a 180 s drain
  timeout. It now fails in the sizing arm with one sentence naming the machine as too slow.
  At `--cpus 0.75` (0.61 s a chunk, about 2.2x the runner) the shortest corpus measured
  2.679 s, over a 2.5 s ceiling, while the graceful arm itself passed with a 2.881 s stop.
  The ceiling is a precondition that no arm asserts against; the product's own limit is the
  5 s join, and the shutdown around the source in hand measured under 0.4 s at every speed.
  So it moved to 3.5 s, and the target stays 1.5 s (2.3x under it). At `--cpus 0.5` the
  proof still does not fit, and it does not fail in the sizing arm either: the probe
  predicted 2.2 s at 225 words, the control arm then took about 4.6 s a source (one batch,
  32 sources, 148.8 s), because a short source's fixed cost does not shrink with its words.
  It fails as the control arm's 180 s drain timeout, with the batch line in the output.
  That machine is about 5x slower a chunk than the arm64 runner, and a worst-case 64-chunk
  source there would take the product itself about 90 s against a 5 s join, so this is the
  honest edge of the envelope, not a regression.

## Accept output

Run by the session that built the change (no separate verifier, by the maintainer's
decision). Every exit code read directly, never through a pipe. Image `glosswork:main`,
built from `8ec0cc9`; this change touches no file the image is built from, and the tests
copy `_gw_client.py` into each container at run time. Machine: Apple silicon, Docker
28.4.0 VM with 12 CPUs.

**Unfixed** (`main`'s test files, with only `GW_CONTAINER_CPUS` added):

| Run | Exit | Result |
| --- | --- | --- |
| `test_clean_shutdown.py`, full speed | 0 | 10 passed; control 0.574 s a source (drain), graceful `[32]` released `[30]` |
| `test_clean_shutdown.py`, `--cpus 1` | 1 | control 3.617 s a source, "outside the 0.3-2.5s band": the arm64 failure reproduced (runner: 3.696 s) |
| `test_backup_restore.py`, full speed, writer not awaited, new window count | 0 | 6, 6, 5 writes inside the window in 3 runs: the amd64 race does not reproduce on this machine (P1) |

**Fixed**, final tree (`179d5f5` plus this commit's docs):

| AC | Command | Exit | Result |
| --- | --- | --- | --- |
| AC1 | `uv run ruff check .`; `uv run ruff format --check .` | 0; 0 | |
| AC2 | `uv run mypy` on the four touched modules | 0 | no issues in 4 files |
| AC3 | `uv run pytest -s -q container_tests` | 0 | 29 passed, 1 skipped (the same skip as CI) |
| AC4 | `GW_CONTAINER_CPUS=1`, clean shutdown and backup | 0 | 13 passed |
| AC5 | `GW_CONTAINER_CPUS=0.75`, same two files | 0 | 13 passed |
| AC5b | `GW_CONTAINER_CPUS=0.5`, same two files | 1 | backup 3 passed; clean shutdown out of envelope (D4) |
| AC6 | `uv run pytest -q` | 0 | 2048 passed, 3 xfailed |
| AC7 | checkpoint disabled, `--cpus 1` (and full speed on the D3 tree) | 1 (1) | 3 failed each: `released`, the stop bound (`embedding_worker_stop_timed_out`; stop 7.15 s), and the reclaim fence |

The numbers each speed produced:

| Speed | Probe s / 2800 words | Words | Chunks | Worker s / source | Required at signal | Graceful stop | Backup window | Writes inside |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 0.835 | 5027 | 34 | 1.421 | 10 | 1.751 s, released 30 of 32 | 0.030 s | 14 |
| `--cpus 1` | 5.748 | 731 | 6 | 1.734 | 8 | 2.013 s, released 30 of 32 | 0.071 s | 9 |
| `--cpus 0.75` | 13.398 | 313 | 3 | 2.007 | 7 | 2.509 s, released 30 of 32 | 0.086 s | 8 |
| `--cpus 0.5` | 27.1 | 225 | | about 4.6 | | | 0.192 s | 9 |

`--cpus 1` matches the arm64 runner: 5.748 s over a 20-chunk probe is 0.29 s a chunk; the
runner's 3.696 s over 13 distinct chunks (D1) is 0.28.

**AC7 is the negative case, and it is a fence for the clean-shutdown discriminators only.**
The backup assertion's negative case (a backup that blocks writes) was not built: the
product has no switch for it, and making one means changing `services/backup.py`. Its
strength rests on the window count's construction: a write that straddles either edge of
the backup call is not counted.

**What is not proven here.** The amd64 runner's speed was not reproduced: nothing on this
machine is faster than full speed, and the amd64 race did not occur locally. Only the next
release dry run on GitHub's runners proves both architectures.

## Durable content moved out of this plan

- `AGENTS.md`, Traps: "A container test sized to one machine fails on the next", with
  `GW_CONTAINER_CPUS`.
- The reasoning behind every constant lives in the constants' own comments in
  `container_tests/test_clean_shutdown.py` and `test_backup_restore.py`, including the measurements behind D1 to D4.
