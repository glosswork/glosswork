"""Each shard rule in ``tests/test_ci_workflow.py`` fails on a workflow that breaks it,
and ``ci-ok``'s script fails when a shard did not end the way the change allows.

Two halves, both against ``.github/workflows/ci.yml`` and neither writing the repository:

- **Mutations.** One at a time, applied to a copy of the workflow in a temporary
  directory, with the test module run against the copy. Every mutation must turn the
  module red and the unmutated copy must stay green. The partition rule is deselected:
  it reads the test suite rather than the workflow, so no mutation can change its answer,
  and it costs five collections each time.
- **The gate, executed.** The tests read ``ci-ok``'s script; nothing in the suite runs
  it. This takes the script from the workflow and runs it under ``bash`` with made-up
  job results in ``NEEDS``, the way GitHub hands them over. Needs ``bash`` and ``jq``.

    uv run python scripts/mutate_ci_workflow.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_MODULE = "tests/test_ci_workflow.py"
PARTITION_RULE = "test_the_shards_partition_the_suite"
GATE = "ci-ok"
ALWAYS_RUN = ("changes", "guards", "secrets", "sast")
Workflow = dict[Any, Any]


def _shards(workflow: Workflow) -> list[str]:
    return sorted(name for name in workflow["jobs"] if re.fullmatch(r"backend-test-\d+", name))


def _pytest_step(workflow: Workflow, job: str) -> dict[str, Any]:
    (step,) = [s for s in workflow["jobs"][job]["steps"] if "pytest" in s.get("run", "")]
    return step


def _gate_step(workflow: Workflow) -> dict[str, Any]:
    (step,) = [s for s in workflow["jobs"][GATE]["steps"] if "run" in s]
    return step


def _replace(text: str, old: str, new: str) -> str:
    assert old in text, f"the mutation must change something: {old!r} not found"
    return text.replace(old, new)


def _each_shard(change: Callable[[Workflow, str], None]) -> Callable[[Workflow], None]:
    def apply(workflow: Workflow) -> None:
        for job in _shards(workflow):
            change(workflow, job)

    return apply


def _dropped_from_needs(workflow: Workflow) -> None:
    workflow["jobs"][GATE]["needs"].remove("backend-test-3")


def _dropped_from_the_loop(workflow: Workflow) -> None:
    step = _gate_step(workflow)
    step["run"] = _replace(step["run"], " backend-test-3", "")


def _deleted(workflow: Workflow) -> None:
    del workflow["jobs"]["backend-test-3"]
    _dropped_from_needs(workflow)
    _dropped_from_the_loop(workflow)


def _two_run_the_same_shard(workflow: Workflow) -> None:
    step = _pytest_step(workflow, "backend-test-3")
    step["run"] = _replace(step["run"], "--shard 3/4", "--shard 2/4")


def _one_counts_against_five(workflow: Workflow) -> None:
    step = _pytest_step(workflow, "backend-test-4")
    step["run"] = _replace(step["run"], "--shard 4/4", "--shard 4/5")


def _one_runs_unsharded(workflow: Workflow) -> None:
    step = _pytest_step(workflow, "backend-test-3")
    step["run"] = _replace(step["run"], " --shard 3/4", "")


def _one_deselects_by_marker(workflow: Workflow) -> None:
    _pytest_step(workflow, "backend-test-3")["run"] += ' -m "not structural"'


def _one_job_continues_on_error(workflow: Workflow) -> None:
    workflow["jobs"]["backend-test-3"]["continue-on-error"] = True


def _one_runs_on_every_change(workflow: Workflow) -> None:
    del workflow["jobs"]["backend-test-3"]["if"]


def _one_only_collects(workflow: Workflow) -> None:
    _pytest_step(workflow, "backend-test-3")["run"] += " --collect-only"


def _step_continues_on_error(workflow: Workflow, job: str) -> None:
    _pytest_step(workflow, job)["continue-on-error"] = True


def _failure_swallowed(workflow: Workflow, job: str) -> None:
    _pytest_step(workflow, job)["run"] += " -x --maxfail=1 || true"


def _addopts_in_the_workflow_env(workflow: Workflow) -> None:
    workflow["env"]["PYTEST_ADDOPTS"] = "--collect-only"


MUTATIONS: dict[str, Callable[[Workflow], None]] = {
    "shard 3 dropped from ci-ok's needs": _dropped_from_needs,
    "shard 3 dropped from the heavy loop": _dropped_from_the_loop,
    "shard 3 deleted entirely": _deleted,
    "two jobs both --shard 2/4": _two_run_the_same_shard,
    "one job --shard 4/5": _one_counts_against_five,
    "one job without --shard": _one_runs_unsharded,
    'one shard -m "not structural"': _one_deselects_by_marker,
    "one shard continue-on-error": _one_job_continues_on_error,
    "one shard's if removed": _one_runs_on_every_change,
    "one shard --collect-only": _one_only_collects,
    "every pytest step continue-on-error": _each_shard(_step_continues_on_error),
    "every pytest line || true": _each_shard(_failure_swallowed),
    "PYTEST_ADDOPTS in the workflow env": _addopts_in_the_workflow_env,
}


def _run_rules(root: Path) -> tuple[int, list[str]]:
    """The module's exit code against the copy in ``root``, and the rules that failed."""
    module = root / TEST_MODULE
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        + ["-k", f"not {PARTITION_RULE}", str(module)],
        capture_output=True,
        text=True,
    )
    failed = re.findall(r"^FAILED \S+::(\w+)", result.stdout, re.MULTILINE)
    return result.returncode, failed


def _mutations(source: str) -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for part in (TEST_MODULE, "scripts/github/ruleset-main.json"):
            (root / part).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO_ROOT / part, root / part)
        target = root / ".github" / "workflows" / "ci.yml"
        target.parent.mkdir(parents=True)

        target.write_text(source)
        baseline, failed = _run_rules(root)
        print(f"{'unmutated':40} exit {baseline} (want 0) {' '.join(failed)}".rstrip())
        failures += baseline != 0

        for name, mutate in MUTATIONS.items():
            workflow: Workflow = yaml.safe_load(source)
            mutate(workflow)
            target.write_text(yaml.safe_dump(workflow, sort_keys=False))
            code, failed = _run_rules(root)
            print(f"{name:40} exit {code} (want 1) {' '.join(failed)}".rstrip())
            failures += code != 1
    return failures


def _needs(workflow: Workflow, code: str, heavy: str) -> dict[str, dict[str, Any]]:
    """What ``toJSON(needs)`` holds when every job ended the way ``code`` allows."""
    needs: dict[str, dict[str, Any]] = {
        job: {"result": "success" if job in ALWAYS_RUN else heavy, "outputs": {}}
        for job in workflow["jobs"][GATE]["needs"]
    }
    needs["changes"]["outputs"] = {"code": code}
    return needs


def _gate(source: str) -> int:
    workflow: Workflow = yaml.safe_load(source)
    script = _gate_step(workflow)["run"]
    shard = "backend-test-3"
    assert shard in _shards(workflow), f"{shard} is not a job"

    def case(code: str, heavy: str, result: str | None = "unchanged") -> str:
        needs = _needs(workflow, code, heavy)
        if result is None:
            del needs[shard]
        elif result != "unchanged":
            needs[shard]["result"] = result
        return json.dumps(needs)

    cases: dict[str, tuple[str, int]] = {
        "code change, every job success": (case("true", "success"), 0),
        "documentation change, heavy jobs skipped": (case("false", "skipped"), 0),
        "code change, shard 3 failure": (case("true", "success", "failure"), 1),
        "code change, shard 3 skipped": (case("true", "success", "skipped"), 1),
        "code change, shard 3 cancelled": (case("true", "success", "cancelled"), 1),
        "documentation change, shard 3 success": (case("false", "skipped", "success"), 1),
        "code change, shard 3 missing from needs": (case("true", "success", None), 1),
    }
    failures = 0
    for name, (needs_json, want) in cases.items():
        code = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "NEEDS": needs_json},
            capture_output=True,
        ).returncode
        print(f"gate: {name:40} exit {code} (want {want})")
        failures += code != want
    return failures


def main() -> int:
    source = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    failures = _mutations(source) + _gate(source)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
