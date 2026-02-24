from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .memory_models import NpcMemory, WorldMemory


def safe_id(value: object, *, fallback: str = "unknown") -> str:
    text = re.sub(r"[^a-zA-Z0-9._:-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    if not text:
        return fallback
    return text[:180]


class MemoryStore:
    def __init__(
        self,
        *,
        memory_root: str = "data/memory",
        index_root: str = "data/memory_index",
    ) -> None:
        self.memory_root = Path(memory_root)
        self.index_root = Path(index_root)
        self.npc_memory_dir = self.memory_root / "npcs"
        self.npc_index_dir = self.index_root / "npcs"
        self.world_memory_path = self.memory_root / "world.json"
        self.world_index_path = self.index_root / "world.faiss"
        self.world_mapping_path = self.index_root / "world.jsonl"
        self.emb_cache_path = self.index_root / "emb_cache.jsonl"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.npc_memory_dir.mkdir(parents=True, exist_ok=True)
        self.npc_index_dir.mkdir(parents=True, exist_ok=True)
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.memory_root.mkdir(parents=True, exist_ok=True)

    def npc_memory_path(self, npc_id: str) -> Path:
        return self.npc_memory_dir / f"{safe_id(npc_id)}.json"

    def npc_index_path(self, npc_id: str) -> Path:
        return self.npc_index_dir / f"{safe_id(npc_id)}.faiss"

    def npc_mapping_path(self, npc_id: str) -> Path:
        return self.npc_index_dir / f"{safe_id(npc_id)}.jsonl"

    def list_npc_ids(self) -> list[str]:
        out: list[str] = []
        if not self.npc_memory_dir.exists():
            return out
        for path in sorted(self.npc_memory_dir.glob("*.json")):
            stem = str(path.stem).strip()
            if stem:
                out.append(stem)
        return out

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(raw, dict):
            return raw
        return None

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def load_npc_memory(self, npc_id: str) -> NpcMemory:
        clean_id = safe_id(npc_id)
        path = self.npc_memory_path(clean_id)
        payload = self._read_json(path)
        if not isinstance(payload, dict):
            mem = NpcMemory(npc_id=clean_id)
            self.save_npc_memory(mem)
            return mem
        payload["npc_id"] = clean_id
        try:
            return NpcMemory.model_validate(payload)
        except Exception:
            mem = NpcMemory(npc_id=clean_id)
            self.save_npc_memory(mem)
            return mem

    def save_npc_memory(self, memory: NpcMemory) -> None:
        clean_id = safe_id(memory.npc_id)
        memory.npc_id = clean_id
        self._write_json(self.npc_memory_path(clean_id), memory.model_dump())

    def load_world_memory(self) -> WorldMemory:
        payload = self._read_json(self.world_memory_path)
        if not isinstance(payload, dict):
            mem = WorldMemory()
            self.save_world_memory(mem)
            return mem
        try:
            return WorldMemory.model_validate(payload)
        except Exception:
            mem = WorldMemory()
            self.save_world_memory(mem)
            return mem

    def save_world_memory(self, memory: WorldMemory) -> None:
        self._write_json(self.world_memory_path, memory.model_dump())

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = str(raw or "").strip()
                if not line:
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict):
                    out.append(payload)
        except Exception:
            return []
        return out

    def write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        lines: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.append(json.dumps(row, ensure_ascii=False))
        self._atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))

