"""Guards against a hand-edited or stale ``web/src/api/schema.ts``: a check must fail if
the checked-in generated file drifts from the current OpenAPI document.

This regenerates the types from the *current* backend's ``/openapi.json`` (fetched
in-process through the ``client`` fixture, no real network server required) into a
scratch file with the same ``openapi-typescript`` version the project pins, and
diffs that output against the committed file byte-for-byte. A mismatch means someone
either hand-edited the generated file or changed the backend without regenerating it.

Requires ``npm``/``npx`` on PATH, same as the rest of this repo's frontend toolchain
(Vitest, Playwright); this test does not skip when it's missing; it fails loudly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_DIR = _REPO_ROOT / "web"
_COMMITTED_SCHEMA = _WEB_DIR / "src" / "api" / "schema.ts"


def test_generated_api_types_are_fresh(client: TestClient, tmp_path: Path) -> None:
    openapi_document = client.get("/openapi.json").json()
    openapi_path = tmp_path / "openapi.json"
    openapi_path.write_text(json.dumps(openapi_document))

    regenerated_path = tmp_path / "schema.ts"
    result = subprocess.run(
        [
            "npx",
            "--prefix",
            str(_WEB_DIR),
            "openapi-typescript",
            str(openapi_path),
            "-o",
            str(regenerated_path),
        ],
        cwd=_WEB_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "openapi-typescript failed to run (is npm/npx on PATH and "
        f"`npm --prefix web install` done?):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    regenerated = regenerated_path.read_text()
    committed = _COMMITTED_SCHEMA.read_text()
    assert regenerated == committed, (
        "web/src/api/schema.ts is stale against the current /openapi.json. "
        "Regenerate it against a running instance and commit the result: "
        "npm --prefix web run generate:api-types"
    )
