"""Shared FastAPI dependencies for the REST layer (DD-3: routes are thin adapters).

``ActorContext`` is constructed once at the edge by ``RequestContextMiddleware`` and
stashed on ``request.state`` (DD-4); these dependencies expose it and the service
bundle to route handlers so no route reaches into ``request.state`` directly.
"""

from __future__ import annotations

from fastapi import Request

from glosswork.actor import ActorContext
from glosswork.config import Settings
from glosswork.services import ServiceBundle


def get_actor(request: Request) -> ActorContext:
    actor: ActorContext = request.state.actor
    return actor


def get_services(request: Request) -> ServiceBundle:
    services: ServiceBundle = request.app.state.services
    return services


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_request_id(request: Request) -> str:
    request_id: str = request.state.request_id
    return request_id


def source_ip(request: Request) -> str:
    """The address the login attempt came from, for the rate limiter's key.

    ``request.client`` is what uvicorn's ``proxy_headers=True`` rewrites from
    ``X-Forwarded-For``, but only for proxies named in ``GW_TRUSTED_PROXY_IPS``
    (FR-P7, ``entrypoint.py``). That is the correct dependency: taking the header
    directly would let any client pick its own limiter bucket by forging it, which is
    a limiter that limits nothing. A deployment behind an untrusted proxy sees every
    attempt as coming from the proxy, which fails closed -- shared budget -- rather
    than open.
    """
    return request.client.host if request.client else "unknown"
