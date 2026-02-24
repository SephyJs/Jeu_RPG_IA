from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any, Callable
from uuid import uuid4

from .embeddings import EmbeddingProvider
from .memory_compactor import compact_npc_memory, compact_world_memory, log_compaction_result
from .memory_models import (
    MemoryDebt,
    MemoryEvent,
    MemoryFact,
    MemoryPromise,
    NpcMemory,
    ShortTurn,
    WorldMemory,
    clean_tag,
    clean_text,
    text_hash,
    utc_now_iso,
)
from .memory_retrieval import retrieve_context as retrieve_context_hybrid
from .memory_store import MemoryStore, safe_id
from .vector_index import VectorIndex


LOG = logging.getLogger(__name__)


def _to_importance_01(value: object, default: float = 0.45) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(default)
    if num > 1.0:
        num = num / 5.0
    return max(0.0, min(1.0, num))


def _role_tags(role: str) -> list[str]:
    clean = str(role or "").strip().casefold()
    if clean == "player":
        return ["player"]
    if clean == "system":
        return ["system"]
    if clean == "narration":
        return ["narration"]
    return ["npc"]


def _extract_kind_tags(text: str) -> list[str]:
    hay = str(text or "").casefold()
    rules = {
        "trade": ["acheter", "vendre", "prix", "or", "echange", "marchand"],
        "quest": ["quete", "mission", "objectif", "contrat"],
        "combat": ["combat", "attaque", "monstre", "donjon", "defaite", "victoire"],
        "training": ["entrain", "competence", "sort", "niveau"],
        "travel": ["voyage", "route", "deplacement", "ville"],
        "promise": ["promis", "promets", "je vais", "je ferai"],
        "debt": ["dette", "rembours", "payer", "creance"],
        "relationship": ["confiance", "trahison", "amour", "haine", "respect"],
    }
    out: list[str] = []
    for tag, words in rules.items():
        if any(word in hay for word in words):
            out.append(tag)
    return out


@dataclass
class PromptMemoryContext:
    short_lines: list[str]
    long_lines: list[str]
    world_lines: list[str]
    retrieved_lines: list[str]

    def short_text(self) -> str:
        return "\n".join(self.short_lines) if self.short_lines else "(aucun echange recent)"

    def long_text(self) -> str:
        return "\n".join(self.long_lines) if self.long_lines else "(aucune memoire long terme)"

    def world_text(self) -> str:
        return "\n".join(self.world_lines) if self.world_lines else "(aucune memoire globale)"

    def retrieved_text(self) -> str:
        return "\n".join(self.retrieved_lines) if self.retrieved_lines else "(aucun rappel semantique)"


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStore | None = None,
        embeddings: EmbeddingProvider | None = None,
        compaction_planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store if isinstance(store, MemoryStore) else MemoryStore()
        self.embeddings = embeddings if isinstance(embeddings, EmbeddingProvider) else EmbeddingProvider()
        self.compaction_planner = compaction_planner
        self._npc_indexes: dict[str, VectorIndex] = {}
        self._world_index: VectorIndex | None = None
        self._npc_index_loaded: set[str] = set()
        self._world_index_loaded = False

    def scoped_npc_id(self, *, profile_key: str | None, npc_id: str | None) -> str:
        scope = safe_id(profile_key or "default")
        npc = safe_id(npc_id or "unknown")
        return f"{scope}__{npc}"

    def _base_npc_id(self, scoped_npc_id: str) -> str:
        text = str(scoped_npc_id or "").strip()
        if "__" in text:
            return text.split("__", 1)[1]
        return text

    def base_npc_id(self, scoped_npc_id: str) -> str:
        return self._base_npc_id(scoped_npc_id)

    def load_npc_memory(self, *, profile_key: str | None, npc_id: str | None) -> NpcMemory:
        scoped = self.scoped_npc_id(profile_key=profile_key, npc_id=npc_id)
        mem = self.store.load_npc_memory(scoped)
        if not mem.npc_id:
            mem.npc_id = scoped
        return mem

    def save_npc_memory(self, memory: NpcMemory) -> None:
        self.store.save_npc_memory(memory)

    def load_world_memory(self) -> WorldMemory:
        return self.store.load_world_memory()

    def save_world_memory(self, memory: WorldMemory) -> None:
        self.store.save_world_memory(memory)

    def _memory_turn(self, *, role: str, text: str, tags: list[str] | None, importance: float, turn_id: str | None = None) -> ShortTurn:
        clean_tags = [clean_tag(tag) for tag in (tags or []) if clean_tag(tag)]
        for tag in _role_tags(role):
            if tag not in clean_tags:
                clean_tags.append(tag)
        for tag in _extract_kind_tags(text):
            clean = clean_tag(tag)
            if clean and clean not in clean_tags:
                clean_tags.append(clean)
        return ShortTurn(
            role=str(role or "npc").strip().casefold() or "npc",
            text=clean_text(text, max_len=460),
            tags=clean_tags[:24],
            importance=max(0.0, min(1.0, float(importance))),
            turn_id=str(turn_id or uuid4()),
        )

    def append_short(
        self,
        *,
        profile_key: str | None,
        npc_id: str | None,
        role: str,
        text: str,
        tags: list[str] | None = None,
        importance: float = 0.45,
        turn_id: str | None = None,
    ) -> bool:
        clean_text_value = clean_text(text, max_len=460)
        if not clean_text_value:
            return False
        memory = self.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
        memory.short.append(
            self._memory_turn(
                role=role,
                text=clean_text_value,
                tags=tags,
                importance=_to_importance_01(importance, default=0.45),
                turn_id=turn_id,
            )
        )
        compacted = compact_npc_memory(
            memory,
            ai_enabled=callable(self.compaction_planner),
            planner=self.compaction_planner,
        )
        log_compaction_result(f"npc={memory.npc_id}", compacted)
        self.save_npc_memory(memory)
        if compacted.changed:
            self.rebuild_npc_index(profile_key=profile_key, npc_id=npc_id)
        return True

    def append_world_short(
        self,
        *,
        role: str,
        text: str,
        tags: list[str] | None = None,
        importance: float = 0.4,
        turn_id: str | None = None,
    ) -> bool:
        clean_text_value = clean_text(text, max_len=460)
        if not clean_text_value:
            return False
        memory = self.load_world_memory()
        memory.short.append(
            self._memory_turn(
                role=role,
                text=clean_text_value,
                tags=tags,
                importance=_to_importance_01(importance, default=0.4),
                turn_id=turn_id,
            )
        )
        compacted = compact_world_memory(
            memory,
            ai_enabled=callable(self.compaction_planner),
            planner=self.compaction_planner,
        )
        log_compaction_result("world", compacted)
        self.save_world_memory(memory)
        if compacted.changed:
            self.rebuild_world_index()
        return True

    def remember_dialogue_turn(
        self,
        *,
        profile_key: str | None,
        npc_id: str | None,
        player_text: str,
        npc_reply: str,
        scene_title: str = "",
    ) -> None:
        shared_turn_id = str(uuid4())
        context_tags = [clean_tag(scene_title)] if scene_title else []
        if player_text:
            self.append_short(
                profile_key=profile_key,
                npc_id=npc_id,
                role="player",
                text=player_text,
                tags=context_tags,
                importance=0.5,
                turn_id=shared_turn_id,
            )
        if npc_reply:
            self.append_short(
                profile_key=profile_key,
                npc_id=npc_id,
                role="npc",
                text=npc_reply,
                tags=context_tags,
                importance=0.48,
                turn_id=shared_turn_id,
            )

    def remember_system_event(
        self,
        *,
        profile_key: str | None,
        npc_id: str | None,
        fact_text: str,
        kind: str = "system",
        importance: float = 0.65,
        world_only: bool = False,
    ) -> None:
        clean = clean_text(fact_text, max_len=420)
        if not clean:
            return
        tags = [clean_tag(kind)] + [clean_tag(tag) for tag in _extract_kind_tags(clean)]
        tags = [tag for tag in tags if tag]
        if not world_only:
            memory = self.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
            added = False
            if kind == "promise" or "promise" in tags:
                entry = MemoryPromise(
                    text=clean,
                    status="open",
                    tags=tags,
                    importance=_to_importance_01(importance, 0.7),
                    text_hash=text_hash(clean),
                )
                if not any(str(row.text_hash or "") == entry.text_hash for row in memory.long.promises):
                    memory.long.promises.append(entry)
                    added = True
            elif kind == "debt" or "debt" in tags:
                entry = MemoryDebt(
                    text=clean,
                    status="open",
                    tags=tags,
                    importance=_to_importance_01(importance, 0.7),
                    text_hash=text_hash(clean),
                )
                if not any(str(row.text_hash or "") == entry.text_hash for row in memory.long.debts):
                    memory.long.debts.append(entry)
                    added = True
            elif kind == "event" or "quest" in tags or "combat" in tags:
                impact = "med"
                if any(word in clean.casefold() for word in ("mort", "defaite", "rupture", "boss")):
                    impact = "high"
                entry = MemoryEvent(
                    text=clean,
                    impact=impact,
                    tags=tags,
                    importance=_to_importance_01(importance, 0.62),
                    text_hash=text_hash(clean),
                )
                if not any(str(row.text_hash or "") == entry.text_hash for row in memory.long.events):
                    memory.long.events.append(entry)
                    added = True
            else:
                entry = MemoryFact(
                    text=clean,
                    confidence=0.72,
                    tags=tags,
                    importance=_to_importance_01(importance, 0.55),
                    text_hash=text_hash(clean),
                )
                if not any(str(row.text_hash or "") == entry.text_hash for row in memory.long.facts):
                    memory.long.facts.append(entry)
                    added = True
            if added:
                memory.long.summary.ts = utc_now_iso()
                memory.long.summary.text = clean_text(clean, max_len=900)
                memory.long.facts = memory.long.facts[-500:]
                memory.long.events = memory.long.events[-500:]
                memory.long.promises = memory.long.promises[-100:]
                memory.long.debts = memory.long.debts[-100:]
                self.save_npc_memory(memory)

        world = self.load_world_memory()
        world.long.events.append(
            MemoryEvent(
                text=clean,
                impact="med",
                tags=tags or ["system"],
                importance=_to_importance_01(importance, 0.55),
                text_hash=text_hash(clean),
            )
        )
        world.long.events = world.long.events[-500:]
        world.long.summary.ts = utc_now_iso()
        world.long.summary.text = clean_text(clean, max_len=900)
        self.save_world_memory(world)

    def _records_from_npc_memory(self, memory: NpcMemory) -> list[dict[str, Any]]:
        base_id = self._base_npc_id(memory.npc_id)
        rows: list[dict[str, Any]] = []
        for chunk in memory.chunks:
            text = clean_text(chunk.summary, max_len=1000)
            if not text:
                continue
            rows.append(
                {
                    "record_id": f"chunk:{chunk.chunk_id}",
                    "text": text,
                    "meta": {
                        "kind": "chunk",
                        "npc_id": base_id,
                        "scope_npc_id": memory.npc_id,
                        "ts": str(chunk.ts_range[1] if chunk.ts_range else ""),
                        "tags": list(chunk.tags),
                        "importance": float(chunk.importance),
                    },
                }
            )
        for item in memory.long.facts:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"fact:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": "fact",
                        "npc_id": base_id,
                        "scope_npc_id": memory.npc_id,
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        for item in memory.long.events:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"event:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": f"event:{item.impact}",
                        "npc_id": base_id,
                        "scope_npc_id": memory.npc_id,
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        for item in memory.long.promises:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"promise:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": f"promise:{item.status}",
                        "npc_id": base_id,
                        "scope_npc_id": memory.npc_id,
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        for item in memory.long.debts:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"debt:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": f"debt:{item.status}",
                        "npc_id": base_id,
                        "scope_npc_id": memory.npc_id,
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        return rows

    def _records_from_world_memory(self, memory: WorldMemory) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for chunk in memory.chunks:
            text = clean_text(chunk.summary, max_len=1000)
            if not text:
                continue
            rows.append(
                {
                    "record_id": f"world_chunk:{chunk.chunk_id}",
                    "text": text,
                    "meta": {
                        "kind": "world_chunk",
                        "ts": str(chunk.ts_range[1] if chunk.ts_range else ""),
                        "tags": list(chunk.tags),
                        "importance": float(chunk.importance),
                    },
                }
            )
        for item in memory.long.facts:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"world_fact:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": "world_fact",
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        for item in memory.long.events:
            if not item.text:
                continue
            rows.append(
                {
                    "record_id": f"world_event:{item.id}",
                    "text": item.text,
                    "meta": {
                        "kind": f"world_event:{item.impact}",
                        "ts": str(item.ts),
                        "tags": list(item.tags),
                        "importance": float(item.importance),
                    },
                }
            )
        return rows

    def _load_npc_index(self, scoped_npc_id: str) -> VectorIndex:
        key = safe_id(scoped_npc_id)
        index = self._npc_indexes.get(key)
        if isinstance(index, VectorIndex):
            return index
        index = VectorIndex(prefer_faiss=True)
        self._npc_indexes[key] = index
        return index

    def _ensure_npc_index_loaded(self, scoped_npc_id: str) -> VectorIndex:
        key = safe_id(scoped_npc_id)
        index = self._load_npc_index(key)
        if key in self._npc_index_loaded:
            return index
        index_path = self.store.npc_index_path(key)
        map_path = self.store.npc_mapping_path(key)
        index.load(index_path=index_path, mapping_path=map_path)
        self._npc_index_loaded.add(key)
        return index

    def _ensure_world_index_loaded(self) -> VectorIndex:
        if not isinstance(self._world_index, VectorIndex):
            self._world_index = VectorIndex(prefer_faiss=True)
        if self._world_index_loaded:
            return self._world_index
        self._world_index.load(index_path=self.store.world_index_path, mapping_path=self.store.world_mapping_path)
        self._world_index_loaded = True
        return self._world_index

    def rebuild_npc_index(self, *, profile_key: str | None, npc_id: str | None) -> int:
        scoped = self.scoped_npc_id(profile_key=profile_key, npc_id=npc_id)
        memory = self.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
        records = self._records_from_npc_memory(memory)
        index = self._load_npc_index(scoped)
        added = index.rebuild_from_records(records=records, embed_texts=self.embeddings.embed_texts)
        index.persist(index_path=self.store.npc_index_path(scoped), mapping_path=self.store.npc_mapping_path(scoped))
        self._npc_index_loaded.add(safe_id(scoped))
        return added

    def rebuild_world_index(self) -> int:
        memory = self.load_world_memory()
        records = self._records_from_world_memory(memory)
        index = self._ensure_world_index_loaded()
        added = index.rebuild_from_records(records=records, embed_texts=self.embeddings.embed_texts)
        index.persist(index_path=self.store.world_index_path, mapping_path=self.store.world_mapping_path)
        self._world_index_loaded = True
        return added

    def _vector_hits(
        self,
        *,
        profile_key: str | None,
        npc_id: str | None,
        query: str,
        mode: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not self.embeddings.enabled():
            return []
        query_vec = self.embeddings.embed_text(query)
        if not query_vec:
            return []

        clean_mode = str(mode or "npc").strip().casefold()
        hits: list[dict[str, Any]] = []
        if clean_mode in {"npc", "both"}:
            scoped = self.scoped_npc_id(profile_key=profile_key, npc_id=npc_id)
            index = self._ensure_npc_index_loaded(scoped)
            hits.extend(index.search(query_vec, top_k=max(1, top_k)))
        if clean_mode in {"world", "both"}:
            world_index = self._ensure_world_index_loaded()
            hits.extend(world_index.search(query_vec, top_k=max(1, top_k)))
        return hits

    def retrieve_context(
        self,
        *,
        profile_key: str | None,
        npc_id: str | None,
        query: str,
        mode: str = "npc",
        short_limit: int = 8,
        long_limit: int = 12,
        retrieved_limit: int = 10,
    ) -> PromptMemoryContext:
        clean_mode = str(mode or "npc").strip().casefold()
        if clean_mode not in {"npc", "world", "both"}:
            clean_mode = "npc"

        npc_memory = self.load_npc_memory(profile_key=profile_key, npc_id=npc_id) if clean_mode in {"npc", "both"} else None
        world_memory = self.load_world_memory() if clean_mode in {"world", "both"} else None

        if clean_mode in {"npc", "both"} and npc_memory and not npc_memory.chunks:
            try:
                self.rebuild_npc_index(profile_key=profile_key, npc_id=npc_id)
            except Exception:
                pass
        if clean_mode in {"world", "both"} and world_memory and not world_memory.chunks:
            try:
                self.rebuild_world_index()
            except Exception:
                pass

        hits = self._vector_hits(
            profile_key=profile_key,
            npc_id=npc_id,
            query=query,
            mode=clean_mode,
            top_k=max(1, retrieved_limit),
        )
        retrieved = retrieve_context_hybrid(
            npc_memory=npc_memory,
            world_memory=world_memory,
            query=query,
            mode=clean_mode,
            vector_hits=hits,
            short_limit=max(1, short_limit),
            long_limit=max(1, long_limit),
            retrieved_limit=max(1, retrieved_limit),
        )
        short_lines = retrieved.get("short", [])
        long_lines = retrieved.get("long", [])
        retrieved_lines = retrieved.get("retrieved", [])
        world_lines = []
        if clean_mode in {"world", "both"}:
            world_lines = [line for line in (retrieved.get("combined") or []) if isinstance(line, str)][: max(1, long_limit)]
        return PromptMemoryContext(
            short_lines=[str(line) for line in short_lines if str(line).strip()],
            long_lines=[str(line) for line in long_lines if str(line).strip()],
            world_lines=[str(line) for line in world_lines if str(line).strip()],
            retrieved_lines=[str(line) for line in retrieved_lines if str(line).strip()],
        )

    def list_scoped_npc_ids(self, *, profile_key: str | None = None) -> list[str]:
        all_ids = self.store.list_npc_ids()
        scope = safe_id(profile_key or "").strip()
        if not scope:
            return all_ids
        prefix = f"{scope}__"
        return [row for row in all_ids if str(row).startswith(prefix)]

    def purge_short(self, *, profile_key: str | None, npc_id: str | None) -> bool:
        memory = self.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
        if not memory.short:
            return False
        memory.short = []
        self.save_npc_memory(memory)
        return True


_MEMORY_SERVICE: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _MEMORY_SERVICE
    if isinstance(_MEMORY_SERVICE, MemoryService):
        return _MEMORY_SERVICE
    _MEMORY_SERVICE = MemoryService()
    return _MEMORY_SERVICE


def set_memory_service(service: MemoryService | None) -> None:
    global _MEMORY_SERVICE
    _MEMORY_SERVICE = service
