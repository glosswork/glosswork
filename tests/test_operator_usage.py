"""The operator usage endpoint (DD-39, FR-P10).

``GET /api/v1/usage`` answers an operator who holds ``GW_OPERATOR_TOKEN`` with counts,
and answers everybody else -- including a workspace administrator holding an ``admin``
personal access token -- with one refusal that discloses nothing, not even whether the
feature is configured.

**What this file is really guarding.** The endpoint is easy; the boundary is the work.
Three of the five fields it reports are counted over strings the tenant
chooses (object type keys, agent labels, attachment filenames) and a fourth, the tool
name, is read straight off the caller's own request. ``test_no_content_*`` writes one
sentinel into every one of those places and sweeps the whole response for it, sweeps the
**failure** body too (an error *message* carries tenant values routinely where an error
*code* cannot), pins the response's key set by equality (a sweep for one string cannot
prove that a field nobody thought of is absent), and sweeps every response body and log
line for the configured operator token.

Run it bare: ``uv run pytest -q tests/test_operator_usage.py``. A summary line that says
anything but ``N passed`` -- ``skipped``, ``xfailed``, ``xpassed``, ``deselected``,
``error`` -- is a failure whatever the exit code says, because a skipped test is a
collected test and a collected count proves nothing.

``-k operator_backup_setting`` selects the startup behaviour of ``GW_OPERATOR_BACKUP``
(change 20), the setting that lets the same credential take a backup: 5 passed.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.app import create_app
from glosswork.config import ConfigError, Settings
from glosswork.db import Database
from glosswork.errors import STATUS_BY_CODE
from glosswork.services import ServiceBundle
from glosswork.services.usage import (
    KNOWN_HARNESSES,
    KNOWN_TOOLS,
    OPERATOR_TOKEN_HEADER,
    OTHER_HARNESS,
    UNKNOWN_ERROR_CODE,
    UNKNOWN_TOOL_NAME,
    UsageService,
    outcome,
)
from tests.conftest import auth, make_actor, mint_scope_tokens
from tests.fake_relay import new_token
from tests.mcp_support import memory_session, seed_task_type

# Obviously a fixture, deliberately not a random-looking blob: a 32-character base64 literal
# once shipped in two test files and it trips secret scanners. Exactly 32 characters, so it
# also proves the floor admits its own minimum.
OPERATOR_TOKEN = "operator-token-for-tests-0123456"
WRONG_TOKEN = "operator-token-for-tests-9999999"

# One string, written into every tenant-chosen place the endpoint counts over, then swept
# for in the whole response body. Chosen to survive nothing: it is not a substring of any
# field name, any tool name, any error code or any harness literal.
SENTINEL = "zqxleak7"

# The response's complete key set, pinned by equality. A sweep for one
# string cannot prove that a field nobody thought about is absent, and a tenant value that
# arrives base64, URL-encoded or JSON-escaped survives a substring sweep. Adding a field
# turns this red before anyone has to notice.
RESPONSE_KEYS = frozenset(
    {
        "since",
        "records_live",
        "records_deleted",
        "attachment_count",
        "attachment_bytes_logical",
        "attachment_bytes_stored",
        "humans_active",
        "humans_total",
        "agent_labels_distinct",
        "agent_label_calls_total",
        "agent_labels_by_harness",
        "object_types",
        "fields",
        "tool_calls",
    }
)


# ------------------------------------------------------------------------ fixtures


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url="http://testserver",
        **overrides,
    )


@pytest.fixture
def metered_app(tmp_path: Path) -> FastAPI:
    """An app with ``GW_OPERATOR_TOKEN`` configured."""
    return create_app(_settings(tmp_path, operator_token=OPERATOR_TOKEN))


@pytest.fixture
def metered_client(metered_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(metered_app) as test_client:
        tokens = mint_scope_tokens(metered_app.state.services)
        test_client.scope_tokens = tokens  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def unmetered_client(tmp_path: Path) -> Iterator[TestClient]:
    """An app that never set the variable: the endpoint is off."""
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


def operator(token: str = OPERATOR_TOKEN) -> dict[str, str]:
    return {OPERATOR_TOKEN_HEADER: token}


def usage(client: TestClient, headers: dict[str, str] | None = None) -> Any:
    return client.get("/api/v1/usage", headers=headers or operator())


def test_usage_answers_with_a_trial_end_set(tmp_path: Path) -> None:
    """The usage read calls the service method that builds the workspace document, so
    whatever a trial end's value could do to that method it would do to the hosting
    operator's read as well (change 30). The end time is converted once, at startup, and
    this is the read that would answer 500 if it were converted per request instead."""
    settings = _settings(
        tmp_path,
        operator_token=OPERATOR_TOKEN,
        trial_ends_at="2026-10-09T15:00:00Z",
        subscribe_url="https://example.com/subscribe",
    )
    with TestClient(create_app(settings)) as trial:
        response = usage(trial)
    assert response.status_code == 200, response.text
    assert set(response.json()) == RESPONSE_KEYS, response.text


# ---------------------------------------------------------- a tenant PAT is refused


@pytest.mark.parametrize("scope", ["read", "write", "admin"])
def test_a_tenant_pat_is_refused_whatever_its_scope(metered_client: TestClient, scope: str) -> None:
    """The issue's own clause: "a test proves a tenant admin PAT is refused".

    An ``admin`` PAT is the highest credential a workspace can mint, and a workspace
    administrator can mint it for themselves, so if it opened this endpoint the operator
    token would be decoration. It is refused at the route, not at the edge: the path is
    credential-exempt, so ``Authorization`` is never resolved on it at all.
    """
    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    response = metered_client.get("/api/v1/usage", headers=auth(tokens[scope]))
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error"]["code"] == "operator_token_refused"
    assert RESPONSE_KEYS.isdisjoint(body), body


def test_a_tenant_pat_is_refused_even_beside_a_correct_operator_token(
    metered_client: TestClient,
) -> None:
    """The two credentials are read from different headers and neither shadows the
    other: presenting a tenant PAT does not spoil a correct operator token, and the
    operator token is what decides."""
    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    response = metered_client.get("/api/v1/usage", headers={**auth(tokens["admin"]), **operator()})
    assert response.status_code == 200, response.text


# ------------------------------------------------------ off, and off is not detectable


def test_unconfigured_and_wrong_credential_answer_identically(
    unmetered_client: TestClient, metered_client: TestClient
) -> None:
    """An unauthenticated prober cannot tell a metered workspace from an unmetered one.

    Answering ``feature_disabled`` when the variable is unset would be the wrong way round:
    ``feature_disabled`` is **409** where a credential refusal is **401**. That pair is
    exactly the one-request oracle this asserts the absence of. So the refusal is compared
    **before** the feature check, which is the opposite order from
    ``BootstrapService.claim``; DD-39 records the difference and why the two differ.
    """
    off = usage(unmetered_client)
    wrong = usage(metered_client, operator(WRONG_TOKEN))
    assert off.status_code == wrong.status_code == 401
    assert off.content == wrong.content, (off.text, wrong.text)
    assert off.json()["error"]["code"] == "operator_token_refused"


def test_unconfigured_refuses_a_caller_presenting_nothing_at_all(
    unmetered_client: TestClient, metered_client: TestClient
) -> None:
    """A missing header is a mismatch, not a separate answer (the same rule
    ``BootstrapService._secret_matches`` applies to an absent secret)."""
    off = unmetered_client.get("/api/v1/usage")
    on = metered_client.get("/api/v1/usage")
    assert off.status_code == on.status_code == 401
    assert off.content == on.content


def test_unconfigured_by_a_blank_value_is_exactly_unconfigured(tmp_path: Path) -> None:
    """Blank counts as unset, and this is the test the pattern never had.

    ``.env.example`` ships every variable with an empty value, so an operator running the
    image with that file as ``--env-file`` must get a deployment that starts and has the
    feature off. ``GW_BOOTSTRAP_SECRET`` is meant to behave this way too; this module
    writes the test rather than inheriting the gap twice.
    """
    with TestClient(create_app(_settings(tmp_path, operator_token=""))) as blank:
        response = usage(blank)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "operator_token_refused"


def test_a_short_operator_token_refuses_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-blank value under the floor is a startup refusal naming the variable, not a
    deployment quietly serving counts behind a guessable string.

    Note the neighbouring case and that it behaves differently on purpose: ``"  "`` is
    truthy, so it reaches the floor and refuses to start, where ``""`` is off. The floor
    is a length and not an entropy check, exactly as ``BOOTSTRAP_SECRET_MIN_LENGTH``'s own
    comment says of itself.
    """
    from glosswork.config import load_settings

    for value in ("too-short", "  "):
        monkeypatch.setenv("GW_OPERATOR_TOKEN", value)
        with pytest.raises(ConfigError) as caught:
            load_settings()
        assert "GW_OPERATOR_TOKEN" in str(caught.value)
        assert value.strip() not in str(caught.value) or not value.strip()


# ------------------------------------------------- GW_OPERATOR_BACKUP at startup


def _load_with(monkeypatch: pytest.MonkeyPatch, **environment: str) -> Settings:
    """``load_settings`` over exactly these variables, whatever the machine has set."""
    import os

    from glosswork.config import load_settings

    for name in list(os.environ):
        if name.startswith("GW_"):
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return load_settings()


def test_operator_backup_setting_on_with_no_operator_token_refuses_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller of the backup route is a scheduled job. A nightly 401 for a reason set
    at boot is a diagnosis nobody can act on from there, so it is a startup refusal."""
    for token in (None, ""):
        environment = {"GW_OPERATOR_BACKUP": "true"}
        if token is not None:
            environment["GW_OPERATOR_TOKEN"] = token
        with pytest.raises(ConfigError) as caught:
            _load_with(monkeypatch, **environment)
        assert "GW_OPERATOR_BACKUP" in str(caught.value)


def test_operator_backup_setting_refuses_a_relay_token_equal_to_the_operator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored form of a sign-in code is keyed from the relay token. If that token were
    also the credential the backup route accepts, the holder of the credential would hold
    the key. Refused only when the backup is on: with it off, the same pair loads."""
    shared = {
        "GW_OPERATOR_TOKEN": OPERATOR_TOKEN,
        "GW_RELAY_TOKEN": OPERATOR_TOKEN,
        "GW_RELAY_URL": "https://relay.example/v1/relay/send",
    }
    with pytest.raises(ConfigError) as caught:
        _load_with(monkeypatch, GW_OPERATOR_BACKUP="true", **shared)
    message = str(caught.value)
    assert "GW_RELAY_TOKEN" in message and "GW_OPERATOR_TOKEN" in message, message
    assert OPERATOR_TOKEN not in message

    for off in ({}, {"GW_OPERATOR_BACKUP": "false"}):
        loaded = _load_with(monkeypatch, **shared, **off)
        assert loaded.operator_backup is False


@pytest.mark.parametrize("value", ["", "maybe"])
def test_operator_backup_setting_blank_or_malformed_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A boolean like every other boolean here: a blank value never reads as on, and
    never reads as off either. It refuses startup naming the variable."""
    with pytest.raises(ConfigError) as caught:
        _load_with(monkeypatch, GW_OPERATOR_TOKEN=OPERATOR_TOKEN, GW_OPERATOR_BACKUP=value)
    assert "GW_OPERATOR_BACKUP" in str(caught.value)
    assert OPERATOR_TOKEN not in str(caught.value)


def test_operator_backup_setting_loads_off_and_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _load_with(monkeypatch).operator_backup is False
    assert _load_with(monkeypatch, GW_OPERATOR_TOKEN=OPERATOR_TOKEN).operator_backup is False
    off = _load_with(monkeypatch, GW_OPERATOR_TOKEN=OPERATOR_TOKEN, GW_OPERATOR_BACKUP="false")
    assert off.operator_backup is False
    on = _load_with(
        monkeypatch,
        GW_OPERATOR_TOKEN=OPERATOR_TOKEN,
        GW_OPERATOR_BACKUP="true",
        # Made at run time, so no credential-shaped literal sits in the tree.
        GW_RELAY_TOKEN=new_token(),
        GW_RELAY_URL="https://relay.example/v1/relay/send",
    )
    assert on.operator_backup is True


# ------------------------------------------------------------------------ the counts


def test_counts_match_what_the_run_itself_created(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """Every number is compared against a value computed in this run, never a constant
    (``docs/changes/README.md``, "A count in an assertion comes from the run")."""
    services: ServiceBundle = metered_app.state.services
    before = usage(metered_client).json()

    seed_task_type(services)
    actor = make_actor()
    live = services.records.create_record(actor, "task", {"title": "Live one"})
    doomed = services.records.create_record(actor, "task", {"title": "Doomed one"})
    services.records.delete_record(actor, doomed.key)

    body = usage(metered_client).json()
    assert body["records_live"] == before["records_live"] + 1
    assert body["records_deleted"] == before["records_deleted"] + 1
    assert body["object_types"] == before["object_types"] + 1
    assert body["fields"] > before["fields"]
    assert body["humans_active"] == services.workspace.get_workspace().people
    assert body["agent_labels_distinct"] == services.workspace.get_workspace().agents
    assert body["humans_total"] >= body["humans_active"]
    assert body["since"] is not None and body["since"].endswith("Z")
    assert live.key not in usage(metered_client).text


def test_counts_deduplicate_stored_attachment_bytes(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """One file uploaded twice is two attachments and one blob, and reporting a single
    number would silently pick a side: storage and billing disagree about it."""
    services: ServiceBundle = metered_app.state.services
    payload = b"the same bytes, twice over"
    for name in ("first.txt", "second.txt"):
        services.attachments.upload(make_actor(), name, "text/plain", payload)

    body = usage(metered_client).json()
    assert body["attachment_count"] == 2
    assert body["attachment_bytes_logical"] == 2 * len(payload)
    assert body["attachment_bytes_stored"] == len(payload)


def test_counts_tool_calls_by_name_and_error_code(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """The field nothing in the deployment had a source for before this endpoint."""
    import anyio

    services: ServiceBundle = metered_app.state.services
    seed_task_type(services)
    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    server = _server_for(metered_app)

    async def drive() -> None:
        async with memory_session(server, tokens["admin"]) as session:
            await session.call_tool("list_object_types", {})
            await session.call_tool("list_object_types", {})
            await session.call_tool("get_record", {"record": "TSK-404"})

    anyio.run(drive)

    rows = usage(metered_client).json()["tool_calls"]
    by_pair = {(row["tool"], row["error_code"]): row["count"] for row in rows}
    assert by_pair[("list_object_types", None)] == 2
    assert by_pair[("get_record", "not_found")] == 1
    assert rows == sorted(rows, key=lambda r: (r["tool"], r["error_code"] or "")), rows


# -------------------------------------------------------------------- no content leaks


def test_no_content_reaches_the_operator(
    metered_client: TestClient, metered_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """The boundary, asserted rather than asserted about.

    One sentinel goes into an object type key, a field key, a record value, an attachment
    filename, a principal display name, an agent label, and the *name* of a tool that is
    called. None of it may come back, and neither may the operator's own credential.
    """
    import anyio

    services: ServiceBundle = metered_app.state.services
    actor = make_actor()
    services.schema.create_object_type(
        actor,
        key=SENTINEL,
        name=SENTINEL.title(),
        name_plural=f"{SENTINEL.title()}s",
        description=f"A type whose every string carries {SENTINEL} on purpose.",
        key_prefix="ZQX",
        fields=[
            {
                "key": SENTINEL,
                "name": SENTINEL.title(),
                "type": "short_text",
                "description": f"A field key carrying {SENTINEL}.",
                "required": True,
            }
        ],
    )
    services.records.create_record(actor, SENTINEL, {SENTINEL: SENTINEL})
    services.attachments.upload(actor, f"{SENTINEL}.txt", "text/plain", b"x")
    services.principals.create_user(
        actor, email=f"{SENTINEL}@example.com", display_name=SENTINEL, role="member"
    )
    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    server = _server_for(metered_app)

    async def drive() -> None:
        async with memory_session(server, tokens["admin"], agent_label=SENTINEL) as session:
            await session.call_tool("list_object_types", {})
            await session.call_tool(SENTINEL, {})

    with caplog.at_level(logging.DEBUG):
        anyio.run(drive)
        response = usage(metered_client)

    assert response.status_code == 200, response.text
    assert SENTINEL not in response.text
    assert OPERATOR_TOKEN not in response.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert OPERATOR_TOKEN not in logged


def test_no_content_in_the_response_key_set(metered_client: TestClient) -> None:
    """Pinned by equality, not swept: a sweep for one string cannot prove that
    a field nobody thought about is absent, and a tenant value that is base64,
    URL-encoded or JSON-escaped survives a substring search."""
    body = usage(metered_client).json()
    assert set(body) == set(RESPONSE_KEYS)
    assert set(body["agent_labels_by_harness"]) <= set(KNOWN_HARNESSES) | {OTHER_HARNESS}


def test_no_content_in_the_failure_body(
    metered_client: TestClient, metered_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary was written for the success path, and an error
    *message* carries tenant values routinely where an error *code* cannot:
    ``unknown_field`` names the field key and lists every valid key in one string. So the
    service catches everything that is not its own refusal and raises ``internal_error``,
    which discloses a request id and nothing else."""
    from glosswork.errors import UnknownFieldError

    services: ServiceBundle = metered_app.state.services

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise UnknownFieldError(SENTINEL, [SENTINEL, f"{SENTINEL}_two"])

    monkeypatch.setattr(services.usage, "_read_counts", explode)
    response = usage(metered_client)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert SENTINEL not in response.text
    assert OPERATOR_TOKEN not in response.text


# ---------------------------------------------------------------------- the allowlists


def test_the_allowlist_holds_for_an_unregistered_tool_name(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """The tool name in ``tools/call`` is the caller's own string, so it is
    written only when the catalog knows it and otherwise the literal ``unknown_tool``.
    Two outcomes carry no error code at all -- an unregistered name and a Pydantic
    coercion failure both answer ``isError: true`` with no ``structuredContent`` -- and
    both collapse to the literal ``unknown``. That is intended, not a gap: an operator
    reading ``unknown`` learns an agent is calling wrongly and nothing more."""
    import anyio

    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    server = _server_for(metered_app)

    async def drive() -> None:
        async with memory_session(server, tokens["admin"]) as session:
            await session.call_tool(f"not_a_tool_{SENTINEL}", {})
            await session.call_tool("get_record", {"record": {"not": "a string"}})

    anyio.run(drive)

    rows = usage(metered_client).json()["tool_calls"]
    names = {row["tool"] for row in rows}
    codes = {row["error_code"] for row in rows}
    assert names == {UNKNOWN_TOOL_NAME, "get_record"}, rows
    assert codes <= set(STATUS_BY_CODE) | {None, UNKNOWN_ERROR_CODE}
    assert UNKNOWN_ERROR_CODE in codes
    assert SENTINEL not in usage(metered_client).text


def test_the_allowlist_emits_its_own_literal_and_never_a_prefix(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """Harness names match exactly. A prefix match would report
    ``claude-code-ACME-CORP`` as a known harness while carrying the tenant's customer
    name. Exact equality after case-folding, or ``other``."""
    import anyio

    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    server = _server_for(metered_app)

    async def drive() -> None:
        for label in ("claude-code", "Claude-Code", f"claude-code-{SENTINEL}"):
            async with memory_session(server, tokens["admin"], agent_label=label) as session:
                await session.call_tool("list_object_types", {})

    anyio.run(drive)

    body = usage(metered_client).json()
    harnesses = body["agent_labels_by_harness"]
    assert harnesses["claude-code"] == 2, harnesses
    assert harnesses[OTHER_HARNESS] == 1, harnesses
    assert SENTINEL not in usage(metered_client).text


# -------------------------------------------------- counting failure never fails a call


def test_counting_failure_never_fails_a_call(
    metered_app: FastAPI, metered_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counting never fails a call, and the finding that makes that necessary.

    The seam's own ``except Exception`` answers ``tools/call`` with
    ``error_result(self._unclassified())``, so counting code that raises where the seam
    can see it converts a call that **succeeded** into ``internal_error`` for the caller.
    Every successful tool call in the deployment, from one ``AttributeError``. The counter
    therefore sits outside that ``try`` and carries its own.
    """
    import anyio

    services: ServiceBundle = metered_app.state.services

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AttributeError("'dict' object has no attribute 'is_error'")

    monkeypatch.setattr(services.usage, "record_tool_call", explode)
    tokens: dict[str, str] = metered_client.scope_tokens  # type: ignore[attr-defined]
    server = _server_for(metered_app)
    seed_task_type(services)

    async def drive() -> list[Any]:
        async with memory_session(server, tokens["admin"]) as session:
            return [await session.call_tool("list_object_types", {})]

    results = anyio.run(drive)
    assert results[0].is_error is False, results[0]
    assert results[0].structured_content is not None


# ---------------------------------------------------- the classifier reads every shape


def test_outcome_classifier_reads_a_successful_call(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The first shape, and the one that catches the easiest wrong assumption:
    ``result.is_error`` raises ``AttributeError`` here, because what reaches the seam is
    a plain camelCase wire ``dict``."""
    seed_task_type(services)
    seen = _classify(mcp_server, pat["admin"], "list_object_types", {})
    assert seen == [(True, None)], seen


def test_outcome_classifier_reads_a_domain_error(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The second shape: the same plain ``dict``, ``isError`` true, the code under
    ``structuredContent.error.code``."""
    seed_task_type(services)
    seen = _classify(mcp_server, pat["admin"], "get_record", {"record": "TSK-404"})
    assert seen == [(True, "not_found")], seen


def test_outcome_classifier_reads_an_unregistered_tool_name(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The third shape: a plain ``dict`` with **no ``structuredContent`` key at all**, so there
    is no code anywhere and the literal ``unknown`` is the answer."""
    seen = _classify(mcp_server, pat["admin"], "no_such_tool", {})
    assert seen == [(True, UNKNOWN_ERROR_CODE)], seen


def test_outcome_classifier_reads_a_coercion_failure(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The fourth shape: the SDK's own argument validation, which also returns no
    ``structuredContent``."""
    seed_task_type(services)
    seen = _classify(mcp_server, pat["admin"], "get_record", {"record": {"not": "a string"}})
    assert seen == [(True, UNKNOWN_ERROR_CODE)], seen


def test_outcome_classifier_reads_a_short_circuited_refusal(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The fifth shape: ``insufficient_scope`` is the middleware's own refusal, so it is a
    ``CallToolResult`` **object** with ``is_error`` and ``structured_content`` -- the one
    shape it is easy to assume is the only shape."""
    seen = _classify(mcp_server, pat["read"], "create_object_type", {})
    assert seen == [(True, "insufficient_scope")], seen


def test_outcome_classifier_refuses_to_count_an_unfinished_call() -> None:
    """``result_type`` is ``Literal["complete", "input_required"] | str`` and this
    repository already imports ``InputRequiredResult``. No tool returns one today; when
    the first elicitation tool does, an unfinished call must not be recorded as a
    finished one. Constructed rather than driven, because nothing produces it yet."""
    assert outcome({"resultType": "input_required", "isError": False}) == (False, None)
    assert outcome(None) == (False, None)


# ------------------------------------------------ in memory, flushed, and a stop is clean


def test_a_read_tool_call_still_opens_no_write_transaction(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """Counts are held in memory. A read tool called with no agent
    label opens **zero** write transactions on this product today, so writing a counter
    row on every call would add a writer-lock acquisition to exactly the traffic a
    metering endpoint exists to watch. Counts are held in memory and flushed on an
    interval, the way ``services/tokens.py`` already coarsens ``last_used_at`` in this
    same product and for this same reason."""
    import anyio

    seed_task_type(services)

    async def drive() -> None:
        async with memory_session(mcp_server, pat["read"]) as session:
            await session.call_tool("list_object_types", {})

    # The first call of a token warms ``last_used_at``, which is itself a write; the
    # claim above was measured with both tokens warm and this is that condition.
    anyio.run(drive)

    writes = _count_write_transactions(services)
    with writes:
        anyio.run(drive)
    assert writes.count == 0, f"{writes.count} write transactions on a read tool call"
    assert services.usage.pending_count() == 1


def test_a_stop_loses_nothing(
    services: ServiceBundle, mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The objection to an in-memory counter, closed rather than accepted. A lossy
    counter loses counts when a container scales to zero, which is the normal way a
    hosted workspace stops. Every container gets a clean SIGTERM drain, so the counter
    flushes on shutdown too and a stop loses nothing."""
    import anyio

    seed_task_type(services)

    async def drive() -> None:
        async with memory_session(mcp_server, pat["admin"]) as session:
            for _ in range(3):
                await session.call_tool("list_object_types", {})

    anyio.run(drive)
    assert services.usage.pending_count() == 1
    assert _persisted(services) == {}

    services.usage.stop()

    assert services.usage.pending_count() == 0
    assert _persisted(services) == {("list_object_types", "ok"): 3}


def test_the_counter_flush_is_its_own_transaction(services: ServiceBundle) -> None:
    """The flush is its own transaction, and why getting it wrong is a hang rather than a
    red test: a ``db.write()`` opened inside an open ``db.write()`` on this ``Database``
    blocks for nearly thirteen seconds before ``database is locked`` on ``BEGIN IMMEDIATE``.

    The flush therefore never runs inside anybody else's transaction. Recording, which
    does run inside one (it happens at the MCP seam, after a write tool has committed but
    while nothing is open), touches no connection at all.
    """
    services.usage.record_tool_call(tool_name="list_object_types", error_code=None, registered=True)
    with services.usage._db.write() as conn:  # noqa: SLF001 - the point of the test
        conn.exec_driver_sql("SELECT 1")
        # Recording inside an open write transaction is free, because it is memory only.
        services.usage.record_tool_call(
            tool_name="list_object_types", error_code=None, registered=True
        )
    services.usage.flush()
    assert _persisted(services) == {("list_object_types", "ok"): 2}


def test_the_flush_fails_safe_and_keeps_its_counts(
    services: ServiceBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flush that cannot take the writer lock must not lose the counts it was holding
    and must not raise into the caller. They go back into the pending map and the next
    flush carries them."""
    services.usage.record_tool_call(tool_name="list_object_types", error_code=None, registered=True)

    def locked(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(services.usage, "_write_counts", locked)
    services.usage.flush()
    assert services.usage.pending_count() == 1

    monkeypatch.undo()
    services.usage.flush()
    assert _persisted(services) == {("list_object_types", "ok"): 1}


# ------------------------------------------------------------------------- the helpers


def _server_for(app: FastAPI) -> MCPServer:
    from glosswork.auth import PatTokenResolver
    from glosswork.mcp_server import create_mcp_server

    services: ServiceBundle = app.state.services
    return create_mcp_server(lambda: services, PatTokenResolver(lambda: services))


def _classify(
    server: MCPServer, token: str, tool: str, arguments: dict[str, Any]
) -> list[tuple[bool, str | None]]:
    """Drive one real ``tools/call`` and return what ``outcome()`` made of what the seam
    actually received, rather than of a result constructed by the test."""
    import anyio

    seen: list[tuple[bool, str | None]] = []
    # The adapter's own middleware, wrapped in place: what the classifier is asked about
    # is what the seam actually received from the SDK, not a result the test built.
    inner = server.middleware[-1]

    async def spy(ctx: Any, call_next: Any) -> Any:
        result = await inner(ctx, call_next)
        if ctx.method == "tools/call":
            seen.append(outcome(result))
        return result

    async def drive() -> None:
        async with memory_session(server, token) as session:
            try:
                await session.call_tool(tool, arguments)
            except Exception:  # noqa: BLE001 - a refused call is a shape under test
                pass

    server.middleware[-1] = spy
    try:
        anyio.run(drive)
    finally:
        server.middleware[-1] = inner
    return seen


class _WriteCounter:
    """Counts ``Database.write`` transactions opened while it is active."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._original = db.write
        self.count = 0

    def __enter__(self) -> _WriteCounter:
        counter = self

        def counted() -> Any:
            counter.count += 1
            return counter._original()

        self._db.write = counted  # type: ignore[method-assign]
        return self

    def __exit__(self, *_exc: object) -> None:
        self._db.write = self._original  # type: ignore[method-assign]


def _count_write_transactions(services: ServiceBundle) -> _WriteCounter:
    return _WriteCounter(services.usage._db)  # noqa: SLF001 - the bundle shares one Database


def _persisted(services: ServiceBundle) -> dict[tuple[str, str], int]:
    from sqlalchemy import text

    with services.usage._db.read() as conn:  # noqa: SLF001
        rows = conn.execute(text("SELECT tool_name, error_code, count FROM usage_counters")).all()
    return {(row[0], row[1]): int(row[2]) for row in rows}


def test_the_credential_comparison_is_one_function_with_four_answers(
    db: Database, tmp_path: Path
) -> None:
    """One function answers all four ways a caller can fail
    to be the operator, so none of them is distinguishable from outside."""
    from glosswork.services import build_services

    configured = build_services(
        db, tmp_path, _settings(tmp_path, operator_token=OPERATOR_TOKEN)
    ).usage
    unset = build_services(db, tmp_path, _settings(tmp_path)).usage
    blank = build_services(db, tmp_path, _settings(tmp_path, operator_token="")).usage

    assert configured.token_matches(OPERATOR_TOKEN) is True
    assert configured.token_matches(WRONG_TOKEN) is False
    assert configured.token_matches(None) is False
    assert unset.token_matches(OPERATOR_TOKEN) is False
    assert blank.token_matches("") is False
    assert isinstance(configured, UsageService)


# ------------------------------------------------- the read-time filter on tool names


#: Planted straight into ``usage_counters``, bypassing ``counter_key``. Five rows, chosen
#: so that three of them collapse onto two already-occupied keys: that is what makes the
#: uniqueness and conservation assertions below able to fail. The first
#: carries a string no tenant could have written accidentally.
PLANTED_ROWS: tuple[tuple[str, str, int], ...] = (
    (f"SENTINEL-TOOL-NAME-leak-me-{SENTINEL}", "ok", 3),
    (f"second-unknown-name-{SENTINEL}", "ok", 5),
    (f"third-unknown-name-{SENTINEL}", "not_found", 7),
    (UNKNOWN_TOOL_NAME, "ok", 2),
    ("get_record", f"not-a-real-error-code-{SENTINEL}", 4),
)

PLANTED_TOTAL = sum(count for _tool, _code, count in PLANTED_ROWS)


def _plant_counter_rows(services: ServiceBundle, rows: tuple[tuple[str, str, int], ...]) -> None:
    """Write rows into ``usage_counters`` without going through ``counter_key``.

    This is the second writer the boundary has never had. It is the shape a backfilling
    migration, a restored backup from an older schema, or a future second call site would
    take, and the read-time filter exists because the write-time rule is the only thing standing
    between such a writer and the operator's screen.
    """
    from sqlalchemy import text

    with services.usage._db.write() as conn:  # noqa: SLF001 - the bundle shares one Database
        for tool_name, error_code, count in rows:
            conn.execute(
                text(
                    "INSERT INTO usage_counters (tool_name, error_code, count) "
                    "VALUES (:tool, :code, :count)"
                ),
                {"tool": tool_name, "code": error_code, "count": count},
            )


def test_a_planted_tool_name_never_reaches_the_operator(
    metered_client: TestClient, metered_app: FastAPI
) -> None:
    """A planted tool name never reaches the operator.

    ``counter_key`` is sound and is the only writer today, so the boundary is closed at
    write time. The read-time filter ends an asymmetry: the harness column was filtered
    again at read time (``_emit_harnesses``) and the tool name was not, so a second writer
    would leak with nothing failing first.

    Four assertions, and the last two are the ones that matter. The filter's first
    wording collapsed unknown names to the literal ``unknown_tool`` **without summing**,
    which emitted two rows for ``(unknown_tool, null)`` where the table's primary key
    admits one, and an operator bills from these numbers. A test that only
    swept for the sentinel passed while that was true.
    """
    services: ServiceBundle = metered_app.state.services
    _plant_counter_rows(services, PLANTED_ROWS)

    response = usage(metered_client)
    assert response.status_code == 200, response.text
    assert SENTINEL not in response.text

    rows = response.json()["tool_calls"]
    assert {row["tool"] for row in rows} == {UNKNOWN_TOOL_NAME, "get_record"}, rows

    pairs = [(row["tool"], row["error_code"]) for row in rows]
    assert len(pairs) == len(set(pairs)), f"duplicate (tool, error_code) rows: {rows}"

    assert sum(int(row["count"]) for row in rows) == PLANTED_TOTAL, rows
    assert {(row["tool"], row["error_code"]): int(row["count"]) for row in rows} == {
        # 3 + 5 + 2: two tenant strings and the already-collapsed literal, summed onto
        # one key rather than emitted as three rows.
        (UNKNOWN_TOOL_NAME, None): 10,
        (UNKNOWN_TOOL_NAME, "not_found"): 7,
        ("get_record", UNKNOWN_ERROR_CODE): 4,
    }, rows


@pytest.mark.anyio
async def test_known_tools_is_exactly_the_live_catalog(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """``KNOWN_TOOLS`` against a live ``list_tools()``, not against a literal.

    Modelled on ``test_catalog_is_complete_with_exactly_the_documented_parameters`` and
    deliberately **not** on ``tests/test_mcp_read_only.py``'s pinning of
    ``READ_TOOLS_ABOVE_READ_SCOPE``, which compares the constant to a literal frozenset
    written inside the test and therefore cannot fail when a tool is added: it fails only
    when somebody edits the constant, which merely restates it.

    ``admin`` is the top of the scope ladder today (``actor.Scope`` is exactly
    ``read``/``write``/``admin``, ``auth.scope_allows`` is one ordering), so this listing
    is the whole catalog and nothing can hide above it. Introducing a fourth
    scope would make that caveat live and this test would need revisiting.
    """
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    assert KNOWN_TOOLS == {tool.name for tool in listing.tools}
