"""Infrastructure acceptance criteria (FR-P4, FR-P5, FR-P6; docs/DATA_MODEL.md
section 14).

Covers: migration idempotency and readiness reporting, SQLite connection discipline
(WAL, foreign keys, busy timeout, BEGIN IMMEDIATE writes vs. plain-BEGIN reads),
atomic rollback of a full logical write when the audit step fails, structured JSON
access logs carrying request id and actor fields, and uvicorn's own access logger
being disabled in favor of the application's JSON access log.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event, text

from glosswork import entrypoint
from glosswork.actor import ActorContext, bootstrap_actor
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.migrations import run_migrations
from glosswork.repositories.models import AuditEvent, FieldDef, ObjectType
from glosswork.services import ServiceBundle


def _raise_runtime_error(conn: Connection, events: list[AuditEvent]) -> list[int]:
    """A drop-in replacement for ``AuditRepository.append`` that fails mid-write,
    so a caller can assert the surrounding logical write rolled back fully."""
    raise RuntimeError("simulated audit failure")


# --------------------------------------------------------------------------- FR-P6


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """A second run against an already-migrated database is a no-op, and exactly
    one row per migration is tracked in ``schema_migrations`` (FR-P6)."""
    database = Database.connect(tmp_path / "fresh.sqlite3")
    try:
        first_run = run_migrations(database)
        assert first_run == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

        second_run = run_migrations(database)
        assert second_run == []

        with database.read() as conn:
            rows = conn.execute(text("SELECT number FROM schema_migrations")).all()
        assert [row[0] for row in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    finally:
        database.close()


# --------------------------------------------------------------------------- FR-P4


def test_readyz_reports_pending_migrations_then_ready(tmp_path: Path) -> None:
    """``/readyz`` is not-ready and lists pending migration numbers against a
    database that has never been migrated; a normal app start migrates it and
    ``/readyz`` reports ok (FR-P4)."""
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)

    unmigrated_app = create_app(settings, migrate_on_startup=False)
    with TestClient(unmigrated_app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["pending_migrations"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

    migrated_app = create_app(settings)
    with TestClient(migrated_app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ------------------------------------------------------------- connection discipline


def test_connection_discipline(db: Database) -> None:
    """WAL journal mode, foreign keys on, and a 5s busy timeout are set on every
    connection (docs/DATA_MODEL.md section 14)."""
    with db.read() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_write_transactions_open_with_begin_immediate(
    db: Database,
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """Write transactions open with ``BEGIN IMMEDIATE``; reads use a plain
    ``BEGIN`` (docs/DATA_MODEL.md section 14)."""
    statements: list[str] = []

    @event.listens_for(db.engine, "connect")
    def _trace(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.set_trace_callback(statements.append)

    # Drop any pooled connections opened by fixture setup so the next checkout is
    # a fresh connection carrying the trace callback registered above.
    db.engine.dispose()

    services.records.create_record(actor, "artifact", {"title": "x"})
    assert any(s.startswith("BEGIN IMMEDIATE") for s in statements)

    statements.clear()
    services.records.query_records(actor, "artifact")
    assert not any(s.startswith("BEGIN IMMEDIATE") for s in statements)


def test_an_empty_link_list_is_refused_before_a_write_transaction_opens(
    db: Database,
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """Refusing an empty link list costs no write lock, pinned rather than asserted in
    prose.

    ``link_records``'s empty-``to_refs`` guard ran before ``db.write()`` was entered
    until the body was extracted into ``link_records_in_txn``, which moved it after.
    The exception type and message were identical either way, so no black-box test
    could see it; what changed was that a refusable call started taking SQLite's
    single writer lock first. The guard is now duplicated, and this asserts the half
    that no other test can: refusing costs no ``BEGIN IMMEDIATE``.

    Found by the `verify` agent reading the diff, which no black-box test could have done.
    """
    record = services.records.create_record(actor, "artifact", {"title": "orphan"})

    statements: list[str] = []

    @event.listens_for(db.engine, "connect")
    def _trace(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.set_trace_callback(statements.append)

    # As above: drop pooled connections so the next checkout carries the callback.
    db.engine.dispose()

    with pytest.raises(ValidationFailedError):
        services.records.link_records(actor, record.key, "parent", [])

    assert not any(s.startswith("BEGIN IMMEDIATE") for s in statements), (
        f"an empty to_refs must be refused without opening a write transaction: {statements}"
    )


# --------------------------------------------------------- atomic logical writes


def test_record_create_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``create_record`` rolls back the
    record insert and does not burn a key sequence number
    (docs/DATA_MODEL.md section 14)."""
    first = services.records.create_record(actor, "artifact", {"title": "first"})
    assert first.key == "ART-001"

    monkeypatch.setattr(services.records._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.records.create_record(doomed_actor, "artifact", {"title": "doomed"})
    monkeypatch.undo()  # restore the real audit repository before proceeding

    result = services.records.query_records(
        actor, "artifact", filter={"field": "title", "op": "eq", "value": "doomed"}
    )
    assert result.total_count == 0

    second = services.records.create_record(actor, "artifact", {"title": "second"})
    assert second.key == "ART-002"


def test_comment_add_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``add_comment`` rolls back the
    comment insert and the denormalized counters on the record
    (docs/DATA_MODEL.md section 14, FR-C8)."""
    record = services.records.create_record(actor, "artifact", {"title": "commented"})

    monkeypatch.setattr(services.comments._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.comments.add_comment(doomed_actor, record.key, "doomed comment")
    monkeypatch.undo()  # restore the real audit repository before proceeding

    refreshed = services.records.get_record(actor, record.key)
    assert refreshed.comment_count == 0
    assert refreshed.last_comment_at is None
    assert services.comments.list_comments(actor, record.key) == []


def test_link_creation_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``link_records`` rolls back the link
    row and its auto-maintained reciprocal together (docs/DATA_MODEL.md section 14,
    FR-L3)."""
    parent = services.records.create_record(actor, "artifact", {"title": "parent"})
    child = services.records.create_record(actor, "artifact", {"title": "child"})

    monkeypatch.setattr(services.records._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.records.link_records(doomed_actor, child.key, "parent", [parent.key])
    monkeypatch.undo()  # restore the real audit repository before proceeding

    assert services.records.list_links(actor, child.key, "parent") == []
    assert services.records.list_links(actor, parent.key, "children") == []
    # And the record can now be deleted: no inbound link survived the rollback.
    services.records.delete_record(actor, parent.key)


# --------------------------------------------------------------------------- FR-P5


def test_access_logs_are_structured_json_with_request_and_actor_fields(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Access logs are emitted as JSON lines carrying request id, method, path,
    status, principal, and surface; the response carries the same request id in
    an ``x-request-id`` header, and a caller-supplied ``X-Request-ID`` is honored
    (FR-P5).

    Uses ``capfd``, not ``capsys``: structlog's ``PrintLoggerFactory`` binds to
    real stdout, so only file-descriptor-level capture sees these lines.
    """
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        default_response = client.get("/healthz")
        fixed_response = client.get("/healthz", headers={"X-Request-ID": "fixed-id-123"})

    # The body, not the status. The SPA catch-all answers 200 for a route that
    # does not exist, so a status-only assertion here proved nothing about /healthz.
    assert default_response.json() == {"status": "ok"}
    assert fixed_response.json() == {"status": "ok"}
    assert "x-request-id" in default_response.headers
    assert fixed_response.headers["x-request-id"] == "fixed-id-123"

    captured = capfd.readouterr()
    events: list[dict[str, Any]] = []
    for line in captured.out.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    access_events = [e for e in events if e.get("event") == "access"]
    assert access_events

    default_request_id = default_response.headers["x-request-id"]
    default_event = next(e for e in access_events if e.get("request_id") == default_request_id)
    assert default_event["method"] == "GET"
    assert default_event["path"] == "/healthz"
    assert default_event["status"] == 200
    assert "principal_id" in default_event
    assert default_event["surface"] == "api"

    fixed_event = next(e for e in access_events if e.get("request_id") == "fixed-id-123")
    assert fixed_event["request_id"] == "fixed-id-123"


def test_entrypoint_disables_uvicorns_own_access_logger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container entry point disables uvicorn's own access logging, since its
    lines are not JSON; the application middleware is the sole source of access
    logs (FR-P5)."""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    assert captured["access_log"] is False
    assert captured["log_config"] is None


def test_entrypoint_passes_trusted_proxy_ips_to_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GW_TRUSTED_PROXY_IPS` (FR-P7) reaches uvicorn's own `forwarded_allow_ips`,
    which is what makes it honor `X-Forwarded-Proto`/`X-Forwarded-For` from the
    configured proxy rather than discarding them (uvicorn's own default trusts only
    loopback)."""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GW_TRUSTED_PROXY_IPS", "10.0.0.5,10.0.0.6")
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    assert captured["forwarded_allow_ips"] == "10.0.0.5,10.0.0.6"
    assert captured["proxy_headers"] is True


def test_entrypoint_serves_one_process_even_when_web_concurrency_asks_for_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image runs one process per database (DD-35, FR-P1), and ``WEB_CONCURRENCY``
    cannot change that.

    uvicorn reads ``WEB_CONCURRENCY`` whenever ``workers`` is left unset, and a second
    process on one database is a second embedding worker whose idle reclaim takes back
    rows the first still holds, plus a second in-memory login limiter. The assertion
    hands what the entry point passed to uvicorn's own ``Config``, so it measures the
    worker count uvicorn would actually start rather than the presence of an argument.
    """
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    # ``log_config=None`` as the entry point passes it: uvicorn's default would install
    # its own handlers on the ``uvicorn`` loggers and break the JSON-logging tests after
    # this one.
    config = uvicorn.Config(
        "glosswork.app:app", workers=captured.get("workers"), log_config=captured["log_config"]
    )
    assert config.workers == 1
    assert captured["workers"] == 1


# ------------------------------------ the workspace's own TLS and its client lock
#
# Certificates are made here, at run time, and never committed. The container proof in
# ``container_tests/test_tls_lock.py`` makes its own the same way: that directory runs
# against a built image, at release and by hand, and stands on nothing in ``tests/``.

TLS_VARIABLES = ("GW_TLS_CERT_FILE", "GW_TLS_KEY_FILE", "GW_TLS_CLIENT_CA_FILE")
TLS_VERSIONS = (ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_2)


@dataclass(frozen=True)
class Authority:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def _tls_authority(common_name: str) -> Authority:
    """A self-signed certificate authority with a fresh key."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return Authority(certificate, key)


def _tls_leaf(
    authority: Authority, directory: Path, stem: str, *, server: bool = False
) -> tuple[Path, Path]:
    """A certificate signed by ``authority``, written with its key. Returns both paths."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    usage = ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, stem)]))
        .issuer_name(authority.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    certificate = builder.sign(authority.key, hashes.SHA256())
    certificate_path = directory / f"{stem}.pem"
    key_path = directory / f"{stem}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


def _write_authority(authority: Authority, path: Path) -> Path:
    path.write_bytes(authority.certificate.public_bytes(serialization.Encoding.PEM))
    return path


@dataclass(frozen=True)
class TlsFiles:
    """What a locked workspace is started with, and the clients that will call it."""

    server_certificate: Path
    server_key: Path
    client_ca: Path
    server_ca: Path
    right_client: tuple[Path, Path]
    other_ca_client: tuple[Path, Path]
    same_name_client: tuple[Path, Path]


def _tls_files(directory: Path) -> TlsFiles:
    server_authority = _tls_authority("test server authority")
    client_authority = _tls_authority("test client authority")
    other_authority = _tls_authority("another client authority")
    # A second authority carrying the first one's exact subject name and another key:
    # a certificate it signs names the right issuer and must still be refused.
    twin_authority = _tls_authority("test client authority")
    server_certificate, server_key = _tls_leaf(server_authority, directory, "server", server=True)
    return TlsFiles(
        server_certificate=server_certificate,
        server_key=server_key,
        client_ca=_write_authority(client_authority, directory / "client-ca.pem"),
        server_ca=_write_authority(server_authority, directory / "server-ca.pem"),
        right_client=_tls_leaf(client_authority, directory, "right-client"),
        other_ca_client=_tls_leaf(other_authority, directory, "other-ca-client"),
        same_name_client=_tls_leaf(twin_authority, directory, "same-name-client"),
    )


def _run_entrypoint_capturing_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """Run ``entrypoint.main()`` with ``uvicorn.run`` replaced, and return what it was
    called with: one positional tuple per call, and the keyword arguments."""
    calls: list[tuple[Any, ...]] = []
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        calls.append(args)
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    entrypoint.main()
    return calls, captured


def _set_tls_environment(monkeypatch: pytest.MonkeyPatch, files: TlsFiles) -> None:
    monkeypatch.setenv("GW_TLS_CERT_FILE", str(files.server_certificate))
    monkeypatch.setenv("GW_TLS_KEY_FILE", str(files.server_key))
    monkeypatch.setenv("GW_TLS_CLIENT_CA_FILE", str(files.client_ca))


def test_entrypoint_with_all_three_tls_settings_hands_uvicorn_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the three settings, uvicorn receives the certificate, the key, the client CA
    and ``CERT_REQUIRED`` together, and its own ``Config`` reads them as TLS on."""
    files = _tls_files(tmp_path)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    _set_tls_environment(monkeypatch, files)

    _, captured = _run_entrypoint_capturing_uvicorn(monkeypatch)

    assert {key: value for key, value in captured.items() if key.startswith("ssl_")} == {
        "ssl_certfile": str(files.server_certificate),
        "ssl_keyfile": str(files.server_key),
        "ssl_ca_certs": str(files.client_ca),
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
    }
    assert uvicorn.Config("glosswork.app:app", **captured).is_ssl is True


async def _no_op_application(scope: Any, receive: Any, send: Any) -> None:
    """An ASGI application that does nothing: the handshake test never serves a request."""


@dataclass(frozen=True)
class Handshake:
    client_returned: bool
    server_completed: bool
    server_error: ssl.SSLError | None


def _handshake_in_memory(
    server_context: ssl.SSLContext,
    files: TlsFiles,
    version: ssl.TLSVersion,
    client: tuple[Path, Path] | None,
) -> Handshake:
    """One TLS handshake between a client and ``server_context``, over memory buffers.

    No socket, no thread and no wait. The two sides take turns, and the bytes one wrote
    are handed to the other, until both have finished or the server's own
    ``do_handshake()`` raises. A client that cannot verify the server raises out of here,
    which is a test error and never a refusal.
    """
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.minimum_version = version
    client_context.maximum_version = version
    client_context.load_verify_locations(files.server_ca)
    if client is not None:
        client_context.load_cert_chain(*client)

    to_client, from_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    to_server, from_server = ssl.MemoryBIO(), ssl.MemoryBIO()
    client_side = client_context.wrap_bio(to_client, from_client, server_hostname="localhost")
    server_side = server_context.wrap_bio(to_server, from_server, server_side=True)

    client_returned = server_completed = False
    for _ in range(10):
        if not client_returned:
            try:
                client_side.do_handshake()
                client_returned = True
            except ssl.SSLWantReadError:
                pass
        to_server.write(from_client.read())
        if not server_completed:
            try:
                server_side.do_handshake()
                server_completed = True
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLError as exc:
                return Handshake(client_returned, False, exc)
        to_client.write(from_server.read())
        if client_returned and server_completed:
            break
    return Handshake(client_returned, server_completed, None)


def test_the_tls_context_uvicorn_builds_refuses_in_its_own_handshake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arguments the entry point passes, handed to uvicorn's own ``Config``, build a
    context whose handshake refuses every client without a certificate from the named CA.

    This is the direct measurement of "refused at the handshake", and it runs on every
    pull request, which the container proof does not. On TLS 1.3 the client's handshake
    call has already returned when the server's raises, because in that version the
    client finishes before the server has checked its certificate; on TLS 1.2 it has not.
    The exception type and which side raised are asserted. OpenSSL's reason is carried in
    the assertion message and not asserted.
    """
    files = _tls_files(tmp_path)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    _set_tls_environment(monkeypatch, files)
    _, captured = _run_entrypoint_capturing_uvicorn(monkeypatch)

    config = uvicorn.Config(
        _no_op_application,
        log_config=None,
        **{key: value for key, value in captured.items() if key.startswith("ssl_")},
    )
    config.load()
    context = config.ssl
    assert context is not None, "uvicorn built no TLS context from the entry point's arguments"
    assert context.verify_mode == ssl.CERT_REQUIRED
    stats = context.cert_store_stats()
    assert (stats["x509"], stats["x509_ca"]) == (1, 1), stats

    refused = {
        "no certificate": None,
        "another CA's certificate": files.other_ca_client,
        "a certificate from a second CA with the same name": files.same_name_client,
    }
    for version in TLS_VERSIONS:
        accepted = _handshake_in_memory(context, files, version, files.right_client)
        assert (accepted.client_returned, accepted.server_completed) == (True, True), (
            f"{version.name}: the right certificate did not complete: {accepted}"
        )
        for what, client in refused.items():
            outcome = _handshake_in_memory(context, files, version, client)
            assert isinstance(outcome.server_error, ssl.SSLError), (
                f"{version.name}, {what}: the server's handshake did not raise: {outcome}"
            )
            assert outcome.client_returned is (version is ssl.TLSVersion.TLSv1_3), (
                f"{version.name}, {what}: the client's handshake call returned="
                f"{outcome.client_returned} when the server raised {outcome.server_error!r}"
            )


def test_entrypoint_with_a_partial_tls_set_exits_before_uvicorn_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The client CA alone, handed to uvicorn as it is, serves plain HTTP to anyone with
    no warning. The entry point exits 1 saying why, and uvicorn is never called."""
    files = _tls_files(tmp_path)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    for name in TLS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_TLS_CLIENT_CA_FILE", str(files.client_ca))
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.startswith("Configuration error: GW_TLS_")
    assert calls == []


def test_entrypoint_with_no_tls_setting_calls_uvicorn_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A fence.** With none of the three settings the call uvicorn receives is the one
    it received before they existed: the same eight keyword arguments, key for key and
    value for value, and no ``ssl_*`` argument at all. It guards behaviour this change
    does not alter, so it passes on a tree without the change by construction."""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    for name in (*TLS_VARIABLES, "GW_LOG_LEVEL", "GW_TRUSTED_PROXY_IPS"):
        monkeypatch.delenv(name, raising=False)

    calls, captured = _run_entrypoint_capturing_uvicorn(monkeypatch)

    assert calls == [("glosswork.app:app",)]
    assert captured == {
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        "log_config": None,
        "log_level": "info",
        "access_log": False,
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1",
    }
    assert uvicorn.Config("glosswork.app:app", **captured).is_ssl is False
