"""The SPA catch-all, and what it must never absorb.

``create_app`` registers a ``GET /{full_path:path}`` fallback last, so a hard refresh of
a client-side route like ``/initiative/INIT-014`` serves ``index.html``. That route
exists **only when ``web/dist`` sits beside the source**, which is why it has had no
coverage at all: the Python suite never runs ``npm run build``, so on most machines the
catch-all is simply absent and every test that would have noticed it passes.

Two consequences, and this file exists for both.

The first is a defect. An unknown path under ``/api/`` was answered ``200 text/html``
rather than ``404 application/json`` whenever the frontend was built, which is always in
the image. It needed a credential to see: unauthenticated, ``/api/v1/no-such-route``
answers ``401 application/json``, so a reproduction without a token concludes there is no
bug.

The second is a trap. Thirteen assertions in this repository read
``assert client.get("/readyz").status_code == 200`` and passed against a route that did
not exist, because the catch-all answered 200 in its place. Those thirteen now assert a
body. ``test_a_status_only_assertion_cannot_see_a_missing_route`` is the demonstration
that makes the repair legible.

Every test here builds its own ``web/dist`` rather than depending on the environment. A
criterion proven only in a tree where ``web/dist`` happens to exist proves nothing about
CI, and one proven only where it does not exist proves nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import glosswork.app as app_module
from glosswork.app import create_app
from glosswork.config import Settings
from tests.conftest import auth, mint_scope_tokens

#: Enough of a build for ``FileResponse`` to serve. ``assets/`` is deliberately absent:
#: the mount is optional and this file is about the catch-all, not about static serving.
INDEX_HTML = "<!doctype html><html><body><div id=root></div></body></html>"

#: A path the real frontend routes on. It matches no server route, so it is the catch-all
#: or nothing, and asserting it first is what keeps the ``/api/`` assertions from passing
#: because the fallback was never registered.
SPA_PATH = "/initiative/INIT-014"


def _app_with_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """An application whose SPA fallback is live, built over its own two-file dist."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(INDEX_HTML)
    monkeypatch.setattr(app_module, "FRONTEND_DIST_DIR", dist)
    return create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))


def _app_without_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """An application whose SPA fallback was never registered."""
    monkeypatch.setattr(app_module, "FRONTEND_DIST_DIR", tmp_path / "no-such-dist")
    return create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))


@pytest.fixture
def spa_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client on an app with the fallback live, carrying a real ``admin`` PAT.

    The credential matters: without one, ``/api/`` answers 401 before the router ever
    reaches the catch-all, and the whole defect is invisible.
    """
    app = _app_with_dist(tmp_path, monkeypatch)
    with TestClient(app) as client:
        tokens = mint_scope_tokens(app.state.services)
        client.headers.update(auth(tokens["admin"]))
        yield client


def test_the_catch_all_is_live_and_serves_the_spa(spa_client: TestClient) -> None:
    """The premise every other assertion in this file rests on."""
    response = spa_client.get(SPA_PATH)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.text == INDEX_HTML


@pytest.mark.parametrize("credentialed", [True, False])
def test_an_unknown_api_path_is_404_json_with_the_fallback_live(
    spa_client: TestClient, credentialed: bool
) -> None:
    """Matching the predicate rather than exceeding it: a ``GET`` whose
    path begins with the exact string ``/api/`` and matches no route returns 404 JSON.
    ``is_api_path`` is ``path.startswith("/api/")`` and nothing more.

    Uncredentialed the answer is 401 JSON rather than 404 JSON, which is the auth layer
    answering first and is equally not HTML. Both are asserted because the credentialed
    case is the one nobody could see.
    """
    headers = None if credentialed else {"Authorization": ""}
    response = spa_client.get("/api/v1/no-such-route", headers=headers)
    assert response.status_code == (404 if credentialed else 401), response.text
    assert "application/json" in response.headers["content-type"]
    assert "<!doctype html>" not in response.text.lower()


def test_an_unknown_api_path_is_404_json_with_no_frontend_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same answer in the tree CI actually runs in.

    With no ``web/dist`` the catch-all does not exist, so the router answers 404 by
    itself. The point of asserting it is that the two environments now agree, which is
    what makes the repair provable in a frontend-free job.
    """
    app = _app_without_dist(tmp_path, monkeypatch)
    with TestClient(app) as client:
        tokens = mint_scope_tokens(app.state.services)
        client.headers.update(auth(tokens["admin"]))
        api = client.get("/api/v1/no-such-route")
        spa = client.get(SPA_PATH)
    assert api.status_code == 404, api.text
    assert "application/json" in api.headers["content-type"]
    assert spa.status_code == 404, spa.text


def test_a_status_only_assertion_cannot_see_a_missing_route(spa_client: TestClient) -> None:
    """Why the thirteen repaired sites now assert a body.

    ``/readyz`` is removed from the live application's router and the catch-all is left
    in place, which is exactly the state the thirteen assertions could not distinguish
    from a healthy deployment.

    **This is a fence for the ``/api/`` rule** and is labelled one: ``/readyz`` is not
    under ``/api/``, so ``is_api_path`` does not touch it and these assertions hold
    without the rule too. What it proves is the property the thirteen repairs depend on,
    that the strengthened assertion form is capable of failing where the status-only one
    is not, which is a claim about the assertions and not about the route.
    """
    app: FastAPI = spa_client.app  # type: ignore[assignment]
    routes = app.router.routes
    kept = [route for route in routes if getattr(route, "path", None) != "/readyz"]
    assert len(kept) == len(routes) - 1, "the /readyz route was not found to remove"
    app.router.routes = kept
    try:
        response = spa_client.get("/readyz")
    finally:
        app.router.routes = routes

    # The trap, stated rather than implied: the old assertion form still passes here.
    assert response.status_code == 200
    # The repaired form, which is what the thirteen sites now make.
    assert "text/html" in response.headers["content-type"]
    with pytest.raises(ValueError):
        response.json()


@pytest.mark.parametrize(
    "path",
    ["//api/v1/no-such-route", "/API/v1/no-such-route", "/%2fapi/v1/no-such-route", "/api"],
)
def test_the_four_near_misses_still_answer_the_spa(spa_client: TestClient, path: str) -> None:
    """The predicate's edges, pinned as chosen rather than missed.

    ``is_api_path`` is an exact ``startswith("/api/")``, so a doubled slash, a different
    case, a percent-encoded slash and the bare prefix all fall through to the catch-all.
    None of them is a route, none is written by a test, and widening the predicate would
    put a second path rule beside the one ``middleware.is_api_path`` already keeps honest
    against ``scopes.SCOPE_EXEMPT_PATHS``.

    **This is a fence.** It cannot fail by construction, it is not counted as coverage,
    and it exists so a later reader sees the edges.
    """
    response = spa_client.get(path)
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers["content-type"]
