# Memory System (Hybrid, Scalable, Local)

## Objectif
Memoire hybride pour RPG:
- `short` (fenetre glissante)
- `long` canonique structuree (`facts/events/promises/debts/relationships/summary`)
- retrieval semantique via index vectoriel local (FAISS si dispo, fallback numpy + mots-cles)

## Arborescence
- `data/memory/npcs/{profile}__{npc}.json`
- `data/memory/world.json`
- `data/memory_index/npcs/{profile}__{npc}.faiss`
- `data/memory_index/npcs/{profile}__{npc}.jsonl`
- `data/memory_index/world.faiss`
- `data/memory_index/world.jsonl`
- `data/memory_index/emb_cache.jsonl`

## Modules
- `app/core/memory/memory_models.py`:
  schemas Pydantic versionnes (`schema_version=2`).
- `app/core/memory/memory_store.py`:
  persistance JSON/JSONL + write atomique.
- `app/core/memory/memory_compactor.py`:
  compaction short->chunks + patch long (fallback regles, extension IA possible).
- `app/core/memory/embeddings.py`:
  embeddings local:
  1) Ollama embeddings si dispo
  2) sentence-transformers si dispo
  3) mode desactive sinon.
- `app/core/memory/vector_index.py`:
  index vectoriel (FAISS si dispo, sinon numpy), mapping `vector_id -> record`.
- `app/core/memory/memory_retrieval.py`:
  retrieval hybride + re-score:
  `score = vector*0.6 + tags*0.2 + recency*0.1 + importance*0.1`.
- `app/core/memory/memory_service.py`:
  service central (append/remember/compact/retrieve/rebuild index).
- `app/core/memory/memory_admin.py`:
  facade admin (inspect, compact, rebuild, purge).
- `app/core/memory/migration.py`:
  bootstrap depuis historique de sauvegardes existant.

## Integration actuelle
- Ecriture memoire:
  `app/gamemaster/conversation_memory.py` delegue vers `MemoryService`.
- Injection prompts:
  `conversation_short_term`, `conversation_long_term`, `conversation_global_memory`,
  `conversation_retrieved_memory` sont prepares avant generation.
  Limites appliquees: `8 short + 12 long + 10 retrieved`.
- Rappel anti-hallucination ajoute dans les prompts.
- UI admin disponible sur `/memory-admin`.

## Commandes
- Rebuild index:
```bash
python -m tools.rebuild_memory_index
```
- Bootstrap + rebuild:
```bash
python -m tools.rebuild_memory_index --bootstrap --saves-root saves
```
- Bootstrap seul:
```bash
python -m tools.bootstrap_memory_from_history --saves-root saves
```
- Verification coherence:
```bash
python -m tools.check_memory_keys
```

## Notes de robustesse
- Aucun crash si embeddings indisponibles: fallback retrieval mots-cles.
- Compaction fallback sans IA active.
- Ecritures JSON atomiques pour les payloads principaux.
- Les donnees legacy (`conversation_*` dans save) restent maintenues pour compatibilite.
