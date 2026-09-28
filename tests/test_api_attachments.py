"""HTTP-layer acceptance tests for attachment routes (docs/DATA_MODEL.md section 8).

These prove upload and download are reachable over HTTP, with no logic duplicated
in the route handlers (DD-3). Content-hash dedup and max_bytes/max_files
enforcement are already proven at the service layer in ``tests/test_attachments.py``;
this module only proves the routes wire correctly onto ``AttachmentService``.
"""

from __future__ import annotations

from email.message import Message
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from tests.conftest import make_actor


def test_upload_returns_metadata_with_a_real_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["filename"] == "notes.txt"
    assert body["content_type"] == "text/plain"
    assert body["byte_size"] == len(b"hello world")
    assert body["uploaded_by"] == BOOTSTRAP_PRINCIPAL_ID
    assert body["uploaded_at"]
    assert body["sha256"]


def test_get_attachment_returns_same_metadata_as_upload(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()

    response = client.get(f"/api/v1/attachments/{uploaded['id']}")

    assert response.status_code == 200
    assert response.json() == uploaded


def test_download_returns_original_bytes_and_headers(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()

    response = client.get(f"/api/v1/attachments/{uploaded['id']}/download")

    assert response.status_code == 200
    assert response.content == b"hello world"
    # Starlette appends a charset to text/* media types by default; the stored
    # content_type itself is still exactly what was uploaded (see the metadata test).
    assert response.headers["content-type"].startswith("text/plain")
    assert 'filename="notes.txt"' in response.headers["content-disposition"]


def test_uploading_identical_content_twice_produces_two_ids(client: TestClient) -> None:
    first = client.post(
        "/api/v1/attachments",
        files={"file": ("a.txt", b"same bytes", "text/plain")},
    ).json()
    second = client.post(
        "/api/v1/attachments",
        files={"file": ("b.txt", b"same bytes", "text/plain")},
    ).json()

    assert first["id"] != second["id"]
    assert first["sha256"] == second["sha256"]

    first_download = client.get(f"/api/v1/attachments/{first['id']}/download")
    second_download = client.get(f"/api/v1/attachments/{second['id']}/download")
    assert first_download.content == second_download.content == b"same bytes"


def test_download_of_unknown_id_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/attachments/does-not-exist/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_download_of_a_row_whose_blob_is_missing_on_disk_returns_not_found(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """Regression for the backup-restore negative case: an ``attachments`` row can
    exist with no bytes behind it on disk. Over a real ASGI server this used to send a
    200 status line and then die mid-stream (``repositories/blobs.py``'s
    ``open_stream`` deferred its existence check until the first chunk was pulled,
    which is after ``StreamingResponse`` has already committed the status). Left at
    the default ``raise_server_exceptions=True``, this test would fail loudly with
    that ``RuntimeError`` if the eager check regresses."""
    uploaded = client.post(
        "/api/v1/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()
    blob_path = app_services.attachments._blobs._path_for(uploaded["sha256"])  # type: ignore[attr-defined]
    blob_path.unlink()

    response = client.get(f"/api/v1/attachments/{uploaded['id']}/download")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["details"]["entity"] == "attachment blob"


def test_attachment_field_on_a_record_accepts_id_from_a_real_http_upload(
    client: TestClient, app_services: ServiceBundle
) -> None:
    uploaded = client.post(
        "/api/v1/attachments",
        files={"file": ("report.pdf", b"pdf bytes", "application/pdf")},
    ).json()

    app_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the attachment HTTP test suite.",
        key_prefix="ART",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable title.",
                "required": True,
            },
            {
                "key": "files",
                "name": "Files",
                "type": "attachment",
                "description": "Supporting documents.",
            },
        ],
    )

    record = app_services.records.create_record(
        make_actor(), "artifact", {"title": "Has a real upload", "files": [uploaded["id"]]}
    )

    assert record.data["files"] == [uploaded["id"]]


# --------------------------------------------------------- bounded, and inert

# The cap and the download headers, both reproduced as defects against a tree without
# them: a 25 MiB + 1 upload returned 200 and stored 26,214,401 bytes, and a
# hand-rolled multipart body landed a raw `"` in Content-Disposition and stored
# `text/html; charset=utf-8` verbatim with no `X-Content-Type-Options`.


def _multipart(filename: str, content_type: str, body: bytes) -> tuple[bytes, str]:
    """A multipart body assembled by hand, not by the test client's encoder.

    The encoder escapes the filename, which is exactly the escaping the route used to
    rely on and must no longer: to prove the header is built safely, the raw bytes have
    to carry the quote, the semicolon and the CRLF themselves.
    """
    boundary = "----gw-016-probe"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + body + tail, f"multipart/form-data; boundary={boundary}"


def test_an_upload_at_the_cap_is_accepted(client: TestClient, app: FastAPI) -> None:
    cap = app.state.settings.max_attachment_bytes
    response = client.post(
        "/api/v1/attachments",
        files={"file": ("big.bin", b"x" * cap, "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["byte_size"] == cap


def test_one_byte_past_the_cap_is_refused_naming_the_variable(
    client: TestClient, app: FastAPI
) -> None:
    cap = app.state.settings.max_attachment_bytes
    response = client.post(
        "/api/v1/attachments",
        files={"file": ("big.bin", b"x" * (cap + 1), "application/octet-stream")},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "GW_MAX_ATTACHMENT_BYTES" in error["message"]
    assert error["details"]["max_bytes"] == cap


def test_a_refused_upload_writes_no_audit_row(
    client: TestClient, app_services: ServiceBundle, app: FastAPI
) -> None:
    """The refusal happens before the write transaction, so there is nothing to undo."""
    cap = app.state.settings.max_attachment_bytes
    before = app_services.audit.search(make_actor(), limit=500).events
    client.post(
        "/api/v1/attachments",
        files={"file": ("big.bin", b"x" * (cap + 1), "application/octet-stream")},
    )
    after = app_services.audit.search(make_actor(), limit=500).events
    assert len(after) == len(before)
    assert not [e for e in after if e.entity_type == "attachment"]


def test_a_body_between_the_edge_cap_and_the_attachment_cap_still_uploads(
    client: TestClient, app: FastAPI
) -> None:
    """The upload route is exempt from the edge body cap. Without the
    exemption every attachment over 4 MiB would 413, which is the whole reason the
    exemption exists."""
    settings = app.state.settings
    size = settings.max_request_bytes + 1024
    assert size < settings.max_attachment_bytes
    response = client.post(
        "/api/v1/attachments",
        files={"file": ("mid.bin", b"y" * size, "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["byte_size"] == size


def test_the_stored_content_type_has_its_parameters_stripped(client: TestClient) -> None:
    body, content_type = _multipart("page.html", "text/html; charset=utf-8", b"<b>hi</b>")
    uploaded = client.post(
        "/api/v1/attachments", content=body, headers={"Content-Type": content_type}
    )
    assert uploaded.status_code == 200
    # The type itself is preserved -- lying about it would break every legitimate
    # download -- and only the parameters are gone.
    assert uploaded.json()["content_type"] == "text/html"


def test_an_unparseable_content_type_becomes_octet_stream(client: TestClient) -> None:
    body, content_type = _multipart("odd.bin", "not-a-media-type", b"hello")
    uploaded = client.post(
        "/api/v1/attachments", content=body, headers={"Content-Type": content_type}
    )
    assert uploaded.json()["content_type"] == "application/octet-stream"


def test_every_download_carries_nosniff(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/attachments", files={"file": ("notes.txt", b"hello", "text/plain")}
    ).json()
    response = client.get(f"/api/v1/attachments/{uploaded['id']}/download")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_hostile_filename_produces_a_well_formed_content_disposition(
    client: TestClient,
) -> None:
    """A quote, a semicolon, non-ASCII and a CRLF, sent through a hand-rolled body and
    parsed back with ``email.message`` rather than by substring."""
    hostile = 'a";b; naïve\r\nX-Injected: yes.txt'
    body, content_type = _multipart(hostile, "application/octet-stream", b"payload")
    uploaded = client.post(
        "/api/v1/attachments", content=body, headers={"Content-Type": content_type}
    )
    assert uploaded.status_code == 200

    response = client.get(f"/api/v1/attachments/{uploaded.json()['id']}/download")
    assert response.status_code == 200
    assert "x-injected" not in response.headers

    parsed = Message()
    parsed["Content-Disposition"] = response.headers["content-disposition"]
    assert parsed.get_content_disposition() == "attachment"
    ascii_name = parsed.get_param("filename", header="Content-Disposition")
    assert isinstance(ascii_name, str)
    assert '"' not in ascii_name and "\r" not in ascii_name and "\n" not in ascii_name
    assert ascii_name.isascii()
    # The real name is still recoverable, percent-encoded, through RFC 5987.
    encoded = response.headers["content-disposition"].split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == uploaded.json()["filename"]
