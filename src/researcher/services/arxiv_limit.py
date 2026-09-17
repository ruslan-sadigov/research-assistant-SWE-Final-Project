"""Local arXiv request pacing shared across processes using one directory."""

import asyncio
import errno
import logging
import math
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


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


def _read_time(path: Path) -> float | None:
    """Return a persisted time, None when absent, or NaN when unreadable.

    A process killed between truncating and rewriting a file leaves it empty.
    """
    try:
        value = float(path.read_text(encoding="ascii"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeDecodeError):
        return math.nan
    return value if math.isfinite(value) else math.nan


@dataclass
class ArxivRequestSlot:
    """Cooldown to persist before releasing the active request lock."""

    retry_after_seconds: float = 0.0


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
    async def request_slot(self) -> AsyncIterator[ArxivRequestSlot]:
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
            now = self._clock()
            timestamp = directory / "last-request"
            previous = _read_time(timestamp)
            cooldown = directory / "retry-after-until"
            retry_at = _read_time(cooldown)
            if retry_at is not None and math.isnan(retry_at):
                # The lost cooldown cannot be recovered; pace conservatively instead.
                logger.warning("arXiv limiter cooldown unreadable; waiting a full interval")
                cooldown.unlink(missing_ok=True)
                retry_at = None
                previous = now
            if previous is not None and (math.isnan(previous) or previous > now):
                # Unreadable state or a clock moved back: assume a request just finished.
                logger.warning("arXiv limiter timestamp unusable; waiting a full interval")
                previous = now
            allowed_at = max(retry_at or 0.0, previous + 3.0 if previous is not None else 0.0)
            remaining = allowed_at - now
            if remaining > 0:
                await self._sleep(remaining)
            started = self._clock()
            timestamp.write_text(str(started), encoding="ascii")
            slot = ArxivRequestSlot()
            try:
                yield slot
            finally:
                if slot.retry_after_seconds > 0:
                    cooldown.write_text(
                        str(self._clock() + slot.retry_after_seconds), encoding="ascii"
                    )
                # A cancelled/failed HTTP attempt also consumes a request slot.
                timestamp.write_text(str(max(started, self._clock())), encoding="ascii")
