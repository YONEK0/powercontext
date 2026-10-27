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

"""Captured Pi session branch history as immutable event evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import (
    Connector,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_CONNECTOR_NAME = "pi-session-history"
PI_SOURCE_VERSION = "1"
PI_SESSION_VERSION = 3

_VISIBLE_MESSAGE_ROLES = frozenset({"user", "assistant", "toolResult", "bashExecution"})


class PiSessionInput(BaseModel):
    """One complete Pi JSONL archive and the branch leaf to walk from."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch with its original record."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event evidence identified by session and event identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        if self.source_id != self.name:
            raise ValueError("source_id must match the Source name")  # noqa: TRY003
        return self


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the stable identity of one event without delimiter collisions."""

    source_id = f"pi:{session_id}:{event_id}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
        source_id = f"pi:{session_id[:64]}:{digest}"
        if len(source_id) > MAX_SOURCE_ID_LENGTH:
            raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def _decode_archive(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session content must be JSONL") from error
        if not isinstance(record, dict):
            raise ValueError("every Pi session record must be a JSON object")  # noqa: TRY003
        records.append(record)
    if not records:
        raise ValueError("Pi session content must not be empty")  # noqa: TRY003
    return records


def _validate_header(header: dict[str, Any]) -> str:
    if header.get("type") != "session":
        raise ValueError("the first Pi record must be a session header")  # noqa: TRY003
    if header.get("version") != PI_SESSION_VERSION:
        raise ValueError("unsupported Pi session version")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must contain a non-empty id")  # noqa: TRY003
    return session_id


def _build_graph(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    by_id: dict[str, dict[str, Any]] = {}
    last_id: str | None = None
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("every Pi record must contain a non-empty id")  # noqa: TRY003
        if event_id in by_id:
            raise ValueError(f"duplicate Pi event id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                raise ValueError("parentId must be a non-empty string or null")  # noqa: TRY003
            if parent_id == event_id:
                raise ValueError("a Pi event must not be its own parent")  # noqa: TRY003
            if parent_id not in by_id:
                raise ValueError(f"missing Pi parent {parent_id!r}")  # noqa: TRY003
        by_id[event_id] = record
        last_id = event_id
    return by_id, last_id


def _branch_path(by_id: dict[str, dict[str, Any]], leaf_id: str) -> list[dict[str, Any]]:
    if leaf_id not in by_id:
        raise ValueError(f"missing Pi leaf {leaf_id!r}")  # noqa: TRY003
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("Pi session history must not contain cycles")  # noqa: TRY003
        seen.add(current)
        record = by_id[current]
        path.append(record)
        current = record.get("parentId")
    path.reverse()
    return path


def _json_arguments(arguments: object) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("tool call arguments must be JSON serializable") from error


def _text_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _message_text(message: dict[str, Any]) -> tuple[str, str] | None:
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return None
    if role == "toolResult":
        name = message.get("toolName")
        call_id = message.get("toolCallId")
        body = _text_blocks(message.get("content"))
        return role, f"[tool_result {name} {call_id}] {body}"
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        body = f"$ {command}\n{output}"
        return role, body
    if role not in _VISIBLE_MESSAGE_ROLES and role != "assistant":
        return None
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif block_type == "toolCall":
                name = block.get("name")
                call_id = block.get("id")
                parts.append(f"[tool_call {name} {call_id}] {_json_arguments(block.get('arguments'))}")
    else:
        text = content if isinstance(content, str) else ""
        if text:
            parts.append(text)
    if not parts:
        return None
    return role, "\n".join(parts)


def _event_text(record: dict[str, Any]) -> tuple[str, str] | None:
    record_type = record.get("type")
    if record_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        rendered = _message_text(message)
        if rendered is None:
            return None
        role, body = rendered
        return role, f"{role}: {body}"
    if record_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        return record_type, f"{record_type}: {summary}"
    if record_type == "custom_message":
        body = _text_blocks(record.get("content"))
        if not body:
            return None
        return "custom", f"custom: {body}"
    return None


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events of one selected branch from root to leaf."""

    records = _decode_archive(value.content)
    session_id = _validate_header(records[0])
    by_id, last_id = _build_graph(records[1:])
    leaf_id = value.leaf_id if value.leaf_id is not None else last_id
    if leaf_id is None:
        raise ValueError("Pi session must contain at least one event")  # noqa: TRY003
    events: list[PiEventInput] = []
    for record in _branch_path(by_id, leaf_id):
        rendered = _event_text(record)
        if rendered is None:
            continue
        role, content = rendered
        record_type = record.get("type")
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=record["id"],
                parent_id=record.get("parentId"),
                event_type=record_type if isinstance(record_type, str) else "",
                role=role,
                content=content,
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read Pi event Sources without altering the record."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Project one Pi event into the standard textual evidence envelope."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version=PI_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class PiSessionConnector(Connector):
    """Import unseen visible events from one Pi session branch."""

    name = PI_CONNECTOR_NAME
    version = PI_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(
            PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id)
        )
        checkpoint = session.checkpoint
        imported: dict[str, str] = {}
        if isinstance(checkpoint, dict):
            stored = checkpoint.get("events")
            if isinstance(stored, dict):
                imported = {key: value for key, value in stored.items() if isinstance(value, str)}
        for event in events:
            digest = _record_digest(event.record)
            previous = imported.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError(f"Pi event {event.event_id!r} was rewritten after import")  # noqa: TRY003
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            imported[event.event_id] = digest
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"events": dict(sorted(imported.items()))},
        )


__all__ = [
    "PI_CONNECTOR_NAME",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
