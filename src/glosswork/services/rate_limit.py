"""Login rate limiting (FR-I1; DD-9; DD-14).

Argon2id's ~33 ms per verification is "a partial throttle, not a limiter": it caps a single
core near 30 guesses per second, which slows an online attack by a constant factor and stops
nothing. The bootstrap administrator is a local password account created by the operator CLI
and is the highest-value credential in the deployment, so the login endpoint is the one
place where that constant factor is not enough.

**There are two windows, and an attempt is counted into both.**

The first is keyed on the (lowercased email, source IP) **pair**, and the behaviour it
wants settles why that rather than either half alone: "a successful login from a
different IP is unaffected". An email-only counter cannot
satisfy that -- tripping it for one attacker would lock the real owner out from
everywhere, converting a password-guessing attempt into a denial of service against
exactly the account this protects. An IP-only counter would let a shared office NAT
lock out a floor of colleagues between them.

That reasoning is right as anti-lockout policy, and it is not enough on its own: the
unknown-email branch runs a real Argon2id verification against a dummy hash so timing
matches (``services/passwords.py``), at ``m=65536 KiB`` and ``p=4`` per call, so with a
pair window alone **a fresh random email per request would open a fresh pair window
every time**. Thirty requests from one address would buy thirty full hashes and no
refusal. ``MAX_TRACKED_KEYS`` bounds the dictionary but not the work, which would be
unauthenticated memory and CPU amplification against a memory-capped container.

So the second window is keyed on the **source address alone**, with its own and far
more generous budget (``GW_LOGIN_IP_MAX_ATTEMPTS``, default 60 per five minutes -- six
times the per-account budget). It is not a sentinel key in the first dictionary:
``("", ip)`` is exactly what a login POST with an empty email produces, and
``check_and_record`` runs before anything validates the email, so such a request would
be counted twice into one bucket, trip at half budget, and be unclearable by ``reset``.
A second dictionary with the same window values and the same prune has no such overlap.

**A success clears the pair window only.** Clearing the source's budget on success
would hand an attacker who holds one valid account an unlimited budget for guessing
every other one.

**Counting happens before anything looks the email up**, which is what preserves the
account-existence indistinguishability ``login_with_password`` builds in with its
dummy-hash branch. An unknown email and a known one consume the same budget and get
the same 429 with the same body. A limiter that skipped counting for unknown accounts
-- or that said so in its message -- would hand back the enumeration oracle the
dummy-hash branch exists to close. The 429 does name which window tripped, but in its
``details`` rather than its message: the message is what a person reads, and "too many
login attempts" is the right sentence for both.

**Eviction may never clear a window that is doing the blocking.** The cap applies to
the union of the two dictionaries; expired windows are pruned first, and only then is a
live one dropped. "Fails open for one key" would be an acceptable description of that if
every key were a pair. It is a bypass of the control once a key can be the thing
blocking an attacker: an order deque that appended a key only when it was new would
never re-append a long-lived source window, which would sit permanently at the head and
be the *first* thing dropped once the cap was reached -- roughly
``MAX_TRACKED_KEYS / ip_max_attempts`` other sources would hand a blocked address a
clean budget. Eviction therefore picks the least *recently updated* window and skips any
window at or over its own ceiling.

In-process and non-persistent, deliberately. FR-P1 is a single container and a single
process, so there is no second replica for an in-memory counter to disagree with, and
a restart clearing the window is a worse outcome than a schema migration and a write
on every login attempt would be. A deployment that grows past one process needs a
shared limiter, and that is a different decision than this one.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import NamedTuple, TypeVar

from glosswork.errors import RateLimitedError

# A ceiling on how many windows are tracked at once, across **both** dictionaries, so
# that an attacker cycling addresses cannot grow them without bound. Expired windows
# are pruned first; only if that is not enough is a live one dropped, and never one at
# or over its ceiling.
MAX_TRACKED_KEYS = 10_000

K = TypeVar("K", bound=Hashable)


class _Window(NamedTuple):
    """One fixed window. ``updated`` is separate from ``started`` because eviction
    orders by last use and the window's start must not move when it is touched."""

    started: float
    attempts: int
    updated: float


class LoginRateLimiter:
    """Two fixed-window attempt counters: one per (lowercased email, source IP) pair,
    one per source address.

    Fixed window rather than sliding: it is trivially auditable ("ten attempts per five
    minutes"), and its known weakness -- up to twice the ceiling across a window boundary --
    is irrelevant against an attacker who needs millions of guesses, not twenty.
    """

    def __init__(self, max_attempts: int, ip_max_attempts: int, window_seconds: int) -> None:
        if max_attempts < 1 or ip_max_attempts < 1 or window_seconds < 1:
            raise ValueError(
                "max_attempts, ip_max_attempts and window_seconds must all be positive"
            )
        self._max_attempts = max_attempts
        self._ip_max_attempts = ip_max_attempts
        self._window_seconds = window_seconds
        # Both are LRU-ordered: every touch moves the key to the end, so the front is
        # the least recently updated window and eviction is O(1) in the common case.
        self._windows: OrderedDict[tuple[str, str], _Window] = OrderedDict()
        self._ip_windows: OrderedDict[str, _Window] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(email: str, source_ip: str) -> tuple[str, str]:
        """Lowercase and strip the email so ``Ada@Example.com `` and ``ada@example.com``
        share a budget; an attacker must not get a fresh window per capitalization."""
        return (email.strip().lower(), source_ip)

    def check_and_record(self, email: str, source_ip: str, *, attempt: str = "login") -> None:
        """Count one attempt against both windows, or refuse it.

        Called *before* the credential is checked, so the attempt is counted whether
        or not the account exists and whether or not the password is right. The source
        window is checked first and the pair window second; both are counted before the
        lookup, so neither can become an account-existence oracle. Raises
        :class:`~glosswork.errors.RateLimitedError` carrying the seconds left in
        the window that refused and, in its details, which window that was.

        ``attempt`` is passed straight through to ``RateLimitedError`` so its
        message names what kind of attempt tripped the limiter -- ``"login"`` by
        default, so every existing caller is unaffected, and ``"password change"`` for
        the one other route that shares this limiter's windows and keys.
        """
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._count(self._ip_windows, source_ip, self._ip_max_attempts, "source", now, attempt)
            self._count(
                self._windows,
                self._key(email, source_ip),
                self._max_attempts,
                "account",
                now,
                attempt,
            )
            self._evict(now)

    def reset(self, email: str, source_ip: str) -> None:
        """Clear the **pair** window after a successful login.

        So that a colleague who mistypes their password twice in the morning has a
        full budget again in the afternoon. Safe to expose: a successful login is
        already known to whoever performed it, so clearing on success leaks nothing an
        attacker who succeeded does not have. The source window is deliberately left
        alone -- see the module docstring.
        """
        key = self._key(email, source_ip)
        with self._lock:
            self._windows.pop(key, None)

    # ------------------------------------------------------------ internals

    def _count(
        self,
        windows: OrderedDict[K, _Window],
        key: K,
        ceiling: int,
        label: str,
        now: float,
        attempt: str,
    ) -> None:
        window = windows.get(key)
        if window is None or now - window.started >= self._window_seconds:
            window = _Window(started=now, attempts=0, updated=now)  # new, or it rolled
        if window.attempts >= ceiling:
            remaining = self._window_seconds - (now - window.started)
            # Ceil, and never below 1: a Retry-After of 0 invites an immediate
            # retry that is certain to be refused again.
            raise RateLimitedError(max(1, math.ceil(remaining)), window=label, attempt=attempt)
        windows[key] = _Window(window.started, window.attempts + 1, now)
        windows.move_to_end(key)

    def _prune(self, now: float) -> None:
        """Drop windows that have fully expired.

        Amortized over calls rather than run on a timer: the endpoint this guards is
        low-traffic by nature, so there is no thread to justify and nothing to stop at
        shutdown.
        """
        self._prune_one(self._windows, now)
        self._prune_one(self._ip_windows, now)

    def _prune_one(self, windows: OrderedDict[K, _Window], now: float) -> None:
        expired = [
            key for key, window in windows.items() if now - window.started >= self._window_seconds
        ]
        for key in expired:
            del windows[key]

    def _evict(self, now: float) -> None:
        """Hold the union of both dictionaries under ``MAX_TRACKED_KEYS``.

        Picks the least recently updated window across the two, and **never one at or
        over its ceiling**: a window that is currently refusing attempts is the
        control itself, and dropping it hands whoever it is blocking a clean budget. If
        every remaining window is blocking, nothing is dropped and the dictionaries sit
        marginally above the cap rather than the limiter defeating itself; each is still
        bounded by its own ceiling times the number of live windows it can hold.
        """
        del now  # windows are already pruned; eviction orders by last update
        while len(self._windows) + len(self._ip_windows) > MAX_TRACKED_KEYS:
            pair = self._oldest_evictable(self._windows, self._max_attempts)
            source = self._oldest_evictable(self._ip_windows, self._ip_max_attempts)
            if pair is None and source is None:
                return
            if source is None or (pair is not None and pair[1] <= source[1]):
                assert pair is not None
                del self._windows[pair[0]]
            else:
                del self._ip_windows[source[0]]

    @staticmethod
    def _oldest_evictable(windows: OrderedDict[K, _Window], ceiling: int) -> tuple[K, float] | None:
        """The least recently updated window that is not at or over ``ceiling``."""
        for key, window in windows.items():
            if window.attempts < ceiling:
                return (key, window.updated)
        return None
