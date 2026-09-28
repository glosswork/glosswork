"""Retired names stay retired, everywhere in the repository.

This module searches the whole tree, documents included, so it sits in the ``structural``
lane: CI runs that lane on every change, and a documentation-only change is exactly the
kind that could write a retired name back. It moved here from
``tests/test_interim_credential_removed_from_web.py``, where it ran only when a change touched code.

No document may write the retired flag's literal name, this module's own docstring
included: the allowlist admits this file alone, and names it by ``__file__``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gw_insecure_browser_token_auth_appears_only_in_documentation() -> None:
    """The variable and the flag it guarded are gone from the product entirely, and this
    file is the only place the literal legitimately appears. A hit anywhere else means the mechanism
    came back."""
    needle = "GW_INSECURE_BROWSER_TOKEN_AUTH"
    result = subprocess.run(
        ["grep", "-rl", needle, str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    hits = [
        line
        for line in result.stdout.splitlines()
        if line
        and "/.git/" not in line
        and "/node_modules/" not in line
        and "/__pycache__/" not in line
        and "/web/dist/" not in line
    ]
    allowed = {
        str(Path(__file__).resolve()),  # names the variable to assert its absence elsewhere
    }
    offenders = [line for line in hits if line not in allowed]
    assert offenders == [], offenders
