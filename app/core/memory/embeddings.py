from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from .memory_models import text_hash


LOG = logging.getLogger(__name__)


class EmbeddingProvider:
    def __init__(
        self,
        *,
        cache_path: str = "data/memory_index/emb_cache.jsonl",
        ollama_base_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "nomic-embed-text",
    ) -> None:
        self.cache_path = Path(cache_path)
        self.ollama_base_url = str(ollama_base_url).rstrip("/")
        self.ollama_model = str(ollama_model or "nomic-embed-text").strip()
        self._mode: str | None = None
        self._sentence_model: Any = None
        self._cache: dict[str, list[float]] = {}
        self._cache_dirty = False
        self._load_cache()

    @property
    def mode(self) -> str:
        if self._mode is None:
            self._mode = self._detect_mode()
        return self._mode

    def enabled(self) -> bool:
        return self.mode in {"ollama", "sentence"}

    def _detect_mode(self) -> str:
        forced = str(os.getenv("MEMORY_EMBED_MODE", "")).strip().casefold()
        if forced in {"off", "none", "disabled"}:
            return "disabled"
        if forced in {"ollama", "sentence"}:
            return forced
        if self._ollama_is_available():
            return "ollama"
        if self._sentence_available():
            return "sentence"
        return "disabled"

    def _ollama_is_available(self) -> bool:
        try:
            with httpx.Client(timeout=0.9) as client:
                res = client.get(f"{self.ollama_base_url}/api/tags")
            return bool(200 <= int(res.status_code) < 500)
        except Exception:
            return False

    def _sentence_available(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            if self._sentence_model is None:
                self._sentence_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            return True
        except Exception:
            return False

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                raw = str(line or "").strip()
                if not raw:
                    continue
                row = json.loads(raw)
                if not isinstance(row, dict):
                    continue
                key = str(row.get("text_hash") or "").strip().casefold()
                vector = row.get("vector")
                if not key or not isinstance(vector, list):
                    continue
                parsed = [float(x) for x in vector]
                if parsed:
                    self._cache[key] = parsed
        except Exception:
            self._cache = {}

    def flush_cache(self) -> None:
        if not self._cache_dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"text_hash": key, "vector": vec} for key, vec in self._cache.items()]
        rows.sort(key=lambda row: str(row.get("text_hash") or ""))
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if content:
            content += "\n"
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self.cache_path)
        self._cache_dirty = False

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        if not vector:
            return []
        arr = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return [float(x) for x in arr.tolist()]

    def _embed_with_ollama(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=8.0) as client:
                payload = {"model": self.ollama_model, "input": texts}
                res = client.post(f"{self.ollama_base_url}/api/embed", json=payload)
                if 200 <= int(res.status_code) < 300:
                    data = res.json()
                    embeds = data.get("embeddings") if isinstance(data, dict) else None
                    if isinstance(embeds, list):
                        out: list[list[float]] = []
                        for row in embeds:
                            if isinstance(row, list):
                                out.append(self._normalize_vector([float(x) for x in row]))
                        if len(out) == len(texts):
                            return out
            out_rows: list[list[float]] = []
            with httpx.Client(timeout=8.0) as client:
                for text in texts:
                    payload = {"model": self.ollama_model, "prompt": text}
                    res = client.post(f"{self.ollama_base_url}/api/embeddings", json=payload)
                    res.raise_for_status()
                    data = res.json()
                    vector = data.get("embedding") if isinstance(data, dict) else None
                    if not isinstance(vector, list):
                        out_rows.append([])
                    else:
                        out_rows.append(self._normalize_vector([float(x) for x in vector]))
            return out_rows
        except Exception as exc:
            LOG.warning("memory embeddings: ollama fallback (%s)", exc)
            return []

    def _embed_with_sentence(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            if self._sentence_model is None:
                from sentence_transformers import SentenceTransformer  # type: ignore

                self._sentence_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            raw = self._sentence_model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
            out: list[list[float]] = []
            for row in raw:
                vec = [float(x) for x in row]
                out.append(self._normalize_vector(vec))
            return out
        except Exception as exc:
            LOG.warning("memory embeddings: sentence-transformers fallback (%s)", exc)
            return []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        clean_texts = [str(row or "").strip() for row in texts]
        hashes = [text_hash(text) for text in clean_texts]
        out: list[list[float] | None] = [None] * len(clean_texts)

        missing_indexes: list[int] = []
        missing_texts: list[str] = []
        for idx, key in enumerate(hashes):
            cached = self._cache.get(key)
            if isinstance(cached, list) and cached:
                out[idx] = list(cached)
            else:
                missing_indexes.append(idx)
                missing_texts.append(clean_texts[idx])

        if missing_texts:
            generated: list[list[float]] = []
            if self.mode == "ollama":
                generated = self._embed_with_ollama(missing_texts)
                if not generated and self._sentence_available():
                    self._mode = "sentence"
                    generated = self._embed_with_sentence(missing_texts)
                elif not generated:
                    self._mode = "disabled"
            elif self.mode == "sentence":
                generated = self._embed_with_sentence(missing_texts)
                if not generated and self._ollama_is_available():
                    self._mode = "ollama"
                    generated = self._embed_with_ollama(missing_texts)
                elif not generated:
                    self._mode = "disabled"

            if not generated:
                generated = [[] for _ in missing_texts]

            for local_idx, vec in enumerate(generated):
                source_idx = missing_indexes[local_idx]
                normalized = self._normalize_vector(vec)
                out[source_idx] = normalized
                key = hashes[source_idx]
                if normalized:
                    self._cache[key] = normalized
                    self._cache_dirty = True

        rows = [row if isinstance(row, list) else [] for row in out]
        if self._cache_dirty:
            self.flush_cache()
        return rows

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        if not vectors:
            return []
        return vectors[0]
