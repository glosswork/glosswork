"""The workspace's own TLS and its client-certificate lock, proven against the real image
(FR-P1, FR-P3).

Deliberately outside ``pyproject.toml``'s ``testpaths``, like its siblings here, so
``uv run pytest -q`` never needs Docker; run it with
``uv run pytest -q container_tests/test_tls_lock.py``.

With ``GW_TLS_CERT_FILE``, ``GW_TLS_KEY_FILE`` and ``GW_TLS_CLIENT_CA_FILE`` set, the
image terminates TLS itself and answers only a client whose certificate chains to the
named CA. Everyone else is refused in the TLS handshake, before any request is read.

**What "refused" looks like from the client's side, and why each refused case carries
controls.** On TLS 1.2 the client's own handshake call fails. On TLS 1.3 the client's
handshake call *returns*, because in that version the client finishes before the server
has checked the client's certificate, and the connection is then closed with no byte of a
response. So a refused case observes three things: zero bytes received, the TLS 1.2
handshake failing, and the container's count of ``access`` events not moving.

Those three are also true of a server with no lock at all, which answers a TLS client
with nothing it can read, and of a client that cannot verify the server. So each refused
case first proves it was talking TLS to this server:

- one function, :func:`_probe`, builds every connection, and an accepted and a refused
  case differ only in the client certificate handed to it;
- on TLS 1.3 the handshake call must return and ``version()`` must read ``TLSv1.3``
  before the zero-bytes assertion. That happens only when the server spoke TLS 1.3 and
  the client accepted its certificate, so the silence that follows is the server's
  refusal;
- on TLS 1.2 the handshake failure must not be the client rejecting the server;
- a request with the right certificate, on the same version and from the same function,
  is answered immediately afterwards.

The symptom of a TLS 1.3 refusal depends on the path between client and server: three
different exceptions were measured for the one refusal, two of them not ``ssl.SSLError``.
So :func:`_probe` catches ``OSError`` on the send and on the read and asserts no
particular one. A timeout is not a refusal and is raised, so a run that waited and got
nothing is red and never a pass.

**How "no access event" is made a statement about the log and not about timing.** Every
request this module sends carries its own ``X-Request-ID``, and every answered one is
followed by a wait for the ``access`` event carrying that id. So whenever a count is
read, every event this module has caused is already in the log, and the count after a
refused attempt and one answered request is exactly the count before plus one.

Certificates and keys are generated here at run time and never committed.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from container_tests import docker_support as ds

SETTLE_TIMEOUT_S = 90
LOG_TIMEOUT_S = 30
SOCKET_TIMEOUT_S = 10

#: TLS 1.3 first: its handshake returning is the control that fails against a server
#: with no lock, so every refused case meets it before any other assertion.
TLS_VERSIONS = (ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_2)

#: Where the files sit inside the container.
TLS_DIR_IN_CONTAINER = "/tls"
CERT_IN_CONTAINER = f"{TLS_DIR_IN_CONTAINER}/server.pem"
KEY_IN_CONTAINER = f"{TLS_DIR_IN_CONTAINER}/server.key"
CLIENT_CA_IN_CONTAINER = f"{TLS_DIR_IN_CONTAINER}/client-ca.pem"

LOCKED_ENVIRONMENT = {
    "GW_TLS_CERT_FILE": CERT_IN_CONTAINER,
    "GW_TLS_KEY_FILE": KEY_IN_CONTAINER,
    "GW_TLS_CLIENT_CA_FILE": CLIENT_CA_IN_CONTAINER,
}


# ------------------------------------------------------------------ certificates


@dataclass(frozen=True)
class Authority:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def _authority(common_name: str) -> Authority:
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


def _write(path: Path, content: bytes) -> Path:
    """Written ``0644``: ``docker cp`` keeps the mode and the owner is the host's user,
    so anything narrower is unreadable by the image's uid 1000."""
    path.write_bytes(content)
    path.chmod(0o644)
    return path


def _leaf(
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
    return (
        _write(directory / f"{stem}.pem", certificate.public_bytes(serialization.Encoding.PEM)),
        _write(
            directory / f"{stem}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
    )


@dataclass(frozen=True)
class TlsFiles:
    """The directory copied into a container, and the clients that call it.

    ``in_container`` holds only what the server is given: its certificate, its key and
    the client CA. The clients' keys and the server's CA stay on the host.
    """

    in_container: Path
    server_ca: Path
    right_client: tuple[Path, Path]
    other_ca_client: tuple[Path, Path]
    same_name_client: tuple[Path, Path]


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory: pytest.TempPathFactory) -> TlsFiles:
    root = tmp_path_factory.mktemp("tls-lock")
    in_container = root / "tls"
    in_container.mkdir()
    # The image's uid 1000 has to enter the directory, and ``docker cp`` keeps its mode.
    in_container.chmod(0o755)
    clients = root / "clients"
    clients.mkdir()

    server_authority = _authority("container test server authority")
    client_authority = _authority("container test client authority")
    other_authority = _authority("another client authority")
    # A second authority carrying the first one's exact subject name and another key:
    # a certificate it signs names the right issuer and must still be refused.
    twin_authority = _authority("container test client authority")

    _leaf(server_authority, in_container, "server", server=True)
    pem = serialization.Encoding.PEM
    _write(in_container / "client-ca.pem", client_authority.certificate.public_bytes(pem))
    return TlsFiles(
        in_container=in_container,
        server_ca=_write(root / "server-ca.pem", server_authority.certificate.public_bytes(pem)),
        right_client=_leaf(client_authority, clients, "right-client"),
        other_ca_client=_leaf(other_authority, clients, "other-ca-client"),
        same_name_client=_leaf(twin_authority, clients, "same-name-client"),
    )


# ------------------------------------------------------------------------ probes


@dataclass(frozen=True)
class Probe:
    """What one connection saw.

    ``handshake_returned`` is whether the client's own handshake call came back, and
    ``version`` what the connection then reported. ``received`` is every byte of a
    response that arrived, which for a refused caller is none.
    """

    request_id: str
    handshake_returned: bool
    version: str | None
    handshake_error: OSError | None
    received: bytes
    io_error: OSError | None


def _request(request_id: str) -> bytes:
    return (
        f"GET /readyz HTTP/1.1\r\nHost: localhost\r\nX-Request-ID: {request_id}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()


def _exchange(sock: socket.socket, request_id: str) -> tuple[bytes, OSError | None]:
    """Send one request and read until the peer closes. ``OSError`` on the send or on a
    read is how a refusal arrives and is returned; a timeout is not a refusal."""
    received = b""
    try:
        sock.sendall(_request(request_id))
        while chunk := sock.recv(65536):
            received += chunk
    except TimeoutError:
        raise
    except OSError as exc:
        return received, exc
    return received, None


def _probe(
    port: int, tls_files: TlsFiles, version: ssl.TLSVersion, client: tuple[Path, Path] | None
) -> Probe:
    """One TLS connection pinned to ``version``, presenting ``client`` or no certificate,
    and one ``GET /readyz`` on it. Every TLS connection in this module is made here."""
    request_id = f"tls-lock-{uuid.uuid4().hex}"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = version
    context.maximum_version = version
    context.load_verify_locations(tls_files.server_ca)
    if client is not None:
        context.load_cert_chain(*client)

    with socket.create_connection(("127.0.0.1", port), timeout=SOCKET_TIMEOUT_S) as raw:
        try:
            sock = context.wrap_socket(raw, server_hostname="localhost", suppress_ragged_eofs=False)
        except TimeoutError:
            raise
        except OSError as exc:
            return Probe(request_id, False, None, exc, b"", None)
        with sock:
            negotiated = sock.version()
            received, io_error = _exchange(sock, request_id)
            return Probe(request_id, True, negotiated, None, received, io_error)


def _plain_http(port: int) -> tuple[str, bytes]:
    """One ``GET /readyz`` in plain HTTP. Returns the request id and what came back."""
    request_id = f"tls-lock-{uuid.uuid4().hex}"
    with socket.create_connection(("127.0.0.1", port), timeout=SOCKET_TIMEOUT_S) as sock:
        received, _ = _exchange(sock, request_id)
    return request_id, received


# ----------------------------------------------------------------- the container log


def _access_events(cid: str) -> list[dict[str, object]]:
    events = []
    for line in ds.logs(cid, tail=None).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") == "access":
            events.append(event)
    return events


def _wait_for_access_event(cid: str, request_id: str) -> None:
    """Wait until the request carrying ``request_id`` is in the log. A timeout is a
    failure."""
    deadline = time.monotonic() + LOG_TIMEOUT_S
    while time.monotonic() < deadline:
        if any(event.get("request_id") == request_id for event in _access_events(cid)):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"no access event for request {request_id} within {LOG_TIMEOUT_S}s.\n"
        f"Container logs:\n{ds.logs(cid)}"
    )


def _answered_with_the_right_certificate(
    cid: str, port: int, tls_files: TlsFiles, version: ssl.TLSVersion
) -> Probe:
    """A request with the right certificate, asserted answered, and its access event
    waited for. This is the request that closes every count in this module."""
    probe = _probe(port, tls_files, version, tls_files.right_client)
    assert probe.handshake_returned, (
        f"{version.name}: the right certificate was refused: {probe.handshake_error!r}"
    )
    # The status line and the body, never the status alone: the application's catch-all
    # answers 200 for paths that do not exist.
    assert probe.received.startswith(b"HTTP/1.1 200"), (version.name, probe)
    assert b'"status"' in probe.received, (version.name, probe)
    _wait_for_access_event(cid, probe.request_id)
    return probe


# ------------------------------------------------------------------- containers


def _state(cid: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", cid],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.decode().strip()


def _wait_until_settled(cid: str, port: int, tls_files: TlsFiles) -> str:
    """Wait until the container has done one of the four things it can do, and say which:
    ``"exited"``, ``"tls"`` (it answered ``/readyz`` over TLS to the right certificate),
    ``"http"`` (it answered ``/readyz`` in plain HTTP) or ``"tls-refusing"`` (it speaks
    TLS 1.3 and gave the right certificate no answer).

    It asserts nothing about which, so a fixture built on it starts the same way against
    an image with the lock and one without, and the tests do the asserting. Waiting for
    every outcome is also what makes a container that should have refused to start, and
    did not, fail in seconds rather than at a timeout, and what makes a lock that shuts
    out the right client a failed assertion rather than a fixture that never settles.
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        if _state(cid) == "exited":
            return "exited"
        try:
            probe = _probe(port, tls_files, ssl.TLSVersion.TLSv1_3, tls_files.right_client)
        except OSError:
            probe = None
        if probe is not None and probe.received.startswith(b"HTTP/1.1 "):
            _wait_for_access_event(cid, probe.request_id)
            if probe.received.startswith(b"HTTP/1.1 200"):
                return "tls"
        if probe is not None and probe.handshake_returned and not probe.received:
            # uvicorn listens only once the application has started, so a completed
            # handshake with no answer is a running server refusing this client.
            return "tls-refusing"
        try:
            request_id, received = _plain_http(port)
        except OSError:
            received = b""
        # uvicorn's own answer to bytes it cannot parse is a 400 with no access event,
        # so only a response the application wrote is waited for.
        if received.startswith(b"HTTP/1.1 ") and not received.startswith(b"HTTP/1.1 400"):
            _wait_for_access_event(cid, request_id)
            if received.startswith(b"HTTP/1.1 200"):
                return "http"
        time.sleep(0.5)
    raise AssertionError(
        f"container {cid} neither exited nor answered /readyz within {SETTLE_TIMEOUT_S}s.\n"
        f"Container logs:\n{ds.logs(cid)}"
    )


@dataclass(frozen=True)
class Started:
    cid: str
    port: int
    settled: str


@contextmanager
def _start(
    image_tag: str, tls_files: TlsFiles, environment: dict[str, str], name: str
) -> Iterator[Started]:
    """A container with ``environment`` and the TLS files at ``/tls``, started and
    settled. The files are placed while it is still stopped, so its first start sees
    them."""
    port = ds.free_port()
    volume = ds.create_volume(f"gw-tls-{name}-{uuid.uuid4().hex[:8]}")
    cid = ds.create_container(
        image_tag,
        volume=volume,
        name_prefix=f"gw-tls-{name}",
        environment=environment,
        port=port,
    )
    try:
        ds.copy_in(cid, tls_files.in_container, TLS_DIR_IN_CONTAINER)
        ds.start_stopped_container(cid)
        yield Started(cid, port, _wait_until_settled(cid, port, tls_files))
    finally:
        ds.remove_container(cid)
        ds.remove_volume(volume)


@pytest.fixture(scope="module")
def locked(image_tag: str, tls_files: TlsFiles) -> Iterator[Started]:
    """One container for the module, started with all three settings."""
    with _start(image_tag, tls_files, LOCKED_ENVIRONMENT, "locked") as started:
        yield started


# ------------------------------------------------------------------------ the lock


def test_the_right_certificate_answers(locked: Started, tls_files: TlsFiles) -> None:
    """A client holding a certificate from the named CA is answered, on TLS 1.3 and on
    TLS 1.2."""
    for version in TLS_VERSIONS:
        probe = _answered_with_the_right_certificate(locked.cid, locked.port, tls_files, version)
        assert probe.version == version.name.replace("TLSv1_", "TLSv1."), probe


def _assert_refused_at_the_handshake(
    locked: Started, tls_files: TlsFiles, client: tuple[Path, Path] | None
) -> None:
    """The refused case, with its controls, on each version in turn."""
    for version in TLS_VERSIONS:
        before = len(_access_events(locked.cid))
        refused = _probe(locked.port, tls_files, version, client)

        if version is ssl.TLSVersion.TLSv1_3:
            # The control. The client's handshake call returns on TLS 1.3 only when the
            # server spoke TLS 1.3 and the client accepted its certificate, so what
            # follows is this server refusing and not a server with no TLS at all.
            assert refused.handshake_returned, (
                "TLSv1_3: the client's handshake call did not return, so this is not a "
                f"TLS 1.3 server refusing a client certificate: {refused.handshake_error!r}"
            )
            assert refused.version == "TLSv1.3", refused
        else:
            assert not refused.handshake_returned, refused
            # The control. The failure is the server refusing the client, not the client
            # rejecting the server.
            assert not isinstance(refused.handshake_error, ssl.SSLCertVerificationError), (
                f"TLSv1_2: the client rejected the server: {refused.handshake_error!r}"
            )
        assert refused.received == b"", (version.name, refused)

        # The control, on the same version, built by the same function: the server was
        # there and answering. Waiting for its access event is what makes the count
        # below a statement about the log rather than about timing.
        _answered_with_the_right_certificate(locked.cid, locked.port, tls_files, version)
        events = _access_events(locked.cid)
        assert len(events) == before + 1, (version.name, before, len(events))
        assert all(event.get("request_id") != refused.request_id for event in events)


def test_no_certificate_is_refused_at_the_handshake(locked: Started, tls_files: TlsFiles) -> None:
    """A caller with no certificate receives nothing, and no request of its is read."""
    _assert_refused_at_the_handshake(locked, tls_files, None)


@pytest.mark.parametrize("presented", ["other_ca_client", "same_name_client"])
def test_another_cas_certificate_is_refused_at_the_handshake(
    locked: Started, tls_files: TlsFiles, presented: str
) -> None:
    """A certificate from another CA is refused, and so is one from a second CA that
    carries the right CA's exact subject name: the lock is the CA's key, not its name."""
    _assert_refused_at_the_handshake(locked, tls_files, getattr(tls_files, presented))


def test_the_port_answers_nothing_in_plain_http(locked: Started, tls_files: TlsFiles) -> None:
    """With the lock on there is no plain HTTP listener: a plain request gets no byte
    back. The right-certificate request afterwards shows the server was there."""
    _, received = _plain_http(locked.port)
    assert received == b"", received[:200]
    _answered_with_the_right_certificate(locked.cid, locked.port, tls_files, ssl.TLSVersion.TLSv1_3)


PARTIAL_ENVIRONMENTS = {
    # Handed to uvicorn as it is, this serves plain HTTP to anyone.
    "client-ca-only": {"GW_TLS_CLIENT_CA_FILE": CLIENT_CA_IN_CONTAINER},
    # And this serves TLS to anyone.
    "certificate-and-key-only": {
        "GW_TLS_CERT_FILE": CERT_IN_CONTAINER,
        "GW_TLS_KEY_FILE": KEY_IN_CONTAINER,
    },
    # A misspelt name and nothing else: the settings loader ignores a name it does not
    # know, which would start the workspace with no lock and no message.
    "stray-name-only": {"GW_TLS_CA_FILE": CLIENT_CA_IN_CONTAINER},
}


@pytest.mark.parametrize("name", list(PARTIAL_ENVIRONMENTS))
def test_a_partial_set_refuses_to_start(image_tag: str, tls_files: TlsFiles, name: str) -> None:
    """Some of the three, or a name under ``GW_TLS_`` that is not one of them, stops the
    container with exit 1 and a reason, before anything listens."""
    with _start(image_tag, tls_files, PARTIAL_ENVIRONMENTS[name], name) as started:
        log = ds.logs(started.cid)
        assert started.settled == "exited", (
            f"the container started and answered over {started.settled}.\nLogs:\n{log}"
        )
        assert ds.exit_code(started.cid) == 1
        assert "Configuration error: GW_TLS_" in log, log


def test_there_is_one_process(locked: Started) -> None:
    """**A fence.** The lock is uvicorn's own TLS inside the one process (FR-P1): no
    proxy, no sidecar, no supervisor. ``/proc`` holds PID 1 and the command that listed
    it, and nothing else. It guards what this change does not alter, so it passes against
    an image without the lock by construction."""
    listing = ds.exec_in(
        locked.cid,
        [
            "python3",
            "-c",
            "import json, os; print(json.dumps({'self': os.getpid(), "
            "'all': sorted(int(p) for p in os.listdir('/proc') if p.isdigit())}))",
        ],
    )
    assert listing.returncode == 0, listing.stderr.decode(errors="replace")
    seen = json.loads(listing.stdout.decode().strip().splitlines()[-1])
    assert set(seen["all"]) == {1, seen["self"]}, seen
