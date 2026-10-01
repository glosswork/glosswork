"""REST scope enforcement (FR-I3, FR-M4's REST counterpart, DD-8).

This suite exists to make "enforcement lands as one
change across all routes" *checkable* rather than asserted: the sweep below cannot
pass while any route is unadopted, and the completeness meta-test cannot pass while
any route ships without a declared scope.

Four properties, each its own test:

1. **Completeness.** Every registered route declares a required scope, and the
   exemption allowlist contains exactly the named entries — so a new unprotected route
   cannot be waved through by widening the allowlist.
2. **Declaration discipline.** No route module reads scope at all. Enforcement is one
   central comparison, not forty handler-local ones.
3. **The sweep.** A `read` PAT is refused on every `write` and `admin` route and
   accepted on every `read` route; a `write` PAT is refused on every `admin` route.
4. **Cross-surface consistency.** Every capability on both surfaces declares the same
   scope on each, walking the docs/MCP_TOOLS.md section 8 parity table.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.middleware import AUTH_PUBLIC_PATHS
from glosswork.scopes import (
    SCOPE_EXEMPT_PATHS,
    RouteScope,
    route_roles,
    route_scopes,
    undeclared_routes,
)
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor
from tests.mcp_support import memory_session
from tests.test_mcp_catalog import SECTION_8_PARITY

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = REPO_ROOT / "src" / "glosswork" / "routes"

# Methods that carry a request body. The sweep sends `{}` for these so a JSON parse
# error cannot preempt the scope check and make an unenforced route look enforced.
BODY_METHODS = frozenset({"POST", "PATCH", "PUT"})


def _request(client: TestClient, entry: RouteScope) -> Any:
    """Issue the least interesting possible request against one route.

    Path parameters get a placeholder that resolves to nothing, because the sweep is
    about authorization and not about the handler: an in-scope call answering 404 is a
    pass, and an out-of-scope call must be refused before the handler is reached at
    all.
    """
    path = entry.path
    for placeholder in ("{ref}", "{key}", "{object_type_key}", "{field_key}", "{label_id}"):
        path = path.replace(placeholder, "sweep")
    while "{" in path:
        start = path.index("{")
        end = path.index("}", start)
        path = path[:start] + "00000000-0000-4000-8000-0000000000ff" + path[end + 1 :]
    kwargs: dict[str, Any] = {}
    if entry.method in BODY_METHODS:
        kwargs["json"] = {}
    return client.request(entry.method, path, **kwargs)


def _is_insufficient_scope(response: Any) -> bool:
    if response.status_code != 403:
        return False
    try:
        return bool(response.json()["error"]["code"] == "insufficient_scope")
    except Exception:  # pragma: no cover - a 403 without the envelope is a failure
        return False


def _routes_at(app: FastAPI, *scopes: str) -> list[RouteScope]:
    return [entry for entry in route_scopes(app) if entry.scope in scopes]


# ------------------------------------------------------------------ completeness


def test_every_registered_route_declares_a_required_scope(app: FastAPI) -> None:
    """The completeness meta-test, in the style of the service-method-to-route walk. A
    route added without a scope fails the suite here, which is what makes "declared once
    at registration" structural rather than a review promise."""
    missing = undeclared_routes(app)
    assert missing == [], (
        "These registered routes declare no required scope. Add "
        "dependencies=[require_scope(...)] to each, or, if it genuinely needs no "
        f"credential, name it explicitly in scopes.SCOPE_EXEMPT_PATHS: {missing}"
    )


def test_the_exemption_allowlist_is_exactly_the_named_entries() -> None:
    """The allowlist is asserted to be exactly these entries, so a new unprotected
    route cannot be waved through by quietly widening it. Changing this set is
    supposed to require changing this test, in a diff a reviewer sees.

    ``/api/v1/usage`` is the twelfth entry and the second non-auth-flow one (DD-39): its
    authority is ``GW_OPERATOR_TOKEN``, compared in one function in
    ``services/usage.py``, and a scope declaration on it would pass with no credential
    presented at all."""
    assert SCOPE_EXEMPT_PATHS == frozenset(
        {
            "/healthz",
            "/readyz",
            "/openapi.json",
            "/docs",
            "/mcp",
            "/assets",
            "/{full_path:path}",
            "/api/v1/auth/login",
            "/api/v1/auth/oidc/start",
            "/api/v1/auth/oidc/callback",
            "/api/v1/auth/modes",
            "/api/v1/auth/code/request",
            "/api/v1/auth/code/verify",
            "/api/v1/bootstrap",
            "/api/v1/usage",
        }
    )


def test_the_credential_exempt_allowlist_is_exactly_the_named_paths() -> None:
    """``AUTH_PUBLIC_PATHS`` pinned by equality, exactly as its sibling
    ``SCOPE_EXEMPT_PATHS`` is. Without a test reference, widening it would show in no
    diff a reviewer is obliged to read -- and the branch it feeds is the one that
    decides whether a request needs a credential at all.

    The fifth entry is the first one that is not an auth-flow route:
    ``POST /api/v1/bootstrap`` exchanges ``GW_BOOTSTRAP_SECRET`` for the first admin
    credential, so by construction no credential exists yet to present (DD-15,
    DD-37). The secret rides in the body rather than in ``Authorization``,
    because the edge reads that header as a PAT on every non-exempt path and one header
    meaning two kinds of credential is how a future edit confuses them.

    The sixth, ``GET /api/v1/usage``, is bootstrap's case reversed
    (DD-39). Bootstrap is exempt because no credential exists yet; this one is exempt
    because the credential the edge resolves must **not** open it. A workspace ``admin``
    personal access token is the highest thing a tenant can mint and it is refused here,
    so the header the edge reads and the header this route reads have to be different
    headers, and the route can declare no scope at all: the exempt branch's anonymous
    actor carries ``read``, so ``require_scope("read")`` would pass for a caller
    presenting nothing.
    """
    assert AUTH_PUBLIC_PATHS == frozenset(
        {
            "/api/v1/auth/login",
            "/api/v1/auth/oidc/start",
            "/api/v1/auth/oidc/callback",
            "/api/v1/auth/modes",
            "/api/v1/auth/code/request",
            "/api/v1/auth/code/verify",
            "/api/v1/bootstrap",
            "/api/v1/usage",
        }
    )


def test_every_credential_exempt_path_is_also_scope_exempt() -> None:
    """The two allowlists are not independent: a path that needs no credential cannot
    meaningfully declare a scope, so ``AUTH_PUBLIC_PATHS`` must be a subset of
    ``SCOPE_EXEMPT_PATHS``. The reverse does not hold -- ``/healthz`` and the SPA
    fallback are scope-exempt without being credential-exempt at the middleware, since
    they are not ``/api/`` paths at all.

    **Fence**: true by construction.
    """
    assert AUTH_PUBLIC_PATHS <= SCOPE_EXEMPT_PATHS


def test_a_path_drifting_into_the_credential_exempt_set_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A security probe, as a test.

    If the credential-exempt branch built ``bootstrap_actor``, which carries
    ``scope="admin"`` and belongs to the seeded principal whose role is ``admin``, a
    path added to ``AUTH_PUBLIC_PATHS`` would not run *without* an actor -- it would run
    as a full administrator, satisfying ``require_scope("admin")`` and ``require_role``
    with no credential at all. The existing entries declare no scope, so nothing would
    be exploitable; the failure mode of the *next* entry would be silent full-admin
    exposure rather than a 401. That is precisely the kind of defect a test has to
    catch before the entry is added, not after.

    Simulated rather than waited for, which ``AUTH_PUBLIC_PATHS`` being read as a
    module global on every request (``middleware.py``, ``path in AUTH_PUBLIC_PATHS``)
    makes possible: monkeypatching it takes effect on the next request.

    Asserted in both directions, because "fails closed" is a claim about both: a
    ``read`` route drifting in is answered as a reader, and a ``write`` route drifting
    in is refused. Both halves were watched to fail against a branch that built
    ``bootstrap_actor``, and separately, since an assertion that runs only after a
    failing one has never been measured:

    - the ``read`` half failed on ``scope`` being ``admin`` rather than ``read``;
    - the ``write`` half failed with **404 ``unknown_object_type``** rather than 403.
      That is the defect stated as sharply as it can be. A 404 from the service layer
      means the request was never refused at the credential gate at all -- it passed
      ``require_scope("write")`` with no credential, entered ``RecordService``, and got
      as far as looking the object type up. The scope refusal is what stands in front
      of it.
    """
    import glosswork.middleware

    read_path = "/api/v1/me"
    write_path = "/api/v1/object-types/task/records"
    monkeypatch.setattr(
        glosswork.middleware,
        "AUTH_PUBLIC_PATHS",
        frozenset(AUTH_PUBLIC_PATHS | {read_path, write_path}),
    )
    no_credential = {"Authorization": ""}

    answered = client.get(read_path, headers=no_credential)
    assert answered.status_code == 200
    body = answered.json()
    assert body["scope"] == "read", body
    assert body["id"] == BOOTSTRAP_PRINCIPAL_ID, body

    refused = client.post(write_path, json={"values": {"title": "x"}}, headers=no_credential)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "insufficient_scope", refused.text


def test_route_roles_is_exactly_the_fifteen_system_routes(app: FastAPI) -> None:
    """Asserted as an exact set, by the same argument
    `SCOPE_EXEMPT_PATHS` is: a pattern would silently absorb a future route, and a list
    has to be edited in a diff a reviewer sees.

    These are the routes a `creator` must not reach. It holds `admin` credential scope
    because every schema route declares `admin` (section 2), so scope alone cannot keep
    it out of a whole-database dump or the principal table -- which is exactly why the
    role declaration exists instead of the schema routes being weakened."""
    assert {entry.key for entry in route_roles(app)} == {
        "POST /api/v1/admin/backup",
        "GET /api/v1/admin/export",
        "POST /api/v1/admin/blobs/sweep",
        "GET /api/v1/admin/agent-labels",
        "GET /api/v1/admin/search-index",
        "POST /api/v1/admin/search-index/reindex",
        "GET /api/v1/principals",
        "POST /api/v1/principals",
        "GET /api/v1/principals/{principal_id}",
        "PATCH /api/v1/principals/{principal_id}",
        "DELETE /api/v1/principals/{principal_id}",
        "POST /api/v1/principals/{principal_id}/password",
        "GET /api/v1/invites",
        "POST /api/v1/invites",
        "DELETE /api/v1/invites/{invite_id}",
    }
    assert {entry.role for entry in route_roles(app)} == {"admin"}


def test_every_role_declaring_route_also_declares_admin_scope(app: FastAPI) -> None:
    """`require_role` is *always* an additional narrowing on top of an unchanged
    credential floor, never a mechanism for admitting a weaker credential. A route
    that declared a role and a lower scope would be the opposite."""
    declared = {entry.key: entry.scope for entry in route_scopes(app)}
    for entry in route_roles(app):
        assert declared[entry.key] == "admin", entry


def test_the_sweep_actually_reaches_every_route(app: FastAPI) -> None:
    """A guard on the guards. `fastapi==0.141` keeps an included router as a single
    wrapper object on `app.routes`, so a walk that did not descend into it would see
    six wrappers, find nothing undeclared, and pass vacuously. Asserting a floor on
    the count makes that failure mode loud."""
    entries = route_scopes(app)
    # Raised from 45 when the three grant routes landed (58 -> 61 actual).
    # The floor is a guard on the walk, not a count of the API: it only has to be high
    # enough that a walk which stopped at the six router wrappers would trip it.
    assert len(entries) >= 55, entries
    assert {entry.scope for entry in entries} == {"read", "write", "admin"}


def test_every_scope_declaring_route_lives_under_the_api_prefix(app: FastAPI) -> None:
    """Ties two separate decisions together so they cannot drift.

    `RequestContextMiddleware` demands a credential for `/api/` and nothing else, so
    that health probes and the static bundle (which is what *carries* the credential)
    stay reachable. If a scope-declaring route were ever registered outside that
    prefix it would silently run with the edge actor and enforce nothing. This is the
    test that makes that impossible rather than merely unlikely.
    """
    from glosswork.middleware import is_api_path

    outside = [entry.key for entry in route_scopes(app) if not is_api_path(entry.path)]
    assert outside == [], outside


# ----------------------------------------------------------------- discipline


def test_no_route_module_reads_scope() -> None:
    """Grep-backed: what is not acceptable is any `if actor.scope`
    inside a handler body. Required scope is declared in the decorator and compared in
    exactly one place; a handler that reasons about scope itself is how forty
    slightly-different checks get written.

    `body.scope` in `routes/identity.py` is the *token being minted*'s scope — a
    request field, not the caller's credential — so the pattern is anchored to the
    actor rather than to the bare word.
    """
    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            "-E",
            r"(actor|identity|credential|request)\.scope",
            str(ROUTES_DIR),
        ],
        capture_output=True,
        text=True,
    )
    assert result.stdout == "", (
        "A route module reads the caller's scope. Enforcement belongs in "
        f"scopes.require_scope, not in a handler:\n{result.stdout}"
    )


def test_route_modules_do_not_import_the_enforcement_internals() -> None:
    """Route modules may declare a scope; they may not enforce one. Importing
    `enforce_scope` or `scope_allows` into a route module would be the first step
    back towards handler-local checks."""
    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            "-E",
            r"import .*(enforce_scope|scope_allows)",
            str(ROUTES_DIR),
        ],
        capture_output=True,
        text=True,
    )
    assert result.stdout == "", result.stdout


# ------------------------------------------------------------------- the sweep


def test_read_pat_is_refused_on_every_write_and_admin_route(
    app: FastAPI, client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The headline assertion of this suite. This is the test that cannot pass while
    any route is unadopted: it walks every registered route rather than a list
    somebody maintained by hand."""
    header = auth(api_tokens["read"])
    unenforced: list[tuple[str, int]] = []
    for entry in _routes_at(app, "write", "admin"):
        response = _request(TestClient(app, headers=header), entry)
        if not _is_insufficient_scope(response):
            unenforced.append((entry.key, response.status_code))
    assert unenforced == [], (
        "A 'read' PAT was not refused with 403 insufficient_scope on these "
        f"write/admin routes: {unenforced}"
    )


def test_read_pat_is_accepted_on_every_read_route(
    app: FastAPI, client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The other half: enforcement that refuses everything is not enforcement. A read
    route may answer 404 or 422 for the sweep's placeholder arguments — anything at
    all except `insufficient_scope`."""
    header = auth(api_tokens["read"])
    refused: list[tuple[str, int]] = []
    for entry in _routes_at(app, "read"):
        response = _request(TestClient(app, headers=header), entry)
        if _is_insufficient_scope(response):
            refused.append((entry.key, response.status_code))
    assert refused == [], f"A 'read' PAT was refused on these read routes: {refused}"


def test_write_pat_is_refused_on_every_admin_route(
    app: FastAPI, client: TestClient, api_tokens: dict[str, str]
) -> None:
    header = auth(api_tokens["write"])
    unenforced: list[tuple[str, int]] = []
    for entry in _routes_at(app, "admin"):
        response = _request(TestClient(app, headers=header), entry)
        if not _is_insufficient_scope(response):
            unenforced.append((entry.key, response.status_code))
    assert unenforced == [], f"A 'write' PAT was not refused on these admin routes: {unenforced}"


def test_write_pat_is_accepted_on_write_routes(
    app: FastAPI, client: TestClient, api_tokens: dict[str, str]
) -> None:
    header = auth(api_tokens["write"])
    refused: list[str] = []
    for entry in _routes_at(app, "write"):
        response = _request(TestClient(app, headers=header), entry)
        if _is_insufficient_scope(response):
            refused.append(entry.key)
    assert refused == [], f"A 'write' PAT was refused on these write routes: {refused}"


def test_insufficient_scope_envelope_names_both_scopes(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """`insufficient_scope` is produced and exercised over REST, through the same error
    class and envelope the MCP gate uses."""
    response = client.post(
        "/api/v1/object-types",
        json={},
        headers=auth(api_tokens["read"]),
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "insufficient_scope"
    assert error["details"]["required_scope"] == "admin"
    assert error["details"]["actual_scope"] == "read"
    assert error["details"]["path"] == "/api/v1/object-types"
    assert error["details"]["method"] == "POST"
    assert "admin" in error["message"] and "read" in error["message"]


def test_a_read_pat_cannot_reach_the_admin_agent_label_view(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """FR-I7's cross-user view is a separate admin-scoped route rather than
    `?all=true` on the personal one, precisely so that its higher
    requirement is a declaration and not a branch inside a handler."""
    assert client.get("/api/v1/agent-labels", headers=auth(api_tokens["read"])).status_code == 200
    forbidden = client.get("/api/v1/admin/agent-labels", headers=auth(api_tokens["read"]))
    assert _is_insufficient_scope(forbidden)
    assert client.get("/api/v1/admin/agent-labels").status_code == 200


def test_the_all_query_parameter_is_gone(client: TestClient) -> None:
    """`?all=true` no longer widens the personal route. Asserting the parameter is
    absent from the schema, not just ignored, is what stops it coming back."""
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/agent-labels"]["get"]
    assert {p["name"] for p in operation.get("parameters", [])} == set()
    assert "/api/v1/admin/agent-labels" in spec["paths"]


def test_attachment_download_now_requires_a_resolvable_credential(
    client: TestClient, app_services: ServiceBundle, api_tokens: dict[str, str]
) -> None:
    """The download path needs an authenticated actor context. Without one it would
    serve bytes to anyone who could guess an id; it needs a credential that resolves,
    at `read` scope."""
    uploaded = app_services.attachments.upload(
        make_actor(), filename="notes.txt", content_type="text/plain", content=b"hello"
    )
    path = f"/api/v1/attachments/{uploaded.id}/download"

    # An unverifiable credential no longer serves the bytes. (The no-header case is
    # covered in tests/test_pat_resolver.py, which builds its own bare client; the
    # MCP session manager here can only be entered once per app instance.)
    assert client.get(path, headers=auth("gw_pat_nope")).status_code == 401

    ok = client.get(path, headers=auth(api_tokens["read"]))
    assert ok.status_code == 200
    assert ok.content == b"hello"


# ----------------------------------------------------------- cross-surface parity


@pytest.mark.anyio
async def test_rest_and_mcp_declare_the_same_scope_for_every_shared_capability(
    app: FastAPI, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """FR-A1 parity means the same *behavior* and the same *authorization*, and a drift
    between the two surfaces is a test failure here rather than a discovery in
    production.

    Walked against the docs/MCP_TOOLS.md section 8 table the other parity suites
    already walk, so there is one parity table rather than two that can disagree.
    """
    declared = {entry.key: entry.scope for entry in route_scopes(app)}
    # The MCP catalog is not directly reachable from a built server, so the tool's
    # scope is read the way a client sees it: the narrowest scope whose listing
    # includes the tool.
    visible: dict[str, set[str]] = {}
    for scope in ("read", "write", "admin"):
        async with memory_session(mcp_server, token=pat[scope]) as session:
            for tool in (await session.list_tools()).tools:
                visible.setdefault(tool.name, set()).add(scope)

    def tool_scope(name: str) -> str:
        scopes = visible[name]
        for candidate in ("read", "write", "admin"):
            if candidate in scopes:
                return candidate
        raise AssertionError(name)

    drift: list[tuple[str, str, str, str]] = []
    for tool, method, path in SECTION_8_PARITY:
        rest_scope = declared[f"{method} {path}"]
        mcp_scope = tool_scope(tool)
        if rest_scope != mcp_scope:
            drift.append((tool, f"{method} {path}", str(mcp_scope), str(rest_scope)))
    assert drift == [], f"(tool, route, mcp scope, rest scope): {drift}"


def test_bulk_update_dry_run_is_declared_write_like_its_mcp_counterpart(
    app: FastAPI, client: TestClient, api_tokens: dict[str, str]
) -> None:
    """A trip hazard settled in advance: `dry_run: true`
    performs no write, so it is tempting to call it a read. It is declared `write`,
    matching `bulk_update_records` in the MCP catalog, rather than reasoning about it
    separately on this surface."""
    entry = next(
        e for e in route_scopes(app) if e.path.endswith("/bulk-update") and e.method == "POST"
    )
    assert entry.scope == "write"
    response = client.post(
        "/api/v1/object-types/task/bulk-update",
        json={"filter": {}, "changes": {}, "dry_run": True},
        headers=auth(api_tokens["read"]),
    )
    assert _is_insufficient_scope(response)
