"""Container entry point: validate configuration, then serve."""

from __future__ import annotations

import ssl
import sys
from typing import Any

import uvicorn

from glosswork.config import ConfigError, load_settings


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # The workspace's own TLS (DD-46). The four arguments travel together, from this one
    # branch, or not at all: the certificate and key without the other two are TLS that
    # lets anyone in, and the CA without the certificate is ignored by uvicorn, which
    # then serves plain HTTP. With the settings off the call below carries no ``ssl_*``
    # argument, so it is the call it was before they existed.
    tls: dict[str, Any] = {}
    if settings.tls_enabled:
        tls = {
            "ssl_certfile": str(settings.tls_cert_file),
            "ssl_keyfile": str(settings.tls_key_file),
            "ssl_ca_certs": str(settings.tls_client_ca_file),
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
        }

    uvicorn.run(
        "glosswork.app:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        # One process per database, by design: startup reclaims every `running`
        # embedding job (DD-35), and the login limiter and usage counter live in
        # memory. Left unset, uvicorn reads WEB_CONCURRENCY and would start a second
        # process whose embedding worker reclaims rows the first still holds, so the
        # count is pinned and that variable is ignored.
        workers=1,
        log_config=None,
        log_level=settings.log_level,
        # The application middleware emits JSON access logs; uvicorn's own access
        # lines are not JSON (FR-P5).
        access_log=False,
        proxy_headers=True,
        # FR-P7: uvicorn's own default trusts only loopback. Behind a
        # TLS-terminating proxy that connects from anywhere else (a container's bridge
        # gateway, for instance), the unconfigured default silently discards
        # X-Forwarded-Proto, which is exactly the trap GW_COOKIE_SECURE's own default
        # exists to not depend on getting right — but the access log's client
        # attribution (FR-P5) and GW_BASE_URL's OIDC redirect still need this set
        # correctly for a real deployment.
        forwarded_allow_ips=settings.trusted_proxy_ips,
        **tls,
    )


if __name__ == "__main__":
    main()
