"""The relay definition in ``docs/DEPLOYMENT.md`` section 5a is the one the code keeps.

Two checks, both structural (they read files in this repository):

1. Section 5a names exactly the templates and fields the fake relay's models define
   (``tests/fake_relay.py``), which is the one enforcer every behavioural test sends
   through. A field added to the driver and the fake but not the document, or the reverse,
   fails here.
2. The golden requests under ``docs/relay/`` are byte-identical to what
   ``build_relay_request`` produces for their fixed message. The hosting control plane's
   tests send copies of those files, so they must be what this driver sends.

To rewrite the golden files after a deliberate change to the definition::

    uv run python -m tests.test_relay_definition --write
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glosswork.config import Settings
from glosswork.services.relay import RelayMessage, build_relay_request, render_http
from tests.fake_relay import TEMPLATE_FIELDS

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = REPO_ROOT / "docs" / "DEPLOYMENT.md"
GOLDEN_DIR = REPO_ROOT / "docs" / "relay"

#: The golden messages. Fixed ids, clock, code and a token that is plainly a placeholder.
GOLDEN_URL = "https://api.glosswork.dev/v1/relay/send"
GOLDEN_TOKEN = "RELAY_TOKEN_PLACEHOLDER_NOT_A_CREDENTIAL"
GOLDEN_MESSAGES: dict[str, RelayMessage] = {
    "sign_in_code": RelayMessage.sign_in_code(
        "ada@example.com",
        "004217",
        datetime(2026, 9, 29, 15, 25, 0, tzinfo=UTC),
        message_id="3f1c2a9e-7b4d-4e8a-9c1f-5d6e7a8b9c0d",
    ),
    "invite": RelayMessage.invite(
        "lin@example.com",
        "Grace Hopper",
        message_id="8a2b4c6d-1e3f-4a5b-8c7d-9e0f1a2b3c4d",
    ),
}


def _golden_bytes(template: str) -> bytes:
    settings = Settings(relay_url=GOLDEN_URL, relay_token=GOLDEN_TOKEN, embedding_enabled=False)
    return render_http(build_relay_request(GOLDEN_MESSAGES[template], settings))


def _section_5a() -> str:
    text = DEPLOYMENT.read_text()
    start = text.index("## 5a.")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_section_5a_names_exactly_the_templates_and_fields_the_fake_relay_enforces() -> None:
    section = _section_5a()
    table = section[section.index("| Template | Field | Type and rule |") :]
    table = table[: table.index("\n\n")]
    documented = set(re.findall(r"^\| `(\w+)` \| `(\w+)` \|", table, re.MULTILINE))
    enforced = {
        (template, field)
        for template, model in TEMPLATE_FIELDS.items()
        for field in model.model_fields
    }
    assert documented == enforced


def test_section_5a_names_exactly_the_top_level_keys_the_fake_relay_enforces() -> None:
    from tests.fake_relay import RelayMessage as FakeMessage

    section = _section_5a()
    body_table = section[section.index("| Key | Type | Meaning |") :]
    body_table = body_table[: body_table.index("\n\n")]
    documented = re.findall(r"^\| `(\w+)` \|", body_table, re.MULTILINE)
    assert documented == list(FakeMessage.model_fields)


@pytest.mark.parametrize("template", sorted(GOLDEN_MESSAGES))
def test_the_golden_request_is_what_the_driver_produces(template: str) -> None:
    path = GOLDEN_DIR / f"{template}.http"
    assert path.read_bytes() == _golden_bytes(template), (
        f"{path} is not what build_relay_request produces. If the definition changed on "
        "purpose, run: uv run python -m tests.test_relay_definition --write"
    )


def test_the_golden_requests_are_inside_the_definition() -> None:
    """The golden bodies pass the fake relay's models, apart from the expiry's distance
    from now, which a fixed clock cannot keep."""
    import json

    from tests.fake_relay import RelayMessage as FakeMessage

    for template in GOLDEN_MESSAGES:
        raw = (GOLDEN_DIR / f"{template}.http").read_bytes()
        body = json.loads(raw.split(b"\n\n", 1)[1])
        message = FakeMessage.model_validate(body)
        assert message.template == template
        assert set(body["fields"]) == set(TEMPLATE_FIELDS[template].model_fields)


def _write() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for template in GOLDEN_MESSAGES:
        (GOLDEN_DIR / f"{template}.http").write_bytes(_golden_bytes(template))
        print(f"wrote {GOLDEN_DIR / f'{template}.http'}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
