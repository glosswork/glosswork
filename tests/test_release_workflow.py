"""The release workflow publishes only from a version tag, and only what it tested.

``.github/workflows/release.yml`` is the one place an image is published. Each rule below
guards one way it could publish something it should not: a trigger other than a version
tag or a manual dry run, a job holding more permission than it uses, the Docker Hub
credential reachable outside the one step that signs in with it, the two architectures'
build jobs drifting apart, a push before the tests and the notices check, a check that
cannot fail, a dry run that pushes, a published version overwritten, ``latest`` moved
to anything but the build just published, or an unpinned action.

The settings half of the credential rule (the ``release`` environment admits tags
matching ``v*`` only) lives in ``scripts/github/configure.sh``, whose ``check`` reads
it back from GitHub.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
CONFIGURE = REPO_ROOT / "scripts" / "github" / "configure.sh"

BUILDS = {"build-amd64": "ubuntu-24.04", "build-arm64": "ubuntu-24.04-arm"}
JOBS = {"preflight", "publish", *BUILDS}
REVISION_BUILD_ARG = "--build-arg GW_REVISION=${{ github.sha }}"
HUB_SECRET = "secrets.DOCKERHUB_TOKEN"
ON_TAG = "github.event_name == 'push'"
TESTS = "GW_IMAGE=glosswork:release uv run pytest -q container_tests"
VERSION_READ_BACK = "Both registries serve both architectures, built from this commit"
BOTH_REGISTRIES = 'for image in "$GHCR_IMAGE" "$HUB_IMAGE"; do'
NOTICES = (
    "uv run python scripts/notices_coverage.py --image glosswork:release "
    '--summary "$GITHUB_STEP_SUMMARY"'
)


def _workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())
    return data


def _jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = _workflow()["jobs"]
    return jobs


def _steps(job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _jobs()[job]["steps"]
    return steps


def _step(job: str, fragment: str) -> tuple[int, dict[str, Any]]:
    (found,) = [(i, s) for i, s in enumerate(_steps(job)) if fragment in s.get("name", "")]
    return found


def test_only_a_version_tag_or_a_manual_dry_run_starts_it() -> None:
    workflow = _workflow()
    # PyYAML reads a bare `on` key as the boolean True (YAML 1.1).
    triggers: dict[str, Any] = workflow["on"] if "on" in workflow else workflow[True]
    assert triggers == {"push": {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}, "workflow_dispatch": None}


def test_no_job_holds_a_permission_it_was_not_given_by_name() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {}
    assert set(_jobs()) == JOBS
    assert _jobs()["preflight"]["permissions"] == {"contents": "read", "actions": "read"}
    for job in BUILDS:
        assert _jobs()[job]["permissions"] == {"contents": "read", "packages": "write"}
    assert _jobs()["publish"]["permissions"] == {"packages": "write"}


def test_the_docker_hub_credential_reaches_one_step_of_one_job() -> None:
    """Only ``publish`` names the environment, and only its sign-in step the secret.

    ``publish`` checks nothing out, so no code from the repository runs where the
    credential is, and no step before sign-in exists to read it early.
    """
    assert HUB_SECRET not in yaml.safe_dump(_workflow().get("env", {}))
    for name, job in _jobs().items():
        if name == "publish":
            continue
        assert HUB_SECRET not in yaml.safe_dump(job), name
        assert "environment" not in job, name
    publish = _jobs()["publish"]
    assert publish["environment"] == "release"
    assert publish["if"] == ON_TAG
    assert HUB_SECRET not in yaml.safe_dump(publish.get("env", {}))
    holders = [s["name"] for s in _steps("publish") if HUB_SECRET in yaml.safe_dump(s)]
    assert holders == ["Sign in to both registries"]
    assert _steps("publish")[0]["name"] == "Sign in to both registries"
    assert not any("uses" in step for step in _steps("publish"))


def test_the_release_environment_admits_version_tags_only() -> None:
    script = CONFIGURE.read_text()
    assert '"name": "v*"' in script and '"type": "tag"' in script
    assert '"custom_branch_policies": true' in script


def test_the_two_builds_are_the_same_job_on_different_runners() -> None:
    amd64, arm64 = (_jobs()[job] for job in BUILDS)
    assert amd64["steps"] == arm64["steps"]
    for job, runner in BUILDS.items():
        assert _jobs()[job]["runs-on"] == runner
        assert _jobs()[job]["needs"] == "preflight"
    assert {_jobs()[job]["env"]["PLATFORM"] for job in BUILDS} == {"linux/amd64", "linux/arm64"}


def test_the_image_is_tested_and_its_notices_checked_before_anything_signs_in() -> None:
    for job in BUILDS:
        tests_at, tests = _step(job, "container_tests")
        notices_at, notices = _step(job, "notices")
        sign_in_at, _ = _step(job, "Sign in")
        push_at, _ = _step(job, "Push")
        assert tests_at < notices_at < sign_in_at < push_at, job
        # Exactly these commands, so neither can be neutralised (`|| true`, `--co`).
        assert tests["run"] == TESTS, job
        assert notices["run"] == NOTICES, job
        for step in _steps(job):
            assert "continue-on-error" not in step, (job, step.get("name"))
            assert "|| true" not in step.get("run", ""), (job, step.get("name"))
            assert "env" not in step or "Sign in" in step.get("name", ""), (job, step)


def test_only_the_push_step_pushes_and_only_on_a_tag() -> None:
    for job in BUILDS:
        push_at, push = _step(job, "Push")
        for i, step in enumerate(_steps(job)):
            run = step.get("run", "")
            if i != push_at:
                assert "push=true" not in run and "--push" not in run, (job, step.get("name"))
        assert "push-by-digest=true" in push["run"]
        for fragment in ("Sign in", "Push"):
            assert _step(job, fragment)[1]["if"] == ON_TAG, (job, fragment)
        builds = [s["run"] for s in _steps(job) if "docker buildx build" in s.get("run", "")]
        assert len(builds) == 2, job
        assert all(REVISION_BUILD_ARG in run and "SOURCE_DATE_EPOCH" in run for run in builds)


def test_the_pushed_image_must_be_the_tested_one() -> None:
    for job in BUILDS:
        _, push = _step(job, "Push")
        assert "containerimage.config.digest" in push["run"]
        assert re.search(
            r'if \[ "\$tested" != "\$config" \] && \[ "\$tested" != "\$manifest" \]; then\n'
            r"\s+echo [^\n]+\n\s+exit 1",
            push["run"],
        )


def test_publish_never_overwrites_a_version() -> None:
    assert set(_jobs()["publish"]["needs"]) == JOBS - {"publish"}
    _, tag = _step("publish", "Tag X.Y.Z")
    run = tag["run"]
    assert 'grep -q "not found"' in run
    assert "never replaced" in run and "exit 1" in run
    assert run.count("imagetools create") == 1
    assert run.index('elif [ -n "$have" ]') < run.index("imagetools create")
    names = [step["name"] for step in _steps("publish")]
    assert names.index(tag["name"]) < names.index(VERSION_READ_BACK)
    assert "latest" not in run


def test_publish_moves_latest_to_the_build_it_just_published() -> None:
    """``latest`` is the one moving tag, written from the digests ``X.Y.Z`` was.

    It moves only after ``X.Y.Z`` is published and read back in both registries, with no
    condition, so repeating the step on a rerun is safe; and it is then read back.
    """
    names = [step["name"] for step in _steps("publish")]
    move_at, move = _step("publish", "Move latest")
    read_at, read = _step("publish", "as latest")
    assert names.index(VERSION_READ_BACK) < move_at < read_at
    assert BOTH_REGISTRIES in move["run"]
    assert move["run"].count("imagetools create") == 1
    assert (
        'imagetools create -t "$image:latest" "$GHCR_IMAGE@$AMD64" "$GHCR_IMAGE@$ARM64"'
        in move["run"]
    )
    assert BOTH_REGISTRIES in read["run"]
    assert 'want="linux/amd64=$AMD64 linux/arm64=$ARM64"' in read["run"]
    assert 'imagetools inspect "$image:latest"' in read["run"]
    assert re.search(
        r'if \[ "\$platforms" != "\$want" \]; then\n\s+echo [^\n]+\n\s+exit 1', read["run"]
    )
    for step in _steps("publish"):
        assert "if" not in step, step["name"]
        assert "continue-on-error" not in step, step["name"]
        assert "|| true" not in step["run"], step["name"]


def test_only_publish_names_latest_so_a_dry_run_never_moves_it() -> None:
    assert _jobs()["publish"]["if"] == ON_TAG
    for name, job in _jobs().items():
        if name != "publish":
            assert "latest" not in yaml.safe_dump(job), name


def test_preflight_checks_the_version_main_and_ci() -> None:
    names = [step.get("name", "") for step in _steps("preflight")]
    assert "The tag is vX.Y.Z and X.Y.Z is the project's version" in names
    assert "The tagged commit is on main" in names
    assert "CI passed for this commit" in names
    _, ci = _step("preflight", "CI passed")
    assert "if" not in ci, "the CI check runs on the dry run too"


def test_every_action_is_pinned_by_sha() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), (
                    name,
                    step["uses"],
                )
