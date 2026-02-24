from __future__ import annotations

import json
from pathlib import Path

from app.core.memory.embeddings import EmbeddingProvider
from app.core.memory.memory_compactor import CompactionPatch, PatchItem, _apply_patch_to_long, compact_npc_memory
from app.core.memory.memory_models import LongMemory, NpcMemory, ShortTurn, WorldMemory
from app.core.memory.memory_models import MemoryFact
from app.core.memory.memory_retrieval import retrieve_context
from app.core.memory.memory_service import MemoryService
from app.core.memory.memory_store import MemoryStore
from app.core.memory.migration import bootstrap_from_existing_history
from app.core.memory.vector_index import VectorIndex


def test_compaction_fallback_creates_chunk_and_trims_short() -> None:
    memory = NpcMemory(npc_id="tester")
    memory.stats.short_max = 20
    memory.stats.chunk_target_turns = 10
    for i in range(42):
        memory.short.append(
            ShortTurn(
                role="player" if i % 2 == 0 else "npc",
                text=f"Ligne {i} promets mission {i}",
                turn_id=f"turn_{i}",
            )
        )

    result = compact_npc_memory(memory, ai_enabled=False)

    assert result.changed is True
    assert result.compacted_chunks >= 1
    assert len(memory.short) <= 20
    assert len(memory.chunks) >= 1
    assert memory.long.facts or memory.long.events or memory.long.promises


def test_apply_patch_dedup() -> None:
    long_memory = LongMemory()
    patch = CompactionPatch(
        chunk_summary="Resume",
        facts=[PatchItem(text="Le joueur a jure fidelite.", importance=0.7)],
    )
    _apply_patch_to_long(memory_long=long_memory, patch=patch, now_iso="2026-02-24T10:00:00+00:00")
    _apply_patch_to_long(memory_long=long_memory, patch=patch, now_iso="2026-02-24T10:01:00+00:00")
    assert len(long_memory.facts) == 1


def test_vector_index_add_search_persist_reload(tmp_path: Path) -> None:
    idx = VectorIndex(prefer_faiss=False)
    idx.add("chunk:1", "combat au pont", {"kind": "chunk"}, [1.0, 0.0, 0.0])
    idx.add("chunk:2", "commerce au marche", {"kind": "chunk"}, [0.0, 1.0, 0.0])

    hits = idx.search([1.0, 0.0, 0.0], top_k=1)
    assert hits
    assert hits[0]["record_id"] == "chunk:1"

    index_path = tmp_path / "npc.faiss"
    mapping_path = tmp_path / "npc.jsonl"
    idx.persist(index_path=index_path, mapping_path=mapping_path)

    loaded = VectorIndex(prefer_faiss=False)
    loaded.load(index_path=index_path, mapping_path=mapping_path)
    hits2 = loaded.search([1.0, 0.0, 0.0], top_k=1)
    assert hits2
    assert hits2[0]["record_id"] == "chunk:1"


def test_retrieve_context_limits_output() -> None:
    memory = NpcMemory(npc_id="npc")
    world = WorldMemory()
    for i in range(30):
        memory.short.append(ShortTurn(role="player", text=f"Message {i} mission commerce", turn_id=f"t{i}"))
    for i in range(20):
        memory.long.facts.append(
            MemoryFact(
                id=f"f{i}",
                ts="2026-02-24T10:00:00+00:00",
                text=f"Fait {i} sur la mission",
                confidence=0.7,
                tags=["quest"],
                importance=0.6,
                text_hash=f"hf{i}",
            )
        )

    out = retrieve_context(
        npc_memory=memory,
        world_memory=world,
        query="mission commerce",
        mode="npc",
        short_limit=8,
        long_limit=12,
        retrieved_limit=10,
    )
    assert len(out["short"]) <= 8
    assert len(out["long"]) <= 12
    assert len(out["retrieved"]) <= 10


def test_bootstrap_from_existing_history(tmp_path: Path) -> None:
    saves_root = tmp_path / "saves"
    profile_dir = saves_root / "profiles" / "alice"
    profile_dir.mkdir(parents=True, exist_ok=True)
    slot_path = profile_dir / "slot_1.json"
    slot_payload = {
        "version": 2,
        "state": {
            "conversation_short_term": {
                "marchande_city": [
                    {
                        "at": "2026-02-20T10:00:00+00:00",
                        "speaker": "Joueur",
                        "role": "player",
                        "text": "Je te promets de revenir.",
                    },
                    {
                        "at": "2026-02-20T10:01:00+00:00",
                        "speaker": "Marchande",
                        "role": "npc",
                        "text": "N'oublie pas ta dette.",
                    },
                ]
            },
            "conversation_long_term": {
                "marchande_city": [
                    {
                        "at": "2026-02-20T10:02:00+00:00",
                        "summary": "Le joueur promet de revenir.",
                        "kind": "promise",
                        "importance": 4,
                    }
                ]
            },
            "conversation_global_long_term": [
                {
                    "at": "2026-02-20T10:03:00+00:00",
                    "summary": "Emeute au marche central.",
                    "kind": "event",
                    "importance": 4,
                }
            ],
        },
    }
    slot_path.write_text(json.dumps(slot_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    store = MemoryStore(memory_root=str(tmp_path / "data" / "memory"), index_root=str(tmp_path / "data" / "memory_index"))
    service = MemoryService(store=store, embeddings=EmbeddingProvider(cache_path=str(tmp_path / "emb_cache.jsonl")))
    report = bootstrap_from_existing_history(service=service, saves_root=str(saves_root))

    assert int(report["slots"]) == 1
    assert int(report["npcs_touched"]) >= 1
    assert int(report["indexes_rebuilt"]) >= 1
    npc_files = list((tmp_path / "data" / "memory" / "npcs").glob("*.json"))
    assert npc_files
