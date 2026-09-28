"""Cookie names and attributes shared between the middleware (which reads them) and
the auth routes (which set them), so the two cannot drift apart (DD-9, DD-10).

Every cookie this application sets is ``HttpOnly`` except ``gw_csrf``, which the SPA
must read with script to echo in the ``X-GW-CSRF`` header — it is not a credential on
its own, only a value the server can prove it issued.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from glosswork.config import Settings

SESSION_COOKIE_NAME = "gw_session"
CSRF_COOKIE_NAME = "gw_csrf"
OIDC_TRANSACTION_COOKIE_NAME = "gw_oidc_tx"

CSRF_HEADER_NAME = "x-gw-csrf"

# Methods a CSRF-protected request never needs to guard: no GET/HEAD/OPTIONS route
# mutates (a fact asserted by its own test), so restricting enforcement to everything
# else is what makes `SameSite=Lax` sufficient as the first layer rather than assumed
# sufficient (DD-10).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# ~10 minutes: long enough for a human to complete an IdP login, short enough that an
# abandoned transaction cookie is not a standing liability (DD-10).
OIDC_TRANSACTION_MAX_AGE_SECONDS = 600
OIDC_CALLBACK_PATH_PREFIX = "/api/v1/auth/oidc"


class CookieKwargs(TypedDict):
    path: str
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]


def cookie_kwargs(settings: Settings, *, http_only: bool, path: str = "/") -> CookieKwargs:
    """The attributes every cookie this application sets shares: ``Secure`` from
    configuration (never inferred from the request scheme — see ``Settings.cookie_secure``
    for why), ``SameSite=Lax`` (DD-10's OIDC-callback constraint), ``Path``, and no
    ``Domain`` (host-only, which also narrows DD-10's subdomain exposure)."""
    return {
        "path": path,
        "httponly": http_only,
        "secure": settings.cookie_secure,
        "samesite": "lax",
    }
