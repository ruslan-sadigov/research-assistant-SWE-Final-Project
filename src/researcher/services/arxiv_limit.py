"""Local arXiv request pacing shared across processes using one directory."""

import asyncio
import errno
import math
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO


def _try_lock(file: BinaryIO) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise
    return True


class ArxivLimiter:
    """Serialize requests and leave three seconds after the previous completion.

    The OS lock is released when the file closes, including on cancellation.
    Use local disk: independent hosts/directories do not share this limiter.
    Clock/sleep injection supports deterministic offline tests.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._directory = directory
        self._clock = clock
        self._sleep = sleep

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        directory = self._directory or Path.home() / ".cache" / "research-assistant" / "arxiv"
        directory.mkdir(parents=True, exist_ok=True)
        # Never replace or unlink this file: all processes must lock the same inode.
        with (directory / "request.lock").open("a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            while not _try_lock(lock):
                await self._sleep(0.05)
            timestamp = directory / "last-request"
            try:
                previous = float(timestamp.read_text(encoding="ascii"))
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if not math.isfinite(previous):
                    raise ValueError("Invalid arXiv limiter timestamp")
                remaining = previous + 3.0 - self._clock()
                if remaining > 0:
                    await self._sleep(remaining)
            started = self._clock()
            timestamp.write_text(str(started), encoding="ascii")
            try:
                yield
            finally:
                # A cancelled/failed HTTP attempt also consumes a request slot.
                timestamp.write_text(str(max(started, self._clock())), encoding="ascii")
