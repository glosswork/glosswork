"""The interim-auth vocabulary that had to leave `src/` when real tokens replaced it.

The second guard of that removal, the build-time-PAT acknowledgement variable and its
module, protected the credential the browser carried until the session cookie replaced
it, and was removed along with that credential. Its tests moved to
`test_interim_credential_removed_from_web.py`, which proves the removal rather than the
guard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from glosswork.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# The interim credential vocabulary, from before real tokens. None of it may appear in
# any non-test source file under `src/`. Tests may still name these where
# they exercise `PatTokenResolver` against legacy-shaped input or document the removal;
# no non-test code may.
FORBIDDEN_IN_SRC = (
    "gw_pat_admin",
    "gw_pat_read",
    "gw_pat_write",
    "InterimTokenResolver",
    "GW_INSECURE_INTERIM_AUTH",
)


@pytest.mark.parametrize("needle", FORBIDDEN_IN_SRC)
def test_no_interim_auth_vocabulary_survives_in_src(needle: str) -> None:
    """Grep-backed, over `src/` only.

    `test_interim_credential_removed_from_web.py` carries the equivalent sweep scoped to
    `web/src/` and `web/dist`, which is where the bundle-baked interim credential lived.
    """
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", needle, str(SRC_DIR)], capture_output=True, text=True
    )
    assert result.stdout == "", (
        f"{needle!r} still appears in non-test source under src/:\n{result.stdout}"
    )


def test_the_deleted_names_are_actually_gone_from_the_auth_module() -> None:
    """A grep proves absence of a string; this proves absence of the *symbols*, so the
    test cannot be satisfied by a rename that leaves the interim resolver in place."""
    import glosswork.auth as auth_module
    import glosswork.config as config_module

    assert not hasattr(auth_module, "InterimTokenResolver")
    assert not hasattr(auth_module, "InsecureInterimAuthNotAcknowledgedError")
    assert not hasattr(auth_module, "INTERIM_TOKENS")
    assert "insecure_interim_auth" not in Settings.model_fields
    assert not hasattr(config_module, "INTERIM_TOKENS")


def test_fixed_scope_resolver_survives_because_it_is_a_test_seam() -> None:
    """`FixedScopeResolver` is deliberately kept. It is not an interim
    credential: it returns a fixed identity for a test that wants to assert what a
    given scope can see, and it is what proved the DD-8 seam was swappable."""
    from glosswork.auth import FixedScopeResolver

    assert FixedScopeResolver("read").resolve(None).scope == "read"
