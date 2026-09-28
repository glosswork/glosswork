"""Every operator the API accepts has an English word, for every field type.

**Why this test is in Python.** The frontend cannot enumerate the operators. The full list is
published only on MCP, by ``describe_capabilities``; REST publishes operators per field, inside
``describe_object_type``. So a frontend test would have to transcribe the twenty-four names, and
a transcribed list that iterates itself asserts that every key it has, it has.
Here the list is computed from ``fieldtypes`` itself, and the words are read off disk -- the same
cross-surface shape ``tests/test_approval_message.py`` uses, and the reason both carry
``pytestmark = pytest.mark.structural``.

**What it is for.** Without these words the filter builder renders ``{op}`` raw, so a person read
``gte`` on screen. Adding an operator to ``_TYPE_OPS`` without adding a word here turns this red
instead of shipping that.

It iterates the real ``(field type, operator)`` cross-product rather than the union, because
the word is a function of both: ``lt`` reads "is before" on a ``date`` and "is less than"
on an ``integer``.

**The closure half is scoped to a named literal, not to the module.** ``fieldTypeWord``'s
table lives in the same file and its keys are field-type names, so "the module declares no key
that is not an operator" would fail against the very module that holds the one vocabulary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from glosswork.fieldtypes import FIELD_TYPES, operators_for

REPO_ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = REPO_ROOT / "web" / "src" / "ui" / "vocabulary.ts"

# This file reads a source file from disk rather than exercising the running app, so it belongs
# in the lane CI runs on every pipeline. ``tests/test_structural_lane.py`` pins the marked set by
# equality, so this file's name is in its ``MARKED`` set: the two land together.
pytestmark = pytest.mark.structural

#: ``  eq: "is",`` -- one entry of a flat object literal. The keys are operator names, which are
#: valid JS identifiers, so no quoting case needs handling.
ENTRY = re.compile(r'^\s*([a-z_][a-z0-9_]*):\s*"([^"]*)",\s*$', re.MULTILINE)


def _object_literal(source: str, name: str) -> dict[str, str]:
    """The ``key: "word"`` entries of one named object literal in a TypeScript module.

    Parsed rather than executed: node is not a test dependency here, and the point is to read the
    table a human maintains, in the file they maintain it in.
    """
    opener = re.search(rf"^const {re.escape(name)}\b[^=]*= \{{$", source, re.MULTILINE)
    assert opener is not None, (
        f"{VOCABULARY.name} no longer declares `const {name} = {{`. This test reads that literal "
        "by name; if it was renamed, rename it here too rather than deleting the assertion."
    )
    body = source[opener.end() : source.index("\n};", opener.end())]
    return {match.group(1): match.group(2) for match in ENTRY.finditer(body)}


@pytest.fixture(scope="module")
def source() -> str:
    return VOCABULARY.read_text()


@pytest.fixture(scope="module")
def operator_words(source: str) -> dict[str, str]:
    return _object_literal(source, "OPERATOR_WORDS")


def test_every_field_type_and_operator_pair_has_a_word(operator_words: dict[str, str]) -> None:
    """The cross-product, not the union.

    ``operatorWord(op, fieldType)`` resolves an override first and falls back to
    ``OPERATOR_WORDS``, so a pair has a word exactly when the operator is in that table. The
    failure names the pair, because "some operator is missing a word" is not a message anyone can
    act on.
    """
    pairs = [
        (field_type, op) for field_type in sorted(FIELD_TYPES) for op in operators_for(field_type)
    ]
    assert pairs, "the cross-product is empty, so this test asserted nothing"

    missing = sorted({(field_type, op) for field_type, op in pairs if op not in operator_words})
    assert missing == [], (
        "these (field type, operator) pairs render with no English word, so a person would read "
        f"the API name on screen: {missing}. Add the operator to OPERATOR_WORDS in "
        f"{VOCABULARY.relative_to(REPO_ROOT)}."
    )


def test_the_operator_table_declares_nothing_that_is_not_an_operator(
    operator_words: dict[str, str],
) -> None:
    """The closure half, scoped to the named literal.

    A stale key is a word for an operator the API no longer accepts, which is how a vocabulary
    drifts from the grammar it glosses.
    """
    accepted = {op for field_type in FIELD_TYPES for op in operators_for(field_type)}

    assert set(operator_words) == accepted


#: The one operator whose API name is already the English word docs/DESIGN.md 5 asks for. Pinned
#: rather than inferred, so "gloss it with its own key" stays a deliberate act for exactly one
#: operator instead of a loophole available to all twenty-four.
SELF_GLOSSING = {"contains"}


def test_every_word_is_a_word_and_not_the_api_name(operator_words: dict[str, str]) -> None:
    """docs/DESIGN.md 5: "no jargon the person did not type". A table mapping ``gte`` to ``gte``
    would satisfy every other assertion in this file."""
    unglossed = {op for op, word in operator_words.items() if word == op}
    assert unglossed <= SELF_GLOSSING, (
        f"these are glossed with their own API name: {sorted(unglossed - SELF_GLOSSING)}"
    )

    machine_shaped = sorted(op for op, word in operator_words.items() if "_" in word)
    assert machine_shaped == [], f"these read as snake_case rather than English: {machine_shaped}"

    blank = sorted(op for op, word in operator_words.items() if not word.strip())
    assert blank == [], f"these have an empty word: {blank}"


def test_dates_read_their_comparisons_as_time(source: str, operator_words: dict[str, str]) -> None:
    """One operator key, two words.

    ``docs/DESIGN.md`` 5 asks for "is before" and "is after"; those are ``lt`` and ``gt`` **on a
    date**, and the same two operators belong to ``integer`` and ``decimal``, where "Amount is
    after 1000" is not English. The override has to exist, has to cover every comparison a date
    accepts, and has to differ from the quantity reading -- an override table that repeated the
    base words would pass the first two checks and change nothing on screen.
    """
    chronological = _object_literal(source, "CHRONOLOGICAL_WORDS")

    date_ops = set(operators_for("date")) & {"gt", "gte", "lt", "lte"}
    assert date_ops == {"gt", "gte", "lt", "lte"}, "a date's comparison operators moved"
    assert set(chronological) == date_ops

    for op in sorted(date_ops):
        assert chronological[op] != operator_words[op], (
            f"`{op}` has the same word on a date as on a number, so the override is inert"
        )

    for field_type in ("date", "datetime"):
        assert f"  {field_type}: CHRONOLOGICAL_WORDS," in source, (
            f"`{field_type}` no longer reads its comparisons chronologically"
        )


def test_the_field_type_words_cover_every_field_type(source: str) -> None:
    """The half moved in from ``inbox/proposalSentence.ts``.

    It was complete when it arrived and nothing may narrow it; a thirteenth field
    type added to ``FIELD_TYPES`` without a word is the same defect as an operator without one.
    """
    field_type_words = _object_literal(source, "FIELD_TYPE_WORDS")

    assert set(field_type_words) == set(FIELD_TYPES)


def test_proposal_sentence_imports_the_words_rather_than_declaring_them() -> None:
    """One display vocabulary layer, singular.

    The words used to live in ``inbox/``. If a copy reappears there, the premise this module
    guards -- that there is exactly one display vocabulary -- has quietly stopped being true.
    """
    proposal_sentence = (REPO_ROOT / "web" / "src" / "inbox" / "proposalSentence.ts").read_text()

    assert 'from "../ui/vocabulary"' in proposal_sentence
    assert "FIELD_TYPE_WORDS" not in proposal_sentence
