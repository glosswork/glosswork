"""What a built image's third-party notices cover, and whether that is everything.

Publishing the image is redistribution, and most of the licences involved (MIT, BSD,
Apache-2.0) require their notice to travel with every copy. This reads a **built image**,
not the ``Dockerfile``, and answers one question per third-party package it carries:
does that package's licence text travel in the image? It covers the packages the
project chose, from ``uv.lock`` and ``web/package-lock.json``; the base image's own
contents (CPython, Debian, the base image's ``pip``) are the base image's to notice, and
are not counted.

- A **Python distribution** under ``/opt/venv`` is covered when one of the files it
  installed is a licence file (named ``LICENSE``, ``LICENCE``, ``COPYING`` or ``NOTICE``,
  with any suffix but ``.py``), or when ``THIRD_PARTY_NOTICES.md`` gives it a heading.
  ``glosswork`` itself is covered by ``LICENSE`` and is not counted.
- An **npm package** compiled into ``/app/web/dist`` is covered when
  ``THIRD_PARTY_NOTICES.md`` gives it a heading. The bundle is minified and carries no
  licence text of its own, and the image carries no ``node_modules``, so there is no
  other place its notice could be. The packages are the production entries of
  ``web/package-lock.json`` plus :data:`BUNDLED_BUILD_TOOLS`, the development packages
  measured to inject code into the bundle.

"Gives it a heading" means a Markdown heading line of that file names the package in
backticks, as in ``## `react` ``. A mention in prose does not count, because the file's
own scope paragraph names the packages it does *not* cover.

It exits 1 while anything is uncovered, and the release workflow runs it before it
pushes anything, so an image whose notices do not cover what it carries is never
published. Run it against a local build:

    docker build -t glosswork:local .
    uv run python scripts/notices_coverage.py --image glosswork:local

The container it starts runs Python only, with no network, and is removed on exit.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTICES = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
PACKAGE_LOCK = REPO_ROOT / "web" / "package-lock.json"

#: The project's own distribution. Its licence is ``LICENSE``, copied to ``/app/``.
OWN_DISTRIBUTION = "glosswork"

#: Development dependencies whose own code ends up in ``web/dist``, so they are
#: redistributed although the lockfile marks them ``dev``. Measured 2026-09-28 by
#: grepping the built bundle: Vite's modulepreload polyfill (``relList.supports``) in the
#: JavaScript, and Tailwind's preflight and ``--tw-*`` properties in the CSS.
BUNDLED_BUILD_TOOLS = ("tailwindcss", "vite")

#: A file a distribution installed that carries licence text, matched on its own name:
#: ``LICENSE``, ``LICENSE.txt``, ``licenses/LICENSE.APACHE``, ``NOTICE``. Not a module.
LICENCE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)([-_.][^/]*)?$", re.IGNORECASE)

#: Runs inside the image with the image's own interpreter, which is the venv's (the
#: image puts ``/opt/venv/bin`` first on ``PATH``). Prints one JSON document: every
#: installed distribution's name and the paths of the files it records.
_LIST_DISTRIBUTIONS = """
import importlib.metadata as md, json
print(json.dumps([
    {"name": d.metadata["Name"], "files": [str(f) for f in (d.files or [])]}
    for d in md.distributions()
]))
"""

#: A backticked name: `react`, `@tanstack/react-query`.
BACKTICKED = re.compile(r"`([^`\s]+)`")


@dataclass(frozen=True)
class Package:
    ecosystem: str
    name: str
    covered: bool
    how: str


def normalise(name: str) -> str:
    """PEP 503 normalisation, so `Huggingface_Hub` and `huggingface-hub` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def named_in_notices(notices_text: str) -> set[str]:
    """Every package a heading of the notices file names in backticks."""
    headings = [line for line in notices_text.splitlines() if line.startswith("#")]
    return {normalise(name) for line in headings for name in BACKTICKED.findall(line)}


def python_packages(distributions: list[dict[str, object]], named: set[str]) -> list[Package]:
    packages: list[Package] = []
    for distribution in distributions:
        name = str(distribution["name"])
        if normalise(name) == OWN_DISTRIBUTION:
            continue
        files = distribution.get("files")
        paths = [str(path) for path in files] if isinstance(files, list) else []
        if any(LICENCE_FILE.search(path) and not path.endswith(".py") for path in paths):
            packages.append(Package("python", name, True, "installs a licence file"))
        elif normalise(name) in named:
            packages.append(Package("python", name, True, "a heading in THIRD_PARTY_NOTICES.md"))
        else:
            packages.append(Package("python", name, False, "installs no licence file, no heading"))
    return sorted(packages, key=lambda package: normalise(package.name))


def npm_production_names(lock: dict[str, object]) -> list[str]:
    """Every package the lockfile installs for production, by name, once each, plus the
    build tools that inject code into the bundle.

    ``packages`` is keyed by install path (``node_modules/a/node_modules/b``); the name is
    what follows the last ``node_modules/``. The root entry (key ``""``) is the project.
    """
    entries = lock.get("packages")
    if not isinstance(entries, dict):
        raise ValueError("package-lock.json has no `packages` map (lockfileVersion 2 or 3)")
    names = set()
    for path, entry in entries.items():
        if not path or not isinstance(entry, dict):
            continue
        if entry.get("dev") or entry.get("devOptional") or entry.get("link"):
            continue
        names.add(path.rsplit("node_modules/", 1)[-1])
    return sorted(names | set(BUNDLED_BUILD_TOOLS))


def npm_packages(names: list[str], named: set[str]) -> list[Package]:
    return [
        Package("npm", name, True, "a heading in THIRD_PARTY_NOTICES.md")
        if normalise(name) in named
        else Package("npm", name, False, "the bundle carries no licence text, no heading")
        for name in names
    ]


def image_distributions(image: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image]
        + ["-c", _LIST_DISTRIBUTIONS],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"listing distributions in {image} failed: {result.stderr.strip()}")
    distributions: list[dict[str, object]] = json.loads(result.stdout)
    return distributions


def report(packages: list[Package]) -> str:
    lines = ["| Ecosystem | Packages | Covered | Not covered |", "| --- | --- | --- | --- |"]
    for ecosystem in ("python", "npm"):
        these = [package for package in packages if package.ecosystem == ecosystem]
        covered = sum(package.covered for package in these)
        lines.append(f"| {ecosystem} | {len(these)} | {covered} | {len(these) - covered} |")
    uncovered = [package for package in packages if not package.covered]
    if uncovered:
        lines += ["", "Not covered:", ""]
        lines += [f"- {package.ecosystem} `{package.name}`: {package.how}" for package in uncovered]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--image", required=True, help="a built image, by tag or id")
    parser.add_argument("--notices", type=Path, default=NOTICES)
    parser.add_argument("--package-lock", type=Path, default=PACKAGE_LOCK)
    parser.add_argument("--summary", type=Path, help="also append the report to this file")
    args = parser.parse_args(argv)

    named = named_in_notices(args.notices.read_text())
    lock = json.loads(args.package_lock.read_text())
    packages = python_packages(image_distributions(args.image), named) + npm_packages(
        npm_production_names(lock), named
    )
    text = report(packages)
    sys.stdout.write(text)
    if args.summary is not None:
        with args.summary.open("a") as summary:
            summary.write("## Third-party notices coverage\n\n" + text)
    uncovered = sum(not package.covered for package in packages)
    if uncovered:
        print(
            f"{uncovered} of {len(packages)} third-party packages in {args.image} have no "
            "licence text in the image. Publishing it would redistribute them without "
            "their notices.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
