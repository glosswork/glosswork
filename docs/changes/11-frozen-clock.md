# 11: The embedding worker behaves after its clock has been frozen

Approved as written by the maintainer on 2026-10-01, with the open question below answered:
pin one process (`workers=1`).

| | |
| --- | --- |
| Issue | #11, https://github.com/glosswork/glosswork/issues/11 |
| Branch | `11-frozen-clock` |
| Spec | docs/DATA_MODEL.md section 10, rule 5; docs/DEPLOYMENT.md section 2a |
| Decisions | DD-35 |
| Requirements | FR-Q7, FR-P1 |
| Depends on | nothing; cut from `main` at `5988eef` |

## Why

A workspace whose virtual machine is paused instead of stopped resumes with its wall clock moved
on and its monotonic clock where it was. Hosted workspaces will be paused that way once they
suspend rather than stop, and self-hosted ones already are whenever a laptop running Docker
Desktop sleeps. The embedding worker's idle reclaim judges "abandoned" by a wall-clock age, and
its own docstring justifies that age by saying a live worker may still hold the row. If a pause
longer than the ten-minute threshold could make the worker reclaim rows it is still holding,
every such pause would charge sources an attempt and hand them out twice.

This change establishes that, in one process, it cannot; closes the one setting that would make
it possible (a second uvicorn worker process); proves both with tests that simulate the pause;
corrects the comments that made the hazard look real; and records what a pause does to every
other clock comparison in the product.

## Premises

**P1. Inside Docker Desktop on this Mac, a host sleep freezes the container's monotonic and boot
clocks and not its wall clock.** Measured 2026-10-01 with a throwaway container
(`docker run --rm glosswork/glosswork:0.1.0 python -c ...`, exit 0): `CLOCK_MONOTONIC`,
`CLOCK_BOOTTIME` and `/proc/uptime` all read 566,833 s, while the Docker VM's host process
(`com.docker.virtualization`) had started 695,246 s earlier by the host clock (`ps -o lstart`).
The container's wall clock agreed with the host's to within a second. The host's power log
(`pmset -g log`, which starts 2026-09-24 09:56) records 222 sleeps totalling 106,843 s in that
window, against the 128,413 s the guest lost; the remainder falls in the 25 hours before the log
begins. Docker 28.4.0, kernel 6.10.14-linuxkit, arm64. So the condition is already live for
self-hosting on a laptop, and `CLOCK_BOOTTIME` gives no escape, because the guest is paused
rather than suspended and never learns it slept.

**P2. A suspended Fly machine shows the same condition.** Inherited from CP-02 run 2
(2026-09-19, the standup runbook `fly-tenant` (outside this repository), "What a resumed process carries with it"):
`/proc/uptime` read 508 s on a machine 2,240 s old, wall clock correct five seconds after
resume. Not re-measured. The same source quotes Fly: the first request after a resume "may
still be served before the clock is updated" (F5).

**P3. The idle reclaim compares wall clock with wall clock; the monotonic clock plays no part
in it.** Read at `src/glosswork/services/embedding_worker.py`: `_claim` stamps
`updated_at = moment`, `moment = now or self._clock()` (`utc_now`); `reclaim_stale` computes
`stale_before = moment - 600 s` from the same clock. The worker's only monotonic read is the
`duration_ms` of its `embedding_batch` log line. So a pause does not make the clocks disagree
inside the reclaim; it makes the gap between a claim and the next reclaim long.

**P4. In one process, only the thread that holds a batch ever runs the idle reclaim, and it runs
it only when it holds nothing.** Read: `reclaim_stale` has one caller in `src/`,
`EmbeddingWorker.tick`, which calls it only when `run_once` claimed nothing; `tick` has one
caller, `_run`, on the one worker thread; `reclaim_all` has one caller, `start`;
`EmbeddingWorker` is constructed in one place, the application lifespan in
`src/glosswork/app.py`, which never restarts it. `run_once` finishes, releases or fails every job
it claimed, **or raises**: an `Exception` escaping outside `_process`'s handler (for example
`fail_job`'s own write failing on `busy_timeout`) leaves the rest of the batch `running`, the
thread survives, and those rows are exactly what the idle reclaim exists for (F7). A
`BaseException` kills the thread instead, and only the next startup recovers its rows (measured
by the adversarial pass: a provider raising `SystemExit`, clock advanced 1,200 s, 3.5 s later
the thread was dead with 3 rows `running`). Measured for the pause: the real worker thread over
four sources, with the injected wall clock jumping 1,200 s inside the second source's embedding
and the batch then held for three idle polls of real time, embedded every source once and
reclaimed nothing (scratch test, exit 0).

**P5. The hazard needs a second reclaimer.** Measured: the same test against a mutation that
starts a second thread in `start()` calling `reclaim_stale` every idle poll failed,
`a row the worker still held was reclaimed`, `assert 3 == 0`, exit 1. Two workers on one database,
one claiming, the clock jumped 1,201 s, the other ticking: the second reclaimed the live row and
charged it an attempt (`('pending', 1, 'reclaimed after exceeding the running timeout')`). Done
in sequence, the source was embedded once because the first worker reused the second's chunks
by hash; two processes working at the same moment would both call the model (F8).

**P6. A second process, and so the hazard, is one environment variable away.**
`src/glosswork/entrypoint.py` calls `uvicorn.run("glosswork.app:app", ...)` with no `workers`,
and uvicorn reads `WEB_CONCURRENCY` when `workers is None` (`uvicorn/config.py:352`, read in the
repo's `.venv`). Measured by the adversarial pass: `docker run -e WEB_CONCURRENCY=2` on the
`main` image logs `Started server process` twice and `embedding_worker_started` twice, two
workers on one database. ARCHITECTURE, DD-5, DEPLOYMENT and FR-P1 all say one process, and
nothing enforces it. It also splits the in-memory login limiter in two (F1).

**P7. So, on `main`, the flaw PROD-44 describes exists only in a deployment that sets
`WEB_CONCURRENCY` above 1.** It follows from P3 to P6. Its source is the docstring of
`SqliteSearchRepository.reclaim_stale` and the comment in `EmbeddingWorker.start`, which say
the threshold exists because "a live worker may genuinely hold the row", and the same claim
repeated in the standup runbook `fly-tenant` (outside this repository).

**P8. A real laptop sleep has not been observed against the worker yet, and cannot show the flaw
on this code.** `standup/drafts/prod44_sleep_probe.py` runs a throwaway container
(`glosswork-sleep-probe`) with minutes of queued work for Chris to sleep the Mac across. On `main`
the only reclaimer runs after the queue drains, when nothing is held, so the probe cannot exit 1
by construction (F4): it is a fence, and its value is the clock numbers it records for
DEPLOYMENT. Dry-run without a sleep on `glosswork:prod44-before-5988eef` (built 2026-10-01 from
`5988eef`): 150 notes, 1,500 chunks, 6 batches, 0 reclaims, and it refused to call the run a test
(exit 2) because no clock had frozen. Its validity bar is one batch holding its rows through a
single freeze of at least 600 s, because this Mac's power nap and dark wakes (200 against 226
sleeps in the log, wake requests every 926 to 961 s) can cut one lid-close into pieces.

**P9. Every other clock comparison, and what a pause does to it.** Found by
`grep -rnE "monotonic|perf_counter|\.wait\(|join\(timeout|time\.sleep|time\.time\(\)" src/glosswork`,
reading each `utc_now` comparison, and (adversarial pass) the third-party code on the auth path:

| Where | Clock | What a pause does | Verdict |
| --- | --- | --- | --- |
| `services/rate_limit.py`, login and password-change windows | monotonic | A window does not age while paused, so a lockout in force at the pause lasts its full 300 s of running time after it. `Retry-After` stays true. | Acceptable: errs toward refusing. Unchanged, documented. |
| `services/sign_in_codes.py`, send throttles (hour and day windows) | wall | Age across a pause, as a person expects. | Correct. Unchanged. |
| Sessions, tokens, upload tickets, sign-in codes, invites, embedding backoff, stale reclaim | wall | Age advances across a pause. If the first request after a resume is served before the guest's clock is set (P2), an expiry that fell during the pause reads as not yet passed for that request. | Correct once the clock is set; the window is the platform's, documented. |
| `services/oidc.py`, ID-token `exp` and `iat` (PyJWT, leeway 0) | wall, against the identity provider's | In the same window a sign-in can be refused as "not yet valid". | Documented. A leeway is a separate change, not this one. |
| PyJWT's JWKS cache TTL | monotonic | Does not age while paused; a key ID it does not know triggers a refresh anyway. | Correct. |
| Worker idle poll, usage flush interval (`Event.wait`; CPython 3.13 uses `CLOCK_MONOTONIC`) | monotonic | Waits out its interval in running time after resume. | Correct. |
| `duration_ms` of batches and requests, backup `snapshot_ms`, export `elapsed_ms` | monotonic | A unit of work that straddles a pause logs its running time. | Correct; documented so a log reader is not surprised. |
| Field fan-out `time.sleep`, `join(timeout)` in both `stop()`s, SQLite busy handler | monotonic or counted sleeps | Only lengthens or not at all. | Not affected. |

## What changes

1. **The entry point pins one uvicorn worker** (subject to Chris's choice, see "Open question"):
   `workers=1` in `uvicorn.run` in `src/glosswork/entrypoint.py`, with a comment that the product
   is one process by design (DD-35's reclaim at startup, the in-memory limiter and usage counter)
   and that this is why `WEB_CONCURRENCY` is ignored. A test asserts `uvicorn.run` receives
   `workers=1` even with `WEB_CONCURRENCY=2` in the environment.
2. **Two tests** in `tests/test_embedding_worker.py`, under "reclaim", replacing the threaded
   test of run 1's draft (F2, F3):
   - `test_a_pause_longer_than_the_timeout_inside_a_batch_charges_nothing`: synchronous. A SQLite
     trigger records every update to `embedding_jobs` that raises `attempts`, so a charge from any
     writer is seen, whatever repository instance it uses. `reclaim_all()`, then `tick()` over four
     sources with a provider that advances the injected wall clock `2 * RUNNING_TIMEOUT_SECONDS`
     inside the second source, then an idle `tick()`. Asserts the trigger recorded nothing, no job
     is left, and each source was embedded exactly once. No real sleep.
   - `test_only_the_worker_thread_reclaims`: an AST walk over `src/`, in the pattern of
     `tests/test_one_principal_resolver.py`, asserting `reclaim_stale` is called only from
     `EmbeddingWorker.tick`, `reclaim_all` only from `EmbeddingWorker.start`, `tick` only from
     `EmbeddingWorker._run`, and `EmbeddingWorker(` constructed exactly once. This is what catches
     a second reclaimer of any period.
   Both pass on this tree, and the docstrings say what each guards. Each is shown able to fail
   against its own mutation (checklist steps 3 and 4).
3. **The comments that state the false premise are corrected**, in place: `EmbeddingWorker.start`
   and `SqliteSearchRepository.reclaim_stale`. They say instead that the idle reclaim runs only
   on the worker's own thread, between batches, while it holds nothing, so a row it finds
   `running` was abandoned by that same process (an exception escaping `run_once` outside
   `_process`'s handler); that the threshold is margin, not protection for a live holder; that a
   pause of any length cannot make the worker reclaim its own work; and that a second process on
   one database is unsupported, not impossible. The `BaseException` example is replaced with the
   real one, and the docstring says a `BaseException` kills the thread so only the next startup
   recovers its rows (F7).
4. **docs/DATA_MODEL.md section 10, rule 5** gets the same correction, including the example,
   and names both new tests. DD-35's "Held by" gains the AST test.
5. **docs/DEPLOYMENT.md section 2a** gets a short subsection, "Pausing is not stopping": a laptop
   sleep or a suspended machine freezes the container's monotonic clock; the wall clock is set on
   resume, and on Fly may lag for the first request, during which expiries read early and an OIDC
   sign-in can be refused; the indexing queue is unaffected, with the reason; login lockouts do
   not age while paused; logged durations exclude the pause. Plus P1's measured numbers, and the
   sleep probe's if Chris has run it. It also says the image runs one process and ignores
   `WEB_CONCURRENCY`.
6. **Outside the product repo**, in the same run as the closeout: the standup runbook `fly-tenant`
   lines 584 to 590 are corrected so the claim that the worker "does exactly this" does not
   survive in the document that started the task (F8).

## What does not change

- **The ten-minute threshold and both reclaim paths.** No failure argues for changing them.
- **The rate limiter's clock.** Kept monotonic (P9).
- **No logic in `src/` changes** other than the one `workers=1` argument.
- **No new setting, no migration, no change to any API, MCP tool or error.**
- **No OIDC leeway.** P9 records the window; changing token validation is its own decision.
- **The sleep probe stays out of the product.** It is Mac- and Docker-Desktop-specific and needs
  a person to close a lid.

## Constraints

- One worker per database stays the invariant (DD-35); this change enforces it rather than
  relaxing it.
- Existing migrations are never edited (none are touched).
- Every new assertion is run against the unfixed tree first: the two worker tests pass there
  (they are fences for the worker) and must fail against their mutations; the `workers=1` test
  must fail on the unfixed entry point.
- Both halves of the backend lint, read separately.

## Checklist

1. Add the `workers=1` test. Run it on the unfixed tree: it fails. Record how.
2. Add the two worker tests. Run them on the unfixed tree: they pass. Record.
3. Mutation for the synchronous test: a `reclaim_stale()` call after each source in `run_once`.
   Run it: fails on the trigger's record. Revert; `git diff -- src/` empty.
4. Mutation for the AST test: a sweeper thread in `start()` calling `reclaim_stale` every 60 s.
   Run it: fails naming the extra caller. Revert; `git diff -- src/` empty.
5. Pin `workers=1` in `src/glosswork/entrypoint.py`. Step 1's test passes.
6. Correct the comments and docstrings ("What changes" 3).
7. Documentation ("What changes" 4 and 5).
8. Build the branch image; `docker run -e WEB_CONCURRENCY=2` on it logs `Started server process`
   and `embedding_worker_started` once each. Remove the container and its anonymous volume.
9. Run the Accept block.

## Accept

- **AC1.** `uv run pytest -q tests/test_embedding_worker.py -k "pause_longer_than_the_timeout or only_the_worker_thread_reclaims"` exits 0.
- **AC2.** Steps 3 and 4's mutation runs each exited 1 on the intended assertion, recorded with
  output.
- **AC3.** The `workers=1` test exits 0, and exited 1 on the unfixed entry point (step 1, recorded).
- **AC4.** Step 8's container log holds exactly one `embedding_worker_started`, counted with
  `grep -c` and the count compared to 1 in the same command.
- **AC5.** `uv run pytest -q` exits 0.
- **AC6.** `uv run pytest -q -m structural` exits 0.
- **AC7.** `uv run ruff check .` exits 0, and separately `uv run ruff format --check .` exits 0.
- **AC8.** `uv run mypy src` exits 0.
- **AC9.** `standup/drafts/PROD-44-ast-same.py`, run from the repo root with
  `src/glosswork/entrypoint.py` excluded, exits 0 and lists only
  `src/glosswork/services/embedding_worker.py` and `src/glosswork/repositories/sqlite.py`: every
  other `src/` change is docstrings only.
- **Evidence, not a gate.** The laptop-sleep probe on the `main` image, if Chris runs it: the JSON
  and exit code recorded. Exit 0 or 2 changes nothing in this plan; exit 1 would falsify P4 and
  stop the change. There is no branch-image run, because the branch changes nothing the probe can
  see except `WEB_CONCURRENCY`, which AC4 covers.

## Open question for Chris

**Pin one process (`workers=1`), or only document `WEB_CONCURRENCY` as unsupported?** Pinning is
one argument and a test, and it turns "one process per database" from a sentence in four
documents into something the image enforces; a self-hoster who sets the variable gets one
process instead of a quietly broken queue and a split login limiter. Documenting only keeps
`src/` logic untouched but leaves the hazard one environment variable away. Recommended: pin.
If Chris chooses document-only, "What changes" 1, checklist steps 1, 5 and 8, AC3 and AC4 are
removed and AC9 runs with nothing excluded.

## Adversarial pass

Run 2026-10-01 by a separate Opus session that did not write the plan. It could not falsify the
single-process claim; it found that the single-process premise rests on an unguarded setting.

- **F1. `WEB_CONCURRENCY` starts a second process.** Measured (P6). **Folded:** P4 to P7
  rewritten; "What changes" 1 and the open question added.
- **F2. The draft test's counting repository saw only its own instance's reclaims.** A sweeper
  with a fresh `SqliteSearchRepository()` passed it. **Folded:** a SQLite trigger on `attempts`
  sees every writer; measured failing against that sweeper and passing on `main`.
- **F3. The threaded test cost 5.3 s of real sleep and missed any reclaimer slower than its 3 s
  hold** (4 s and 10 s sweepers passed). **Folded:** replaced by a synchronous test (0.11 s on
  `main`) and an AST call-site test (0.79 s, fails against a 60 s sweeper).
- **F4. The sleep probe could not exit 1 by construction, its chunk-count check was vacuous
  under hash reuse, its validity bar (60 s) was below the threshold, and the branch-image run
  measured nothing.** **Folded:** the probe now requires one batch spanning a freeze of at least
  600 s and the container clock within 2 s of the host's, reports only reclaim lines, and calls
  itself a fence; the branch-image run is gone; the main-image run is evidence, not a gate.
- **F5. P8's sweep missed OIDC `exp`/`iat`, the JWKS cache, the sign-in-code throttles and the
  fan-out sleep, and "the wall clock is correct on resume" ignores Fly's first-request window.**
  **Folded:** P9's table and DEPLOYMENT text.
- **F6. The draft AC7 could not fail on a code change.** **Folded:** AC9, a docstring-stripped AST
  comparison against `main`, measured exiting 0 for a docstring edit and 1 for a changed
  constant.
- **F7. The docstring being rewritten kept a false example: a `BaseException` kills the thread,
  so the idle reclaim never runs for it.** Measured. **Folded:** P4 and "What changes" 3 and 4.
- **F8. Minor.** The Fly runbook still repeats the claim (folded as "What changes" 6); P5's "not
  repeated embedding" held only in sequence (folded); P2 is inherited (already said); DD-35 is the
  less natural home than rule 5 (folded: DD-35 gets only "Held by").

## Build record

Built 2026-10-01 on `11-frozen-clock`, in checklist order. Each step's command and result:

1. `uv run pytest -q tests/test_infra.py -k web_concurrency` on the unfixed entry point: exit 1,
   `assert config.workers == 1`, `assert 2 == 1`. uvicorn's own `Config` read
   `WEB_CONCURRENCY=2`. Re-measured after D3 with the same result.
2. `uv run pytest -q tests/test_embedding_worker.py -k "pause_longer_than_the_timeout or
   only_the_worker_thread_reclaims"` on the unfixed tree: exit 0, 2 passed. Both are fences for
   the worker, as planned.
3. Mutation, `self.reclaim_stale()` after each `_process` in `run_once`: exit 1,
   `AssertionError: a row the worker still held was charged an attempt`, the trigger recording
   `(3, 1, 'reclaimed after exceeding the running timeout')` and one more. Reverted;
   `git diff -- src/ | wc -l` read 0.
4. Mutation, a `_sweep` thread started in `start()` calling `self.reclaim_stale()` every 60 s
   (the module imports, exit 0): exit 1, the extra caller named,
   `['EmbeddingWorker.start._sweep', 'EmbeddingWorker.tick', 'EmbeddingWorker.reclaim_stale']`.
   Reverted; `git diff -- src/ | wc -l` read 0.
5. `workers=1` in `src/glosswork/entrypoint.py`; step 1's test exit 0.
6. Comments and docstrings corrected in `EmbeddingWorker.start`, `EmbeddingWorker.tick` and
   `SqliteSearchRepository.reclaim_stale` (and see D2).
7. DATA_MODEL section 10 rule 5, DD-35's "Held by", and DEPLOYMENT section 2a's new "Pausing is
   not stopping".
8. `docker build -t glosswork:prod44-11-17b47f1 .`, exit 0. `docker run -d -e WEB_CONCURRENCY=2`
   on it: `Started server process` 1, `embedding_worker_started` 1, each compared to 1 with
   `[ "$n" -eq 1 ]`, exit 0. Control on the `main` image (`glosswork:prod44-before-5988eef`),
   same variable: 2 and 2. Both containers removed with `docker rm -f -v`, exit 0, and neither
   anonymous volume remains (`docker volume ls -q | grep`, exit 1).
9. Accept, run 2026-10-01 at `568eb9e`: AC1 exit 0 (2 passed); AC3 exit 0; AC5 exit 0 (2,164
   passed, 3 xfailed); AC6 exit 0 (101 passed); AC7 `ruff check .` exit 0 and, separately,
   `ruff format --check .` exit 0; AC8 exit 0 (88 source files); AC9 exit 0, listing only
   `src/glosswork/repositories/sqlite.py` and `src/glosswork/services/embedding_worker.py`.
   AC2 and AC4 are steps 3, 4 and 8 above. The laptop-sleep probe was not run; it is evidence,
   not a gate.

## Deviations from the approved plan

- **D1. The plan's two citations of standup documents are prose here.** Backticked, they name
  `.md` files outside this repository and fail `tests/test_documentation_structure.py`. The
  draft's preamble about the unknown issue number became the approval line; nothing else in the
  approved text changed.
- **D2. Two test docstrings repeated the false premise, and were corrected too.**
  `test_reclaim_runs_on_the_idle_poll_of_a_worker_that_never_restarted` gave the
  `BaseException` example, and `test_a_restart_reclaims_a_running_row_of_any_age_and_counts_an_attempt`
  said a live worker may hold the row. Retiring a superseded claim is part of the change
  (`AGENTS.md`, Traps); docstrings only.
- **D3. The `workers=1` test first broke two logging tests that ran after it.** Building
  uvicorn's `Config` with its default `log_config` installed uvicorn's own handlers, so
  `test_each_named_third_party_logger_emits_json[uvicorn]` and `[uvicorn.error]` failed in the
  full suite (2 failed, 2,162 passed) and passed alone. The test now passes `log_config` as the
  entry point does (`None`); step 1 was re-measured failing on the unfixed entry point.
- **D4. The call-site test allows each worker reclaim method to call the repository's
  same-named method.** "`reclaim_stale` only from `EmbeddingWorker.tick`" read literally would
  forbid the worker's own wrapper, which is the one delegation the design has. The allowed sets
  are `{tick, reclaim_stale}` and `{start, reclaim_all}`, matched exactly and counted, and the
  walk counts every load of the name rather than only calls, so a bound method handed to a thread
  counts too.
- **D5. DD-35's "Held by" also names `tests/test_infra.py`**, for the one-process test.

## Durable content moved out of this plan

To be filled at closeout: DATA_MODEL section 10 rule 5, DD-35, DEPLOYMENT section 2a.
