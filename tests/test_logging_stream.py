"""A log line goes to ``sys.stdout`` as it is when the line is written (FR-P5).

``configure_logging`` once handed structlog the ``sys.stdout`` of the moment it ran, and
a logger caches its printer on first use. Inside a test that captures output, that
stream is the test's buffer, which is closed when the test ends, so every later line
from the same logger raised ``ValueError: I/O operation on closed file`` in whichever
test happened to log next. The suite passed only in the order its files happened to
run in, which is what stopped it being split into shards.

The fix is ``structlog.PrintLoggerFactory()`` with no ``file``: structlog then prints
to whatever ``sys.stdout`` is for each line. That leans on how the pinned structlog
treats a missing file, so the first test here is also what fails if an upgrade ever
changes it.
"""

from __future__ import annotations

import copy
import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from glosswork.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _logging_left_as_found() -> Iterator[None]:
    """These tests configure logging against buffers of their own; put back what was there."""
    config = structlog.get_config()
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    structlog.configure(**config)
    root.handlers = handlers
    root.setLevel(level)


def _events(stream: io.StringIO) -> list[str]:
    return [json.loads(line)["event"] for line in stream.getvalue().splitlines()]


def test_a_logger_outlives_the_stream_it_first_wrote_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = io.StringIO()
    monkeypatch.setattr("sys.stdout", first)
    configure_logging("info")
    logger = get_logger("tests.logging_stream.outlives")
    logger.info("while the first stream is open")
    assert _events(first) == ["while the first stream is open"]

    # What pytest does at the end of a test that captured output, and what the next
    # test's ``create_app`` does at its start.
    second = io.StringIO()
    monkeypatch.setattr("sys.stdout", second)
    first.close()
    configure_logging("info")

    logger.info("after the first stream is closed")
    assert _events(second) == ["after the first stream is closed"]


def test_a_line_lands_in_the_stream_that_is_current_when_it_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_configure = io.StringIO()
    monkeypatch.setattr("sys.stdout", at_configure)
    configure_logging("info")
    logger = get_logger("tests.logging_stream.current")
    logger.info("first")

    later = io.StringIO()
    monkeypatch.setattr("sys.stdout", later)
    logger.info("second")

    assert _events(at_configure) == ["first"]
    assert _events(later) == ["second"]


def test_a_bound_logger_can_still_be_deep_copied(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    configure_logging("info")
    logger = get_logger("tests.logging_stream.copied").bind(request_id="r1")
    logger.info("original")

    copy.deepcopy(logger).info("copy")

    assert _events(stream) == ["original", "copy"]
