"""Shared fixtures for the backend suite.

Conventions:

- ``actor`` is a fresh bootstrap ActorContext for the test. When a test performs
  several logical write calls and cares about audit request_id grouping (version
  conflicts, per-call audit correlation), use ``make_actor()`` for each call so each
  gets its own request id, exactly as separate real requests would.
- ``sink_type`` creates an object type exercising every one of the thirteen field
  types (attachment as a definition/validation target only).
- For HTTP-layer tests: use ``client`` to call the REST API, and ``app_services``
  (the same running app's service bundle) to seed fixture data directly through the
  service layer when a test isn't specifically about the seeding endpoint itself.
  Do not mix the ``services``/``db`` fixtures with ``client`` in one test: they point
  at different databases.

``--shard k/N`` runs the k-th of N parts of whatever was collected, which is how CI runs
the suite as N jobs (``.github/workflows/ci.yml``). A test belongs to the part its node
id hashes to, so the parts are disjoint, together they are the whole collection, and a
test is in the same part on every machine. Without the option every test runs.
"""

from __future__ import annotations

import re
import uuid
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp.server import MCPServer

from glosswork.actor import ActorContext, bootstrap_actor
from glosswork.app import create_app
from glosswork.auth import PatTokenResolver
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.mcp_server import create_mcp_server
from glosswork.migrations import run_migrations
from glosswork.repositories.models import FieldDef, ObjectType, PrincipalRow
from glosswork.repositories.sqlite import (
    SqliteCommentRepository,
    SqlitePrincipalRepository,
    SqliteRecordRepository,
    SqliteSchemaRepository,
    SqliteSearchRepository,
)
from glosswork.services import ServiceBundle, build_services
from glosswork.services.embedding_worker import EmbeddingWorker
from tests.search_support import FakeClock, FakeEmbeddingProvider, real_provider

# starlette's TestClient hard-imports `httpx`; the project's dev dependency is the
# `httpx2` successor package, which ships this official shim precisely for that case.
# Must run before anything else imports httpx/httpcore.
httpx2.alias_httpx()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        default=None,
        metavar="k/N",
        help="run only the k-th of N parts of the collected tests, by a hash of each node id",
    )


def _shard(config: pytest.Config) -> tuple[int, int] | None:
    value: str | None = config.getoption("--shard")
    if value is None:
        return None
    match = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
    if match is None or not 1 <= int(match.group(1)) <= int(match.group(2)):
        raise pytest.UsageError(
            f"--shard {value!r}: expected k/N, two whole numbers with 1 <= k <= N"
        )
    return int(match.group(1)), int(match.group(2))


def pytest_configure(config: pytest.Config) -> None:
    # Refused here rather than in the collection hook, which runs only after everything
    # has been collected.
    _shard(config)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = _shard(config)
    if shard is None:
        return
    k, count = shard
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        # crc32, not hash(): Python salts str hashes per process.
        mine = zlib.crc32(item.nodeid.encode()) % count == k - 1
        (kept if mine else deselected).append(item)
    items[:] = kept
    config.hook.pytest_deselected(items=deselected)


def make_actor() -> ActorContext:
    """A bootstrap actor with a fresh request id: one per logical call."""
    return bootstrap_actor(str(uuid.uuid4()))


SCOPES: tuple[str, ...] = ("read", "write", "admin")

# A second real principal, seeded on demand with a **fixed** id so a parametrize list can
# name it at import time. It exists because ``user_ref`` values are now resolved
# rather than merely shape-checked: a fabricated UUID used to be a harmless "matches
# nobody" probe in the operator matrix and is now ``validation_failed``, which is the
# point -- a filter that silently returns zero rows is the worst possible answer to a
# typo. The probe is a real person instead, so the operator cases stay discriminating.
SECOND_PRINCIPAL_ID = "00000000-0000-4000-8000-0000000000aa"
SECOND_PRINCIPAL_EMAIL = "rowan.tate@example.com"
SECOND_PRINCIPAL_NAME = "Rowan Tate"


def seed_second_principal(
    database: Database,
    principal_id: str = SECOND_PRINCIPAL_ID,
    display_name: str = SECOND_PRINCIPAL_NAME,
    email: str = SECOND_PRINCIPAL_EMAIL,
    is_active: bool = True,
) -> str:
    """Insert one active ``user`` principal with a caller-chosen id, through the
    repository rather than ``create_user`` (which mints its own UUID). Returns the id."""
    repo = SqlitePrincipalRepository()
    row = PrincipalRow(
        id=principal_id,
        type="user",
        display_name=display_name,
        email=email,
        role="member",
        auth_provider="local",
        external_id=None,
        password_hash=None,
        is_active=is_active,
        description=None,
        created_at="2026-01-01T00:00:00Z",
        created_by=None,
    )
    with database.write() as conn:
        repo.insert(conn, row)
    return principal_id


def mint_scope_tokens(bundle: ServiceBundle) -> dict[str, str]:
    """One real PAT per scope, minted for the seeded bootstrap principal.

    This is what replaced `GW_INSECURE_INTERIM_AUTH` in the suite. Every REST and MCP
    test now presents a token that `PatTokenResolver` verifies against
    `access_tokens` exactly as it would in production — there is no test-only
    resolver, no permissive default, and no literal scope selector left anywhere.
    """
    actor = make_actor()
    return {
        scope: bundle.tokens.mint(actor, name=f"test-{scope}", scope=scope).plaintext
        for scope in SCOPES
    }


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database.connect(tmp_path / "test.sqlite3")
    run_migrations(database)
    yield database
    database.close()


@pytest.fixture
def services(db: Database, tmp_path: Path) -> ServiceBundle:
    """The service layer with **embedding disabled**.

    Deliberate: migration 6 gives every test a loadable-extension dependency, but no
    test should acquire a *model* dependency it did not ask for. With embedding off,
    keyword rows are still maintained on every write and no worker exists, so the
    806 tests written before embedding existed keep testing what they always tested. The
    ``search_services`` fixture below is the opt-in that turns the model on.
    """
    return build_services(db, tmp_path, Settings(data_dir=tmp_path, embedding_enabled=False))


@pytest.fixture
def actor() -> ActorContext:
    return make_actor()


@pytest.fixture
def pat(services: ServiceBundle) -> dict[str, str]:
    """Real minted PATs keyed by scope, over the ``services`` fixture's database.

    Use with ``mcp_server``; the HTTP-layer equivalent is ``api_tokens``, which mints
    against the running app's own database instead."""
    return mint_scope_tokens(services)


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """The REST app with embedding disabled, for the same reason as ``services``."""
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    return create_app(settings)


@pytest.fixture
def fake_provider() -> FakeEmbeddingProvider:
    """A deterministic, model-free embedding provider (the FR-Q8 seam)."""
    return FakeEmbeddingProvider()


@pytest.fixture
def search_services(
    db: Database, tmp_path: Path, fake_provider: FakeEmbeddingProvider
) -> ServiceBundle:
    """Services with embedding **enabled**, over the deterministic fake provider.

    This is what the queue, worker, retry, reclaim, and restart tests use: they are
    about the state machine, not about vector quality, and running them against the
    real model would cost inference time per case while proving nothing extra.
    Tests that genuinely need the real model use ``real_search_services``.
    """
    return build_services(
        db,
        tmp_path,
        Settings(data_dir=tmp_path, embedding_enabled=True),
        embedding_provider=fake_provider,
    )


@pytest.fixture
def search_app(tmp_path: Path, fake_provider: FakeEmbeddingProvider) -> FastAPI:
    """The REST app with embedding **enabled** over the deterministic fake provider
    REST and MCP shape tests use ``keyword`` mode under it -- its vectors are
    hash-seeded, so semantic order is meaningless there -- and the real provider only
    where semantics matter. No worker thread is started against the fake: tests that
    need a drain build one with ``make_worker`` over ``search_app.state.db``."""
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=True)
    return create_app(settings, embedding_provider=fake_provider)


@pytest.fixture
def search_client(search_app: FastAPI) -> Iterator[TestClient]:
    """``client`` over ``search_app``: an ``admin`` PAT on every request by default."""
    with TestClient(search_app) as test_client:
        tokens = mint_scope_tokens(search_app.state.services)
        test_client.headers["Authorization"] = f"Bearer {tokens['admin']}"
        test_client.scope_tokens = tokens  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def real_search_services(db: Database, tmp_path: Path) -> ServiceBundle:
    """Services with embedding enabled over the **real** bundled model.

    Fails, rather than skips, when the model is absent (DD-32).
    """
    return build_services(
        db,
        tmp_path,
        Settings(data_dir=tmp_path, embedding_enabled=True),
        embedding_provider=real_provider(),
    )


def make_worker(
    db: Database, services: ServiceBundle, clock: FakeClock, batch_size: int = 32
) -> EmbeddingWorker:
    """A worker over the same database a ``*_search_services`` bundle writes to."""
    assert services.embedding_provider is not None
    return EmbeddingWorker(
        db,
        SqliteSearchRepository(),
        SqliteSchemaRepository(),
        SqliteRecordRepository(),
        SqliteCommentRepository(),
        services.embedding_provider,
        clock=clock,
        batch_size=batch_size,
    )


@pytest.fixture
def anon_client(app: FastAPI) -> Iterator[TestClient]:
    """The REST API with no credential attached by default.

    For the auth surface itself — login, logout, OIDC, session cookies — where the
    ``client`` fixture's default ``Authorization`` header would take precedence over
    a cookie under test (DD-9's precedence rule). ``TestClient`` persists cookies
    across requests made with the same instance, so a test can log in and then make
    further requests as that session.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """The REST API over a fresh, migrated database (lifespan runs migrations).

    Presents a real ``admin`` PAT on every request by default: an absent
    ``Authorization`` header once resolved to ``admin`` and is now refused with 401, so
    the fixture supplies the credential the older tests were implicitly relying on.
    Tests about authorization itself override the header per request, or use
    ``api_tokens`` to pick a different scope.
    """
    with TestClient(app) as test_client:
        tokens = mint_scope_tokens(app.state.services)
        test_client.headers["Authorization"] = f"Bearer {tokens['admin']}"
        test_client.scope_tokens = tokens  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def api_tokens(client: TestClient) -> dict[str, str]:
    """The ``client`` fixture's minted PATs, keyed by scope, for tests that need to
    present something other than the default ``admin`` credential."""
    tokens: dict[str, str] = client.scope_tokens  # type: ignore[attr-defined]
    return tokens


def auth(token: str) -> dict[str, str]:
    """An ``Authorization`` header for one token, for per-request overrides."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app_services(app: FastAPI, client: TestClient) -> ServiceBundle:
    """The running app's own service bundle, for seeding fixture data on the same
    database the ``client`` fixture talks to. Depends on ``client`` so the app's
    lifespan (which constructs ``app.state.services``) has already started."""
    services: ServiceBundle = app.state.services
    return services


def select_options(*values: str) -> list[dict[str, str]]:
    return [
        {"value": v, "label": v.replace("_", " ").title(), "description": f"The {v} state."}
        for v in values
    ]


# Every one of the thirteen field types (FR-S4). The relation field is
# self-referential with an auto-created inverse, which also covers FR-L2/FR-L3.
KITCHEN_SINK_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the artifact.",
        "required": True,
    },
    {
        "key": "summary",
        "name": "Summary",
        "type": "long_text",
        "description": "Longer narrative of what this artifact is and why it exists.",
    },
    {
        "key": "points",
        "name": "Points",
        "type": "integer",
        "description": "Effort estimate in story points.",
    },
    {
        "key": "score",
        "name": "Score",
        "type": "decimal",
        "description": "Weighted priority score between 0 and 100.",
    },
    {
        "key": "active",
        "name": "Active",
        "type": "boolean",
        "description": "Whether the artifact is currently being worked.",
    },
    {
        "key": "due",
        "name": "Due date",
        "type": "date",
        "description": "Date the artifact is due, in the deployment's local convention.",
    },
    {
        "key": "seen_at",
        "name": "Last seen",
        "type": "datetime",
        "description": "UTC timestamp of the last review of this artifact.",
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Delivery state; use 'doing' only for actively worked artifacts.",
        "config": {"options": select_options("todo", "doing", "done")},
    },
    {
        "key": "tags",
        "name": "Tags",
        "type": "multi_select",
        "description": "Free classification labels for grouping artifacts.",
        "config": {"options": select_options("red", "green", "blue")},
    },
    {
        "key": "owner",
        "name": "Owner",
        "type": "user_ref",
        "description": "Principal accountable for delivering this artifact.",
    },
    {
        "key": "parent",
        "name": "Parent",
        "type": "relation",
        "description": "The larger artifact this one rolls up into; self-referential.",
        "config": {
            "target_type_key": "artifact",
            "cardinality": "one",
            "inverse_field_key": "children",
        },
    },
    {
        "key": "homepage",
        "name": "Homepage",
        "type": "url",
        "description": "Canonical external link for this artifact.",
    },
    {
        "key": "files",
        "name": "Files",
        "type": "attachment",
        "description": "Supporting documents attached to the record.",
    },
]


@pytest.fixture
def anyio_backend() -> str:
    """The MCP tests are ``async def`` under ``@pytest.mark.anyio``; the SDK's
    client is anyio-based and the suite runs it on asyncio only."""
    return "asyncio"


@pytest.fixture
def mcp_server(services: ServiceBundle) -> MCPServer:
    """The MCP server over the ``services`` fixture's database, built against the
    production ``PatTokenResolver`` exactly as ``create_app`` builds it. Pair it
    with the ``pat`` fixture for real per-scope tokens. Drive it only through
    ``mcp.Client`` sessions (``tests/mcp_support.py``), never by calling tools."""
    return create_mcp_server(lambda: services, PatTokenResolver(lambda: services))


@pytest.fixture
def sink_type(
    services: ServiceBundle, actor: ActorContext
) -> tuple[ObjectType, dict[str, FieldDef]]:
    object_type = services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite; exercises every "
        "field type the platform supports.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    _, fields = services.schema.get_object_type(actor, "artifact")
    return object_type, {f.key: f for f in fields}
