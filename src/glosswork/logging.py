"""Structured JSON logging to stdout (PRD FR-P5).

Two producers reach stdout and both must be JSON:

- **This application**, through ``structlog``'s own logger factory, which emits JSON.
- **Third-party libraries** — the MCP SDK, ``httpx2``, ``uvicorn``'s error logger —
  through the standard library's ``logging``. Left alone, these reach the root handler with
  a bare ``%(message)s`` formatter and interleave plain text into a stream FR-P5 promises is
  structured. A single non-JSON line is enough to break a log shipper's parser for the whole
  stream, and the lines that appear are exactly the ones an operator needs during an
  incident.

The fix is ``structlog.stdlib.ProcessorFormatter`` on the root handler: stdlib records
are passed through a processor chain ending in the same ``JSONRenderer`` the
application's own events use, so both producers emit one object per line with the same
keys. The application's path deliberately keeps ``PrintLoggerFactory`` rather than
being routed through ``logging`` as well — that would change the format of every line
this product already emits in order to fix the lines it does not, and only the second
needs fixing.
"""

from __future__ import annotations

import logging
import sys

import structlog

# Loggers whose output is known to reach stdout and is not ours. Named explicitly, in
# the spirit of ``scopes.SCOPE_EXEMPT_PATHS``: the root handler below catches
# everything regardless, and this list exists so the test that proves the fix has
# something concrete to exercise rather than asserting over an open set.
THIRD_PARTY_LOGGERS: tuple[str, ...] = (
    "mcp",
    "httpx",
    "httpx2",
    "httpcore",
    "uvicorn",
    "uvicorn.error",
    "asyncio",
)


def configure_logging(log_level: str) -> None:
    level = logging.getLevelName(log_level.upper())
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        # No ``file`` argument, and it must stay that way. Without one, structlog prints
        # each line to ``sys.stdout`` as it is when the line is written. Given
        # ``file=sys.stdout``, a logger caches the stream of the moment this function
        # ran; inside a test that captures output that is the test's buffer, which is
        # closed when the test ends, and every later line from that logger raises
        # ``ValueError: I/O operation on closed file`` in some other test. In a
        # deployment the two are the same code path, because ``sys.stdout`` never
        # changes there. tests/test_logging_stream.py holds this.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # One handler on the root logger, rendering foreign records through the same
    # JSONRenderer. ``foreign_pre_chain`` runs only for records that did not come from
    # structlog, which is every third-party line: it adds the level, the timestamp and
    # the emitting logger's name, so an operator can tell an httpx line from an SDK
    # line without pattern-matching the message.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                timestamper,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    # Replaced rather than appended: ``configure_logging`` runs once per application,
    # and a test suite that builds many apps would otherwise stack a handler per app
    # and emit each line as many times.
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> structlog.BoundLogger:
    logger: structlog.BoundLogger = structlog.get_logger(name)
    return logger
