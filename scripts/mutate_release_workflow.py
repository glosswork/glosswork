"""Each rule in ``tests/test_release_workflow.py`` fails on a workflow that breaks it.

Plan 1's Accept block runs this: it applies one mutation at a time to a copy of
``.github/workflows/release.yml`` in a temporary directory, runs the test module against
the copy, and fails unless every mutation turns the module red and the unmutated copy
stays green. The repository is never written.

    uv run python scripts/mutate_release_workflow.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDS = ("build-amd64", "build-arm64")
Workflow = dict[Any, Any]


def _steps(workflow: Workflow, job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = workflow["jobs"][job]["steps"]
    return steps


def _named(workflow: Workflow, job: str, fragment: str) -> dict[str, Any]:
    (step,) = [s for s in _steps(workflow, job) if fragment in s.get("name", "")]
    return step


def _each_build(change: Callable[[Workflow, str], None]) -> Callable[[Workflow], None]:
    def apply(workflow: Workflow) -> None:
        for job in BUILDS:
            change(workflow, job)

    return apply


def _push_before_tests(workflow: Workflow, job: str) -> None:
    steps = _steps(workflow, job)
    push = _named(workflow, job, "Push")
    steps.remove(push)
    steps.insert(3, push)


def _push_in_first_build(workflow: Workflow, job: str) -> None:
    build = _named(workflow, job, "Build into")
    build["run"] = build["run"].replace("--load", "--load --push")


def _secret_in_workflow_env(workflow: Workflow) -> None:
    workflow["env"]["T"] = "${{ secrets.DOCKERHUB_TOKEN }}"


def _secret_in_job_env(workflow: Workflow) -> None:
    workflow["jobs"]["publish"]["env"]["T"] = "${{ secrets.DOCKERHUB_TOKEN }}"


def _secret_in_build(workflow: Workflow, job: str) -> None:
    _named(workflow, job, "container_tests")["env"] = {"T": "${{ secrets.DOCKERHUB_TOKEN }}"}


def _notices_non_fatal(workflow: Workflow, job: str) -> None:
    step = _named(workflow, job, "notices")
    step["run"] += " || true"


def _tests_collect_only(workflow: Workflow, job: str) -> None:
    step = _named(workflow, job, "container_tests")
    step["run"] = step["run"].replace("pytest -q", "pytest -q --co")


def _continue_on_error(workflow: Workflow, job: str) -> None:
    _named(workflow, job, "container_tests")["continue-on-error"] = True


def _pull_request_trigger(workflow: Workflow) -> None:
    workflow[True]["pull_request"] = None


def _arm_differs(workflow: Workflow) -> None:
    _steps(workflow, "build-arm64").pop(4)


def _no_existence_check(workflow: Workflow) -> None:
    step = _named(workflow, "publish", "Tag X.Y.Z")
    step["run"] = step["run"].replace('elif [ -n "$have" ]', 'elif false && [ -n "$have" ]')


def _dry_run_pushes(workflow: Workflow, job: str) -> None:
    del _named(workflow, job, "Push")["if"]


def _publish_on_dispatch(workflow: Workflow) -> None:
    del workflow["jobs"]["publish"]["if"]


def _environment_on_build(workflow: Workflow, job: str) -> None:
    workflow["jobs"][job]["environment"] = "release"


def _identity_check_softened(workflow: Workflow, job: str) -> None:
    step = _named(workflow, job, "Push")
    softened = step["run"].replace('  exit 1\nfi\necho "digest=', '  true\nfi\necho "digest=')
    assert softened != step["run"], "the mutation must change the step"
    step["run"] = softened


MUTATIONS: dict[str, Callable[[Workflow], None]] = {
    "push moved before the tests": _each_build(_push_before_tests),
    "--push on the tested build": _each_build(_push_in_first_build),
    "secret in the workflow env": _secret_in_workflow_env,
    "secret in publish's job env": _secret_in_job_env,
    "secret in a build step": _each_build(_secret_in_build),
    "notices check made non-fatal": _each_build(_notices_non_fatal),
    "container_tests collect only": _each_build(_tests_collect_only),
    "container_tests continue on error": _each_build(_continue_on_error),
    "pull_request trigger": _pull_request_trigger,
    "arm64 steps differ": _arm_differs,
    "existence check disabled": _no_existence_check,
    "dry run pushes": _each_build(_dry_run_pushes),
    "publish runs on a dry run": _publish_on_dispatch,
    "build jobs enter the environment": _each_build(_environment_on_build),
    "tested-equals-pushed check softened": _each_build(_identity_check_softened),
}


def _run(root: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        + [str(root / "tests" / "test_release_workflow.py")],
        capture_output=True,
    ).returncode


def main() -> int:
    source = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for part in ("tests/test_release_workflow.py", "scripts/github/configure.sh"):
            (root / part).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO_ROOT / part, root / part)
        target = root / ".github" / "workflows" / "release.yml"
        target.parent.mkdir(parents=True)

        target.write_text(source)
        baseline = _run(root)
        print(f"{'unmutated':40} exit {baseline} (want 0)")
        failures += baseline != 0

        for name, mutate in MUTATIONS.items():
            workflow: Workflow = yaml.safe_load(source)
            mutate(workflow)
            target.write_text(yaml.safe_dump(workflow, sort_keys=False))
            code = _run(root)
            print(f"{name:40} exit {code} (want 1)")
            failures += code != 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
