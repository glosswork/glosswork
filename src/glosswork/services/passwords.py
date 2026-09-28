"""Password hashing for local ``standalone`` accounts (FR-I1).

Argon2id via ``argon2-cffi``, never hand-rolled: no ``hashlib`` call touches a
password anywhere in this codebase. The PHC output string is self-describing
(``$argon2id$v=19$m=65536,t=3,p=4$...``), so ``principals.password_hash TEXT`` carries
its own parameters and a cost change needs no schema migration —
``check_needs_rehash`` upgrades a stored hash transparently on the owner's next
successful login.

Cost parameters come from ``Settings`` (``GW_ARGON2_TIME_COST``,
``GW_ARGON2_MEMORY_KIB``, ``GW_ARGON2_PARALLELISM``) because the memory cost is a
real working-set consideration: each concurrent hash holds ``memory_kib`` KiB, 64 MiB
at the shipped default.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from glosswork.config import Settings
from glosswork.errors import ValidationFailedError

# Verified against a real hash rather than a constant so it costs the same as a real
# verification: the unknown-email branch of a login runs this to keep the two paths
# indistinguishable in timing (FR-I1).
_DUMMY_PASSWORD = "gw-dummy-password-for-constant-time-verification"


class PasswordPolicy:
    """Minimal and stated: a length floor only, no composition rules.

    FR-S10's "min/max and regex validation are out of scope" is about *field*
    constraints, so this is its own small decision rather than implementer taste: a
    too-short password is rejected with ``validation_failed``, and nothing else about
    a password's contents is ever checked.
    """

    def __init__(self, min_length: int) -> None:
        self.min_length = min_length

    def check(self, password: str) -> None:
        if len(password) < self.min_length:
            raise ValidationFailedError(
                f"Password must be at least {self.min_length} characters. This deployment "
                "enforces a length floor only: there are no composition rules.",
                min_length=self.min_length,
            )


class PasswordService:
    """Hash, verify, and transparently upgrade local-account passwords."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )
        self.policy = PasswordPolicy(settings.password_min_length)
        self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)

    @property
    def parameters(self) -> tuple[int, int, int]:
        """``(time_cost, memory_kib, parallelism)`` actually in force, so a test can
        assert a non-default ``Settings`` value reached the hasher."""
        return (self._hasher.time_cost, self._hasher.memory_cost, self._hasher.parallelism)

    def hash(self, password: str) -> str:
        self.policy.check(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """True when ``password`` matches. A mismatched or malformed stored hash is
        False, never an exception, so every caller takes one code path."""
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def verify_dummy(self) -> None:
        """Burn one verification against a real Argon2id hash.

        Called on the unknown-email branch of a login so that branch does the same
        work as the found-principal branch. Without it, "no such user" returns in
        microseconds while "wrong password" takes ~30 ms, which is a user-enumeration
        oracle regardless of the two responses being byte-identical.
        """
        self.verify(self._dummy_hash, _DUMMY_PASSWORD + "-wrong")

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
