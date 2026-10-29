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

"""Batched HTTP scoring-service adapter for Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Select Memory hits using scores from a bounded-batch HTTP service.

    The complete candidate pool is split into requests, but every global rank is sent exactly
    once and all returned scores feed a single global ordering. Hit identity is never rewritten:
    the decision only reports one-based ranks into the original candidate tuple.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate over HTTP and return ranks in global score order."""

        _validate_bounds(len(candidates), limit)
        all_scores: dict[int, float] = {}
        request_count = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            batch_ranks = tuple(range(start + 1, start + len(batch) + 1))
            response = await self._client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text}
                        for rank, candidate in zip(batch_ranks, batch, strict=True)
                    ],
                },
            )
            response.raise_for_status()
            all_scores.update(_parse_scores(response, expected_ranks=batch_ranks))
            request_count += 1
        if len(all_scores) != len(candidates):
            raise ValueError("reranker scores do not cover every candidate")  # noqa: TRY003
        selected = sorted(all_scores, key=lambda rank: (-all_scores[rank], rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(selected),
            usage=InferenceUsage(requests=request_count),
        )


def _parse_scores(response: httpx.Response, *, expected_ranks: tuple[int, ...]) -> dict[int, float]:
    """Validate one batch response and return its rank-to-score mapping."""

    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("reranker response is not valid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")  # noqa: TRY003
    values = payload["scores"]
    if not isinstance(values, list):
        raise ValueError("reranker scores must be a list")  # noqa: TRY003, TRY004
    expected = set(expected_ranks)
    scores: dict[int, float] = {}
    for value in values:
        rank, score = _parse_entry(value, expected=expected)
        if rank in scores:
            raise ValueError("reranker score rank appears more than once")  # noqa: TRY003
        scores[rank] = score
    if len(scores) != len(expected):
        raise ValueError("reranker scores must cover this batch exactly once")  # noqa: TRY003
    return scores


def _parse_entry(value: object, *, expected: set[int]) -> tuple[int, float]:
    """Validate one score object and return its global rank and finite score."""

    if not isinstance(value, dict) or set(value) != {"rank", "score"}:
        raise ValueError("each reranker score must contain exactly rank and score")  # noqa: TRY003
    rank = value["rank"]
    if isinstance(rank, bool) or not isinstance(rank, int) or rank not in expected:
        raise ValueError("reranker score rank is invalid for this batch")  # noqa: TRY003
    score = value["score"]
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError("reranker score must be a number")  # noqa: TRY003, TRY004
    try:
        finite = math.isfinite(score)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite:
        raise ValueError("reranker score must be finite")  # noqa: TRY003
    return rank, float(score)


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["HTTPMemoryReranker"]
