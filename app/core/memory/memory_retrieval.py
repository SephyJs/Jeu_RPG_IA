from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any

from .memory_models import NpcMemory, WorldMemory, clean_text


TOKEN_RE = re.compile(r"[a-zA-Z0-9_:-]{3,}")


def _safe_ts_to_epoch(ts: str) -> float:
    raw = str(ts or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _recency_score(ts: str, now_ts: float) -> float:
    epoch = _safe_ts_to_epoch(ts)
    if epoch <= 0 or now_ts <= 0:
        return 0.25
    age_h = max(0.0, (now_ts - epoch) / 3600.0)
    return max(0.0, min(1.0, math.exp(-age_h / 240.0)))


def _tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for token in TOKEN_RE.findall(str(text or "").casefold()):
        if len(token) >= 3:
            out.add(token)
    return out


def _overlap_score(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    if inter <= 0:
        return 0.0
    denom = max(1, len(a.union(b)))
    return float(inter) / float(denom)


@dataclass
class Candidate:
    source: str
    text: str
    ts: str
    tags: list[str]
    importance: float
    vector_sim: float
    kind: str


def _score_candidate(candidate: Candidate, *, query_tokens: set[str], now_ts: float) -> float:
    tags_score = _overlap_score(set(candidate.tags), query_tokens)
    recency = _recency_score(candidate.ts, now_ts)
    importance = max(0.0, min(1.0, float(candidate.importance)))
    vector_sim = max(0.0, min(1.0, float(candidate.vector_sim)))
    return (vector_sim * 0.6) + (tags_score * 0.2) + (recency * 0.1) + (importance * 0.1)


def _source_date(ts: str) -> str:
    raw = str(ts or "").strip()
    if not raw:
        return "unknown"
    return raw[:10]


def _build_long_candidates(memory: NpcMemory | WorldMemory | None, *, prefix: str) -> list[Candidate]:
    if memory is None:
        return []
    out: list[Candidate] = []
    for row in memory.long.facts:
        out.append(
            Candidate(
                source=f"[{prefix}fact]",
                text=clean_text(row.text, max_len=220),
                ts=str(row.ts),
                tags=[str(tag) for tag in row.tags],
                importance=float(row.importance),
                vector_sim=0.0,
                kind="fact",
            )
        )
    for row in memory.long.events:
        out.append(
            Candidate(
                source=f"[{prefix}event {row.impact}]",
                text=clean_text(row.text, max_len=220),
                ts=str(row.ts),
                tags=[str(tag) for tag in row.tags],
                importance=float(row.importance),
                vector_sim=0.0,
                kind="event",
            )
        )
    for row in memory.long.promises:
        out.append(
            Candidate(
                source=f"[{prefix}promise {row.status}]",
                text=clean_text(row.text, max_len=220),
                ts=str(row.ts),
                tags=[str(tag) for tag in row.tags],
                importance=float(row.importance),
                vector_sim=0.0,
                kind="promise",
            )
        )
    for row in memory.long.debts:
        out.append(
            Candidate(
                source=f"[{prefix}debt {row.status}]",
                text=clean_text(row.text, max_len=220),
                ts=str(row.ts),
                tags=[str(tag) for tag in row.tags],
                importance=float(row.importance),
                vector_sim=0.0,
                kind="debt",
            )
        )
    return out


def _build_short_lines(memory: NpcMemory | None, *, short_limit: int) -> list[str]:
    if memory is None:
        return []
    lines: list[str] = []
    for row in memory.short[-max(1, short_limit) :]:
        text = clean_text(row.text, max_len=180)
        if not text:
            continue
        lines.append(f"- [short {_source_date(row.ts)}] {text}")
    return lines


def _fallback_chunk_candidates(memory: NpcMemory | WorldMemory | None, *, prefix: str, query_tokens: set[str]) -> list[Candidate]:
    if memory is None:
        return []
    out: list[Candidate] = []
    for chunk in memory.chunks:
        text = clean_text(chunk.summary, max_len=220)
        if not text:
            continue
        chunk_tokens = _tokenize(text)
        overlap = _overlap_score(query_tokens, chunk_tokens)
        if overlap <= 0 and query_tokens:
            continue
        out.append(
            Candidate(
                source=f"[{prefix}chunk {_source_date(chunk.ts_range[1] if chunk.ts_range else '')}]",
                text=text,
                ts=str(chunk.ts_range[1] if chunk.ts_range else ""),
                tags=[str(tag) for tag in chunk.tags],
                importance=float(chunk.importance),
                vector_sim=overlap,
                kind="chunk",
            )
        )
    return out


def retrieve_context(
    *,
    npc_memory: NpcMemory | None,
    world_memory: WorldMemory | None,
    query: str,
    mode: str = "npc",
    vector_hits: list[dict[str, Any]] | None = None,
    short_limit: int = 8,
    long_limit: int = 12,
    retrieved_limit: int = 10,
) -> dict[str, list[str]]:
    clean_mode = str(mode or "npc").strip().casefold()
    if clean_mode not in {"npc", "world", "both"}:
        clean_mode = "npc"
    now_ts = datetime.now(timezone.utc).timestamp()
    query_tokens = _tokenize(str(query or ""))

    short_lines = _build_short_lines(npc_memory if clean_mode in {"npc", "both"} else None, short_limit=short_limit)

    long_candidates: list[Candidate] = []
    if clean_mode in {"npc", "both"}:
        long_candidates.extend(_build_long_candidates(npc_memory, prefix=""))
    if clean_mode in {"world", "both"}:
        long_candidates.extend(_build_long_candidates(world_memory, prefix="world/"))

    scored_long = []
    for candidate in long_candidates:
        candidate.vector_sim = _overlap_score(query_tokens, _tokenize(candidate.text))
        score = _score_candidate(candidate, query_tokens=query_tokens, now_ts=now_ts)
        scored_long.append((score, candidate))
    scored_long.sort(key=lambda row: row[0], reverse=True)
    long_lines = [f"- {cand.source} {cand.text}" for _, cand in scored_long[: max(1, long_limit)] if cand.text]

    retrieved_candidates: list[Candidate] = []
    if isinstance(vector_hits, list) and vector_hits:
        for hit in vector_hits:
            if not isinstance(hit, dict):
                continue
            text = clean_text(hit.get("text"), max_len=220)
            if not text:
                continue
            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
            source_kind = str(meta.get("kind") or "chunk")
            source_ts = str(meta.get("ts") or "")
            source = f"[{source_kind} {_source_date(source_ts)}]"
            tags = [str(tag) for tag in (meta.get("tags") if isinstance(meta.get("tags"), list) else [])]
            importance = float(meta.get("importance") or 0.5)
            score = float(hit.get("score") or 0.0)
            sim = max(0.0, min(1.0, (score + 1.0) / 2.0))
            retrieved_candidates.append(
                Candidate(
                    source=source,
                    text=text,
                    ts=source_ts,
                    tags=tags,
                    importance=importance,
                    vector_sim=sim,
                    kind=source_kind,
                )
            )
    else:
        if clean_mode in {"npc", "both"}:
            retrieved_candidates.extend(_fallback_chunk_candidates(npc_memory, prefix="", query_tokens=query_tokens))
        if clean_mode in {"world", "both"}:
            retrieved_candidates.extend(_fallback_chunk_candidates(world_memory, prefix="world/", query_tokens=query_tokens))

    scored_retrieved = []
    for cand in retrieved_candidates:
        score = _score_candidate(cand, query_tokens=query_tokens, now_ts=now_ts)
        scored_retrieved.append((score, cand))
    scored_retrieved.sort(key=lambda row: row[0], reverse=True)
    retrieved_lines = [f"- {cand.source} {cand.text}" for _, cand in scored_retrieved[: max(1, retrieved_limit)] if cand.text]

    combined = []
    for section in (long_lines, retrieved_lines):
        for line in section:
            if line not in combined:
                combined.append(line)
    return {
        "short": short_lines[: max(1, short_limit)],
        "long": long_lines[: max(1, long_limit)],
        "retrieved": retrieved_lines[: max(1, retrieved_limit)],
        "combined": combined[: max(1, long_limit + retrieved_limit)],
    }

