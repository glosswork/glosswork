"""The endpoint teaches itself (DD-30, DD-16).

The invariant this module exists to hold is that **an agent connected to ``/mcp`` with
a ``write`` token and no checkout of this repository can learn how to store a file and
put it on a record, and can then act on what it learned.** Every fact it needs arrives
through the handshake or through a tool result.

So this module reads nothing out of the repository. That is not a stylistic preference:
an earlier design told the agent about files in three places, one of which was a Markdown
file checked into this tree, and an agent in a clean harness could not reach it. A test
that read that file to check the recipe was right would have passed while the endpoint
itself stayed silent. The last test in this module asserts it names no repository path.

Everything goes through a real ``mcp.Client`` session over the real streamable HTTP
transport, and the upload itself is a real HTTP request against the same ASGI app --
because the whole claim is that the credential a tool result hands back is one an
ordinary HTTP client can present.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any

import httpx2
import pytest

from glosswork.app import create_app
from glosswork.config import Settings
from tests.conftest import mint_scope_tokens
from tests.mcp_support import http_session, structured

BASE_URL = "https://tracker.example.com"

# Not a real PDF. What matters is that it is bytes no model would ever emit as a tool
# argument, so a successful round trip proves the HTTP path rather than the text one.
BINARY = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + bytes(range(256)) * 4 + b"\n%%EOF\n"


def settings_for(tmp_path: Path) -> Settings:
    """``GW_BASE_URL`` set, so every agent-facing URL the endpoint publishes is
    absolute. It is derived from that setting only, never from a request
    header -- a poisoned ``Host`` telling an agent where to POST a file is worse than a
    refusal."""
    return Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url=BASE_URL,
        # ``GW_BASE_URL`` is deliberately *not* the host these in-process requests
        # arrive on, which is the point: every agent-facing URL is derived from the
        # setting and never from the request's own ``Host``. The MCP transport's host
        # allowlist is a separate control and is what has to know the test host.
        mcp_allowed_hosts="testserver",
    )


async def write_token_for(settings: Settings) -> str:
    """A real ``write`` PAT, minted through a throwaway app over the same data
    directory whose lifespan is entered and exited before the app under test starts.

    The SDK's session manager can be run only once per app instance, so the app
    that serves the streamable HTTP session cannot also be stood up to mint.
    """
    bootstrap = create_app(settings)
    async with bootstrap.router.lifespan_context(bootstrap):
        return mint_scope_tokens(bootstrap.state.services)["write"]


# ------------------------------------------------------- the handshake is an index


@pytest.mark.anyio
async def test_the_handshake_names_the_manual_and_the_upload_tool(tmp_path: Path) -> None:
    """The handshake names the manual and the upload tool, rather than carrying the file
    recipe itself.

    That test asserted the whole file recipe was *in* ``instructions``. After DD-30 it
    deliberately is not: ``instructions`` is an index sized to survive the smallest
    delivery window a host offers, and its job is to name the tool that holds the
    manual. So what is asserted here is the naming, and the recipe itself is asserted
    where it now lives -- in a tool result, below.
    """
    settings = settings_for(tmp_path)
    token = await write_token_for(settings)
    async with http_session(create_app(settings), token=token) as session:
        instructions = session.instructions
    assert instructions is not None
    assert "describe_capabilities" in instructions
    assert "create_attachment_upload" in instructions
    assert "base64" in instructions.lower()


# ------------------------------------------------- the manual is a write-scope call


@pytest.mark.anyio
async def test_a_write_agent_can_read_the_whole_file_recipe_from_a_tool_result(
    tmp_path: Path,
) -> None:
    """Every fact needed to store a file, in one call, at the
    scope the agent that actually uploads holds."""
    settings = settings_for(tmp_path)
    token = await write_token_for(settings)
    async with http_session(create_app(settings), token=token) as session:
        result = await session.call_tool("describe_capabilities", {})
    assert not result.is_error, result
    document = structured(result)
    attachments = document["attachments"]
    assert attachments["upload_route"] == "POST /api/v1/attachments"
    assert attachments["upload_url"] == f"{BASE_URL}/api/v1/attachments"
    assert attachments["upload_field"] == "file"
    assert attachments["ticket_tool"] == "create_attachment_upload"
    assert attachments["resource_uri_template"] == "attachment://{attachment_id}"
    assert "text/markdown" in attachments["text_content_types"]
    assert attachments["download_url_pattern"].startswith(BASE_URL)
    # The credential sentence: the one fact an agent otherwise hunts for in tool output
    # forever, because the connection's bearer belongs to the MCP client and no part of
    # the protocol hands the model its own credential.
    assert "create_attachment_upload" in attachments["credential"]
    limits = document["limits"]
    assert limits["attachment_max_bytes"] == 26_214_400
    assert limits["upload_ticket_ttl_seconds"] == 300


# ------------------------------------------------------ and the recipe is actionable


@pytest.mark.anyio
async def test_a_minted_ticket_uploads_a_real_file_over_real_http(tmp_path: Path) -> None:
    """The whole point of DD-16.

    The agent mints, POSTs the bytes with exactly the ``authorization`` and
    ``upload_url`` it was handed, and reads the stored file's own metadata back through
    a tool. Nothing here was learned from a file on disk.
    """
    settings = settings_for(tmp_path)
    token = await write_token_for(settings)
    app = create_app(settings)
    async with http_session(app, token=token) as session:
        minted = await session.call_tool(
            "create_attachment_upload",
            {"filename": "quarterly.pdf", "content_type": "application/pdf"},
        )
        assert not minted.is_error, minted
        ticket: dict[str, Any] = structured(minted)

        assert ticket["upload_url"] == f"{BASE_URL}/api/v1/attachments"
        assert ticket["method"] == "POST"
        assert ticket["field"] == "file"
        assert ticket["authorization"].startswith("Bearer gw_upl_")
        assert ticket["max_bytes"] == 26_214_400
        assert ticket["filename"] == "quarterly.pdf"
        # Every interpolated value is shell-quoted, so a tool result the
        # model may paste into a shell cannot be constructed into a second command.
        assert shlex.split(ticket["curl"])[0] == "curl"

        # The upload itself: an ordinary HTTP request presenting the ticket and no other
        # credential, against the same running app the MCP session is talking to.
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                "/api/v1/attachments",
                files={"file": ("ignored-by-the-ticket.bin", BINARY, "application/octet-stream")},
                headers={"Authorization": ticket["authorization"]},
            )
        assert response.status_code == 200, response.text
        stored = response.json()
        # The ticket's filename and content type are authoritative, which is
        # what makes a ticket unrepurposable and removes a mismatch refusal's friction.
        assert stored["filename"] == "quarterly.pdf"
        assert stored["content_type"] == "application/pdf"

        fetched = await session.call_tool("get_attachment", {"attachment_id": stored["id"]})

    assert not fetched.is_error, fetched
    document = structured(fetched)
    assert document["byte_size"] == len(BINARY)
    assert document["sha256"] == hashlib.sha256(BINARY).hexdigest()
    assert document["download_url"] == f"{BASE_URL}/api/v1/attachments/{stored['id']}/download"


@pytest.mark.anyio
async def test_the_ticket_says_what_kind_of_credential_it_is(tmp_path: Path) -> None:
    """A model that believes it holds a reusable token will cache it and be refused
    later for a reason it cannot see, so the result and the tool description both have
    to say single use and short lived."""
    settings = settings_for(tmp_path)
    token = await write_token_for(settings)
    async with http_session(create_app(settings), token=token) as session:
        listing = await session.list_tools()
        minted = await session.call_tool("create_attachment_upload", {"filename": "note.txt"})
    ticket = structured(minted)
    assert ticket["single_use"] is True
    assert ticket["expires_at"].endswith("Z")
    description = next(
        tool.description or "" for tool in listing.tools if tool.name == "create_attachment_upload"
    ).lower()
    assert "single use" in description
    assert "tool call" in description
    assert "update_record" in description


# -------------------------------------------------------------------- self-contained


def test_this_module_reads_nothing_out_of_the_repository() -> None:
    """The constraint this module states in its docstring, asserted.

    A test that reached for a checked-in Markdown file to confirm what the endpoint
    teaches would be testing the documentation, not the endpoint, and would have passed
    while an agent in a clean harness went outside the session for a bearer token.
    """
    source = Path(__file__).read_text()
    # Each needle is assembled rather than written whole, so the assertion cannot match
    # itself.
    for needle in ("docs" + "/", "AGENTS" + ".md", "MCP_TOOLS" + ".md"):
        assert needle not in source, needle
    # ``tmp_path`` and the self-read above are the only filesystem this module touches:
    # the only ``Path`` it ever builds is of its own ``__file__``.
    assert source.count("Path(") == source.count("Path(__file__)") + 1  # + the annotation
