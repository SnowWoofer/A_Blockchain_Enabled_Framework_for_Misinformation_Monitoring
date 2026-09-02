import asyncio
import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List

from .metrics import BATCH_SIZE, FLAGGED_TOTAL, INFERENCE_LATENCY, QUEUE_DEPTH, QUEUE_WAIT, REQUESTS_TOTAL

logger = logging.getLogger(__name__)


class DynamicBatcher:
    """Collects individual inference requests into batches, flushing whichever
    bound is hit first: MAX_BATCH_SIZE requests, or MAX_QUEUE_DELAY_MS elapsed
    since the first request in the batch arrived."""

    def __init__(
        self,
        infer_fn: Callable[[List[str]], List[dict]],
        max_batch_size: int,
        max_queue_delay_ms: int,
        executor: ThreadPoolExecutor,
    ):
        self.infer_fn = infer_fn
        self.max_batch_size = max_batch_size
        self.max_delay_s = max_queue_delay_ms / 1000
        self.executor = executor
        self._queue: "asyncio.Queue[tuple]" = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def submit(self, text: str) -> dict:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put((text, future, loop.time()))
        QUEUE_DEPTH.set(self._queue.qsize())
        return await future

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            batch = [await self._queue.get()]
            deadline = loop.time() + self.max_delay_s
            while len(batch) < self.max_batch_size:
                timeout = deadline - loop.time()
                if timeout <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout=timeout))
                except asyncio.TimeoutError:
                    break
            QUEUE_DEPTH.set(self._queue.qsize())
            await self._process(batch)

    async def _process(self, batch):
        loop = asyncio.get_running_loop()
        now = loop.time()
        for _, _, enqueued_at in batch:
            QUEUE_WAIT.observe(now - enqueued_at)
        BATCH_SIZE.observe(len(batch))

        texts = [item[0] for item in batch]
        futures = [item[1] for item in batch]
        start = time.perf_counter()
        try:
            results = await loop.run_in_executor(self.executor, self.infer_fn, texts)
            INFERENCE_LATENCY.observe(time.perf_counter() - start)
            REQUESTS_TOTAL.inc(len(results))
            FLAGGED_TOTAL.inc(sum(1 for r in results if r["flagged"]))
            for future, result in zip(futures, results):
                if not future.done():
                    future.set_result(result)
        except Exception as exc:  # noqa: BLE001 - propagate to every waiter
            logger.exception("Batch inference failed for %d requests", len(batch))
            for future in futures:
                if not future.done():
                    future.set_exception(exc)
