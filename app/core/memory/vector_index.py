from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np


LOG = logging.getLogger(__name__)


class VectorIndex:
    def __init__(
        self,
        *,
        dim: int = 0,
        prefer_faiss: bool = True,
    ) -> None:
        self.dim = max(0, int(dim))
        self.prefer_faiss = bool(prefer_faiss)
        self._faiss = None
        self._index = None
        self._vectors = np.zeros((0, self.dim), dtype=np.float32) if self.dim > 0 else np.zeros((0, 0), dtype=np.float32)
        self._mapping: list[dict[str, Any]] = []
        self._engine = "numpy"
        self._init_engine()

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def mapping(self) -> list[dict[str, Any]]:
        return list(self._mapping)

    def _init_engine(self) -> None:
        if not self.prefer_faiss:
            self._engine = "numpy"
            return
        try:
            import faiss  # type: ignore

            self._faiss = faiss
            self._engine = "faiss"
            if self.dim > 0:
                self._index = self._faiss.IndexFlatIP(self.dim)
        except Exception:
            self._faiss = None
            self._index = None
            self._engine = "numpy"

    def _ensure_dim(self, dim: int) -> None:
        if self.dim > 0:
            return
        self.dim = max(1, int(dim))
        self._vectors = np.zeros((0, self.dim), dtype=np.float32)
        if self._engine == "faiss" and self._faiss is not None:
            self._index = self._faiss.IndexFlatIP(self.dim)

    def _normalize(self, vector: list[float]) -> np.ndarray:
        arr = np.asarray(vector, dtype=np.float32)
        if arr.ndim != 1:
            arr = arr.reshape(-1)
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return arr

    def clear(self) -> None:
        self._mapping = []
        if self.dim <= 0:
            self._vectors = np.zeros((0, 0), dtype=np.float32)
        else:
            self._vectors = np.zeros((0, self.dim), dtype=np.float32)
        if self._engine == "faiss" and self._faiss is not None and self.dim > 0:
            self._index = self._faiss.IndexFlatIP(self.dim)
        else:
            self._index = None

    def add(self, record_id: str, text: str, metadata: dict[str, Any] | None, vector: list[float]) -> int | None:
        if not isinstance(vector, list) or not vector:
            return None
        arr = self._normalize(vector)
        if arr.size <= 0:
            return None
        self._ensure_dim(int(arr.shape[0]))
        if int(arr.shape[0]) != self.dim:
            return None

        vector_id = len(self._mapping)
        row = {
            "vector_id": vector_id,
            "record_id": str(record_id or "").strip(),
            "text": str(text or "").strip(),
            "meta": metadata if isinstance(metadata, dict) else {},
        }
        self._mapping.append(row)

        if self._engine == "faiss" and self._index is not None:
            self._index.add(arr.reshape(1, -1))
        else:
            if self._vectors.size == 0:
                self._vectors = arr.reshape(1, -1).astype(np.float32)
            else:
                self._vectors = np.vstack([self._vectors, arr.reshape(1, -1).astype(np.float32)])
        return vector_id

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 10,
        filter_meta: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(query_vector, list) or not query_vector:
            return []
        if self.dim <= 0 or not self._mapping:
            return []

        query = self._normalize(query_vector)
        if int(query.shape[0]) != self.dim:
            return []

        candidates: list[tuple[int, float]] = []
        limit = max(1, int(top_k))
        oversample = min(max(limit * 4, 20), max(20, len(self._mapping)))

        if self._engine == "faiss" and self._index is not None:
            scores, indices = self._index.search(query.reshape(1, -1).astype(np.float32), oversample)
            for pos, score in zip(indices[0].tolist(), scores[0].tolist()):
                if int(pos) < 0:
                    continue
                candidates.append((int(pos), float(score)))
        else:
            if self._vectors.size <= 0:
                return []
            sims = self._vectors @ query.astype(np.float32)
            if sims.size <= 0:
                return []
            order = np.argsort(sims)[::-1]
            for idx in order[:oversample]:
                candidates.append((int(idx), float(sims[idx])))

        def _meta_match(meta: dict[str, Any], filters: dict[str, object]) -> bool:
            for key, expected in filters.items():
                if str(meta.get(str(key), "")).casefold() != str(expected or "").casefold():
                    return False
            return True

        hits: list[dict[str, Any]] = []
        for idx, score in candidates:
            if idx < 0 or idx >= len(self._mapping):
                continue
            row = self._mapping[idx]
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            if isinstance(filter_meta, dict) and filter_meta and not _meta_match(meta, filter_meta):
                continue
            hits.append(
                {
                    "vector_id": int(row.get("vector_id") or idx),
                    "record_id": str(row.get("record_id") or ""),
                    "text": str(row.get("text") or ""),
                    "meta": meta,
                    "score": float(score),
                }
            )
            if len(hits) >= limit:
                break
        return hits

    def persist(self, *, index_path: Path, mapping_path: Path) -> None:
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.parent.mkdir(parents=True, exist_ok=True)

        mapping_rows: list[str] = []
        for row in self._mapping:
            mapping_rows.append(json.dumps(row, ensure_ascii=False))
        mapping_text = "\n".join(mapping_rows) + ("\n" if mapping_rows else "")
        mapping_path.write_text(mapping_text, encoding="utf-8")

        if self.dim <= 0:
            index_path.write_bytes(b"")
            return

        if self._engine == "faiss" and self._index is not None and self._faiss is not None:
            self._faiss.write_index(self._index, str(index_path))
            return

        arr = self._vectors.astype(np.float32) if self._vectors.size > 0 else np.zeros((0, self.dim), dtype=np.float32)
        with index_path.open("wb") as fh:
            np.save(fh, arr, allow_pickle=False)

    def load(self, *, index_path: Path, mapping_path: Path) -> None:
        self.clear()
        rows: list[dict[str, Any]] = []
        if mapping_path.exists():
            try:
                for raw in mapping_path.read_text(encoding="utf-8").splitlines():
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        rows.append(payload)
            except Exception:
                rows = []

        self._mapping = rows
        if not index_path.exists():
            return

        if self._engine == "faiss" and self._faiss is not None:
            try:
                index = self._faiss.read_index(str(index_path))
                self._index = index
                self.dim = int(index.d)
                self._vectors = np.zeros((0, self.dim), dtype=np.float32)
                return
            except Exception:
                self._index = None

        try:
            with index_path.open("rb") as fh:
                arr = np.load(fh, allow_pickle=False)
            if isinstance(arr, np.ndarray):
                arr = arr.astype(np.float32)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                self._vectors = arr
                self.dim = int(arr.shape[1]) if arr.ndim == 2 and arr.size > 0 else int(self.dim or 0)
        except Exception:
            self._vectors = np.zeros((0, self.dim), dtype=np.float32) if self.dim > 0 else np.zeros((0, 0), dtype=np.float32)

    def rebuild_from_records(
        self,
        *,
        records: list[dict[str, Any]],
        embed_texts,
    ) -> int:
        self.clear()
        if not records:
            return 0

        texts = [str(row.get("text") or "").strip() for row in records]
        vectors = embed_texts(texts)
        total = 0
        for idx, record in enumerate(records):
            vec = vectors[idx] if idx < len(vectors) and isinstance(vectors[idx], list) else []
            if not vec:
                continue
            record_id = str(record.get("record_id") or "")
            text = str(record.get("text") or "")
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            if self.add(record_id, text, meta, vec) is not None:
                total += 1
        return total

