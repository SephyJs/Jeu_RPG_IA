from __future__ import annotations

from dataclasses import dataclass

from .memory_service import MemoryService, get_memory_service


@dataclass
class MemoryAdmin:
    service: MemoryService

    @classmethod
    def from_default(cls) -> "MemoryAdmin":
        return cls(service=get_memory_service())

    def list_npcs(self, *, profile_key: str | None = None) -> list[str]:
        return self.service.list_scoped_npc_ids(profile_key=profile_key)

    def read_npc(self, *, profile_key: str | None, npc_id: str) -> dict:
        memory = self.service.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
        return memory.model_dump()

    def read_world(self) -> dict:
        memory = self.service.load_world_memory()
        return memory.model_dump()

    def compact_npc_now(self, *, profile_key: str | None, npc_id: str) -> dict:
        memory = self.service.load_npc_memory(profile_key=profile_key, npc_id=npc_id)
        from .memory_compactor import compact_npc_memory  # local import to avoid cycles

        before_short = len(memory.short)
        result = compact_npc_memory(memory, ai_enabled=False)
        self.service.save_npc_memory(memory)
        added = self.service.rebuild_npc_index(profile_key=profile_key, npc_id=npc_id)
        return {
            "changed": bool(result.changed),
            "compacted_chunks": int(result.compacted_chunks),
            "short_before": int(before_short),
            "short_after": len(memory.short),
            "index_records": int(added),
        }

    def rebuild_npc_index(self, *, profile_key: str | None, npc_id: str) -> int:
        return self.service.rebuild_npc_index(profile_key=profile_key, npc_id=npc_id)

    def rebuild_world_index(self) -> int:
        return self.service.rebuild_world_index()

    def purge_short(self, *, profile_key: str | None, npc_id: str) -> bool:
        return self.service.purge_short(profile_key=profile_key, npc_id=npc_id)

