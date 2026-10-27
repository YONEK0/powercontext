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

"""Durable Source lifecycle dependency graph and event journal."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.sources import SourceRef

SourceLifecycleNodeKind = Literal["source", "memory", "topic"]
SourceLifecyclePolicy = Literal["all", "any"]


class _NodeRefPayload(BaseModel):
    """Opaque registered reference discriminated by its node kind."""

    model_config = ConfigDict(frozen=True)

    ref: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourceLifecycleNodeRow:
    """One registered dependency-graph node with its exact evidence reference."""

    node_id: str
    kind: SourceLifecycleNodeKind
    ref: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...]
    policy: SourceLifecyclePolicy
    revision: int | None
    artifact_id: str | None


@dataclass(frozen=True, slots=True)
class SourceLifecycleEventRow:
    """One applied lifecycle event for one registered Source."""

    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str


class SourceLifecycleRepository:
    """Read and append the per-scope lifecycle graph and event journal."""

    async def nodes(self, connection: AsyncConnection, scope_id: str, /) -> tuple[SourceLifecycleNodeRow, ...]:
        rows = (
            (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                    .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
                )
            )
            .mappings()
            .all()
        )
        return tuple(_decode_node(row) for row in rows)

    async def events(self, connection: AsyncConnection, scope_id: str, /) -> tuple[SourceLifecycleEventRow, ...]:
        rows = (
            (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_EVENTS_TABLE)
                    .where(SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id)
                    .order_by(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence,
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(_decode_event(row) for row in rows)

    async def insert_node(
        self,
        connection: AsyncConnection,
        scope_id: str,
        row: SourceLifecycleNodeRow,
        /,
    ) -> None:
        payload = dump_model(
            _NodeRefPayload(ref=_encode_ref(row.ref)),
            kind="source-lifecycle-node",
            name=row.node_id,
        )
        await connection.execute(
            insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                scope_id=scope_id,
                node_id=row.node_id,
                kind=row.kind,
                artifact_id=row.artifact_id,
                parents=json.dumps(list(row.parents), ensure_ascii=False, separators=(",", ":")),
                policy=row.policy,
                revision=row.revision,
                ref_payload=payload,
            )
        )

    async def insert_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        row: SourceLifecycleEventRow,
        /,
    ) -> None:
        await connection.execute(
            insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                scope_id=scope_id,
                node_id=row.node_id,
                sequence=row.sequence,
                revision=row.revision,
                available=row.available,
                reason=row.reason,
            )
        )


def _encode_ref(ref: SourceRef | MemoryCitation | ArtifactRef) -> dict[str, Any]:
    return ref.model_dump(mode="json")


def _decode_node(row: Mapping[Any, Any]) -> SourceLifecycleNodeRow:
    kind = str(row["kind"])
    payload = load_model(
        _NodeRefPayload,
        stored_bytes(row["ref_payload"], column="ref_payload"),
        kind="source-lifecycle-node",
        name=str(row["node_id"]),
    )
    parents = json.loads(str(row["parents"]))
    if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
        raise InvalidStoredPayloadError(
            "source-lifecycle-node",
            str(row["node_id"]),
            "parents column is not a list of node identifiers",
        )
    revision = row["revision"]
    artifact_id = row["artifact_id"]
    return SourceLifecycleNodeRow(
        node_id=str(row["node_id"]),
        kind=kind,  # type: ignore[arg-type]
        ref=_decode_ref(kind, payload.ref),
        parents=tuple(parents),
        policy=str(row["policy"]),  # type: ignore[arg-type]
        revision=None if revision is None else int(revision),
        artifact_id=None if artifact_id is None else str(artifact_id),
    )


def _decode_ref(kind: str, value: dict[str, Any]) -> SourceRef | MemoryCitation | ArtifactRef:
    if kind == "source":
        return SourceRef.model_validate(value)
    if kind == "memory":
        return MemoryCitation.model_validate(value)
    return ArtifactRef.model_validate(value)


def _decode_event(row: Mapping[Any, Any]) -> SourceLifecycleEventRow:
    return SourceLifecycleEventRow(
        node_id=str(row["node_id"]),
        sequence=int(row["sequence"]),
        revision=int(row["revision"]),
        available=bool(row["available"]),
        reason=str(row["reason"]),
    )


__all__ = [
    "SourceLifecycleEventRow",
    "SourceLifecycleNodeKind",
    "SourceLifecycleNodeRow",
    "SourceLifecyclePolicy",
    "SourceLifecycleRepository",
]
