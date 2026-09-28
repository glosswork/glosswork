"""The `web/src/` half of removing the interim credential (DD-8): what the interim
build-time PAT left behind, and what must be gone now that the session cookie replaced it.

Verified non-vacuous against the tree that still carried the interim credential, the same
way the source-tree sweep was: each assertion here had something real to remove before the
session cookie replaced it (`VITE_DT_API_TOKEN` in `web/src/api/client.ts` and
`web/src/vite-env.d.ts`; an `Authorization` header construction in `client.ts`;
`CURRENT_PRINCIPAL_ID` across six files) and a positive companion assertion (the API
client actually sends `credentials: "include"`) so the sweep cannot pass by leaving the
app unable to authenticate at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = REPO_ROOT / "web" / "src"
WEB_DIST = REPO_ROOT / "web" / "dist"


def _grep(needle: str, *paths: Path, extended: bool = False) -> str:
    flags = ["-rn"]
    if extended:
        flags.append("-E")
    result = subprocess.run(
        ["grep", *flags, needle, *[str(p) for p in paths]],
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_vite_api_token_is_gone_from_web_src() -> None:
    """Both spellings. ``VITE_DT_API_TOKEN`` is the name the removed variable actually
    had, so it is the one that proves the removal; ``VITE_GW_API_TOKEN`` is the name a
    reintroduction would carry now that the project is Glosswork, so it is the one that
    keeps this a guard rather than a fossil."""
    assert _grep("VITE_DT_API_TOKEN", WEB_SRC) == ""
    assert _grep("VITE_GW_API_TOKEN", WEB_SRC) == ""


def test_current_principal_id_constant_is_gone_from_web_src() -> None:
    """The removed *symbol*: a local test variable happens to be named
    `TEST_PRINCIPAL_ID` now precisely so this sweep is not satisfied vacuously by a
    same-named local alias."""
    assert _grep("CURRENT_PRINCIPAL_ID", WEB_SRC) == ""


def test_no_authorization_header_is_constructed_in_the_api_client() -> None:
    assert _grep('"Authorization"', WEB_SRC / "api" / "client.ts") == ""


def test_the_api_client_sends_credentials_so_it_can_still_authenticate() -> None:
    """The positive companion assertion: deleting the header and leaving the app
    unable to authenticate at all must not pass this sweep."""
    content = (WEB_SRC / "api" / "client.ts").read_text()
    assert 'credentials: "include"' in content


def test_gw_pat_literal_is_absent_from_a_built_dist_if_one_exists() -> None:
    """The mechanism that could ever put a `gw_pat_` literal in the browser bundle
    (`VITE_DT_API_TOKEN`) no longer exists, so this is regression protection rather
    than an active guard (`frontend_guard.py`, which enforced it at startup, is
    deleted along with the thing it guarded). Skipped when `web/dist` was never
    built, exactly like the deleted guard's own test did."""
    if not WEB_DIST.is_dir():
        return
    assert _grep("gw_pat_", WEB_DIST) == ""
