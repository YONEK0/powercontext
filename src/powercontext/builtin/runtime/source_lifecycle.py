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

"""Source withdrawal and restoration over a per-scope evidence dependency graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryNotFoundError, MemoryEntryVersion
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.source_lifecycle import (
    SourceLifecycleEventRow,
    SourceLifecycleNodeKind,
    SourceLifecycleNodeRow,
    SourceLifecyclePolicy,
    SourceLifecycleRepository,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.limits import MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Reject a lifecycle registration, event, or resolution that breaks the contract."""


_SOURCE = "source"
_MEMORY = "memory"
_TOPIC = "topic"
_PURPOSES = ("generation", "audit")


class SourceLifecycleEvent(BaseModel):
    """One controlled availability transition for one registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: str = Field(min_length=1, max_length=MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH)
    revision: int = Field(ge=1)
    sequence: int = Field(ge=1)
    available: bool
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("node_id", "reason")
    @classmethod
    def require_meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@dataclass(frozen=True, slots=True)
class _ScopeLifecycleState:
    """One scope's registered graph together with its applied event journal."""

    nodes: Mapping[str, SourceLifecycleNodeRow]
    events: tuple[SourceLifecycleEventRow, ...]

    def latest_event(self, node_id: str) -> SourceLifecycleEventRow | None:
        latest: SourceLifecycleEventRow | None = None
        for event in self.events:
            if event.node_id == node_id and (latest is None or event.sequence > latest.sequence):
                latest = event
        return latest

    def availability(self) -> dict[str, bool]:
        """Derive every node's availability from the journal and the dependency graph."""

        available: dict[str, bool] = {}
        for node_id, node in self.nodes.items():
            if node.kind == _SOURCE:
                latest = self.latest_event(node_id)
                available[node_id] = True if latest is None else latest.available
        # Iterate to a fixed point so the derived order can never leave a node
        # stale: a node becomes available only once its parents are settled.
        for _ in range(len(self.nodes)):
            changed = False
            for node_id, node in self.nodes.items():
                if node.kind == _SOURCE:
                    continue
                support = [available.get(parent, False) for parent in node.parents]
                derived = all(support) if node.policy == "all" else any(support)
                if available.get(node_id) != derived:
                    available[node_id] = derived
                    changed = True
            if not changed:
                break
        return available

    def unsupported_topic_artifacts(self) -> frozenset[str]:
        """Return Topic artifact ids whose every registered node lost its support."""

        availability = self.availability()
        supported: set[str] = set()
        tracked: set[str] = set()
        for node_id, node in self.nodes.items():
            if node.kind != _TOPIC or node.artifact_id is None:
                continue
            tracked.add(node.artifact_id)
            if availability.get(node_id, False):
                supported.add(node.artifact_id)
        return frozenset(tracked - supported)


class SourceLifecycleStore:
    """Persist one Runtime's lifecycle graphs and expose derived per-scope state."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
    ) -> None:
        self._database = database
        self._sources = sources
        self._artifacts = artifacts
        self._repository = SourceLifecycleRepository()
        self._scopes: dict[str, _ScopeLifecycleState] = {}

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncConnection]:
        """Own one transaction shared by a verification and its durable write."""

        async with self._database.transaction() as connection:
            yield connection

    async def load(self, scope_id: str, /) -> _ScopeLifecycleState:
        """Return one scope's state, reusing the copy this Runtime already loaded."""

        cached = self._scopes.get(scope_id)
        if cached is not None:
            return cached
        async with self._database.transaction() as connection:
            nodes = await self._repository.nodes(connection, scope_id)
            events = await self._repository.events(connection, scope_id)
        state = _ScopeLifecycleState(nodes={node.node_id: node for node in nodes}, events=events)
        # Replace rather than mutate so a concurrent reader sees one whole snapshot.
        self._scopes[scope_id] = state
        return state

    async def insert_node(
        self,
        connection: AsyncConnection,
        scope_id: str,
        row: SourceLifecycleNodeRow,
        /,
    ) -> None:
        await self._repository.insert_node(connection, scope_id, row)
        self._scopes.pop(scope_id, None)

    async def record_event(self, scope_id: str, row: SourceLifecycleEventRow, /) -> None:
        async with self._database.transaction() as connection:
            await self._repository.insert_event(connection, scope_id, row)
        self._scopes.pop(scope_id, None)

    async def verify_source(self, connection: AsyncConnection, scope_id: str, source_ref: SourceRef, /) -> None:
        try:
            await self._sources.get(connection, scope_id, source_ref)
        except RepositoryNotFoundError as error:
            raise SourceLifecycleError(f"unreadable lifecycle source reference {source_ref!r}") from error

    async def verify_artifact(self, connection: AsyncConnection, scope_id: str, artifact_ref: ArtifactRef, /) -> None:
        try:
            await self._artifacts.get(connection, scope_id, artifact_ref)
        except RepositoryNotFoundError as error:
            raise SourceLifecycleError(f"unreadable lifecycle artifact reference {artifact_ref!r}") from error

    async def unsupported_topic_artifacts(self, scope_id: str, /) -> frozenset[str]:
        state = await self.load(scope_id)
        return state.unsupported_topic_artifacts()


class SourceLifecycleApplication:
    """Select the Source lifecycle service for one scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register evidence dependencies and propagate Source availability in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        """Register one Source root together with its lifecycle revision."""

        self._require_node_id(node_id)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise SourceLifecycleError("a registered Source requires a positive lifecycle revision")
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            state = await self._store().load(self.scope_id)
            existing = state.nodes.get(node_id)
            if existing is not None:
                self._require_same_binding(existing, kind=_SOURCE, ref=source_ref, parents=(), policy="all")
                self._require_source_revision(existing, revision)
                return
            store = self._store()
            async with store.begin() as connection:
                await store.verify_source(connection, self.scope_id, source_ref)
                await store.insert_node(
                    connection,
                    self.scope_id,
                    SourceLifecycleNodeRow(
                        node_id=node_id,
                        kind=_SOURCE,
                        ref=source_ref,
                        parents=(),
                        policy="all",
                        revision=revision,
                        artifact_id=None,
                    ),
                )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: SourceLifecyclePolicy,
    ) -> None:
        """Register one Memory entry supported by the given dependency parents."""

        await self._register_derived(node_id, _MEMORY, citation, parents=parents, policy=policy)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: SourceLifecyclePolicy,
    ) -> None:
        """Register one Topic Memory artifact supported by the given dependency parents."""

        await self._register_derived(node_id, _TOPIC, artifact_ref, parents=parents, policy=policy)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every available node id, sorted."""

        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            state = await self._store().load(self.scope_id)
            node = state.nodes.get(event.node_id)
            if node is None or node.kind != _SOURCE:
                raise SourceLifecycleError(f"unknown lifecycle source {event.node_id!r}")
            self._require_source_revision(node, event.revision)
            latest = state.latest_event(event.node_id)
            if latest is not None:
                if event.sequence < latest.sequence:
                    raise SourceLifecycleError("lifecycle event sequence moved backwards")
                if event.sequence == latest.sequence:
                    if (
                        latest.revision != event.revision
                        or latest.available != event.available
                        or latest.reason != event.reason
                    ):
                        raise SourceLifecycleError("lifecycle event sequence reused with different content")
                    # Replaying an applied event stays idempotent, yet it still runs the
                    # propagation diff so an interrupted event completes on retry.
            if latest is None or event.sequence > latest.sequence:
                await self._store().record_event(
                    self.scope_id,
                    SourceLifecycleEventRow(
                        node_id=event.node_id,
                        sequence=event.sequence,
                        revision=event.revision,
                        available=event.available,
                        reason=event.reason,
                    ),
                )
                state = await self._store().load(self.scope_id)
            availability = state.availability()
            await self._propagate(state, availability)
            return sorted(node_id for node_id, supported in availability.items() if supported)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Resolve one node's original reference for generation or audit."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError(f"unknown lifecycle purpose {purpose!r}")
        async with self._runtime._scoped_operation(self.scope_id):
            state = await self._store().load(self.scope_id)
            node = state.nodes.get(node_id)
            if node is None:
                raise SourceLifecycleError(f"unknown lifecycle node {node_id!r}")
            if purpose == "generation" and not state.availability().get(node_id, False):
                raise SourceLifecycleError(f"lifecycle node {node_id!r} is unavailable for generation")
            return node.ref

    async def _register_derived(
        self,
        node_id: str,
        kind: SourceLifecycleNodeKind,
        ref: SourceRef | MemoryCitation | ArtifactRef,
        *,
        parents: Sequence[str],
        policy: SourceLifecyclePolicy,
    ) -> None:
        self._require_node_id(node_id)
        self._require_parents(parents)
        self._require_policy(policy)
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            state = await self._store().load(self.scope_id)
            existing = state.nodes.get(node_id)
            if existing is not None:
                self._require_same_binding(existing, kind=kind, ref=ref, parents=parents, policy=policy)
                # An identical re-registration is idempotent, but it still settles this
                # node's own Memory state so a previously interrupted registration
                # completes instead of silently reporting success.
                await self._propagate(state, state.availability())
                return
            self._require_new_parents(state, node_id, parents)
            if kind == _MEMORY:
                await self._verify_memory(ref)
            store = self._store()
            async with store.begin() as connection:
                if kind == _TOPIC:
                    await store.verify_artifact(connection, self.scope_id, ref)
                await store.insert_node(
                    connection,
                    self.scope_id,
                    SourceLifecycleNodeRow(
                        node_id=node_id,
                        kind=kind,
                        ref=ref,
                        parents=tuple(parents),
                        policy=policy,
                        revision=None,
                        artifact_id=_artifact_id_of(kind, ref),
                    ),
                )
            state = await store.load(self.scope_id)
            await self._propagate(state, state.availability())

    async def _propagate(self, state: _ScopeLifecycleState, availability: Mapping[str, bool]) -> None:
        """Write the native Memory state every derived availability change requires."""

        memory_nodes = [node for node in state.nodes.values() if node.kind == _MEMORY]
        if not memory_nodes:
            return
        context = await self._runtime._provider.get(self.scope_id)
        service = context.artifacts.memory
        try:
            current = await service.head(context.artifacts.memory_artifact_id)
        except ArtifactNotFoundError:
            return
        entries = {entry.entry_id: entry for entry in await service.entries(current)}
        states = {item.entry_id: item.state for item in current.content.manifest.entries}
        withdraw: list[MemoryEntryVersion] = []
        restore: list[MemoryEntryVersion] = []
        for node in memory_nodes:
            entry_id = node.ref.entry_id  # type: ignore[union-attr]
            desired = "active" if availability.get(node.node_id, False) else "inactive"
            if entry_id not in states or states[entry_id] == desired:
                continue
            version = entries.get(entry_id)
            if version is None:
                continue
            (withdraw if desired == "inactive" else restore).append(version)
        if withdraw:
            current = await service.forget(
                current,
                entries=tuple(withdraw),
                reason="source lifecycle withdrawal",
            )
        if restore:
            await service.reactivate(
                current,
                entries=tuple(restore),
                reason="source lifecycle restoration",
            )

    async def _verify_memory(self, citation: MemoryCitation) -> None:
        context = await self._runtime._provider.get(self.scope_id)
        service = context.artifacts.memory
        if citation.memory_ref.artifact_id != context.artifacts.memory_artifact_id:
            raise SourceLifecycleError("memory citation belongs to another Memory identity")
        try:
            await service.validate_citation(citation)
        except (InvalidMemoryCitationError, MemoryEntryNotFoundError, ArtifactNotFoundError) as error:
            raise SourceLifecycleError(f"unreadable memory citation {citation!r}") from error

    def _require_new_parents(self, state: _ScopeLifecycleState, node_id: str, parents: Sequence[str]) -> None:
        for parent in parents:
            if parent == node_id:
                raise SourceLifecycleError("a lifecycle node cannot depend on itself")
            if parent not in state.nodes:
                raise SourceLifecycleError(f"unknown lifecycle parent {parent!r}")
            if _reaches(state, parent, node_id):
                raise SourceLifecycleError("the lifecycle dependency graph must stay acyclic")

    @staticmethod
    def _require_node_id(node_id: str) -> None:
        if not isinstance(node_id, str) or not node_id.strip():
            raise SourceLifecycleError("a lifecycle node id must be a non-blank string")
        if len(node_id) > MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH:
            raise SourceLifecycleError("a lifecycle node id is too long")

    @staticmethod
    def _require_parents(parents: Sequence[str]) -> None:
        if not parents:
            raise SourceLifecycleError("a derived lifecycle node requires at least one parent")
        if any(not isinstance(parent, str) or not parent.strip() for parent in parents):
            raise SourceLifecycleError("every lifecycle parent must be a non-blank node id")
        if len(set(parents)) != len(tuple(parents)):
            raise SourceLifecycleError("lifecycle parents must be unique")

    @staticmethod
    def _require_policy(policy: str) -> None:
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("a lifecycle policy must be 'all' or 'any'")

    @staticmethod
    def _require_source_revision(node: SourceLifecycleNodeRow, revision: int) -> None:
        if node.revision != revision:
            raise SourceLifecycleError("lifecycle event revision does not match the registered Source")

    def _require_same_binding(
        self,
        existing: SourceLifecycleNodeRow,
        *,
        kind: SourceLifecycleNodeKind,
        ref: SourceRef | MemoryCitation | ArtifactRef,
        parents: Sequence[str],
        policy: SourceLifecyclePolicy,
    ) -> None:
        if (
            existing.kind != kind
            or existing.ref != ref
            or existing.parents != tuple(parents)
            or existing.policy != policy
        ):
            raise SourceLifecycleError("this lifecycle node id is already registered with a different binding")

    def _store(self) -> SourceLifecycleStore:
        store = self._runtime._source_lifecycle
        if store is None:
            raise SourceLifecycleError("source lifecycle persistence is not configured")
        return store


def _reaches(state: _ScopeLifecycleState, start: str, target: str) -> bool:
    """Report whether target is reachable from start through registered parents."""

    seen: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        node = state.nodes.get(current)
        if node is not None:
            pending.extend(node.parents)
    return False


def _artifact_id_of(kind: SourceLifecycleNodeKind, ref: SourceRef | MemoryCitation | ArtifactRef) -> str | None:
    if kind == _TOPIC:
        return ref.artifact_id  # type: ignore[union-attr]
    if kind == _MEMORY:
        return ref.memory_ref.artifact_id  # type: ignore[union-attr]
    return None


__all__ = [
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleStore",
    "ScopedSourceLifecycle",
]
