"""DD-29: an agent can reach a file.

One attachment document on every surface, two tools, and one resource template. The
rule the whole change turns on is that **bytes never travel in a tool argument or a
tool result**: handles ride everywhere, bytes ride HTTP or ``resources/read``, and text
an agent authored is the one payload allowed inside a call.

Every MCP assertion goes through a real ``mcp.Client`` session; nothing
here imports a tool module or the server subclass.
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST

from glosswork.actor import ActorContext, Level, Scope
from glosswork.auth import PatTokenResolver
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.mcp_server import create_mcp_server
from glosswork.migrations import run_migrations
from glosswork.repositories.models import ObjectType
from glosswork.services import ServiceBundle, build_services
from glosswork.services.attachments import (
    ATTACHMENT_URI_TEMPLATE,
    TEXT_ATTACHMENT_TYPES,
    is_text_content_type,
)
from tests.conftest import make_actor, mint_scope_tokens
from tests.mcp_support import memory_session, raw_jsonrpc, structured, tool_names

_MCP_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "glosswork" / "mcp_server"

BASE_URL = "https://tracker.example.com"
MARKDOWN = "# Weekly note\n\nTwo lines, one of them blank.\n"
# Not a real PDF, and it does not need to be: what matters is that the stored content
# type is outside the text predicate, so the read path must answer with a blob.
PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\nbinary \x00\x01\x02 bytes\n"


# ------------------------------------------------------------------- fixtures


def attach_type(services: ServiceBundle, key: str, prefix: str) -> ObjectType:
    created: ObjectType = services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key} carrying files, for the attachment surface tests.",
        key_prefix=prefix,
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this is called, shown wherever it is listed.",
            },
            {
                "key": "files",
                "name": "Files",
                "type": "attachment",
                "description": "Documents supporting this record, uploaded by anyone.",
            },
        ],
    )
    return created


def actor_for(principal_id: str, scope: Scope) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope=scope,
    )


@pytest.fixture
def based(tmp_path: Path) -> ServiceBundle:
    """A bundle whose ``GW_BASE_URL`` is set, so ``download_url`` is absolute."""
    database = Database.connect(tmp_path / "based.sqlite3")
    run_migrations(database)
    bundle = build_services(
        database,
        tmp_path,
        Settings(data_dir=tmp_path, embedding_enabled=False, base_url=BASE_URL),
    )
    attach_type(bundle, "brief", "BRF")
    yield bundle
    database.close()


@pytest.fixture
def based_mcp(based: ServiceBundle) -> tuple[MCPServer, dict[str, str]]:
    return create_mcp_server(lambda: based, PatTokenResolver(lambda: based)), mint_scope_tokens(
        based
    )


@pytest.fixture
def seeded(services: ServiceBundle) -> ServiceBundle:
    """The default bundle (no ``GW_BASE_URL``) with the same object type."""
    attach_type(services, "brief", "BRF")
    return services


# ------------------------------------------------- one document, one URL


def test_download_url_is_absolute_with_a_base_url_and_a_path_without_one(
    based: ServiceBundle, seeded: ServiceBundle
) -> None:
    row = based.attachments.upload(make_actor(), "n.md", "text/markdown", MARKDOWN.encode())
    assert (
        based.attachments.download_url(row.id) == f"{BASE_URL}/api/v1/attachments/{row.id}/download"
    )
    other = seeded.attachments.upload(make_actor(), "n.md", "text/markdown", MARKDOWN.encode())
    assert seeded.attachments.download_url(other.id) == (f"/api/v1/attachments/{other.id}/download")


@pytest.mark.anyio
async def test_rest_metadata_the_tool_and_the_include_block_are_one_document(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]], client: TestClient
) -> None:
    """Three call sites, one serializer. Three hand-written copies once let the include
    block project four of the seven keys, so ``get_record include=attachments`` and
    ``GET /api/v1/attachments/{id}`` disagreed about the same row."""
    server, tokens = based_mcp
    row = based.attachments.upload(make_actor(), "note.md", "text/markdown", MARKDOWN.encode())
    record = based.records.create_record(
        make_actor(), "brief", {"title": "Has a file", "files": [row.id]}
    )

    async with memory_session(server, token=tokens["read"]) as session:
        tool_doc = structured(await session.call_tool("get_attachment", {"attachment_id": row.id}))
        included = structured(
            await session.call_tool(
                "get_record", {"record": record.key, "include": ["attachments"]}
            )
        )["attachments"]["files"]

    assert len(included) == 1
    assert tool_doc == included[0]
    assert set(tool_doc) == {
        "id",
        "sha256",
        "filename",
        "content_type",
        "byte_size",
        "uploaded_at",
        "uploaded_by",
        "download_url",
    }
    assert tool_doc["download_url"] == f"{BASE_URL}/api/v1/attachments/{row.id}/download"

    # And REST, over its own app and its own database, carries the same key set.
    uploaded = client.post(
        "/api/v1/attachments", files={"file": ("note.md", MARKDOWN.encode(), "text/markdown")}
    ).json()
    assert set(uploaded) == set(tool_doc)
    fetched = client.get(f"/api/v1/attachments/{uploaded['id']}").json()
    assert fetched == uploaded
    # No GW_BASE_URL on the ``app`` fixture, so the same key is a path.
    assert uploaded["download_url"] == f"/api/v1/attachments/{uploaded['id']}/download"


# ----------------------------------------------------- resource links


def links_of(result: Any) -> list[Any]:
    return [block for block in result.content if getattr(block, "type", None) == "resource_link"]


@pytest.mark.anyio
async def test_get_attachment_returns_exactly_one_resource_link_matching_the_row(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    server, tokens = based_mcp
    row = based.attachments.upload(make_actor(), "note.md", "text/markdown", MARKDOWN.encode())
    async with memory_session(server, token=tokens["read"]) as session:
        result = await session.call_tool("get_attachment", {"attachment_id": row.id})
    (link,) = links_of(result)
    assert str(link.uri) == f"attachment://{row.id}"
    assert link.name == "note.md"
    assert link.mime_type == "text/markdown"
    assert link.size == len(MARKDOWN.encode())


@pytest.mark.anyio
async def test_get_record_links_every_resolved_attachment_and_nothing_else(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    """A link is a claim the server can serve those bytes, so a dangling id and one
    the attachment read rule withholds each get no link -- ``get_many`` omits both, and the two
    are indistinguishable from the client by design (DD-27)."""
    server, tokens = based_mcp
    first = based.attachments.upload(make_actor(), "a.md", "text/markdown", b"a")
    second = based.attachments.upload(make_actor(), "b.md", "text/markdown", b"b")
    dangling = str(uuid.uuid4())
    record = based.records.create_record(
        make_actor(), "brief", {"title": "Three ids", "files": [first.id, dangling, second.id]}
    )
    async with memory_session(server, token=tokens["read"]) as session:
        result = await session.call_tool(
            "get_record", {"record": record.key, "include": ["attachments"]}
        )
    assert {str(link.uri) for link in links_of(result)} == {
        f"attachment://{first.id}",
        f"attachment://{second.id}",
    }
    # Without the include there is nothing to link.
    async with memory_session(server, token=tokens["read"]) as session:
        plain = await session.call_tool("get_record", {"record": record.key})
    assert links_of(plain) == []


# ------------------------------------------ the template, and the gate


@pytest.mark.anyio
async def test_the_attachment_template_is_listed_for_a_read_pat(
    based_mcp: tuple[MCPServer, dict[str, str]],
) -> None:
    server, tokens = based_mcp
    async with memory_session(server, token=tokens["read"]) as session:
        listing = await session.list_resource_templates()
    templates = {t.uri_template: t for t in listing.resource_templates}
    assert ATTACHMENT_URI_TEMPLATE in templates
    assert templates[ATTACHMENT_URI_TEMPLATE].description


@pytest.mark.parametrize("method", ["resources/list", "resources/templates/list", "resources/read"])
def test_the_three_resource_methods_are_refused_without_a_credential(
    anon_client: TestClient, method: str
) -> None:
    """Measured against the unfixed tree first, which is what makes all three
    assertions rather than descriptions: with no credential ``resources/list`` and
    ``resources/templates/list`` answered **200 with an empty list**, and
    ``resources/read`` answered ``-32602 Unknown resource``.

    The assertion is on the **code**, not merely on the refusal, because with the file
    surface in place a forgotten gate still refuses ``resources/read`` -- just with the
    wrong one.

    **This test runs on the raw wire, not on a client session.** Gating
    ``server/discover`` means an uncredentialed session no longer opens, so a
    ``mcp.Client`` can no longer be used to ask what one resource method answers without
    a credential: all three parameters would collapse into the same assertion about a
    session that never got far enough to send any of them, and the per-method claim this
    test exists to make would quietly be gone. The raw wire still carries one method per
    request, so the parametrization keeps meaning what it says.
    """
    params = {"uri": f"attachment://{uuid.uuid4()}"} if method == "resources/read" else {}
    answer = raw_jsonrpc(anon_client, method, params)
    assert "result" not in answer, answer
    assert answer["error"]["code"] == INVALID_REQUEST, answer
    # Nothing about the deployment leaks with the refusal.
    assert "attachment" not in str(answer["error"]["message"]).lower()


# ------------------------------------------------ reading the bytes


@pytest.mark.anyio
async def test_reading_a_text_attachment_returns_text_with_its_own_mime_type(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    server, tokens = based_mcp
    row = based.attachments.upload(make_actor(), "n.md", "text/markdown", MARKDOWN.encode())
    async with memory_session(server, token=tokens["read"]) as session:
        result = await session.read_resource(f"attachment://{row.id}")
    (content,) = result.contents
    assert content.text == MARKDOWN
    assert content.mime_type == "text/markdown"


@pytest.mark.anyio
async def test_reading_a_binary_attachment_returns_a_blob_with_its_own_mime_type(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    """The assertion that the **override**, not the decorator, is serving this: a
    decorator-registered template carries one ``mime_type`` fixed at registration
    (``resources/templates.py::create_resource``), so it could not answer
    ``text/markdown`` for the test above and ``application/pdf`` for this one."""
    server, tokens = based_mcp
    row = based.attachments.upload(make_actor(), "report.pdf", "application/pdf", PDF_BYTES)
    async with memory_session(server, token=tokens["read"]) as session:
        result = await session.read_resource(f"attachment://{row.id}")
    (content,) = result.contents
    assert content.mime_type == "application/pdf"
    decoded = base64.b64decode(content.blob)
    assert hashlib.sha256(decoded).hexdigest() == row.sha256


@pytest.mark.anyio
async def test_reading_an_unknown_attachment_is_invalid_params_with_a_not_found_envelope(
    based_mcp: tuple[MCPServer, dict[str, str]],
) -> None:
    server, tokens = based_mcp
    missing = str(uuid.uuid4())
    async with memory_session(server, token=tokens["read"]) as session:
        with pytest.raises(MCPError) as excinfo:
            await session.read_resource(f"attachment://{missing}")
    error = excinfo.value.error
    assert error.code == INVALID_PARAMS
    assert error.data["error"]["code"] == "not_found"
    assert missing in error.message


@pytest.mark.anyio
async def test_reading_an_attachment_you_may_not_read_carries_the_services_own_message(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    """FR-M6 applies to a resource read as much as to a tool call. Caught in the
    override precisely so the message survives: left uncaught, the adapter middleware
    classifies it as unclassified and answers with a request id and nothing else."""
    server, based_tokens = based_mcp
    attach_type(based, "secret", "SEC")
    withheld = based.attachments.upload(make_actor(), "payroll.txt", "text/plain", b"salaries")
    based.records.create_record(
        make_actor(), "secret", {"title": "Compensation", "files": [withheld.id]}
    )
    outsider = based.principals.create_user(
        make_actor(),
        email="outsider@example.com",
        display_name="Outsider",
        role="member",
        password="correct-horse-battery-staple",
    ).id
    based.access.grant(make_actor(), "brief", outsider, "read")
    token = based.tokens.mint(actor_for(outsider, "admin"), name="outsider", scope="read").plaintext

    # The message the service itself produces, captured before the read so the
    # comparison is verbatim rather than by substring.
    from glosswork.errors import ForbiddenError

    with pytest.raises(ForbiddenError) as direct:
        based.attachments.get_attachment(actor_for(outsider, "read"), withheld.id)

    async with memory_session(server, token=token) as session:
        with pytest.raises(MCPError) as excinfo:
            await session.read_resource(f"attachment://{withheld.id}")
    error = excinfo.value.error
    assert error.code == INVALID_REQUEST
    assert error.data["error"]["code"] == "forbidden"
    assert error.message == direct.value.message

    # And the read PAT still reaches an attachment it is allowed to read.
    allowed = based.attachments.upload(make_actor(), "ok.md", "text/markdown", MARKDOWN.encode())
    based.records.create_record(make_actor(), "brief", {"title": "Readable", "files": [allowed.id]})
    async with memory_session(server, token=token) as session:
        result = await session.read_resource(f"attachment://{allowed.id}")
    assert result.contents[0].text == MARKDOWN
    assert based_tokens["read"]  # the fixture's own PAT is untouched by this test


# ------------------------------------- create_text_attachment round trip


@pytest.mark.anyio
async def test_create_text_attachment_round_trips_through_a_record_and_resources_read(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    server, tokens = based_mcp
    record = based.records.create_record(make_actor(), "brief", {"title": "Empty"})
    async with memory_session(server, token=tokens["write"]) as session:
        created = structured(
            await session.call_tool(
                "create_text_attachment", {"filename": "summary.md", "text": MARKDOWN}
            )
        )
        assert created["content_type"] == "text/markdown"
        assert created["download_url"].startswith(BASE_URL)
        updated = structured(
            await session.call_tool(
                "update_record",
                {"record": record.key, "values": {"files": [created["id"]]}},
            )
        )
        assert updated["data"]["files"] == [created["id"]]
        result = await session.read_resource(f"attachment://{created['id']}")
    assert result.contents[0].text == MARKDOWN
    assert result.contents[0].mime_type == "text/markdown"


@pytest.mark.anyio
async def test_a_disallowed_content_type_names_the_parameter_and_lists_the_set(
    based_mcp: tuple[MCPServer, dict[str, str]],
) -> None:
    server, tokens = based_mcp
    async with memory_session(server, token=tokens["write"]) as session:
        result = await session.call_tool(
            "create_text_attachment",
            {
                "filename": "report.pdf",
                "text": "not really a pdf",
                "content_type": "application/pdf",
            },
        )
    assert result.is_error
    envelope = structured(result)["error"]
    assert envelope["code"] == "validation_failed"
    assert envelope["details"]["parameter"] == "content_type"
    assert envelope["details"]["allowed"] == list(TEXT_ATTACHMENT_TYPES)
    # The refusal names where a file that is not text actually goes.
    assert "multipart/form-data" in envelope["message"]


@pytest.mark.anyio
async def test_a_read_pat_neither_lists_nor_calls_create_text_attachment(
    based_mcp: tuple[MCPServer, dict[str, str]],
) -> None:
    server, tokens = based_mcp
    async with memory_session(server, token=tokens["read"]) as session:
        names = tool_names(await session.list_tools())
        assert "create_text_attachment" not in names
        assert "get_attachment" in names
        result = await session.call_tool(
            "create_text_attachment", {"filename": "n.md", "text": "x"}
        )
    assert result.is_error
    assert structured(result)["error"]["code"] == "insufficient_scope"


def test_text_over_the_attachment_cap_names_the_setting(tmp_path: Path) -> None:
    """The cap is inherited from ``upload`` rather than restated, so the refusal is the
    one ``upload`` writes and names ``GW_MAX_ATTACHMENT_BYTES``. No new cap."""
    database = Database.connect(tmp_path / "capped.sqlite3")
    run_migrations(database)
    bundle = build_services(
        database,
        tmp_path,
        Settings(data_dir=tmp_path, embedding_enabled=False, max_attachment_bytes=32),
    )
    with pytest.raises(ValidationFailedError) as excinfo:
        bundle.attachments.upload_text(make_actor(), "big.md", "text/markdown", "x" * 33)
    assert excinfo.value.details["setting"] == "GW_MAX_ATTACHMENT_BYTES"
    database.close()


# --------------------------------------- the published attachments block


@pytest.mark.anyio
async def test_the_published_attachments_block_is_the_services_own_answers(
    based: ServiceBundle, based_mcp: tuple[MCPServer, dict[str, str]]
) -> None:
    server, tokens = based_mcp
    async with memory_session(server, token=tokens["admin"]) as session:
        document = structured(await session.call_tool("describe_capabilities", {}))
    block = document["attachments"]
    # DD-16: the block has nine keys, not six: ``upload_url`` and ``ticket_tool`` are
    # the two things the file recipe names and could not otherwise hand over, and
    # ``credential`` says plainly that the connection's bearer is held by the MCP client
    # rather than by this server, so an agent stops hunting for it in tool output.
    assert set(block) == {
        "upload_route",
        "upload_url",
        "upload_field",
        "ticket_tool",
        "credential",
        "download_url_pattern",
        "resource_uri_template",
        "text_content_types",
        "note",
    }
    assert block["upload_url"] == based.attachments.upload_url
    # Not literals: the service's own ``download_url`` over a literal placeholder, so
    # the published pattern cannot describe a route the service does not serve.
    assert block["download_url_pattern"] == based.attachments.download_url("{attachment_id}")
    assert block["download_url_pattern"].startswith(BASE_URL)
    assert block["resource_uri_template"] == ATTACHMENT_URI_TEMPLATE
    assert block["text_content_types"] == list(TEXT_ATTACHMENT_TYPES)
    assert block["upload_route"] == "POST /api/v1/attachments"
    assert block["upload_field"] == "file"
    assert document["limits"]["attachment_max_bytes"] == based.attachments.max_attachment_bytes
    assert document["limits"]["attachment_max_bytes"] == Settings().max_attachment_bytes


# There is deliberately no test that the whole file recipe is carried in the server
# ``instructions``; one existed and was retired.
#
# It is not kept beside its replacement, because keeping it would assert two
# contradictory outcomes at once (a lesson paid for twice). DD-30 makes
# ``instructions`` an *index* under a tested character budget: the recipe deliberately
# is not there in full any more, because on 2026-09-06 a host delivered exactly 2,048
# characters of a 3,319-character string and the Files paragraph -- which began at
# offset 2,078 -- was never shipped at all. What replaced it is
# ``tests/test_endpoint_teaches_itself.py``, which asserts the handshake *names*
# ``describe_capabilities`` and ``create_attachment_upload``, and asserts the recipe
# itself where it now lives: in a tool result, which is never truncated and is readable
# at ``read`` scope.


# ------------------------------------------------------- the layering


def test_the_mcp_package_encodes_no_bytes() -> None:
    """The SDK base64-encodes a blob for ``resources/read`` itself, in
    ``_handle_read_resource``; that is the only place bytes leave this server over MCP.
    An ``import base64`` under ``mcp_server/`` would mean a second one."""
    offenders = [
        f"{path.name}:{lineno}"
        for path in sorted(_MCP_PACKAGE.glob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if re.match(r"^\s*(from|import)\s+base64\b", line)
    ]
    assert offenders == [], offenders


def test_the_text_predicate_and_the_allowlist_cannot_disagree() -> None:
    """Asserted rather than commented: every type
    ``create_text_attachment`` accepts must read back as text through
    ``resources/read``, or a document an agent wrote would come back as a blob."""
    assert all(is_text_content_type(t) for t in TEXT_ATTACHMENT_TYPES)
    assert not is_text_content_type("application/pdf")
    assert not is_text_content_type("application/octet-stream")
    # The structured suffixes, which is why the predicate is not a set membership test.
    assert is_text_content_type("application/ld+json")
    assert is_text_content_type("application/atom+xml")


# --------------------------------------------------------------------- fences


def test_fence_the_exempt_map_names_upload_text() -> None:
    """**Fence**, not counted: ``read_content`` needs no entry because
    ``GATE_EXPRESSIONS`` already contains ``self.get_attachment(``, which is where the
    attachment read rule is applied. ``upload_text`` and ``download_url`` do, and
    ``test_every_exemption_names_a_method_that_exists`` is what stops them rotting."""
    from tests.test_access_completeness import EXEMPT

    assert ("AttachmentService", "upload_text") in EXEMPT
    assert ("AttachmentService", "download_url") in EXEMPT
    assert ("AttachmentService", "read_content") not in EXEMPT


@pytest.mark.anyio
async def test_fence_the_handshake_still_advertises_the_resources_capability(
    based_mcp: tuple[MCPServer, dict[str, str]],
) -> None:
    """**Fence**, not counted: ``Server.get_capabilities`` derives this block from
    ``"resources/list" in self._request_handlers``, which ``MCPServer`` registers
    unconditionally, so it was already advertised on the unfixed tree with zero
    resources registered. It cannot fail there and is not a measured assertion."""
    server, tokens = based_mcp
    async with memory_session(server, token=tokens["read"]) as session:
        capabilities = session.server_capabilities
    assert capabilities is not None
    assert capabilities.resources is not None


def test_fence_the_grant_level_is_unused_here(seeded: ServiceBundle) -> None:
    """**Fence**, not counted. ``upload_text`` acquires no object-type level check,
    matching its REST twin exactly -- an upload names no record, so there is no type to
    check, and the gate is at reference time."""
    level: Level = "none"
    assert level == "none"
    stranger = seeded.principals.create_user(
        make_actor(),
        email="nobody@example.com",
        display_name="Nobody",
        role="member",
        password="correct-horse-battery-staple",
    ).id
    row = seeded.attachments.upload_text(
        actor_for(stranger, "write"), "mine.md", "text/markdown", MARKDOWN
    )
    assert row.uploaded_by == stranger
