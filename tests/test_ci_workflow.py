"""The CI workflow keeps the shape that makes skipping safe.

GitHub treats a skipped job as a passing one, and a workflow skipped by a path filter as a
check that never reports, which blocks a merge forever. So the workflow in
``.github/workflows/ci.yml`` skips work only at job level, behind one ``changes`` job,
and one always-running ``ci-ok`` job decides whether every job ended the way the change
allows. Each rule below guards one way that arrangement silently stops protecting
``main``: a path filter reappearing, a heavy job required on its own, a job dropped from
``ci-ok``'s ``needs``, rename detection turning a code move into a documentation change,
an unpinned action, or a scanner widened or narrowed past what was decided.

The ruleset file is read too, because a required check is only as good as the job
behind it.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RULESET = REPO_ROOT / "scripts" / "github" / "ruleset-main.json"

ALWAYS_RUN = {"changes", "guards", "secrets", "sast"}
HEAVY = {"backend-lint", "frontend-lint", "backend-test", "frontend-test", "e2e", "image"}
GATE = "ci-ok"
HEAVY_IF = "needs.changes.outputs.code == 'true'"
STORAGE_MODULE = "src/glosswork/repositories/sqlite.py"
FILTERS = {"paths", "paths-ignore", "branches", "branches-ignore"}


def _workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())
    return data


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML reads a bare `on` key as the boolean True (YAML 1.1).
    triggers: dict[str, Any] = workflow.get("on", workflow.get(True))
    return triggers


def _jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = _workflow()["jobs"]
    return jobs


def _steps(job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _jobs()[job]["steps"]
    return steps


def _runs(job: str) -> list[str]:
    return [step["run"] for step in _steps(job) if "run" in step]


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_no_path_or_branch_filter_can_leave_a_required_check_pending() -> None:
    """Rule 1. A workflow-level filter leaves required checks "Pending" forever."""
    triggers = _triggers(_workflow())
    for event, config in triggers.items():
        if event == "push":
            # A push filter only narrows which pushes run; it never touches a pull request.
            assert not (FILTERS - {"branches"}) & set(config or {}), event
            continue
        assert not FILTERS & set(config or {}), f"{event} carries a filter"
    types = triggers["pull_request"]["types"]
    assert "edited" in types, "a base-branch change fires only `edited`"
    assert {"opened", "synchronize", "reopened"} <= set(types)


def test_the_gate_always_runs_and_needs_every_other_job() -> None:
    """Rule 2."""
    jobs = _jobs()
    assert jobs[GATE]["if"] == "always()"
    assert set(_needs(jobs[GATE])) == set(jobs) - {GATE}


def test_only_the_heavy_jobs_can_skip_and_only_on_the_classification() -> None:
    """Rule 3."""
    jobs = _jobs()
    assert set(jobs) == ALWAYS_RUN | HEAVY | {GATE}
    for name in ALWAYS_RUN:
        assert "if" not in jobs[name], name
        assert "needs" not in jobs[name], name
    for name in HEAVY:
        assert _needs(jobs[name]) == ["changes"], name
        assert jobs[name]["if"] == HEAVY_IF, name


def test_the_gate_script_judges_exactly_the_always_run_and_heavy_sets() -> None:
    """Rule 4. A job left out of either loop is a job whose result nobody reads."""
    (script,) = _runs(GATE)
    loops = re.findall(r"^\s*for job in ([^;]+); do", script, re.MULTILINE)
    assert len(loops) == 2, loops
    assert set(loops[0].split()) == ALWAYS_RUN
    assert set(loops[1].split()) == HEAVY


def test_the_classifier_diffs_the_merge_without_rename_detection() -> None:
    """Rule 5. With renames on, moving code into docs/ lists only the docs/ side."""
    (script,) = _runs("changes")
    assert "git diff --no-renames --name-only HEAD^1 HEAD" in script
    assert "scripts/ci_changes.py" in script


def test_every_action_is_pinned_by_sha_and_every_image_by_digest() -> None:
    """Rule 6."""
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), (
                    name,
                    step["uses"],
                )
        container = job.get("container")
        if container is not None:
            image = container["image"] if isinstance(container, dict) else container
            assert re.search(r"@sha256:[0-9a-f]{64}$", image), (name, image)


def _allowed(script: str, variable: str) -> set[str]:
    (line,) = [line for line in script.splitlines() if line.strip().startswith(f"{variable}=")]
    words = shlex.split(line.split("grep", 1)[1].split("||", 1)[0])
    return {words[i + 1] for i, word in enumerate(words) if word == "-e"}


def test_the_identity_check_allows_the_company_address_and_github_as_committer() -> None:
    """Rule 7."""
    (step,) = [s for s in _steps("secrets") if "company identity" in s.get("name", "")]
    assert _allowed(step["run"], "authors") == {"hello@glosswork.dev"}
    assert _allowed(step["run"], "committers") == {"hello@glosswork.dev", "noreply@github.com"}


def test_every_required_check_is_a_job_that_cannot_pass_by_being_skipped() -> None:
    """Rule 8. A skipped job reports success, so no heavy job is required on its own."""
    ruleset = json.loads(RULESET.read_text())
    (rule,) = [r for r in ruleset["rules"] if r["type"] == "required_status_checks"]
    required = {check["context"] for check in rule["parameters"]["required_status_checks"]}
    assert GATE in required
    assert required <= set(_jobs())
    assert required <= ALWAYS_RUN | {GATE}


def test_the_scanners_cover_what_was_decided() -> None:
    """Rule 9. The secret scan reads only the history under test; one root commit; and
    the single Semgrep rule exclusion is confined to the storage module."""
    runs = _runs("secrets")
    (gitleaks,) = [run for run in runs if "gitleaks" in run and " git " in run]
    assert "--log-opts=HEAD" in gitleaks
    assert any("git rev-list --max-parents=0 HEAD" in run for run in runs)

    assert WORKFLOW.read_text().count("--exclude-rule") == 1
    semgrep = [" ".join(run.split()) for run in _runs("sast") if "semgrep scan" in run]
    assert len(semgrep) == 2, semgrep
    (narrowed,) = [run for run in semgrep if "--exclude-rule" in run]
    (everything,) = [run for run in semgrep if "--exclude-rule" not in run]
    assert narrowed.split()[-1] == STORAGE_MODULE
    assert f"--exclude {STORAGE_MODULE}" in everything
    assert everything.split()[-1] == "."
