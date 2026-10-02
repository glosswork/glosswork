"""A streaming response that closes the generator it was handed (DD-36).

``BackupService.stream`` stages a full copy of the database on the data volume and
removes it in a ``finally``. A ``finally`` in a generator runs when the generator is
exhausted or **closed**, and Starlette's ``StreamingResponse`` closes nothing: when a
client hangs up mid-download it stops iterating and returns, leaving the generator
suspended inside its ``try``. The file then stays until the cycle collector happens to
reach the generator, which on a quiet server is not soon. Measured under uvicorn: six
abandoned downloads of a 51 MB database left six files and 305 MB behind.

So the response does the closing, when it ends, however it ends. Every route that streams
a backup returns this class, and ``tests/test_operator_backup.py`` pins that.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send


class ClosingStreamingResponse(StreamingResponse):
    """``StreamingResponse`` over a synchronous generator, closed when the response ends.

    Takes a generator, not any iterable: closing is the point, and only a generator has
    a ``finally`` to run.
    """

    def __init__(
        self,
        content: Iterator[bytes],
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers, media_type=media_type)
        self._generator = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self._generator, "close", None)
            if close is not None:
                # In the threadpool, where the generator's own steps ran: its ``finally``
                # does file work. Shielded, because this is cleanup and the usual reason
                # to be here is that the request was cancelled; an unshielded await would
                # be cancelled in turn and the file left exactly as before. By this point
                # no thread is still inside the generator, since the parent awaits each
                # step to completion before it returns.
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(close)
