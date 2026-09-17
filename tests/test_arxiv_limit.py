import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from researcher.config import Settings
from researcher.services.ai_service import AIService
from researcher.services.arxiv_limit import ArxivLimiter, ArxivRequestSlot


@pytest.mark.asyncio
async def test_instances_share_spacing_and_count_failed_requests(tmp_path):
    now = [100.0]
    waits = []

    async def advance(seconds):
        waits.append(seconds)
        now[0] += seconds

    for index in range(3):
        limiter = ArxivLimiter(tmp_path, clock=lambda: now[0], sleep=advance)
        if index == 1:
            with pytest.raises(RuntimeError):
                async with limiter.request_slot():
                    raise RuntimeError("failed request")
        else:
            async with limiter.request_slot():
                now[0] += 0.5
    assert waits == [3.0, 3.0]


@pytest.mark.asyncio
async def test_waiting_task_can_be_cancelled_without_releasing_owner(tmp_path):
    waiting = asyncio.Event()

    async def pause(seconds):
        waiting.set()
        await asyncio.Event().wait()

    async with ArxivLimiter(tmp_path).request_slot():

        async def contend():
            async with ArxivLimiter(tmp_path, sleep=pause).request_slot():
                pytest.fail("concurrent request entered")

        task = asyncio.create_task(contend())
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (tmp_path / "last-request").exists()


@pytest.mark.asyncio
async def test_cancelled_owner_releases_lock_and_preserves_spacing(tmp_path):
    entered = asyncio.Event()

    async def owner():
        async with ArxivLimiter(tmp_path, clock=lambda: 100.0).request_slot():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sleep = AsyncMock()
    async with ArxivLimiter(tmp_path, clock=lambda: 100.0, sleep=sleep).request_slot():
        pass
    sleep.assert_awaited_once_with(3.0)


@pytest.mark.asyncio
async def test_deadline_cancels_spacing_wait_before_request(tmp_path):
    (tmp_path / "last-request").write_text("100")
    limiter = ArxivLimiter(tmp_path, clock=lambda: 100.0)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            async with limiter.request_slot():
                pytest.fail("request started before deadline")
    assert (tmp_path / "last-request").read_text() == "100"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "invalid", "nan", "inf", "1e999", "\u00e9"])
async def test_unreadable_timestamp_waits_full_interval_and_is_repaired(tmp_path, value):
    # A crash between truncating and rewriting the file leaves it empty.
    (tmp_path / "last-request").write_text(value, encoding="utf-8")
    sleep = AsyncMock()
    async with ArxivLimiter(tmp_path, clock=lambda: 100.0, sleep=sleep).request_slot():
        pass
    sleep.assert_awaited_once_with(3.0)
    assert float((tmp_path / "last-request").read_text()) == 100.0


@pytest.mark.asyncio
async def test_timestamp_after_clock_moved_back_waits_only_one_interval(tmp_path):
    (tmp_path / "last-request").write_text("100000")
    sleep = AsyncMock()
    async with ArxivLimiter(tmp_path, clock=lambda: 100.0, sleep=sleep).request_slot():
        pass
    sleep.assert_awaited_once_with(3.0)
    assert float((tmp_path / "last-request").read_text()) == 100.0


def test_lock_shared_between_processes(tmp_path):
    script = """
import asyncio, sys
from pathlib import Path
from researcher.services.arxiv_limit import ArxivLimiter, ArxivRequestSlot
async def main():
    async with ArxivLimiter(Path(sys.argv[1])).request_slot():
        print('entered', flush=True)
        sys.stdin.readline()
asyncio.run(main())
"""
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "entered"

        async def contend():
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.1):
                    async with ArxivLimiter(tmp_path).request_slot():
                        pytest.fail("entered while another process holds the lock")

        asyncio.run(contend())
        _, stderr = process.communicate("\n", timeout=5)
        assert process.returncode == 0, stderr
        sleep = AsyncMock()

        async def after_release():
            async with ArxivLimiter(tmp_path, sleep=sleep).request_slot():
                pass

        asyncio.run(after_release())
        assert sleep.await_count == 1
        assert 0 < sleep.await_args.args[0] <= 3
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.asyncio
async def test_transport_gates_arxiv_redirects_and_retries_only():
    slots = []
    requests = []

    class Gate:
        @asynccontextmanager
        async def request_slot(self):
            slots.append("entered")
            yield ArxivRequestSlot()

    def handler(request):
        requests.append(request)
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(200)
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": "https://export.arxiv.org/api/query"})
        if len(slots) == 2:
            return httpx.Response(503)
        return httpx.Response(200)

    with (
        patch("researcher.services.ai_service.ArxivLimiter", Gate),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        service = AIService(Settings(), transport_factory=lambda: httpx.MockTransport(handler))
        async with service.open_source_client() as client:
            assert (await client.get("https://en.wikipedia.org/w/api.php")).status_code == 200
            assert (await client.get("http://export.arxiv.org/api/query")).status_code == 200
    assert len(requests) == 4
    assert len(slots) == 3


@pytest.mark.asyncio
async def test_server_cooldown_survives_failure_and_new_instance(tmp_path):
    with pytest.raises(RuntimeError):
        async with ArxivLimiter(tmp_path, clock=lambda: 100.0).request_slot() as slot:
            slot.retry_after_seconds = 60
            raise RuntimeError("rate limited")
    assert float((tmp_path / "retry-after-until").read_text()) == 160
    sleep = AsyncMock()
    async with ArxivLimiter(tmp_path, clock=lambda: 110.0, sleep=sleep).request_slot():
        pass
    sleep.assert_awaited_once_with(50.0)


@pytest.mark.asyncio
async def test_cooldown_deadline_does_not_erase_shared_state(tmp_path):
    (tmp_path / "retry-after-until").write_text("160")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            async with ArxivLimiter(tmp_path, clock=lambda: 100.0).request_slot():
                pytest.fail("sent before cooldown expired")
    assert (tmp_path / "retry-after-until").read_text() == "160"
    assert not (tmp_path / "last-request").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "nan"])
async def test_unreadable_cooldown_waits_full_interval_and_is_repaired(tmp_path, value):
    (tmp_path / "retry-after-until").write_text(value)
    sleep = AsyncMock()
    async with ArxivLimiter(tmp_path, clock=lambda: 100.0, sleep=sleep).request_slot():
        pass
    sleep.assert_awaited_once_with(3.0)
    assert not (tmp_path / "retry-after-until").exists()
