from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Callable

from pydantic import BaseModel, Field

from .memory_models import (
    LongSummary,
    MemoryChunk,
    MemoryDebt,
    MemoryEvent,
    MemoryFact,
    MemoryPromise,
    NpcMemory,
    RelationshipNote,
    ShortTurn,
    WorldMemory,
    clean_tag,
    clean_text,
    text_hash,
    utc_now_iso,
)


LOG = logging.getLogger(__name__)

FACT_LIMIT = 500
EVENT_LIMIT = 500
PROMISE_LIMIT = 100
DEBT_LIMIT = 100
CHUNK_LIMIT = 2000


class PatchItem(BaseModel):
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.6
    impact: str = "low"
    status: str = "open"
    importance: float = 0.5


class RelationshipDelta(BaseModel):
    affinity_delta: int = 0
    notes: list[str] = Field(default_factory=list)


class CompactionPatch(BaseModel):
    chunk_summary: str = ""
    chunk_tags: list[str] = Field(default_factory=list)
    chunk_importance: float = 0.5
    facts: list[PatchItem] = Field(default_factory=list)
    events: list[PatchItem] = Field(default_factory=list)
    promises: list[PatchItem] = Field(default_factory=list)
    debts: list[PatchItem] = Field(default_factory=list)
    relationship_delta: RelationshipDelta = Field(default_factory=RelationshipDelta)
    summary: str = ""


@dataclass
class CompactResult:
    changed: bool
    compacted_chunks: int
    logs: list[str]


def _collect_tags(text: str) -> list[str]:
    rules = {
        "trade": ["acheter", "vendre", "prix", "or", "echange", "marchand"],
        "quest": ["quete", "mission", "objectif", "contrat"],
        "combat": ["combat", "attaque", "frappe", "monstre", "donjon"],
        "training": ["entrain", "competence", "sort", "apprendre", "niveau"],
        "travel": ["route", "voyage", "ville", "deplacement", "aller"],
        "promise": ["promis", "promets", "je vais", "je ferai"],
        "debt": ["dette", "dois", "rembourse", "payer"],
        "relationship": ["aime", "deteste", "confiance", "trahis", "respecte"],
    }
    hay = str(text or "").casefold()
    out: list[str] = []
    for tag, keywords in rules.items():
        if any(word in hay for word in keywords):
            out.append(tag)
    return out


def _extract_patch_fallback(turns: list[ShortTurn]) -> CompactionPatch:
    lines: list[str] = []
    all_text = []
    player_text = []
    npc_text = []
    system_text = []
    turn_ids: list[str] = []
    tags: list[str] = []

    for row in turns:
        txt = clean_text(row.text, max_len=260)
        if not txt:
            continue
        role = str(row.role or "npc")
        prefix = role.upper()
        lines.append(f"{prefix}: {txt}")
        all_text.append(txt)
        turn_id = str(row.turn_id or "").strip()
        if turn_id and turn_id not in turn_ids:
            turn_ids.append(turn_id)
        if role == "player":
            player_text.append(txt)
        elif role == "npc":
            npc_text.append(txt)
        else:
            system_text.append(txt)
        for tag in _collect_tags(txt):
            clean = clean_tag(tag)
            if clean and clean not in tags:
                tags.append(clean)

    if not lines:
        return CompactionPatch(chunk_summary="(aucun contenu)", summary="(aucun resume)")

    head = lines[:2]
    tail = lines[-3:] if len(lines) > 3 else []
    summary = " | ".join(head + tail)
    summary = clean_text(summary, max_len=600)
    global_text = " ".join(all_text).casefold()

    chunk_importance = 0.35
    if any(tag in tags for tag in {"promise", "debt", "quest"}):
        chunk_importance += 0.22
    if any(tag in tags for tag in {"combat", "relationship"}):
        chunk_importance += 0.12
    if len(all_text) >= 24:
        chunk_importance += 0.10
    chunk_importance = max(0.15, min(1.0, chunk_importance))

    facts: list[PatchItem] = []
    events: list[PatchItem] = []
    promises: list[PatchItem] = []
    debts: list[PatchItem] = []

    promise_re = re.compile(r"\b(promis|promets|je vais|je ferai|on se retrouve|je m'engage)\b", flags=re.IGNORECASE)
    debt_re = re.compile(r"\b(dette|je te dois|rembours|payer|paiement|creance)\b", flags=re.IGNORECASE)
    event_re = re.compile(r"\b(quete|mission|combat|victoire|defaite|incident|attaque|trouve|perdu)\b", flags=re.IGNORECASE)
    fact_re = re.compile(r"\b(je suis|mon nom|j'habite|je viens|je possede|j'ai)\b", flags=re.IGNORECASE)

    for txt in all_text[-24:]:
        local_tags = [clean_tag(tag) for tag in _collect_tags(txt)]
        local_tags = [tag for tag in local_tags if tag]
        if promise_re.search(txt):
            promises.append(
                PatchItem(
                    text=txt,
                    tags=local_tags or ["promise"],
                    status="open",
                    importance=0.72,
                )
            )
        if debt_re.search(txt):
            debts.append(
                PatchItem(
                    text=txt,
                    tags=local_tags or ["debt"],
                    status="open",
                    importance=0.72,
                )
            )
        if event_re.search(txt) or txt in system_text:
            impact = "low"
            if any(k in txt.casefold() for k in ("defaite", "mort", "boss", "rupture", "incident")):
                impact = "high"
            elif any(k in txt.casefold() for k in ("combat", "quete", "victoire", "attaque")):
                impact = "med"
            events.append(
                PatchItem(
                    text=txt,
                    tags=local_tags or ["event"],
                    impact=impact,
                    importance=0.62 if impact == "high" else 0.54,
                )
            )
        if fact_re.search(txt):
            facts.append(
                PatchItem(
                    text=txt,
                    tags=local_tags or ["fact"],
                    confidence=0.62,
                    importance=0.5,
                )
            )

    if not facts:
        facts.append(
            PatchItem(
                text=clean_text(f"Contexte resume: {summary}", max_len=360),
                tags=tags[:4] or ["general"],
                confidence=0.55,
                importance=0.45,
            )
        )

    positive = sum(1 for t in player_text if any(k in t.casefold() for k in ("merci", "ok", "parfait", "oui", "super")))
    negative = sum(1 for t in player_text if any(k in t.casefold() for k in ("non", "jamais", "colere", "deteste", "mensonge", "nul")))
    affinity_delta = max(-5, min(5, positive - negative))

    rel_notes: list[str] = []
    if affinity_delta > 0:
        rel_notes.append("Le joueur montre davantage de confiance ou d'ouverture.")
    elif affinity_delta < 0:
        rel_notes.append("Le joueur exprime une tension ou une mefiance.")
    elif "relationship" in tags:
        rel_notes.append("La relation reste active, sans bascule claire.")

    brief = clean_text(
        " ".join(
            [
                "Points saillants:",
                summary,
            ]
        ),
        max_len=760,
    )

    return CompactionPatch(
        chunk_summary=summary,
        chunk_tags=tags[:8] or ["general"],
        chunk_importance=chunk_importance,
        facts=facts[:10],
        events=events[:10],
        promises=promises[:10],
        debts=debts[:10],
        relationship_delta=RelationshipDelta(affinity_delta=affinity_delta, notes=rel_notes[:4]),
        summary=brief,
    )


def _validate_patch_payload(payload: dict[str, Any]) -> CompactionPatch | None:
    try:
        patch = CompactionPatch.model_validate(payload)
    except Exception:
        return None
    if not patch.chunk_summary:
        return None
    return patch


def _extract_patch_with_ai(
    turns: list[ShortTurn],
    *,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> CompactionPatch | None:
    if not callable(planner):
        return None
    payload = {
        "turns": [
            {
                "ts": str(row.ts),
                "role": str(row.role),
                "text": str(row.text),
                "tags": list(row.tags),
                "importance": float(row.importance),
                "turn_id": str(row.turn_id),
            }
            for row in turns
        ],
        "expected_format": {
            "chunk_summary": "string",
            "chunk_tags": ["tag"],
            "chunk_importance": 0.0,
            "facts": [{"text": "string", "confidence": 0.7, "tags": ["tag"], "importance": 0.5}],
            "events": [{"text": "string", "impact": "low|med|high", "tags": ["tag"], "importance": 0.5}],
            "promises": [{"text": "string", "status": "open|kept|broken", "tags": ["tag"], "importance": 0.7}],
            "debts": [{"text": "string", "status": "open|paid", "tags": ["tag"], "importance": 0.7}],
            "relationship_delta": {"affinity_delta": 0, "notes": ["string"]},
            "summary": "string",
        },
    }
    try:
        out = planner(payload)
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    return _validate_patch_payload(out)


def _append_unique_by_hash(target: list[Any], item: Any, *, limit: int, hash_attr: str = "text_hash") -> bool:
    item_hash = str(getattr(item, hash_attr, "") or "").strip().casefold()
    if not item_hash:
        return False
    for row in target:
        row_hash = str(getattr(row, hash_attr, "") or "").strip().casefold()
        if row_hash and row_hash == item_hash:
            return False
    target.append(item)
    if len(target) > limit:
        del target[:-limit]
    return True


def _chunk_from_patch(turns: list[ShortTurn], patch: CompactionPatch, *, now_iso: str) -> MemoryChunk:
    ts_start = str(turns[0].ts if turns else now_iso)
    ts_end = str(turns[-1].ts if turns else now_iso)
    turn_ids: list[str] = []
    for row in turns:
        turn_id = str(row.turn_id or "").strip()
        if turn_id and turn_id not in turn_ids:
            turn_ids.append(turn_id)
    summary = clean_text(patch.chunk_summary, max_len=1000)
    return MemoryChunk(
        ts_range=[ts_start, ts_end],
        turn_ids=turn_ids,
        summary=summary,
        tags=[clean_tag(tag) for tag in patch.chunk_tags if clean_tag(tag)],
        importance=max(0.0, min(1.0, float(patch.chunk_importance))),
        text_hash=text_hash(summary),
    )


def _apply_patch_to_long(
    *,
    memory_long,
    patch: CompactionPatch,
    now_iso: str,
) -> list[str]:
    logs: list[str] = []

    for row in patch.facts:
        text = clean_text(row.text, max_len=420)
        if not text:
            continue
        item = MemoryFact(
            ts=now_iso,
            text=text,
            confidence=max(0.0, min(1.0, float(row.confidence))),
            tags=[clean_tag(tag) for tag in row.tags if clean_tag(tag)],
            importance=max(0.0, min(1.0, float(row.importance))),
            text_hash=text_hash(text),
        )
        if _append_unique_by_hash(memory_long.facts, item, limit=FACT_LIMIT):
            logs.append("fact+")

    for row in patch.events:
        text = clean_text(row.text, max_len=420)
        if not text:
            continue
        impact = str(row.impact or "low").strip().casefold()
        if impact not in {"low", "med", "high"}:
            impact = "low"
        item = MemoryEvent(
            ts=now_iso,
            text=text,
            impact=impact,
            tags=[clean_tag(tag) for tag in row.tags if clean_tag(tag)],
            importance=max(0.0, min(1.0, float(row.importance))),
            text_hash=text_hash(text),
        )
        if _append_unique_by_hash(memory_long.events, item, limit=EVENT_LIMIT):
            logs.append("event+")

    for row in patch.promises:
        text = clean_text(row.text, max_len=420)
        if not text:
            continue
        status = str(row.status or "open").strip().casefold()
        if status not in {"open", "kept", "broken"}:
            status = "open"
        item = MemoryPromise(
            ts=now_iso,
            text=text,
            status=status,
            tags=[clean_tag(tag) for tag in row.tags if clean_tag(tag)],
            importance=max(0.0, min(1.0, float(row.importance))),
            text_hash=text_hash(text),
        )
        if _append_unique_by_hash(memory_long.promises, item, limit=PROMISE_LIMIT):
            logs.append("promise+")

    for row in patch.debts:
        text = clean_text(row.text, max_len=420)
        if not text:
            continue
        status = str(row.status or "open").strip().casefold()
        if status not in {"open", "paid"}:
            status = "open"
        item = MemoryDebt(
            ts=now_iso,
            text=text,
            status=status,
            tags=[clean_tag(tag) for tag in row.tags if clean_tag(tag)],
            importance=max(0.0, min(1.0, float(row.importance))),
            text_hash=text_hash(text),
        )
        if _append_unique_by_hash(memory_long.debts, item, limit=DEBT_LIMIT):
            logs.append("debt+")

    delta = max(-5, min(5, int(patch.relationship_delta.affinity_delta)))
    rel = memory_long.relationships.player
    rel.affinity = max(-100, min(100, int(rel.affinity) + delta))
    for note in patch.relationship_delta.notes[:4]:
        text = clean_text(note, max_len=280)
        if not text:
            continue
        rel.notes.append(RelationshipNote(ts=now_iso, text=text))
    if len(rel.notes) > 300:
        del rel.notes[:-300]

    long_summary = clean_text(patch.summary, max_len=1200)
    if long_summary:
        memory_long.summary = LongSummary(ts=now_iso, text=long_summary)
        logs.append("summary~")
    return logs


def compact_npc_memory(
    memory: NpcMemory,
    *,
    ai_enabled: bool = True,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> CompactResult:
    logs: list[str] = []
    compacted = 0
    changed = False
    short_max = max(20, int(memory.stats.short_max))
    chunk_target = max(10, int(memory.stats.chunk_target_turns))
    retain_target = max(20, short_max - chunk_target)

    while len(memory.short) > short_max:
        turns = list(memory.short[:chunk_target])
        now_iso = utc_now_iso()
        patch = _extract_patch_with_ai(turns, planner=planner) if ai_enabled else None
        used_ai = bool(patch is not None)
        if patch is None:
            patch = _extract_patch_fallback(turns)
        chunk = _chunk_from_patch(turns, patch, now_iso=now_iso)
        if chunk.summary:
            if _append_unique_by_hash(memory.chunks, chunk, limit=CHUNK_LIMIT):
                logs.append("chunk+")
        logs.extend(_apply_patch_to_long(memory_long=memory.long, patch=patch, now_iso=now_iso))
        del memory.short[:chunk_target]
        memory.stats.last_compact_ts = now_iso
        compacted += 1
        changed = True
        logs.append("compaction:ai" if used_ai else "compaction:fallback")

    if len(memory.chunks) > CHUNK_LIMIT:
        del memory.chunks[:-CHUNK_LIMIT]
    if changed and len(memory.short) > retain_target:
        del memory.short[:-retain_target]
    return CompactResult(changed=changed, compacted_chunks=compacted, logs=logs)


def compact_world_memory(
    memory: WorldMemory,
    *,
    ai_enabled: bool = True,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> CompactResult:
    logs: list[str] = []
    compacted = 0
    changed = False
    short_max = max(20, int(memory.stats.short_max))
    chunk_target = max(10, int(memory.stats.chunk_target_turns))
    retain_target = max(20, short_max - chunk_target)

    while len(memory.short) > short_max:
        turns = list(memory.short[:chunk_target])
        now_iso = utc_now_iso()
        patch = _extract_patch_with_ai(turns, planner=planner) if ai_enabled else None
        used_ai = bool(patch is not None)
        if patch is None:
            patch = _extract_patch_fallback(turns)
        chunk = _chunk_from_patch(turns, patch, now_iso=now_iso)
        if chunk.summary:
            if _append_unique_by_hash(memory.chunks, chunk, limit=CHUNK_LIMIT):
                logs.append("chunk+")
        logs.extend(_apply_patch_to_long(memory_long=memory.long, patch=patch, now_iso=now_iso))
        del memory.short[:chunk_target]
        memory.stats.last_compact_ts = now_iso
        compacted += 1
        changed = True
        logs.append("compaction:ai" if used_ai else "compaction:fallback")

    if len(memory.chunks) > CHUNK_LIMIT:
        del memory.chunks[:-CHUNK_LIMIT]
    if changed and len(memory.short) > retain_target:
        del memory.short[:-retain_target]
    return CompactResult(changed=changed, compacted_chunks=compacted, logs=logs)


def log_compaction_result(prefix: str, result: CompactResult) -> None:
    if not result.changed:
        return
    LOG.info("%s compaction triggered: chunks=%s logs=%s", prefix, result.compacted_chunks, ",".join(result.logs[:12]))
