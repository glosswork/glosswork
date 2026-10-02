"""The operator backup route (DD-39 extended, FR-P8, FR-P10).

``POST /api/v1/operator/backup`` hands the deployment's backup artifact to the holder of
``GW_OPERATOR_TOKEN``, and only on a deployment that turned it on with
``GW_OPERATOR_BACKUP``. Everybody else, and the operator too while the setting is off,
gets the one refusal ``GET /api/v1/usage`` already gives: ``401 operator_token_refused``.

**Every refusal assertion names the body's code.** On a tree without this route every
caller already gets a ``401`` of a different code (``invalid_token``, from the edge), so a
status-only assertion passes there.

Expected passed counts, per selection (``uv run pytest -q tests/test_operator_backup.py
-k <selection>``); a summary line that says anything but ``N passed`` is a failure:

==================================  ======
``-k``                              passed
==================================  ======
``streams_a_readable_tar``          1
``refusal``                         1
``tenant_pat``                      1
``sweep``                           3
``pin``                             6
``audited``                         1
``read_only``                       1
``opt_in``                          3
``upgrade_keeps_usage_only``        1
``relay_token_is_in_no_artifact``   1
==================================  ======

**Fences**, true on a tree without this change and not counted as coverage: the two
``sweep`` cases with the backup off and unset, the admin half of ``audited``, and
``relay_token_is_in_no_artifact``'s leak assertions.

The pins at the bottom read source, not a document, so this module is not ``structural``
(``tests/test_structural_lane.py`` pins the marked set by equality).
"""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.scopes import flatten_routes
from glosswork.services.backup import SNAPSHOT_ARCNAME
from glosswork.services.usage import OPERATOR_TOKEN_HEADER
from tests.conftest import auth, mint_scope_tokens
from tests.fake_relay import LiveRelay
from tests.relay_support import (
    KNOWN_EMAIL,
    admin_token,
    codes_sent_to,
    live_relay,  # noqa: F401 - fixture
    relay_settings,
    request_code,
    seed_local_user,
    verify_code,
)

# Obviously fixtures, not random-looking blobs (``tests/test_operator_usage.py``).
OPERATOR_TOKEN = "operator-token-for-tests-0123456"
WRONG_TOKEN = "operator-token-for-tests-9999999"

OPERATOR_BACKUP = "/api/v1/operator/backup"
ADMIN_BACKUP = "/api/v1/admin/backup"
USAGE = "/api/v1/usage"
REFUSED = "operator_token_refused"
MARKER = "operator_token"

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

BODY_METHODS = frozenset({"POST", "PATCH", "PUT"})


# ------------------------------------------------------------------------ fixtures


def _settings(data_dir: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": data_dir,
        "embedding_enabled": False,
        "base_url": "http://testserver",
    }
    values.update(overrides)
    return Settings(**values)


def _backup_on(data_dir: Path, **overrides: Any) -> FastAPI:
    """A deployment that opted in: the operator credential and ``GW_OPERATOR_BACKUP``."""
    values: dict[str, Any] = {"operator_token": OPERATOR_TOKEN, "operator_backup": True}
    values.update(overrides)
    return create_app(_settings(data_dir, **values))


@pytest.fixture
def on_app(tmp_path: Path) -> FastAPI:
    return _backup_on(tmp_path / "on")


@pytest.fixture
def on_client(on_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(on_app) as client:
        yield client


def operator(token: str = OPERATOR_TOKEN) -> dict[str, str]:
    return {OPERATOR_TOKEN_HEADER: token}


def _data_dir(app: FastAPI) -> Path:
    data_dir: Path = app.state.settings.data_dir
    return data_dir


def _staged(app: FastAPI) -> list[Path]:
    staging = _data_dir(app) / "backup-tmp"
    return sorted(staging.iterdir()) if staging.is_dir() else []


def _backup_rows(app: FastAPI) -> list[dict[str, Any]]:
    with app.state.db.read() as conn:
        found = conn.execute(
            text(
                "SELECT principal_id, request_id, auth_method, new_value FROM audit_events "
                "WHERE action = 'backup_taken' ORDER BY id"
            )
        ).mappings()
        return [{**row, "new_value": json.loads(row["new_value"])} for row in found]


def _tar_members(content: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r|") as tar:
        return [member.name for member in tar]


def _shape(response: Any) -> tuple[int, bytes, frozenset[str]]:
    """What a prober can compare: the status, the body, and the *names* of the response
    headers. Not their values, because ``x-request-id`` differs on every answer."""
    return (
        response.status_code,
        response.content,
        frozenset(name.lower() for name in response.headers),
    )


def _assert_refused(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith("application/json"), response.headers
    assert response.json()["error"]["code"] == REFUSED, response.text
    assert b"ustar" not in response.content
    assert b"SQLite format 3" not in response.content


# ------------------------------------------------------------------------ AC1


def test_the_operator_token_streams_a_readable_tar(on_app: FastAPI, on_client: TestClient) -> None:
    response = on_client.post(OPERATOR_BACKUP, headers=operator())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/x-tar"
    assert response.headers["cache-control"] == "no-store"
    assert "glosswork-backup.tar" in response.headers["content-disposition"]
    members = _tar_members(response.content)
    assert members[0] == SNAPSHOT_ARCNAME, members
    assert _staged(on_app) == []


# ------------------------------------------------------------------- AC2 and AC3


def test_every_non_operator_gets_one_identical_refusal(tmp_path: Path) -> None:
    """Eight callers who are not the operator, on deployments with the backup turned on,
    and one answer between them: nothing a prober can compare tells them apart, no tar
    byte is sent, no snapshot is staged and nothing is audited.

    The positive control is the last line: the same route, on the same app, hands the
    operator a tar, so this is not passing because the route refuses everybody."""
    main = _backup_on(tmp_path / "main")
    blank = _backup_on(tmp_path / "blank", operator_token="")
    unconfigured = _backup_on(tmp_path / "unconfigured", operator_token=None)
    answers: dict[str, Any] = {}
    with TestClient(main) as client, TestClient(blank) as b, TestClient(unconfigured) as u:
        pat = mint_scope_tokens(main.state.services)["admin"]
        variants: dict[str, tuple[TestClient, dict[str, str]]] = {
            "absent": (client, {}),
            "wrong": (client, operator(WRONG_TOKEN)),
            "blank-configured": (b, operator()),
            "unconfigured": (u, operator()),
            "tenant admin PAT": (client, auth(pat)),
            "PAT in the operator header": (client, operator(pat)),
            "garbage Authorization": (client, {"Authorization": "garbage"}),
            "operator as Bearer": (client, auth(OPERATOR_TOKEN)),
        }
        for name, (caller, headers) in variants.items():
            answers[name] = caller.post(OPERATOR_BACKUP, headers=headers)
        for name, response in answers.items():
            _assert_refused(response)
            assert _shape(response) == _shape(answers["absent"]), name
        for app in (main, blank, unconfigured):
            assert _staged(app) == [], app
            assert _backup_rows(app) == [], app

        allowed = client.post(OPERATOR_BACKUP, headers=operator())
        assert allowed.status_code == 200, allowed.text
        assert _tar_members(allowed.content)[0] == SNAPSHOT_ARCNAME


def test_a_tenant_pat_at_admin_scope_gets_the_anonymous_answer(
    on_app: FastAPI, on_client: TestClient
) -> None:
    """The highest credential a workspace can mint does not open the operator's route,
    in either header, and is answered exactly as a caller presenting nothing is."""
    pat = mint_scope_tokens(on_app.state.services)["admin"]
    anonymous = on_client.post(OPERATOR_BACKUP)
    _assert_refused(anonymous)
    for headers in (auth(pat), operator(pat), {**auth(pat), **operator(pat)}):
        response = on_client.post(OPERATOR_BACKUP, headers=headers)
        _assert_refused(response)
        assert _shape(response) == _shape(anonymous), headers
    assert _backup_rows(on_app) == []


# ------------------------------------------------------------------------ AC4


def _concrete(path: str) -> str:
    while "{" in path:
        start = path.index("{")
        end = path.index("}", start)
        path = path[:start] + "sweep" + path[end + 1 :]
    return path


def _routes_the_operator_token_opens(app: FastAPI, client: TestClient) -> set[tuple[str, str]]:
    """Every registered route entry, asked once with no credential and once with only the
    operator credential. An entry is *opened* when the two answers differ.

    The list is ``scopes.flatten_routes`` in this run, never a constant. The request id is
    pinned so an answer that echoes it does not read as a difference."""
    opened: set[tuple[str, str]] = set()
    swept = 0
    for route in flatten_routes(app):
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in sorted(getattr(route, "methods", None) or ["GET"]):
            if method in ("HEAD", "OPTIONS"):
                continue
            swept += 1
            kwargs: dict[str, Any] = {"json": {}} if method in BODY_METHODS else {}
            pinned = {"X-Request-Id": "sweep"}
            bare = client.request(method, _concrete(path), headers=pinned, **kwargs)
            held = client.request(
                method, _concrete(path), headers={**pinned, **operator()}, **kwargs
            )
            if (bare.status_code, bare.content) != (held.status_code, held.content):
                opened.add((method, path))
    registered = {
        (method, getattr(route, "path", ""))
        for route in flatten_routes(app)
        for method in (getattr(route, "methods", None) or ["GET"])
    }
    assert swept > 50, swept
    assert ("GET", USAGE) in registered
    return opened


def test_the_sweep_with_the_backup_on_opens_exactly_two_routes(
    on_app: FastAPI, on_client: TestClient
) -> None:
    assert _routes_the_operator_token_opens(on_app, on_client) == {
        ("GET", USAGE),
        ("POST", OPERATOR_BACKUP),
    }
    # The MCP surface resolves its own credential and does not know this one.
    # It answers a refusal as a JSON-RPC error, so the body is what is asserted.
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    accept = {"Accept": "application/json, text/event-stream"}
    bare = on_client.post("/mcp", json=call, headers=accept)
    held = on_client.post("/mcp", json=call, headers={**accept, **operator()})
    assert "error" in held.json() and "result" not in held.json(), held.text
    assert "carried no credential" in held.json()["error"]["message"], held.text
    assert held.content == bare.content


@pytest.mark.parametrize("setting", [{"operator_backup": False}, {}], ids=["off", "unset"])
def test_the_sweep_with_the_backup_off_opens_only_the_usage_counts(
    tmp_path: Path, setting: dict[str, Any]
) -> None:
    """**Fence**: true on a tree without the route."""
    app = create_app(_settings(tmp_path / "data", operator_token=OPERATOR_TOKEN, **setting))
    with TestClient(app) as client:
        assert _routes_the_operator_token_opens(app, client) == {("GET", USAGE)}


# ------------------------------------------------------------------------ AC5


def test_an_operator_backup_is_audited_with_the_marker_and_an_admin_backup_without(
    on_app: FastAPI, on_client: TestClient
) -> None:
    services = on_app.state.services
    admin_pat = admin_token(on_app)
    administrator = services.principals.find_by_email("grace@example.com")
    assert administrator is not None and administrator.id != BOOTSTRAP_PRINCIPAL_ID

    by_operator = on_client.post(OPERATOR_BACKUP, headers=operator())
    assert by_operator.status_code == 200, by_operator.text
    by_admin = on_client.post(ADMIN_BACKUP, headers=auth(admin_pat))
    assert by_admin.status_code == 200, by_admin.text

    rows = _backup_rows(on_app)
    assert len(rows) == 2, rows
    operator_rows = [r for r in rows if r["request_id"] == by_operator.headers["x-request-id"]]
    assert len(operator_rows) == 1, rows
    (operator_row,) = operator_rows
    assert operator_row["principal_id"] == BOOTSTRAP_PRINCIPAL_ID
    assert set(operator_row["new_value"]) == {"snapshot_bytes", "snapshot_ms", "trigger"}
    assert operator_row["new_value"]["trigger"] == MARKER

    (admin_row,) = [r for r in rows if r["request_id"] == by_admin.headers["x-request-id"]]
    assert admin_row["principal_id"] == administrator.id
    assert set(admin_row["new_value"]) == {"snapshot_bytes", "snapshot_ms"}

    # What a workspace administrator reads through the API. The route has no ``action``
    # filter, so the parameter is ignored and the events are selected here.
    feed = on_client.get("/api/v1/audit-events?action=backup_taken", headers=auth(admin_pat))
    assert feed.status_code == 200, feed.text
    taken = {e["request_id"]: e for e in feed.json()["events"] if e["action"] == "backup_taken"}
    assert set(taken) == {operator_row["request_id"], admin_row["request_id"]}, taken
    assert taken[operator_row["request_id"]]["new_value"]["trigger"] == MARKER
    assert "trigger" not in taken[admin_row["request_id"]]["new_value"]

    # The caller chooses its request id, on every route. Borrowing the administrator's
    # does not shed the marker.
    borrowed = on_client.post(
        OPERATOR_BACKUP, headers={**operator(), "X-Request-Id": admin_row["request_id"]}
    )
    assert borrowed.status_code == 200, borrowed.text
    newest = _backup_rows(on_app)[-1]
    assert newest["request_id"] == admin_row["request_id"]
    assert newest["principal_id"] == BOOTSTRAP_PRINCIPAL_ID
    assert newest["new_value"]["trigger"] == MARKER
    assert len(_backup_rows(on_app)) == 3


# ------------------------------------------------------------------------ AC7


def test_the_operator_backup_runs_while_the_workspace_is_read_only(tmp_path: Path) -> None:
    """Open while frozen by construction: the route reaches no scope dependency, so it is
    not, and must not be, an entry in ``READ_ONLY_OPEN_ROUTES``."""
    app = _backup_on(tmp_path / "frozen", read_only=True)
    with TestClient(app) as client:
        response = client.post(OPERATOR_BACKUP, headers=operator())
        assert response.status_code == 200, response.text
        assert _tar_members(response.content)[0] == SNAPSHOT_ARCNAME


# ------------------------------------------------------------------- AC9 and AC11


@pytest.mark.parametrize("setting", [{}, {"operator_backup": False}], ids=["unset", "false"])
def test_without_the_opt_in_the_right_token_is_refused_like_a_wrong_one(
    tmp_path: Path, on_client: TestClient, setting: dict[str, Any]
) -> None:
    app = create_app(_settings(tmp_path / "off", operator_token=OPERATOR_TOKEN, **setting))
    with TestClient(app) as client:
        right = client.post(OPERATOR_BACKUP, headers=operator())
        wrong = client.post(OPERATOR_BACKUP, headers=operator(WRONG_TOKEN))
        wrong_where_on = on_client.post(OPERATOR_BACKUP, headers=operator(WRONG_TOKEN))
        for response in (right, wrong, wrong_where_on):
            _assert_refused(response)
        assert _shape(right) == _shape(wrong) == _shape(wrong_where_on)
        assert _staged(app) == []
        assert _backup_rows(app) == []
        # The same credential still opens what it opened before this change.
        assert client.get(USAGE, headers=operator()).status_code == 200


def test_with_the_opt_in_off_the_comparison_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting is consulted after the comparison, never instead of it, so the work
    done for a wrong token is the same whether or not a deployment opted in. No answer
    shows the order, so it is counted: one comparison per call, on or off."""
    for name, setting in (("off", False), ("on", True)):
        app = _backup_on(tmp_path / name, operator_backup=setting)
        with TestClient(app) as client:
            usage = app.state.services.usage
            compared: list[str | None] = []
            real = usage.token_matches

            def counting(
                presented: str | None, seen: list[str | None] = compared, real: Any = real
            ) -> bool:
                seen.append(presented)
                return bool(real(presented))

            monkeypatch.setattr(usage, "token_matches", counting)
            _assert_refused(client.post(OPERATOR_BACKUP, headers=operator(WRONG_TOKEN)))
            assert compared == [WRONG_TOKEN], (name, compared)


def test_an_upgrade_keeps_usage_only_for_a_deployment_that_did_nothing(tmp_path: Path) -> None:
    """A deployment that set the operator credential under "no tenant content" and then
    took the new image without touching its configuration keeps that promise."""
    app = create_app(_settings(tmp_path / "upgraded", operator_token=OPERATOR_TOKEN))
    with TestClient(app) as client:
        _assert_refused(client.post(OPERATOR_BACKUP, headers=operator()))
        counts = client.get(USAGE, headers=operator())
        assert counts.status_code == 200, counts.text
        assert "records_live" in counts.json()
        assert _backup_rows(app) == []


# ------------------------------------------------------------------------ AC16


def _encodings(secret: bytes) -> dict[str, bytes]:
    return {
        "raw": secret,
        "sha256 hex": hashlib.sha256(secret).hexdigest().encode(),
        "base64": base64.b64encode(secret),
        "hex": secret.hex().encode(),
    }


def test_the_relay_token_is_in_no_artifact_a_token_holder_can_obtain(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
    capfd: pytest.CaptureFixture[str],
) -> None:
    """**Fence**: the key that protects a stored sign-in code, and the token it is derived
    from, appear in nothing the operator credential can reach. The derivation is restated
    here rather than imported, so the test says what is being searched for."""
    relay_token = live_relay.relay.token
    derived = hashlib.sha256(b"glosswork.sign-in-code-key.v1:" + relay_token.encode()).digest()
    app = create_app(
        relay_settings(
            tmp_path / "hosted",
            live_relay,
            operator_token=OPERATOR_TOKEN,
            operator_backup=True,
            log_level="debug",
        )
    )
    haystacks: dict[str, bytes] = {}

    def keep(name: str, response: Any) -> None:
        headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        haystacks[name] = headers.encode() + b"\n\n" + response.content

    with TestClient(app) as client:
        seed_local_user(app)
        keep("code request", request_code(client, KNOWN_EMAIL))
        (code,) = codes_sent_to(live_relay.relay, KNOWN_EMAIL)
        signed_in = verify_code(client, KNOWN_EMAIL, code)
        assert signed_in.status_code == 200, signed_in.text
        keep("sign-in", signed_in)
        anonymous = TestClient(app)
        taken = anonymous.post(OPERATOR_BACKUP, headers=operator())
        assert taken.status_code == 200, taken.text
        assert _tar_members(taken.content)[0] == SNAPSHOT_ARCNAME
        keep("operator backup", taken)
        keep("usage", anonymous.get(USAGE, headers=operator()))
        keep("refusal", anonymous.post(OPERATOR_BACKUP, headers=operator(WRONG_TOKEN)))
        keep("openapi", anonymous.get("/openapi.json"))
        keep("modes", anonymous.get("/api/v1/auth/modes"))
        for path in sorted(_data_dir(app).rglob("*")):
            if path.is_file():
                haystacks[f"file {path.relative_to(_data_dir(app))}"] = path.read_bytes()
    out, err = capfd.readouterr()
    haystacks["stdout"] = out.encode()
    haystacks["stderr"] = err.encode()

    assert b"relay_send" in haystacks["stdout"] + haystacks["stderr"], "the log was not captured"
    assert any(name.endswith("glosswork.sqlite3") for name in haystacks), sorted(haystacks)
    needles = {
        **{f"relay token, {k}": v for k, v in _encodings(relay_token.encode()).items()},
        **{f"derived key, {k}": v for k, v in _encodings(derived).items()},
    }
    found = [
        (needle, where)
        for needle, value in needles.items()
        for where, hay in haystacks.items()
        if value in hay
    ]
    assert found == [], found


# ------------------------------------------------------------- structural pins (F7)


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _method_call_sites(method: str) -> set[tuple[str, str]]:
    """``(module, enclosing function)`` for every call of ``<something>.<method>(...)`` in
    ``src/``. A syntax-tree walk, so a mention in a comment or a docstring is not a call."""
    sites: set[tuple[str, str]] = set()
    for path in _modules():
        for function in ast.walk(_tree(path)):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == method
                ):
                    sites.add((_relative(path), function.name))
    return sites


def test_pin_the_operator_credential_is_checked_from_a_named_set_of_places() -> None:
    """One comparison, two raisers, and one caller each. A third reader of the operator
    credential, or a second route calling a raiser, is a visible edit here."""
    assert _method_call_sites("token_matches") == {
        ("src/glosswork/services/usage.py", "require_operator"),
        ("src/glosswork/services/usage.py", "require_operator_backup"),
    }
    assert _method_call_sites("require_operator") == {
        ("src/glosswork/services/usage.py", "snapshot"),
    }
    assert _method_call_sites("require_operator_backup") == {
        ("src/glosswork/routes/operator_backup.py", "operator_backup_credential"),
    }


def test_pin_the_backup_setting_is_read_in_exactly_two_modules() -> None:
    readers = {
        _relative(path)
        for path in _modules()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute) and node.attr == "operator_backup"
    }
    assert readers == {"src/glosswork/config.py", "src/glosswork/services/usage.py"}, readers


def test_pin_a_backup_is_marked_operator_triggered_at_exactly_one_call_site() -> None:
    sites = [
        _relative(path)
        for path in _modules()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "operator_triggered"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
    ]
    assert sites == ["src/glosswork/routes/operator_backup.py"], sites


def test_pin_a_sign_in_code_has_one_stored_form_and_it_is_keyed() -> None:
    """In ``services/sign_in_codes.py``: ``hashlib.sha256(...)`` is called only inside
    ``derive_code_key``, ``hmac.new(...)`` only inside ``code_hash``, and nothing else in
    the module computes a digest. So no function can store a code the unkeyed way."""
    tree = _tree(SRC / "services" / "sign_in_codes.py")
    digests: set[tuple[str, str]] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("hashlib", "hmac")
                and node.func.attr != "compare_digest"
            ):
                digests.add((function.name, f"{node.func.value.id}.{node.func.attr}"))
    assert digests == {("derive_code_key", "hashlib.sha256"), ("code_hash", "hmac.new")}, digests


def test_pin_every_backup_stream_is_handed_to_the_closing_response() -> None:
    """A staged snapshot is removed when its response ends only if the response closes the
    generator. Every ``<services>.backup.stream(...)`` in ``src/`` is therefore a direct
    argument of ``ClosingStreamingResponse(...)``, on both backup routes."""
    wrapped: list[str] = []
    streams = 0
    for path in _modules():
        tree = _tree(path)
        for node in ast.walk(tree):
            if _is_backup_stream(node):
                streams += 1
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ClosingStreamingResponse"
                and any(_is_backup_stream(argument) for argument in node.args)
            ):
                wrapped.append(_relative(path))
    assert sorted(wrapped) == [
        "src/glosswork/routes/admin_ops.py",
        "src/glosswork/routes/operator_backup.py",
    ], wrapped
    assert streams == len(wrapped), (streams, wrapped)


def _is_backup_stream(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "stream"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "backup"
    )


def test_pin_the_operator_credential_routes_are_exactly_the_backup(on_app: FastAPI) -> None:
    """``route_capabilities``' counterpart for the operator credential. The usage route is
    not in it: its check is inside ``UsageService.snapshot``, which the call-site pin
    above covers."""
    from glosswork.scopes import route_operator_credentials

    assert set(route_operator_credentials(on_app)) == {("POST", OPERATOR_BACKUP)}
