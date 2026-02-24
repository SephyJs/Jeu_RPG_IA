from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .memory_compactor import compact_npc_memory, compact_world_memory
from .memory_models import (
    MemoryDebt,
    MemoryEvent,
    MemoryFact,
    MemoryPromise,
    ShortTurn,
    clean_tag,
    clean_text,
    text_hash,
    utc_now_iso,
)
from .memory_service import MemoryService
from .memory_store import safe_id


def _iter_save_state_payloads(saves_root: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    out: list[tuple[str, Path, dict[str, Any]]] = []
    if not saves_root.exists():
        return out

    profile_root = saves_root / "profiles"
    if profile_root.exists():
        for profile_dir in profile_root.iterdir():
            if not profile_dir.is_dir():
                continue
            profile_key = safe_id(profile_dir.name, fallback="default")
            for slot_path in sorted(profile_dir.glob("slot_*.json")):
                try:
                    payload = json.loads(slot_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                state = payload.get("state") if isinstance(payload, dict) else None
                if isinstance(state, dict):
                    out.append((profile_key, slot_path, state))

    for slot_path in sorted(saves_root.glob("slot_*.json")):
        try:
            payload = json.loads(slot_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        state = payload.get("state") if isinstance(payload, dict) else None
        if isinstance(state, dict):
            out.append(("default", slot_path, state))
    return out


def _entry_ts(raw: dict[str, Any]) -> str:
    ts = str(raw.get("at") or raw.get("ts") or "").strip()
    return ts or utc_now_iso()


def _entry_tags(raw: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in ("kind", "role"):
        clean = clean_tag(raw.get(key))
        if clean and clean not in tags:
            tags.append(clean)
    scene = clean_tag(raw.get("scene_title"))
    if scene and scene not in tags:
        tags.append(scene)
    return tags[:12]


def bootstrap_from_existing_history(
    *,
    service: MemoryService,
    saves_root: str = "saves",
) -> dict[str, int]:
    root = Path(saves_root)
    slots = _iter_save_state_payloads(root)
    npc_cache: dict[str, Any] = {}
    world = service.load_world_memory()
    stats = {
        "slots": 0,
        "npcs_touched": 0,
        "short_added": 0,
        "long_added": 0,
        "world_added": 0,
        "indexes_rebuilt": 0,
    }

    def _npc_mem(profile_key: str, npc_key: str):
        scoped = service.scoped_npc_id(profile_key=profile_key, npc_id=npc_key)
        if scoped not in npc_cache:
            npc_cache[scoped] = service.load_npc_memory(profile_key=profile_key, npc_id=npc_key)
        return npc_cache[scoped]

    for profile_key, _slot_path, state in slots:
        stats["slots"] += 1
        short = state.get("conversation_short_term")
        if isinstance(short, dict):
            for raw_npc_key, entries in short.items():
                npc_key = safe_id(raw_npc_key, fallback="unknown")
                if not isinstance(entries, list):
                    continue
                mem = _npc_mem(profile_key, npc_key)
                for row in entries:
                    if not isinstance(row, dict):
                        continue
                    text = clean_text(row.get("text"), max_len=460)
                    if not text:
                        continue
                    role = str(row.get("role") or "npc").strip().casefold()
                    if role not in {"player", "npc", "system", "narration"}:
                        role = "npc"
                    mem.short.append(
                        ShortTurn(
                            ts=_entry_ts(row),
                            role=role,  # type: ignore[arg-type]
                            text=text,
                            tags=_entry_tags(row),
                            importance=0.45,
                            turn_id=str(row.get("turn_id") or row.get("at") or text_hash(text))[:80],
                        )
                    )
                    stats["short_added"] += 1

        long = state.get("conversation_long_term")
        if isinstance(long, dict):
            for raw_npc_key, entries in long.items():
                npc_key = safe_id(raw_npc_key, fallback="unknown")
                if not isinstance(entries, list):
                    continue
                mem = _npc_mem(profile_key, npc_key)
                for row in entries:
                    if not isinstance(row, dict):
                        continue
                    text = clean_text(row.get("summary"), max_len=420)
                    if not text:
                        continue
                    ts = _entry_ts(row)
                    tags = _entry_tags(row)
                    kind = str(row.get("kind") or "general").strip().casefold()
                    h = text_hash(text)
                    added = False
                    if kind == "promise":
                        if not any(str(x.text_hash or "") == h for x in mem.long.promises):
                            mem.long.promises.append(
                                MemoryPromise(
                                    ts=ts,
                                    text=text,
                                    status="open",
                                    tags=tags,
                                    importance=0.7,
                                    text_hash=h,
                                )
                            )
                            added = True
                    elif kind == "debt":
                        if not any(str(x.text_hash or "") == h for x in mem.long.debts):
                            mem.long.debts.append(
                                MemoryDebt(
                                    ts=ts,
                                    text=text,
                                    status="open",
                                    tags=tags,
                                    importance=0.7,
                                    text_hash=h,
                                )
                            )
                            added = True
                    elif kind in {"event", "quest", "combat", "trade", "travel", "training"}:
                        if not any(str(x.text_hash or "") == h for x in mem.long.events):
                            impact = "med" if kind in {"quest", "combat", "event"} else "low"
                            mem.long.events.append(
                                MemoryEvent(
                                    ts=ts,
                                    text=text,
                                    impact=impact,  # type: ignore[arg-type]
                                    tags=tags,
                                    importance=0.62,
                                    text_hash=h,
                                )
                            )
                            added = True
                    else:
                        if not any(str(x.text_hash or "") == h for x in mem.long.facts):
                            mem.long.facts.append(
                                MemoryFact(
                                    ts=ts,
                                    text=text,
                                    confidence=0.65,
                                    tags=tags,
                                    importance=0.52,
                                    text_hash=h,
                                )
                            )
                            added = True
                    if added:
                        stats["long_added"] += 1

        world_rows = state.get("conversation_global_long_term")
        if isinstance(world_rows, list):
            for row in world_rows:
                if not isinstance(row, dict):
                    continue
                text = clean_text(row.get("summary"), max_len=420)
                if not text:
                    continue
                h = text_hash(text)
                if any(str(x.text_hash or "") == h for x in world.long.events):
                    continue
                world.long.events.append(
                    MemoryEvent(
                        ts=_entry_ts(row),
                        text=text,
                        impact="med",
                        tags=_entry_tags(row),
                        importance=0.55,
                        text_hash=h,
                    )
                )
                stats["world_added"] += 1

    for scoped_id, memory in npc_cache.items():
        memory.long.facts = memory.long.facts[-500:]
        memory.long.events = memory.long.events[-500:]
        memory.long.promises = memory.long.promises[-100:]
        memory.long.debts = memory.long.debts[-100:]
        memory.long.summary.ts = utc_now_iso()
        if not memory.long.summary.text or memory.long.summary.text == "(aucun resume)":
            memory.long.summary.text = clean_text(
                " ".join([row.text for row in memory.long.events[-3:] + memory.long.facts[-3:]]),
                max_len=900,
            ) or "(aucun resume)"
        compact_npc_memory(memory, ai_enabled=False)
        service.save_npc_memory(memory)
        stats["npcs_touched"] += 1
        profile_key = scoped_id.split("__", 1)[0] if "__" in scoped_id else "default"
        npc_key = service.base_npc_id(scoped_id)
        try:
            service.rebuild_npc_index(profile_key=profile_key, npc_id=npc_key)
            stats["indexes_rebuilt"] += 1
        except Exception:
            continue

    compact_world_memory(world, ai_enabled=False)
    service.save_world_memory(world)
    try:
        service.rebuild_world_index()
        stats["indexes_rebuilt"] += 1
    except Exception:
        pass
    return stats

