# 28: The backend tests run as four shards in CI, so a pull request's checks finish in about four minutes

| | |
| --- | --- |
| Issue | #28, <https://github.com/glosswork/glosswork/issues/28> |
| Branch | `28-parallel-backend-tests` |
| Spec | `CONTRIBUTING.md` "What CI runs" (the jobs table); `AGENTS.md` "Commands" and "Traps"; `.github/workflows/ci.yml` header; `tests/test_ci_workflow.py` |
| Decisions | none. No DD governs CI or the logging setup |
| Requirements | FR-P5 (structured JSON logs on stdout) is read, not changed: the logging fix below keeps every line's bytes and destination |
| Depends on | nothing unmerged. Branched from `origin/main` at `8daeb30` (change 26 merged) |

**Lane: full.** This changes `.github/workflows/ci.yml`, which judges every other change
(its header, and `CONTRIBUTING.md` "What CI runs"). It also changes one argument in
`src/glosswork/logging.py`, which the suite cannot be split without (P6, F1).

## Why

Every pull request waits 10 to 11 minutes for CI, and nearly all of it is one job.
`backend-test` runs the whole backend suite in one `pytest` process on one runner; every
other job finishes by minute 3.6 (P1). So the merge of any change, however small, waits
on one process.

## Judgment areas

Three things here are the maintainer's to decide. The plan is written for the
recommended answer to each; each area says what changes under the other answer.
Approving the plan as written approves those three answers.

### 1. How the suite is split: four shard jobs, or two shard jobs each running two workers

The runner this private repository gets has **2 vCPUs** (P3), which is what decides this.
Running the suite in parallel inside one job (`pytest-xdist`, two workers on two CPUs)
measured only **1.34 times** faster (P8), which projects to an 8-minute job: it misses the
6-minute target on its own. Splitting the suite across separate runners is what reaches it.

| | A. Four shard jobs (recommended) | B. Two shard jobs, two workers each |
| --- | --- | --- |
| How | Four jobs, `backend-test-1` to `backend-test-4`, each running a quarter of the tests in one plain `pytest` process | Two jobs, each running half the tests under `pytest-xdist -n auto` |
| Projected pull request time, created to completed | about 4 minutes, in a band of 3.3 to 4.2; the floor is 3.2 to 3.9, set by `frontend-test` and `e2e` (P9, F4) | about 4.9 minutes (P9) |
| Billed runner minutes per run, backend part | about 16, up from 11 today: about +5 a run (P10) | about 10: about 1 fewer a run (P10) |
| New dependency | none | `pytest-xdist==3.8.0` and `execnet==2.1.2`, dev only (P11) |
| New hazards | none beyond P6 | two test processes share one runner: the probe-then-bind port race (P7) and the two fixed time budgets (P7) become live |
| When the repository goes public (4 vCPUs, minutes free) | unchanged; still about 3.9 | `-n auto` picks 4 workers and it speeds up by itself |

**Recommended: A.** It is the shortest wait, it adds no dependency, every shard is the same
`pytest` command CI runs today, and no two test processes ever share a machine, so the
hazards in P7 stay dormant. The cost is about 5 more billed minutes per CI run. At this
week's rate of about 23 CI runs in 5 days (P10) that is roughly 700 minutes a month, which
is about $4 a month at $0.006 a minute if the organisation is past its 3,000 included
minutes (P10). **Under B**: the four jobs below become two, each with `--shard k/2` and
`-n auto`; `pytest-xdist==3.8.0` is added to `[dependency-groups] dev` with
`UV_NO_CONFIG=1 uv lock` and `THIRD_PARTY_LICENSES.md` is left as it is (P11); AC5's loop
runs 2 shards, AC11 changes to expect the two packages; P7's two time budgets get a
checklist item of their own.

### 2. The logging fix touches product code

The suite cannot be split today. Run any file first in a fresh process and three files fail
on `main`, and two of four shards fail, all with the same `ValueError: I/O operation on
closed file` (P6). The cause is in `src/glosswork/logging.py`: structlog's logger factory is
handed `sys.stdout` as it is at the moment `configure_logging` runs, and loggers cache that
stream on first use. When that moment is inside a test that captures output, the cached
stream is the test's capture buffer, which pytest closes when the test ends. Every later
log line from that logger, in any test in the same process, raises. Serial CI passes today
only because of the order the files happen to run in.

**Recommended: fix it in the product, by deleting one argument** (changed by the
adversarial pass, F1). `structlog.PrintLoggerFactory(file=sys.stdout)` becomes
`structlog.PrintLoggerFactory()`. With no file given, structlog 26.1.0 prints to whatever
`sys.stdout` is at the time of each line (`structlog/_output.py`, `PrintLogger.msg`). A
deployment already runs that exact code path today, because there the stream handed in is
the one structlog saw at import, and structlog treats that case as "no file given". So in a
deployment nothing changes at all: not the bytes, not the destination (FR-P5), not the code
that runs. Measured, the change makes the three files, the four shards and the whole suite
pass (F1).

**The first alternative** is the plan's original answer: a small writer class whose `write`
and `flush` look up `sys.stdout` on each call. It also makes everything pass (F1), and it
does not lean on how structlog treats a missing file. It costs a new class, and it changes
one thing in a deployment: a logger can no longer be copied with `copy.deepcopy` or pickled
(`Only PrintLoggers to sys.stdout and sys.stderr can be deepcopied`, F1), which works
today. Nothing in `src/` does either to a logger now. Under that answer, checklist item 2
adds the class and `tests/test_logging_stream.py` drops its copy assertion.

**The second alternative** is a test-side fixture that resets structlog's cached loggers
around every test. It leaves the product untouched, but it reaches into structlog's
private caching on proxies that modules create at import, and it would hide the same
failure in any future tool that swaps `sys.stdout`. Under that answer, checklist item 2
becomes an autouse fixture in `tests/conftest.py` and `src/glosswork/logging.py` is not
touched.

### 3. Where the second timed run comes from

The task's measure is two consecutive pull request runs, each under 6 minutes. The pull
request is opened once, at closeout, and runs CI once. **Recommended:** after that run
completes, the dispatcher re-runs it once with `gh run rerun <id>`, on the same commit, so
the second number measures the runner and not a different change. That costs one more run,
about 33 billed minutes under answer A (P10). Two things about a re-run are not
established, because this repository has never had one (F5): whether the agent's token may
start it, and whether the four junit uploads succeed on a second attempt. If the token is
refused, Chris presses "Re-run all jobs" on the run's page. If the re-run fails for a reason
that is not a test, the second number comes from the next pull request instead. **The
alternative** is to read the next pull request's run
instead, which costs nothing extra and arrives whenever the next change does.

## Premises

Measured 2026-10-02 unless stated. "Locally" is Chris's Mac (Apple M2 Max, 12 cores,
`sysctl -n hw.ncpu`). "The two-CPU container" is `python:3.13-slim-bookworm` with uv
0.12.19 and Node 22, run as `docker run --cpuset-cpus 0,1 --memory 8g` with the repository
mounted read-only (`nproc` inside reads `2`). It is arm64 Linux rather than the runner's
x64, so its absolute times are not the runner's; its ratios are the evidence.

- **P1. The time is the tests, not the setup.** Read from
  `gh api repos/glosswork/glosswork/actions/runs/37032041101/jobs` (pull request 22). The
  run took 10.9 minutes, created 16:09:52 to completed 16:20:47. `backend-test` started at
  +11 s and ran 10.63 minutes. Its steps: checkout, uv, Node, model cache, `uv sync`,
  `npm ci` and the model fetch took 32 s together (16:10:04 to 16:10:36); `uv run pytest -q
  --junitxml=report.xml` took 9 minutes 58 seconds (16:10:36 to 16:20:34); the upload and
  post steps 3 s. The other jobs ended at: `frontend-test` +3.57 minutes, `e2e` +3.48,
  `sast` +1.17, `frontend-lint` +1.05, `image` +0.95, `backend-lint` +0.72, `guards` +0.48,
  `secrets` +0.17, `changes` +0.13; `ci-ok` started when `backend-test` ended and ran
  2 s. Across the 20 CI runs since 2026-09-28 in which `backend-test` succeeded, it took
  8.05 to 11.73 minutes, median 9.86 (corrected by F4, which re-read every run; the 5.0
  was the job of a cancelled run).
- **P2. The suite is 2,220 tests and takes 285 s locally, serially.**
  `uv run pytest -q --collect-only` on `8daeb30`: `2220 tests collected`. `uv run pytest -q
  -p no:cacheprovider --junitxml=...`: exit 0, `2217 passed, 3 xfailed`, 285.34 s. The
  runner's junit for P1's run (artifact `backend-junit`, downloaded with `gh run download
  37032041101 -n backend-junit`) reports 2,218 tests, `time="592.560"`, per-test times
  summing to 588 s: the runner is about 2.07 times slower per test than this Mac. The
  slowest file is `tests/test_seed_perf.py`, 88 s over 13 tests; the slowest test is
  `tests/test_csv_import_export.py::TestImportBounds::test_a_file_at_the_row_ceiling_is_accepted`,
  22.7 s.
- **P3. This repository's runners have 2 vCPUs and 8 GB.** `gh api
  repos/glosswork/glosswork --jq .visibility` reads `private`. GitHub's runner reference
  (<https://docs.github.com/en/actions/reference/runners/github-hosted-runners>, read
  2026-10-02) lists `ubuntu-24.04` for private repositories as 2 CPUs, 8 GB, and for public
  ones as 4 CPUs, 16 GB.
- **P4. A hash of the test's node id splits the suite evenly enough, and exactly.** A probe
  plugin (kept in the session's scratch space, not in the repository) keeps a test when
  `zlib.crc32(item.nodeid.encode()) % N == k - 1` and reports the rest as deselected. With
  N = 4 it collected 550, 560, 556 and 554 tests; the four sorted node-id lists
  concatenated and sorted are byte-identical to the unsharded `--collect-only` list (`cmp`
  exit 0; 2,220 lines, 2,220 unique). Applied to the runner's per-test times from P2, the
  four shards would take 171 s for the largest and 133 s for the smallest, against an ideal of 147 s each;
  splitting by file instead gives 213 s for the largest, because `tests/test_seed_perf.py`
  is 88 s on its own. A balanced split by recorded durations (the `pytest-split` approach)
  would reach 147 but needs a durations file kept current; 24 s is not worth that.
- **P5. No test writes into the repository tree.** The serial run in the two-CPU
  container, with the repository mounted read-only and `-p no:cacheprovider`, exited 0 with
  `2217 passed, 3 xfailed`. `grep` over `tests/` finds no fixed `/tmp` path and no
  `tempfile` call outside `tmp_path`.
- **P6. Today the suite depends on the order its files run in, and one cause explains every
  failure.** On unmodified `8daeb30`:
  - Each of the 118 test files run alone (`uv run pytest -q -p no:cacheprovider <file>`,
    six at a time): 115 exit 0; `tests/test_schema_engine.py` (1 failed),
    `tests/test_mcp_transport.py` (3 failed, 17 errors) and
    `tests/test_rest_actor_resolution.py` (1 failed, 2 errors) exit 1.
  - The four hash shards of P4, run concurrently: shards 1 and 2 exit 0; shard 3 exits 1
    (3 failed: two in `tests/test_search_reindex.py`, one in
    `tests/test_search_index_maintenance.py`) and shard 4 exits 1 (4 failed: in
    `tests/test_backup.py`, `tests/test_operator_backup.py` and `tests/test_read_only_mode.py`).
  - `pytest-xdist -n 4 --dist loadfile`: exit 1 twice, 6 failed and 93 errors, then 14
    failed and 75 errors, each time all on one worker; in the second run that worker
    began with `tests/test_schema_engine.py`.
  - Every one of those failures is `ValueError: I/O operation on closed file.`, raised from
    `structlog/_output.py:113`. The cause, read at `src/glosswork/logging.py:59-60`:
    `structlog.PrintLoggerFactory(file=sys.stdout)` with `cache_logger_on_first_use=True`.
    The first time a module-level logger logs, it caches a printer bound to whatever
    `sys.stdout` was when `configure_logging` last ran; `create_app` calls
    `configure_logging`. Reproduced outside pytest: swap `sys.stdout` for a `StringIO`,
    `configure_logging("info")`, log once from `get_logger("probe.module")`, restore and
    close the `StringIO`, `configure_logging("info")` again, log again: `ValueError: I/O
    operation on closed file`, exit 1.
  - With `PrintLoggerFactory(file=...)` given an object whose `write` and `flush` call
    `sys.stdout.write` and `sys.stdout.flush` at the time of the call (applied to the
    working tree, then reverted with the file restored from a copy; `git status` clean
    after): the reproduction prints the log line and exits 0; the three files alone exit 0;
    `tests/test_infra.py`, `tests/test_logs_and_host_allowlist.py`,
    `tests/test_embedding_worker.py` and `tests/test_relay_driver.py`, the modules that
    assert on log output, exit 0 alone; the four shards exit 0 with 549, 559, 553 and 556
    passed (2,217) and 3 xfailed between them; `-n 4 --dist loadfile` exits 0 with `2217
    passed, 3 xfailed`; `uv run mypy src/glosswork/logging.py` reads `Success`.
  - The stdlib root handler on the next line, `logging.StreamHandler(sys.stdout)`, has the
    same binding but is replaced on every `configure_logging` call and reports a write
    error instead of raising it; nothing measured fails because of it. It is left alone.
  - Corrected by F1: the stream is not what a logger caches in a deployment. `PrintLogger`
    compares the file it was given with the `sys.stdout` structlog saw at import; when they
    are the same object it calls `print` with no file, which looks `sys.stdout` up per
    line. Only a file that is some other object is held and written to directly, and inside
    a test using `capfd` or `capsys` it is.
  - A second order dependence is left in place (F2). A cached logger also keeps the level
    it was first used at, so a module logger first used under `info` drops `debug` lines
    after a later `configure_logging("debug")`. No test fails because of it today, in any
    shard or alone.
- **P7. Two hazards are dormant in separate jobs and live when tests share a machine.**
  Read, not triggered: `tests/test_backup.py:397`, `tests/test_sign_in_codes.py:419` and
  `tests/fake_relay.py:280` pick a port by binding to port 0, closing the socket, then
  starting uvicorn on that number, so a second process on the same host can take it in
  between. `tests/test_relay_driver.py:336` asserts a call returns in under 0.5 s and
  `tests/test_sign_in_codes.py:448` in under 1.5 s, against wall time. Neither failed in any
  run in this plan, including `-n 2` in the two-CPU container and `-n 8` locally.
- **P8. Two workers on two CPUs are 1.34 times faster, not twice.** In the two-CPU
  container with P6's fix overlaid read-only on `src/glosswork/logging.py`: serial, exit 0,
  343.65 s; `pytest-xdist==3.8.0 -n 2`, exit 0, 257.11 s (1.34x); shard 1 of 2, 189.59 s;
  shard 1 of 2 with `-n 2`, 140.06 s (1.35x); shard 1 of 4, 88.03 s. An earlier run with
  `--cpus 2` (a quota, `nproc` reading 12) gave 301 s serial and 261 s with `-n 2`; it is
  not used, because the model's thread pool sized itself to 12. Locally, with 12 cores:
  `-n 2` 160 s, `-n 4` 117 s, `-n 8` 107 s and 98 s, all `2217 passed, 3 xfailed`.
- **P9. Projected times, not measured.** Runner time per shard is the runner's per-test
  time (P2) for that shard plus 10 s of startup and collection (P2: 598 s step against
  588 s of tests) plus 35 s of setup and teardown (P1). Under A the slowest shard is
  171 + 10 + 35 = 216 s, about 3.6 minutes, starting at +0.18 minutes, so it ends near
  +3.8; `frontend-test` ends at +3.57 (P1) and `ci-ok` adds a few seconds: about 3.9
  minutes created to completed, against a floor of about 3.7 set by `frontend-test`.
  Under B the container's shard-of-2-with-two-workers time (140 s) scaled by the runner's
  1.74 times the container's serial time (598 / 344) is 244 s, plus 45 s: about 4.8
  minutes per job, 4.9 for the run. Under xdist alone, 257 s x 1.74 = 447 s plus 45 s:
  about 8.2 minutes, which misses the target. These hold only if the runner's ratios are
  the container's; AC12 is where they are measured. F4 puts a band on the figure for A:
  3.3 to 4.2 minutes.
- **P10. What it costs.** The organisation is on GitHub Team (`gh api orgs/glosswork --jq
  .plan.name` reads `team`), which includes 3,000 Actions minutes a month, with Linux
  2-core minutes beyond that at $0.006
  (<https://docs.github.com/en/billing/concepts/product-billing/github-actions>, read
  2026-10-02). The agent's token cannot read the organisation's usage (`gh api
  organizations/glosswork/settings/billing/usage/summary` answers 404). From the jobs API,
  the 23 CI runs created 2026-09-28 to 2026-10-02 add up to about 585 job-minutes when each
  job is rounded up to a whole minute, about 28 a run with `backend-test` at 11 of them.
  That rounding is an assumption: the billing page read does not say whether jobs are
  rounded individually. Under A, `backend-test`'s 11 becomes 4 x 4 = 16; under B, 2 x 5 = 10.
- **P11. A dev dependency stays out of the image and the licence file.** In a scratch copy
  of `pyproject.toml` and `uv.lock`, adding `"pytest-xdist==3.8.0"` to `[dependency-groups]
  dev` and running `UV_NO_CONFIG=1 uv lock` added two packages (`execnet` 2.1.2,
  `pytest-xdist` 3.8.0), 22 lines and none removed (`git diff --no-index --text`), with all
  72 `registry =` lines on `https://pypi.org/simple`; `uv export --frozen --no-dev
  --no-hashes --no-emit-project`, which is what `scripts/third_party_licenses.py:147` reads,
  names neither. The `Dockerfile` installs with `uv sync --frozen --no-dev` (lines 27 and
  30). Versions read from <https://pypi.org/pypi/pytest-xdist/json> (3.8.0, uploaded
  2025-07-01, MIT, requires `execnet>=2.1` and `pytest>=7.0.0`) and `uv run --with pip pip
  index versions` for `execnet` (2.1.2) and `pytest-split` (0.11.0), 2026-10-02. Under A
  none of this is used.
- **P12. A `--shard` option in `tests/conftest.py` is read when `pytest` runs from the
  repository root.** Probe: `pytest_addoption` adding `--shard` appended to
  `tests/conftest.py`, then `uv run pytest -q --collect-only -p no:cacheprovider --shard
  1/4` from the root: exit 0, `2220 tests collected`; file restored after. `tests/conftest.py`
  defines no `pytest_addoption` or `pytest_collection_modifyitems` today (`grep`). A command
  line option is used rather than an environment variable so that the shard is visible in
  the workflow's `run:` line, where `tests/test_ci_workflow.py` can read it, and so a
  `pytest` without this conftest refuses the unknown option instead of silently running
  everything.
- **P13. Nothing else names the job.** `git grep -n "backend-test\|backend-junit"`
  finds `.github/workflows/ci.yml` (the job, its artifact, `ci-ok`'s `needs` and its loop),
  `CONTRIBUTING.md:157` and `tests/test_ci_workflow.py:34`. The ruleset
  (`scripts/github/ruleset-main.json`) requires `ci-ok`, `guards`, `secrets` and `sast`
  only, and `test_every_required_check_is_a_job_that_cannot_pass_by_being_skipped` keeps it
  that way, so renaming the job needs no ruleset change.
- **P14. Matrix legs are not used, because what `needs` reports for one is not documented.**
  GitHub's contexts reference and its matrix how-to (read 2026-10-02) do not say how a
  matrix job's legs combine into `needs.<job>.result`. `ci-ok` judges each job by that
  value, so the parts are four named jobs, as `.github/workflows/release.yml` already does
  for its two builds ("Two build jobs rather than a matrix", its header) with a test that
  pins their steps identical. That is also what lets a mutation drop one part from
  `ci-ok`'s `needs`.

## What changes

- `src/glosswork/logging.py`: `configure_logging` calls `structlog.PrintLoggerFactory()`
  with no `file` argument, so each line goes to `sys.stdout` as it is when the line is
  written (P6, F1). One argument deleted, and a comment saying why it must stay deleted.
- `tests/test_logging_stream.py` (new): a module logger first used while `sys.stdout` was a
  test's buffer still writes after that buffer is closed and logging is configured again
  (P6's reproduction, as a test); the line lands in the stream that is current when it is
  written; and a bound logger can still be deep-copied. The first of these is also what
  fails if a structlog upgrade ever stops looking `sys.stdout` up per line (F1).
- `tests/conftest.py`: `pytest_addoption` adds `--shard k/N`; `pytest_collection_modifyitems`
  keeps the tests whose `zlib.crc32(nodeid) % N == k - 1` and reports the rest through
  `pytest_deselected`. Without `--shard`, nothing changes. A value that is not two positive
  integers with `1 <= k <= N` raises `pytest.UsageError` naming the value.
- `.github/workflows/ci.yml`: `backend-test` becomes `backend-test-1` to `backend-test-4`,
  each the same steps as today with `--shard k/4` on the `pytest` line and the artifact
  named `backend-junit-k`. `ci-ok` needs all four, and its heavy loop names all four. The
  header says why the parts are separate jobs and not a matrix (P14). Each shard's
  `timeout-minutes` is 15, not today's 30: about four times its projected length, and what
  `frontend-test` already uses (F7).
- `tests/test_ci_workflow.py`: `HEAVY` names the four jobs in place of `backend-test`. New
  rules: the shard jobs are exactly `backend-test-1..N` for one N, their steps are identical
  except the `pytest` line's `--shard k/N` and the artifact name, each `k` from 1 to N
  appears once, and every shard's `pytest` line is otherwise today's line exactly; and,
  reading N from the workflow, `--collect-only` with each `--shard k/N` gives non-empty,
  pairwise disjoint sets whose union is the unsharded collection. That last rule is one
  test function of its own. Two rules added by F3, because "identical steps" cannot see a
  change made to all four shards alike: a shard job carries no `continue-on-error` and no
  `env`, and its `pytest` step has no key but `run`; and no `PYTEST_ADDOPTS` appears
  anywhere in the workflow.
- `scripts/mutate_ci_workflow.py` (new), on the pattern of
  `scripts/mutate_release_workflow.py`: applies each mutation below to a copy of the
  workflow in a temporary directory, runs `tests/test_ci_workflow.py` against it, and
  exits 1 unless the unmutated copy is green and every mutation turns it red. Mutations:
  `backend-test-3` dropped from `ci-ok`'s `needs`; dropped from the heavy loop; deleted
  entirely; two jobs both `--shard 2/4`; one job `--shard 4/5`; one job without `--shard`;
  one shard given `-m "not structural"`; one shard `continue-on-error: true`; one shard's
  `if` removed; one shard's `--collect-only` added. Added by F3, each applied to all four
  shards at once: `continue-on-error: true` on the `pytest` step; `-x --maxfail=1 || true`
  appended to the `pytest` line; `PYTEST_ADDOPTS: "--collect-only"` in the workflow's `env`.
  The script deselects the partition rule when it runs a mutation, since no mutation
  depends on it and it costs five collections each time (F6).
- The same script also **runs** the gate, which no test does today (F3): it takes `ci-ok`'s
  `run` script from the workflow, feeds it made-up job results through `NEEDS`, and exits 1
  unless the script exits 0 for an all-green code change and for an all-skipped
  documentation change, and exits 1 for each of: one shard `failure`, one shard `skipped`
  on a code change, one shard `cancelled`, one shard `success` on a documentation change,
  and one shard missing from `needs`. It needs `bash` and `jq`.
- `CONTRIBUTING.md` "What CI runs": the `backend-test` row becomes the four shards, with one
  sentence on how tests are assigned and that the whole suite still runs once.
- `AGENTS.md`: the Commands table gains "Run one CI shard locally": `uv run pytest -q
  --shard 1/4`. Traps gains P6: a logger bound to `sys.stdout` at configure time outlives
  the test that configured it, so a file that passes in the suite can fail alone.

## What does not change

- What each test does, and which tests exist. No test is skipped, marked or moved.
- The `guards` job, `-m structural`, and every other job in `ci.yml`.
- `uv run pytest -q` with no option: it still runs every test, in one process.
- `pyproject.toml`, `uv.lock`, `THIRD_PARTY_LICENSES.md` and the image, under answer A.
- The ruleset and the required checks (P13).
- Log output in a deployment: same bytes, same stream (FR-P5).
- `.github/workflows/release.yml` and `container_tests/`.

## Constraints

- `ci-ok` stays the only required gate and still judges every job, each shard included.
- Every test runs exactly once across the shards: never zero, never twice.
- Every action stays pinned by SHA (rule 6); no new action is introduced.
- Commits are `Glosswork <hello@glosswork.dev>`; the branch keeps `e5a047b` as its only root.

## Checklist

1. Write `tests/test_logging_stream.py`, the `tests/test_ci_workflow.py` changes and
   `scripts/mutate_ci_workflow.py`. Run each against the unfixed tree and record how it
   failed: the logging test with the `ValueError`; the workflow rules because the four jobs
   do not exist; the partition rule because `--shard` is not an option. Record the
   mutation script's output.
2. Fix `src/glosswork/logging.py` (judgment area 2): delete the `file` argument. Run
   `tests/test_logging_stream.py` and the three files of P6 alone, and the four
   logging-asserting modules of P6.
3. Add `--shard` to `tests/conftest.py`. Refuse a bad value in `pytest_configure`, before
   anything is collected (F6). Run AC5 and AC6.
4. Change `.github/workflows/ci.yml`. Run `tests/test_ci_workflow.py` and
   `scripts/mutate_ci_workflow.py`.
5. Update `CONTRIBUTING.md` and `AGENTS.md`.
6. Once, at the end: the whole suite, each shard, and the Accept block.

## Accept

Run from the repository root. Each criterion's exit code is read on its own, never through
a pipe.

- **AC1. The logging regression test passes.** `uv run pytest -q -p no:cacheprovider
  tests/test_logging_stream.py`; exit 0. (It failed in checklist step 1.)
- **AC2. The three files that fail alone today pass alone.** For each of
  `tests/test_schema_engine.py`, `tests/test_mcp_transport.py`,
  `tests/test_rest_actor_resolution.py`: `uv run pytest -q -p no:cacheprovider <file>`;
  exit 0 each.
- **AC3. The workflow rules pass, and every mutation breaks them.** `uv run pytest -q
  -p no:cacheprovider tests/test_ci_workflow.py`; exit 0. `uv run python
  scripts/mutate_ci_workflow.py`; exit 0, with every mutation line reading `exit 1 (want 1)`
  and every gate case reading the exit code it wants (F3).
- **AC4. The structural lane passes.** `uv run pytest -q -m structural`; exit 0.
- **AC5. The shards partition the collection.**
  `uv run pytest -q --collect-only -p no:cacheprovider | grep '::' | sort > /tmp/gw28-all.txt`;
  then `for k in 1 2 3 4; do uv run pytest -q --collect-only -p no:cacheprovider --shard
  $k/4 | grep '::'; done | sort > /tmp/gw28-shards.txt`; then `cmp /tmp/gw28-all.txt
  /tmp/gw28-shards.txt`, exit 0, and `sort -u /tmp/gw28-shards.txt | wc -l` equals
  `wc -l < /tmp/gw28-all.txt`.
- **AC6. A bad shard is refused.** `uv run pytest -q --collect-only --shard 5/4`,
  `--shard 0/4`, `--shard 1of4`: each exits 4 (usage error), naming the value.
- **AC7. Each shard passes.** `uv run pytest -q -p no:cacheprovider --shard k/4` for k = 1
  to 4; exit 0 each; the four `passed` counts plus the `xfailed` counts sum to the
  collection count from AC5.
- **AC8. The unsharded suite passes.** `uv run pytest -q`; exit 0.
- **AC9. Lint and types, each half on its own.** `uv run ruff check .`; exit 0. `uv run
  ruff format --check .`; exit 0. `uv run mypy src`; exit 0.
- **AC10. The workflow is valid.** actionlint 1.7.12 for this machine's platform, from
  <https://github.com/rhysd/actionlint/releases/tag/v1.7.12> and checked against that
  release's published checksums, run as `actionlint -color .github/workflows/ci.yml`;
  exit 0. (`guards` runs the same version on Linux in CI.)
- **AC11. No dependency changed.** `git diff --exit-code origin/main -- pyproject.toml
  uv.lock THIRD_PARTY_LICENSES.md`; exit 0.
- **AC12. On GitHub, read by the dispatcher after the pull request's run (not by the
  verifier).** For the pull request's CI run and for the second run of judgment area 3:
  `gh api repos/glosswork/glosswork/actions/runs/<id> --jq '[.run_attempt,
  .run_started_at, .updated_at, .conclusion]'` shows `success` and under 6 minutes between
  the two times. The start is `run_started_at`, not `created_at`: a re-run keeps the first
  attempt's `created_at`, so the second number would otherwise include the gap between the
  two runs (F5). The first attempt is read before the re-run starts, or afterwards from
  `actions/runs/<id>/attempts/1`. The jobs list
  shows `backend-test-1` to `backend-test-4` each `success`; the four `backend-junit-k`
  artifacts' `tests` attributes sum to the `--collect-only` count on that run's head
  commit. If a run is over 6 minutes, the report gives each job's start and end so the
  floor and what sets it are stated, which the task accepts in place of the target. A run
  whose first job started more than a minute after `run_started_at` waited behind another
  run of the same pull request and is not a sample (F4).

## Adversarial pass

Run once, 2026-10-05, by a different agent from the planner (Opus 5.5). Everything below was
run in a scratch worktree of `24c755e` with its own `.venv`, on Chris's Mac, and the
worktree was removed afterwards. **The design stands: four named shard jobs, a hash of the
node id, and a logging fix in product code. No finding changes the mechanism, adds a
component or adds an entry to the full-lane list, so no second pass is needed.** F1 changes
how the logging fix is written and is the one finding to read before approving.

What the pass reconstructed and found to hold:

- **P6's failure.** On the unmodified tree, `tests/test_schema_engine.py`,
  `tests/test_mcp_transport.py` and `tests/test_rest_actor_resolution.py` each exit 1 alone
  (1 failed; 3 failed and 17 errors; 1 failed and 2 errors), each log naming `I/O operation
  on closed file`. P6's reproduction outside pytest raises the same `ValueError`.
- **P4 and P12.** `--shard k/4` from a `tests/conftest.py` hook collects 550, 560, 556 and
  554; the four lists sorted together are byte-identical to the unsharded 2,220 (`cmp`
  exit 0, 2,220 unique). Two unsharded collections under `PYTHONHASHSEED=1` and `987` are
  byte-identical, and no node id carries a memory address or an absolute path, so a test
  cannot hash into a different shard on a different runner. The same collections succeed
  with `models/` and `web/node_modules` absent, which is the `guards` job's situation, so
  the partition rule can run in the structural lane.
- **The gate.** `ci-ok`'s script, taken from the workflow with the four shard names put in
  its loop and fed made-up results, exits 0 for all green and for a documentation change,
  and exits 1 for a shard that failed, was skipped on a code change, was cancelled,
  succeeded on a documentation change, or is absent from `needs`.

Findings:

- **F1. The logging fix is one deleted argument, not a new class; folded into judgment
  area 2.** `PrintLogger.msg` in structlog 26.1.0 reads `f = self._file if self._file is
  not stdout else None`, then `print(message, file=f, flush=True)`, where `stdout` is
  `sys.stdout` as structlog saw it at import. So a deployment already looks `sys.stdout` up
  on every line: with the unmodified file, a line logged after `sys.stdout` is swapped
  lands in the swapped stream. The failure needs the stream handed in at configure time to
  be a different object from the import-time one, which only a test's `capfd` or `capsys`
  arranges. `PrintLoggerFactory()` removes that case. Measured with it: P6's reproduction
  exits 0; the three files exit 0 alone (80, 25 and 4 passed), two of them also under `-s`;
  the four log-asserting modules exit 0 alone; the four shards exit 0 with 549, 559, 556
  and 553 passed and 3 xfailed between them; the unsharded suite exits 0 with `2217
  passed, 3 xfailed` in 265 s; `mypy` and `ruff check` pass on the file. The plan's writer
  class was reconstructed too and passes the same files and all four shards, but with it
  `copy.deepcopy(logger)` and `pickle.dumps(logger)` raise where they succeed today. What
  the deleted argument leans on is structlog's `print`-based behaviour, documented in
  `PrintLogger`'s docstring since 22.1.0 and pinned here at `structlog==26.1.0`;
  `tests/test_logging_stream.py` fails if an upgrade changes it, and a deployment would be
  unaffected either way because its `sys.stdout` never changes.
- **F2. The fix removes this order dependence, not every one; noted in P6 and the AGENTS
  trap.** `cache_logger_on_first_use=True` also freezes the level filter: a logger first
  used at `info` stays at `info` after `configure_logging("debug")`, while a logger first
  used afterwards honours `debug`. Reproduced outside pytest, under every variant of the
  fix. Dormant today. The trap added to `AGENTS.md` names both, so a future file that
  passes in the suite and fails in a shard has somewhere to start.
- **F3. Nothing would have run the gate, and every planned mutation changed one shard
  only; both folded into "What changes" and AC3.** The task asks that `ci-ok` fail when a
  part fails or is skipped. `tests/test_ci_workflow.py` reads the script's two `for` loops
  with a regular expression and never executes it, so the plan proved that clause by shape
  alone. The mutation script now runs the gate against made-up results. Separately, "the
  shards' steps are identical" passes when the same weakening is applied to all four, so
  the rules now forbid `continue-on-error`, a job `env`, extra keys on the `pytest` step
  and `PYTEST_ADDOPTS`, with three all-shard mutations to prove it. A dropped or
  duplicated shard was already caught: by the existing rules 2, 3 and 4 once `HEAVY` names
  the four, and by the `k` rule.
- **F4. "About 3.9 minutes" rested on one run; the band is 3.3 to 4.2, and the target holds
  across it.** Re-read from the jobs API for all 23 CI runs since 2026-09-28. In the 18
  that did not wait behind another run, the last job other than `backend-test` ended
  between +3.22 and +3.88 minutes in 17 of them (median of all 18: 3.64) and at +5.38 in
  one, when `image` took 5.2 minutes; a run like that one would take about 5.5 whatever
  the shards do. `backend-test` took 8.05 to 11.73 minutes against the 10.63 the projection
  scales from, so the slowest shard's job is about 2.9 to 3.9 minutes, starting at about
  +0.2. `ci-ok` adds 5 to 8
  seconds. Locally, with F1's fix, the shards ran in 69, 62, 79 and 65 s against 265 s
  unsharded: the split costs 10 s in total for fixtures set up in more than one shard, and
  the largest shard is 30 percent of the suite, which agrees with P4's 171 of 588. Two runs
  spent 5.4 and 11.9 minutes waiting for an earlier run in the same concurrency group
  before any job started; AC12 now sets such a run aside.
- **F5. AC12 would have measured the re-run from the wrong clock; folded into AC12 and
  judgment area 3.** GitHub's API description says of `run_started_at`: "The start time of
  the latest run. Resets on re-run." (`github/rest-api-description`, read 2026-10-05).
  `created_at` does not reset. Not established, because no run in this repository has a
  second attempt (`run_attempt` is 1 on all 23): whether the agent token may call `gh run
  rerun`, and whether `actions/upload-artifact` v7.0.1 accepts `backend-junit-k` again on
  attempt 2.
- **F6. Small things for the build.** One collection takes about 1.2 s here, so the
  partition rule adds about 6 s to `guards` and to one shard; run under each of 13
  mutations it would add over a minute for nothing, so the script deselects it. A bad
  `--shard` value raised from the collection hook is reported after pytest has already
  collected, so it is refused in `pytest_configure`. `uv run pytest -q -m structural
  --shard 2/4` composes as expected (33 passed here).
- **F7. A hung shard would hold the gate for 30 minutes; folded into "What changes".**
  The shards inherit `timeout-minutes: 30` from a job that took 10. At 15 a hang is
  reported in half the time and the limit is still nearly four times the slowest projected
  shard.
- **F8. Read, not folded.** On a model cache miss five jobs fetch the model at once (four
  shards and `e2e`) where two do today; the cache key changes only with
  `scripts/fetch_model.py`. Each shard also runs `npm ci` for the one test that needs it,
  which is inside P1's 32 s of setup. Neither changes the design.

## Deviations from the approved plan

Built 2026-10-06 (Opus 5.5), checklist items 1 to 6 in order, one commit each for items 1
to 5. No deviation changes the design, the files touched or any Accept criterion.

- **D1. The partition rule failed in step 1 for a different reason than the checklist
  expected, so it was measured again after step 4.** The rule reads N from the workflow.
  On the unfixed tree there were no shard jobs, so N was 0 and it failed comparing an
  empty union with the whole collection; it never reached `--shard`. After step 4 it was
  run against two temporary breaks of the rule in `tests/conftest.py`, each reverted:
  with the modulus one too large it exits 1 (a test is in no shard); with `<=` in place
  of `==` it exits 1 with "a test is in more than one shard" (2,227 unique of 5,567). It
  exits 0 on the restored file.
- **D2. One new rule cannot fail on the unfixed tree, and is measured by mutations only.**
  "No shard is weakened in a way identical steps cannot see" loops over the shard jobs,
  and with none it passes. The mutations `one shard continue-on-error`, `every pytest step
  continue-on-error` and `PYTEST_ADDOPTS in the workflow env` each turn it red.
- **D3. Step 1's recorded failures.** `tests/test_logging_stream.py`: exit 1, 3 failed.
  The first with `ValueError: I/O operation on closed file`; the second because the line
  written after the swap landed in the stream held from configure time; the third with
  `copy.Error: Only PrintLoggers to sys.stdout and sys.stderr can be deepcopied`, because
  under a swapped `sys.stdout` the unfixed code hands structlog a stream that is not the
  import-time one. `tests/test_ci_workflow.py`: exit 1, 5 failed, 8 passed: rules 3 and 4
  on `backend-test` against the four names, the two shard rules on zero shard jobs, and
  the partition rule (D1). `scripts/mutate_ci_workflow.py`: exit 1, `unmutated exit 1
  (want 0)`, then a `ValueError` from the first mutation, which had no `backend-test-3`
  to remove.
- **D4. The mutation script prints which rules each mutation turned red, and that found a
  defect in the script itself.** Its first draft deselected the partition rule by a node
  id that did not match inside the temporary copy, so the partition rule ran, failed
  there (the copy has no test suite to collect), and made every line read `exit 1 (want
  1)` whatever the mutation did, the unmutated line included. It now deselects with `-k`,
  the unmutated copy exits 0, and every mutation names at least one workflow rule.
- **D5. "Shard 3 deleted entirely" also removes it from `ci-ok`'s `needs` and loop**, so
  the mutated workflow is one GitHub would accept. Deleting the job alone leaves a
  `needs` entry naming no job, which is the first mutation again.
- **D6. The collection is 2,227 tests, not 2,220**: this change adds three logging tests
  and four workflow rules. The shards collect 553, 561, 559 and 554.

## Build results

At `HEAD` of the build, on Chris's Mac. AC12 is not measured: it needs the pull
request's own runs.

- Step 2: `tests/test_logging_stream.py`, the three files of P6 and the four
  log-asserting modules each exit 0 alone.
- Step 3: AC5 `cmp` exit 0, 2,227 lines, 2,227 unique. AC6: `5/4`, `0/4` and `1of4` each
  exit 4, naming the value.
- Step 4: `tests/test_ci_workflow.py` exit 0 (13 passed). `scripts/mutate_ci_workflow.py`
  exit 0: unmutated 0, all 13 mutations `exit 1 (want 1)`, the 7 gate cases each the exit
  code wanted. actionlint 1.7.12 (darwin arm64, checked against the release's checksums
  file) exit 0.
- Step 6: unsharded `uv run pytest -q` exit 0, `2224 passed, 3 xfailed`, 293 s. Shards 1
  to 4 exit 0 with 552, 560, 559 and 553 passed and 1, 1, 0 and 1 xfailed, which is 2,227,
  in 68, 66, 79 and 77 s.

## Durable content moved out of this plan

Moved at closeout, in the build's own commits (`6d588bd`) and checked at closeout:

- The shard rule, the reason for four named jobs rather than a matrix, and the rules that
  forbid weakening all four alike: `CONTRIBUTING.md` "What CI runs" and the `ci.yml` header.
- The command to run one shard locally, and the logging trap (a logger that caches its
  stream outlives its test, so a file fails alone or in one shard): `AGENTS.md` (commands
  table and "Traps").
- No entry in `docs/DESIGN_DECISIONS.md`: the change establishes no product rule.
