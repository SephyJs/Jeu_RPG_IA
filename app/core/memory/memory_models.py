from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


SCHEMA_VERSION = 2


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


def clean_text(value: object, *, max_len: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 3)].rstrip() + "..."


def clean_tag(value: object, *, max_len: int = 48) -> str:
    tag = re.sub(r"[^a-zA-Z0-9:_-]+", "_", str(value or "").strip().casefold()).strip("_")
    if not tag:
        return ""
    return tag[:max_len]


def normalize_for_hash(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def text_hash(value: object) -> str:
    return hashlib.sha1(normalize_for_hash(value).encode("utf-8")).hexdigest()


class ShortTurn(BaseModel):
    ts: str = Field(default_factory=utc_now_iso)
    role: Literal["player", "npc", "system", "narration"] = "npc"
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.0
    turn_id: str = Field(default_factory=new_id)

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=460)

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.0
        return max(0.0, min(1.0, num))


class MemoryFact(BaseModel):
    id: str = Field(default_factory=new_id)
    ts: str = Field(default_factory=utc_now_iso)
    text: str = ""
    confidence: float = 0.65
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    text_hash: str = ""

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=420)

    @field_validator("confidence")
    @classmethod
    def _v_confidence(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.65
        return max(0.0, min(1.0, num))

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.5
        return max(0.0, min(1.0, num))

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("text_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return str(value or "").strip().casefold()


class MemoryEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    ts: str = Field(default_factory=utc_now_iso)
    text: str = ""
    impact: Literal["low", "med", "high"] = "low"
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    text_hash: str = ""

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=420)

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.5
        return max(0.0, min(1.0, num))

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("text_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return str(value or "").strip().casefold()


class MemoryPromise(BaseModel):
    id: str = Field(default_factory=new_id)
    ts: str = Field(default_factory=utc_now_iso)
    text: str = ""
    status: Literal["open", "kept", "broken"] = "open"
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.6
    text_hash: str = ""

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=420)

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.6
        return max(0.0, min(1.0, num))

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("text_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return str(value or "").strip().casefold()


class MemoryDebt(BaseModel):
    id: str = Field(default_factory=new_id)
    ts: str = Field(default_factory=utc_now_iso)
    text: str = ""
    status: Literal["open", "paid"] = "open"
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.6
    text_hash: str = ""

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=420)

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.6
        return max(0.0, min(1.0, num))

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("text_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return str(value or "").strip().casefold()


class RelationshipNote(BaseModel):
    ts: str = Field(default_factory=utc_now_iso)
    text: str = ""

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=280)


class PlayerRelationship(BaseModel):
    affinity: int = 0
    notes: list[RelationshipNote] = Field(default_factory=list)

    @field_validator("affinity")
    @classmethod
    def _v_affinity(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = 0
        return max(-100, min(100, num))


class Relationships(BaseModel):
    player: PlayerRelationship = Field(default_factory=PlayerRelationship)


class LongSummary(BaseModel):
    ts: str = Field(default_factory=utc_now_iso)
    text: str = "(aucun resume)"

    @field_validator("text")
    @classmethod
    def _v_text(cls, value: str) -> str:
        return clean_text(value, max_len=1200) or "(aucun resume)"


class LongMemory(BaseModel):
    facts: list[MemoryFact] = Field(default_factory=list)
    events: list[MemoryEvent] = Field(default_factory=list)
    promises: list[MemoryPromise] = Field(default_factory=list)
    debts: list[MemoryDebt] = Field(default_factory=list)
    relationships: Relationships = Field(default_factory=Relationships)
    summary: LongSummary = Field(default_factory=LongSummary)


class MemoryChunk(BaseModel):
    chunk_id: str = Field(default_factory=new_id)
    ts_range: list[str] = Field(default_factory=lambda: [utc_now_iso(), utc_now_iso()])
    turn_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    text_hash: str = ""

    @field_validator("summary")
    @classmethod
    def _v_summary(cls, value: str) -> str:
        return clean_text(value, max_len=1000)

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            tag = clean_tag(row)
            if tag and tag not in out:
                out.append(tag)
        return out[:24]

    @field_validator("importance")
    @classmethod
    def _v_importance(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = 0.5
        return max(0.0, min(1.0, num))

    @field_validator("text_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return str(value or "").strip().casefold()


class MemoryStats(BaseModel):
    short_max: int = 60
    chunk_target_turns: int = 40
    last_compact_ts: str = ""

    @field_validator("short_max")
    @classmethod
    def _v_short_max(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = 60
        return max(20, min(240, num))

    @field_validator("chunk_target_turns")
    @classmethod
    def _v_chunk_target(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = 40
        return max(10, min(120, num))


class NpcMemory(BaseModel):
    schema_version: int = SCHEMA_VERSION
    npc_id: str = ""
    short: list[ShortTurn] = Field(default_factory=list)
    long: LongMemory = Field(default_factory=LongMemory)
    chunks: list[MemoryChunk] = Field(default_factory=list)
    stats: MemoryStats = Field(default_factory=MemoryStats)

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = SCHEMA_VERSION
        return max(1, num)


class WorldMemory(BaseModel):
    schema_version: int = SCHEMA_VERSION
    short: list[ShortTurn] = Field(default_factory=list)
    long: LongMemory = Field(default_factory=LongMemory)
    chunks: list[MemoryChunk] = Field(default_factory=list)
    world_flags: dict[str, object] = Field(default_factory=dict)
    discovered_locations: list[str] = Field(default_factory=list)
    stats: MemoryStats = Field(default_factory=MemoryStats)

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = SCHEMA_VERSION
        return max(1, num)

    @field_validator("discovered_locations")
    @classmethod
    def _v_discovered(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for row in value:
            text = clean_text(row, max_len=120)
            if text and text not in out:
                out.append(text)
        return out[-1200:]

