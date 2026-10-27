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

"""Incremental context transfer as chunked packs and prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Never

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_HEX_DIGITS = frozenset("0123456789abcdef")


class ContextPackError(ValueError):
    """A context pack or delta failed protocol, chunking, digest, or size validation."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context body split into independently transferable chunks."""

    items: tuple[bytes, ...]
    digest: str = field(init=False)
    byte_count: int = field(init=False)
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        items = _validate_items(self.items)
        digest = _digest_for_items(items)
        byte_count = 0
        while True:
            raw = _pack_wire(items, digest=digest, byte_count=byte_count)
            if len(raw) == byte_count:
                break
            byte_count = len(raw)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "byte_count", byte_count)
        object.__setattr__(self, "_wire", raw)

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a pack from already chunked bytes without re-splitting them."""

        return cls(items)

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = 512) -> ContextPack:
        """Chunk valid UTF-8 content deterministically, preferring newline boundaries."""

        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        raw = _validated_content_bytes(content)
        if not raw:
            return cls.build(())
        items: list[bytes] = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end <= start:
                raise ContextPackError("chunk_bytes cannot hold the next UTF-8 character")  # noqa: TRY003
            newline = raw.rfind(b"\n", start, end)
            if newline >= start:
                end = newline + 1
            items.append(raw[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        """Return the canonical UTF-8 JSON wire representation."""

        return self._wire

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        """Decode and validate one complete pack wire payload."""

        payload = _decode_wire(raw)
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("context pack wire fields are invalid")  # noqa: TRY003
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("context pack schema is unsupported")  # noqa: TRY003
        items = _decode_items(payload["items"])
        digest = _validate_digest(payload["digest"])
        byte_count = payload["byte_count"]
        if type(byte_count) is not int:
            raise ContextPackError("context pack byte_count must be an integer")  # noqa: TRY003
        pack = cls(items)
        if digest != pack.digest or byte_count != pack.byte_count:
            raise ContextPackError("context pack digest or byte_count does not match")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One target pack expressed as a reusable base prefix plus appended chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        base_digest = _validate_digest(self.base_digest)
        target_digest = _validate_digest(self.target_digest)
        prefix_items = _validate_prefix(self.prefix_items)
        appended = _validate_items(self.appended)
        wire = _canonical_json({
            "schema": DELTA_SCHEMA,
            "base_digest": base_digest,
            "target_digest": target_digest,
            "prefix_items": prefix_items,
            "appended": _encode_items(appended),
        })
        object.__setattr__(self, "_wire", wire)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Express the longest shared chunk prefix of two complete packs."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta endpoints must be complete context packs")  # noqa: TRY003
        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(base.digest, target.digest, prefix_items, target.items[prefix_items:])

    def to_bytes(self) -> bytes:
        """Return the canonical UTF-8 JSON wire representation."""

        return self._wire

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        """Decode and validate one delta wire payload."""

        payload = _decode_wire(raw)
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError("context pack delta wire fields are invalid")  # noqa: TRY003
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("context pack delta schema is unsupported")  # noqa: TRY003
        return cls(
            _validate_digest(payload["base_digest"]),
            _validate_digest(payload["target_digest"]),
            _validate_prefix(payload["prefix_items"]),
            _decode_items(payload["appended"]),
        )

    @property
    def byte_count(self) -> int:
        """Return the exact wire size of this delta."""

        return len(self._wire)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild and verify the target pack using only a complete base and a delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a base pack and a delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the base pack")  # noqa: TRY003
    prefix_items = _validate_prefix(delta.prefix_items)
    if prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")  # noqa: TRY003
    target = ContextPack.build(base.items[:prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("rebuilt context does not match the delta target digest")  # noqa: TRY003
    return target


def _validated_content_bytes(content: str | bytes) -> bytes:
    if isinstance(content, str):
        try:
            return content.encode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("context content is not valid UTF-8 text") from error  # noqa: TRY003
    if isinstance(content, bytes):
        try:
            content.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("context content is not valid UTF-8 bytes") from error  # noqa: TRY003
        return content
    raise ContextPackError("context content must be text or UTF-8 bytes")  # noqa: TRY003


def _validate_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, tuple):
        raise ContextPackError("context chunks must be a tuple of bytes")  # noqa: TRY003
    for item in value:
        if not isinstance(item, bytes):
            raise ContextPackError("context chunks must be bytes")  # noqa: TRY003
    return value


def _validate_prefix(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
    return value


def _validate_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in _HEX_DIGITS for char in value[7:])
    ):
        raise ContextPackError("digest must be sha256:<64 lowercase hex>")  # noqa: TRY003
    return value


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("wire chunks must be a JSON array")  # noqa: TRY003
    return tuple(_decode_chunk(chunk) for chunk in value)


def _decode_chunk(value: object) -> bytes:
    if not isinstance(value, str):
        raise ContextPackError("wire chunks must be Base64 strings")  # noqa: TRY003
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError) as error:
        raise ContextPackError("wire chunk is not valid Base64") from error  # noqa: TRY003
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ContextPackError("wire chunk is not canonical Base64")  # noqa: TRY003
    return decoded


def _digest_for_items(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": _encode_items(items)}
    return "sha256:" + hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _pack_wire(items: tuple[bytes, ...], *, digest: str, byte_count: int) -> bytes:
    return _canonical_json({
        "schema": PACK_SCHEMA,
        "items": _encode_items(items),
        "digest": digest,
        "byte_count": byte_count,
    })


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def _decode_wire(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise ContextPackError("wire payload must be bytes")  # noqa: TRY003
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("wire payload is not valid UTF-8") from error  # noqa: TRY003

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContextPackError("wire JSON contains a duplicate field")  # noqa: TRY003
            result[key] = value
        return result

    def reject_constant(value: str) -> Never:
        raise ContextPackError(f"wire JSON contains the non-finite constant {value}")  # noqa: TRY003

    try:
        payload = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except ContextPackError:
        raise
    except (ValueError, RecursionError) as error:
        raise ContextPackError("wire payload is not valid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")  # noqa: TRY003
    return payload


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
