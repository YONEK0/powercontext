# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Concurrent embedding request coalescing with a bounded vector cache.

Several callers in one event loop often need vectors for the same texts at the
same time: concurrent Memory writes repeat the same entries, and a search may
follow a write that already embedded its query. This wrapper joins the texts
those callers are waiting for into shared batches, deduplicates across callers,
and remembers successful vectors so later calls do not pay for them again.

The wrapper owns no module-level state: every instance has its own cache and
in-flight batch table, so independently configured runtimes stay isolated.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Sequence

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]


class _Batch:
    """One underlying embedding request shared by every waiter that needs it."""

    __slots__ = ("future", "texts", "usage")

    def __init__(self, texts: tuple[str, ...]) -> None:
        self.texts = texts
        self.future: asyncio.Future[tuple[EmbeddingVector, ...]] = asyncio.get_running_loop().create_future()
        self.usage: InferenceUsage | None = None


class _Request:
    """The texts one caller still contributes to the coalescing window."""

    __slots__ = ("waiting_for",)

    def __init__(self) -> None:
        self.waiting_for: set[str] = set()


def _consume_exception(future: asyncio.Future[tuple[EmbeddingVector, ...]]) -> None:
    """Mark a failed batch's exception as observed when every waiter cancelled."""

    if not future.cancelled():
        future.exception()


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


class CoalescingEmbeddingModel:
    """Share in-flight embedding work and cache successful vectors per text.

    ``max_batch_size`` caps the number of distinct texts in one underlying
    request, ``coalesce_ms`` is how long a call waits for concurrent callers to
    join its round (``0`` still yields once so already-ready callers can join),
    and ``cache_size`` bounds the per-instance LRU cache (``0`` disables it).
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._waiting: dict[str, set[_Request]] = {}
        self._inflight: dict[str, _Batch] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, sharing and caching underlying work."""

        ordered = tuple(texts)
        unique = tuple(dict.fromkeys(ordered))
        if not unique:
            return EmbeddingResult(vectors=())

        resolved: dict[str, EmbeddingVector] = {}
        missing: list[str] = []
        for text in unique:
            cached = self._cached(text)
            if cached is None:
                missing.append(text)
            else:
                resolved[text] = cached

        batches: list[_Batch] = []
        if missing:
            batches = await self._await_missing(missing, resolved)

        return EmbeddingResult(
            vectors=tuple(resolved[text] for text in ordered),
            usage=self._batch_usage(batches),
        )

    async def _await_missing(
        self,
        missing: list[str],
        resolved: dict[str, EmbeddingVector],
    ) -> list[_Batch]:
        """Register texts for the coalescing round, then await their batches."""

        request = _Request()
        self._register(request, missing)
        try:
            if self._coalesce_seconds:
                await asyncio.sleep(self._coalesce_seconds)
            else:
                # Still yield once so callers that became ready in the same loop
                # iteration register before the first of them dispatches a round.
                await asyncio.sleep(0)
        except BaseException:
            self._withdraw(request)
            raise

        self._dispatch_waiting()
        batches: list[_Batch] = []
        unscheduled: list[str] = []
        for text in missing:
            batch = self._inflight.get(text)
            if batch is not None:
                batches.append(batch)
                continue
            cached = self._cached(text)
            if cached is not None:
                resolved[text] = cached
            else:
                unscheduled.append(text)

        if unscheduled:
            # A batch dispatched by another caller can finish while this call is
            # in its window; its vectors then come from the cache above. Anything
            # still unresolved has no owner yet, so dispatch it now.
            self._register(request, unscheduled)
            self._dispatch_waiting()
            for text in unscheduled:
                batch = self._inflight.get(text)
                if batch is not None:
                    batches.append(batch)
                    continue
                cached = self._cached(text)
                if cached is None:
                    raise RuntimeError("embedding batch was not scheduled")
                resolved[text] = cached

        distinct = tuple(dict.fromkeys(batches))
        # Each waiter shields the shared batch future, so cancelling one caller
        # cannot cancel the provider work every other waiter is still awaiting.
        results = await asyncio.gather(*(asyncio.shield(batch.future) for batch in distinct))
        for batch, vectors in zip(distinct, results, strict=True):
            for text, vector in zip(batch.texts, vectors, strict=True):
                resolved[text] = vector
        return list(distinct)

    def _register(self, request: _Request, texts: Sequence[str]) -> None:
        """Join texts that already have a batch; queue the rest for the next round."""

        for text in texts:
            if text in self._inflight:
                continue
            holders = self._waiting.get(text)
            if holders is None:
                holders = set()
                self._waiting[text] = holders
            holders.add(request)
            request.waiting_for.add(text)

    def _withdraw(self, request: _Request) -> None:
        """Drop a cancelled caller's still-undispatched texts."""

        for text in request.waiting_for:
            holders = self._waiting.get(text)
            if holders is None:
                continue
            holders.discard(request)
            if not holders:
                del self._waiting[text]
        request.waiting_for.clear()

    def _dispatch_waiting(self) -> None:
        """Split every queued text into bounded batches and start their tasks."""

        if not self._waiting:
            return
        texts = tuple(self._waiting)
        for holders in self._waiting.values():
            for request in holders:
                request.waiting_for.clear()
        self._waiting.clear()

        for start in range(0, len(texts), self._max_batch_size):
            batch = _Batch(texts[start : start + self._max_batch_size])
            for text in batch.texts:
                self._inflight[text] = batch
            batch.future.add_done_callback(_consume_exception)
            task = asyncio.create_task(self._run(batch))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, batch: _Batch) -> None:
        """Execute one batch and publish its vectors, cache, or failure."""

        try:
            result = await self._model.embed(batch.texts)
            if len(result.vectors) != len(batch.texts):
                raise ValueError(
                    f"embedding vector count {len(result.vectors)} does not match "
                    f"batch text count {len(batch.texts)}"
                )
            if self._cache_size:
                for text, vector in zip(batch.texts, result.vectors, strict=True):
                    self._remember(text, vector)
            self._discard_inflight(batch)
            batch.usage = result.usage
            if not batch.future.done():
                batch.future.set_result(tuple(result.vectors))
        except asyncio.CancelledError:
            self._discard_inflight(batch)
            if not batch.future.done():
                batch.future.cancel()
            raise
        except BaseException as error:
            self._discard_inflight(batch)
            if not batch.future.done():
                batch.future.set_exception(error)

    def _discard_inflight(self, batch: _Batch) -> None:
        for text in batch.texts:
            if self._inflight.get(text) is batch:
                del self._inflight[text]

    def _cached(self, text: str) -> EmbeddingVector | None:
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _remember(self, text: str, vector: EmbeddingVector) -> None:
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _batch_usage(self, batches: Sequence[_Batch]) -> InferenceUsage:
        """Charge each distinct batch a call waited for exactly once."""

        usages = [batch.usage for batch in batches if batch.usage is not None]
        return InferenceUsage(
            requests=sum(usage.requests for usage in usages),
            input_tokens=_sum_optional(usage.input_tokens for usage in usages),
            output_tokens=_sum_optional(usage.output_tokens for usage in usages),
        )
