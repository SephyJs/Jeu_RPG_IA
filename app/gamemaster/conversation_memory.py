from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from app.core.memory import get_memory_service


SHORT_TERM_MAX_ITEMS = 60
LONG_TERM_PER_NPC_MAX_ITEMS = 500
LONG_TERM_GLOBAL_MAX_ITEMS = 500
MEMORY_KIND_ALLOWED = {
    "general",
    "identity",
    "quest",
    "trade",
    "combat",
    "training",
    "travel",
    "system",
    "secret",
    "mensonge",
    "event",
    "promise",
    "debt",
}
_NO_NPC_KEY = "__no_npc__"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: object, *, max_len: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 3)].rstrip() + "..."


def _clean_key(value: object) -> str:
    key = re.sub(r"\s+", " ", str(value or "")).strip()
    return key[:160] if key else _NO_NPC_KEY


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _memory_profile_key(state: Any) -> str:
    gm_state = getattr(state, "gm_state", None)
    if not isinstance(gm_state, dict):
        return "default"
    explicit = str(
        gm_state.get("memory_profile_key")
        or gm_state.get("profile_key")
        or gm_state.get("save_profile_key")
        or ""
    ).strip()
    if explicit:
        return explicit
    player = getattr(state, "player", None)
    maybe_name = str(getattr(player, "name", "") or "").strip()
    if maybe_name:
        return maybe_name
    return "default"


def _classify_kind(text: str) -> str:
    t = str(text or "").casefold()
    if any(k in t for k in ("promis", "promets", "je vais", "je ferai")):
        return "promise"
    if any(k in t for k in ("dette", "je te dois", "rembours", "payer")):
        return "debt"
    if any(k in t for k in ("mensonge", "menti", "mentir", "fausse piste")):
        return "mensonge"
    if any(k in t for k in ("secret", "cache", "cach", "rival", "preuve")):
        return "secret"
    if any(k in t for k in ("quete", "mission", "objectif", "contrat")):
        return "quest"
    if any(k in t for k in ("acheter", "vendre", "prix", "or", "marchand", "echange", "donner")):
        return "trade"
    if any(k in t for k in ("entrainer", "competence", "sort", "forge", "apprendre", "niveau")):
        return "training"
    if any(k in t for k in ("attaque", "combat", "frappe", "defense", "donjon", "monstre")):
        return "combat"
    if any(k in t for k in ("route", "voyage", "ville", "ruelle", "aller vers", "deplacement")):
        return "travel"
    if any(k in t for k in ("incident", "evenement", "emeute", "intrigue", "alerte")):
        return "event"
    if any(k in t for k in ("je m'appelle", "mon nom", "metier", "fonction", "identite", "qui es-tu")):
        return "identity"
    return "general"


def _estimate_importance_legacy(text: str, kind: str) -> int:
    score = 1
    if kind in {"identity", "quest", "trade", "combat", "training", "event", "promise", "debt"}:
        score += 1
    t = str(text or "").casefold()
    if any(k in t for k in ("promis", "important", "souviens", "urgent", "secret", "reviens", "dettes")):
        score += 1
    if any(k in t for k in ("quete", "mission", "acheter", "vendre", "donjon", "metier", "identite")):
        score += 1
    return max(1, min(5, score))


def _sanitize_short_entry(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = _clean_text(raw.get("text"), max_len=260)
    if not text:
        return None
    speaker = _clean_text(raw.get("speaker"), max_len=80) or "Inconnu"
    role = _clean_text(raw.get("role"), max_len=24).casefold()
    if role not in {"player", "npc", "system", "narration"}:
        role = "npc"
    return {
        "at": _clean_text(raw.get("at"), max_len=40),
        "speaker": speaker,
        "role": role,
        "text": text,
        "scene_id": _clean_text(raw.get("scene_id"), max_len=120),
        "scene_title": _clean_text(raw.get("scene_title"), max_len=120),
        "world_time_minutes": max(0, _safe_int(raw.get("world_time_minutes"), 0)),
    }


def _sanitize_long_entry(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    summary = _clean_text(raw.get("summary"), max_len=360)
    if not summary:
        return None
    kind = _clean_text(raw.get("kind"), max_len=24).casefold()
    if kind not in MEMORY_KIND_ALLOWED:
        kind = "general"
    return {
        "at": _clean_text(raw.get("at"), max_len=40),
        "summary": summary,
        "kind": kind,
        "importance": max(1, min(5, _safe_int(raw.get("importance"), 1))),
        "scene_id": _clean_text(raw.get("scene_id"), max_len=120),
        "scene_title": _clean_text(raw.get("scene_title"), max_len=120),
        "world_time_minutes": max(0, _safe_int(raw.get("world_time_minutes"), 0)),
        "npc_name": _clean_text(raw.get("npc_name"), max_len=80),
        "npc_key": _clean_key(raw.get("npc_key")),
    }


def sanitize_short_term_payload(raw: object) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not isinstance(raw, dict):
        return out
    for key, entries in raw.items():
        if not isinstance(key, str) or not isinstance(entries, list):
            continue
        safe_key = _clean_key(key)
        safe_entries: list[dict] = []
        for item in entries:
            sanitized = _sanitize_short_entry(item)
            if sanitized is not None:
                safe_entries.append(sanitized)
        if safe_entries:
            out[safe_key] = safe_entries[-SHORT_TERM_MAX_ITEMS:]
    return out


def sanitize_long_term_payload(raw: object) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not isinstance(raw, dict):
        return out
    for key, entries in raw.items():
        if not isinstance(key, str) or not isinstance(entries, list):
            continue
        safe_key = _clean_key(key)
        safe_entries: list[dict] = []
        for item in entries:
            sanitized = _sanitize_long_entry(item)
            if sanitized is not None:
                safe_entries.append(sanitized)
        if safe_entries:
            out[safe_key] = safe_entries[-LONG_TERM_PER_NPC_MAX_ITEMS:]
    return out


def sanitize_global_memory_payload(raw: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        sanitized = _sanitize_long_entry(item)
        if sanitized is not None:
            out.append(sanitized)
    return out[-LONG_TERM_GLOBAL_MAX_ITEMS:]


def ensure_conversation_memory_state(state: Any) -> None:
    state.conversation_short_term = sanitize_short_term_payload(getattr(state, "conversation_short_term", {}))
    state.conversation_long_term = sanitize_long_term_payload(getattr(state, "conversation_long_term", {}))
    state.conversation_global_long_term = sanitize_global_memory_payload(
        getattr(state, "conversation_global_long_term", [])
    )
    if not isinstance(getattr(state, "gm_state", None), dict):
        state.gm_state = {}
    state.gm_state.setdefault("memory_profile_key", _memory_profile_key(state))


def _append_unique_entry(target: list[dict], entry: dict, *, max_items: int) -> None:
    if target:
        last = target[-1]
        if (
            str(last.get("summary") or "") == str(entry.get("summary") or "")
            and str(last.get("kind") or "") == str(entry.get("kind") or "")
        ):
            last["at"] = str(entry.get("at") or last.get("at") or "")
            last["importance"] = max(_safe_int(last.get("importance"), 1), _safe_int(entry.get("importance"), 1))
            last["scene_id"] = str(entry.get("scene_id") or last.get("scene_id") or "")
            last["scene_title"] = str(entry.get("scene_title") or last.get("scene_title") or "")
            last["world_time_minutes"] = max(
                _safe_int(last.get("world_time_minutes"), 0),
                _safe_int(entry.get("world_time_minutes"), 0),
            )
            if str(entry.get("npc_name") or ""):
                last["npc_name"] = str(entry.get("npc_name") or "")
            if str(entry.get("npc_key") or ""):
                last["npc_key"] = _clean_key(entry.get("npc_key"))
            return
    target.append(entry)
    if len(target) > max_items:
        del target[:-max_items]


def remember_dialogue_turn(
    state: Any,
    *,
    npc_key: str | None,
    npc_name: str,
    player_text: str,
    npc_reply: str,
    scene_id: str,
    scene_title: str,
    world_time_minutes: int,
) -> None:
    ensure_conversation_memory_state(state)

    key = _clean_key(npc_key)
    safe_npc_name = _clean_text(npc_name, max_len=80) or "PNJ"
    safe_player_text = _clean_text(player_text, max_len=260)
    safe_npc_reply = _clean_text(npc_reply, max_len=260)
    if not safe_player_text and not safe_npc_reply:
        return

    now_iso = _utc_now_iso()
    short_bucket = state.conversation_short_term.setdefault(key, [])

    if safe_player_text:
        short_bucket.append(
            {
                "at": now_iso,
                "speaker": "Joueur",
                "role": "player",
                "text": safe_player_text,
                "scene_id": _clean_text(scene_id, max_len=120),
                "scene_title": _clean_text(scene_title, max_len=120),
                "world_time_minutes": max(0, _safe_int(world_time_minutes, 0)),
            }
        )
    if safe_npc_reply:
        short_bucket.append(
            {
                "at": now_iso,
                "speaker": safe_npc_name,
                "role": "npc",
                "text": safe_npc_reply,
                "scene_id": _clean_text(scene_id, max_len=120),
                "scene_title": _clean_text(scene_title, max_len=120),
                "world_time_minutes": max(0, _safe_int(world_time_minutes, 0)),
            }
        )
    state.conversation_short_term[key] = short_bucket[-SHORT_TERM_MAX_ITEMS:]

    summary_bits: list[str] = []
    if safe_player_text:
        summary_bits.append(f"Joueur: {safe_player_text}")
    if safe_npc_reply:
        summary_bits.append(f"{safe_npc_name}: {safe_npc_reply}")
    summary = _clean_text(" | ".join(summary_bits), max_len=360)
    if summary:
        kind = _classify_kind(f"{safe_player_text} {safe_npc_reply}")
        importance = _estimate_importance_legacy(f"{safe_player_text} {safe_npc_reply}", kind)
        long_entry = {
            "at": now_iso,
            "summary": summary,
            "kind": kind,
            "importance": importance,
            "scene_id": _clean_text(scene_id, max_len=120),
            "scene_title": _clean_text(scene_title, max_len=120),
            "world_time_minutes": max(0, _safe_int(world_time_minutes, 0)),
            "npc_name": safe_npc_name,
            "npc_key": key,
        }
        npc_bucket = state.conversation_long_term.setdefault(key, [])
        _append_unique_entry(npc_bucket, long_entry, max_items=LONG_TERM_PER_NPC_MAX_ITEMS)
        _append_unique_entry(state.conversation_global_long_term, long_entry, max_items=LONG_TERM_GLOBAL_MAX_ITEMS)

    try:
        service = get_memory_service()
        profile_key = _memory_profile_key(state)
        service.remember_dialogue_turn(
            profile_key=profile_key,
            npc_id=key,
            player_text=safe_player_text,
            npc_reply=safe_npc_reply,
            scene_title=scene_title,
        )
    except Exception:
        pass


def remember_system_event(
    state: Any,
    *,
    fact_text: str,
    npc_key: str | None = None,
    npc_name: str = "",
    scene_id: str = "",
    scene_title: str = "",
    world_time_minutes: int = 0,
    kind: str = "system",
    importance: int = 3,
) -> None:
    ensure_conversation_memory_state(state)
    summary = _clean_text(fact_text, max_len=360)
    if not summary:
        return
    normalized_kind = _clean_text(kind, max_len=24).casefold()
    if normalized_kind not in MEMORY_KIND_ALLOWED:
        normalized_kind = "system"
    key = _clean_key(npc_key)
    entry = {
        "at": _utc_now_iso(),
        "summary": summary,
        "kind": normalized_kind,
        "importance": max(1, min(5, _safe_int(importance, 3))),
        "scene_id": _clean_text(scene_id, max_len=120),
        "scene_title": _clean_text(scene_title, max_len=120),
        "world_time_minutes": max(0, _safe_int(world_time_minutes, 0)),
        "npc_name": _clean_text(npc_name, max_len=80),
        "npc_key": key,
    }
    if key != _NO_NPC_KEY:
        bucket = state.conversation_long_term.setdefault(key, [])
        _append_unique_entry(bucket, entry, max_items=LONG_TERM_PER_NPC_MAX_ITEMS)
    _append_unique_entry(state.conversation_global_long_term, entry, max_items=LONG_TERM_GLOBAL_MAX_ITEMS)

    try:
        service = get_memory_service()
        profile_key = _memory_profile_key(state)
        service.remember_system_event(
            profile_key=profile_key,
            npc_id=None if key == _NO_NPC_KEY else key,
            fact_text=summary,
            kind=normalized_kind,
            importance=max(0.0, min(1.0, float(max(1, min(5, _safe_int(importance, 3))) / 5.0))),
            world_only=(key == _NO_NPC_KEY),
        )
    except Exception:
        pass


def _latest_query_text(state: Any) -> str:
    gm_state = getattr(state, "gm_state", None)
    if isinstance(gm_state, dict):
        text = _clean_text(gm_state.get("conversation_last_player_line"), max_len=240)
        if text and text != "(aucune)":
            return text

    short = getattr(state, "conversation_short_term", None)
    if isinstance(short, dict):
        for entries in short.values():
            if not isinstance(entries, list):
                continue
            for row in reversed(entries):
                if not isinstance(row, dict):
                    continue
                if str(row.get("role") or "").strip().casefold() == "player":
                    text = _clean_text(row.get("text"), max_len=240)
                    if text:
                        return text
    return ""


def build_short_term_context(state: Any, npc_key: str | None, *, max_lines: int = 10) -> str:
    ensure_conversation_memory_state(state)
    key = _clean_key(npc_key)
    query = _latest_query_text(state)
    try:
        service = get_memory_service()
        ctx = service.retrieve_context(
            profile_key=_memory_profile_key(state),
            npc_id=key,
            query=query,
            mode="npc",
            short_limit=max(1, max_lines),
            long_limit=12,
            retrieved_limit=10,
        )
        text = ctx.short_text()
        if text and text != "(aucun echange recent)":
            return text
    except Exception:
        pass

    entries = state.conversation_short_term.get(key, [])
    if not isinstance(entries, list) or not entries:
        return "(aucun echange recent)"
    lines: list[str] = []
    for entry in entries[-max(1, max_lines) :]:
        if not isinstance(entry, dict):
            continue
        speaker = _clean_text(entry.get("speaker"), max_len=80) or "Inconnu"
        text = _clean_text(entry.get("text"), max_len=180)
        if not text:
            continue
        lines.append(f"- {speaker}: {text}")
    return "\n".join(lines) if lines else "(aucun echange recent)"


def _select_long_entries(entries: list[dict], *, max_items: int) -> list[dict]:
    if not entries:
        return []
    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in reversed(entries):
        if len(selected) >= max_items:
            break
        if not isinstance(entry, dict):
            continue
        marker = (str(entry.get("kind") or ""), str(entry.get("summary") or ""))
        if marker in seen:
            continue
        if _safe_int(entry.get("importance"), 1) >= 4:
            selected.append(entry)
            seen.add(marker)
    for entry in reversed(entries):
        if len(selected) >= max_items:
            break
        if not isinstance(entry, dict):
            continue
        marker = (str(entry.get("kind") or ""), str(entry.get("summary") or ""))
        if marker in seen:
            continue
        selected.append(entry)
        seen.add(marker)
    selected.reverse()
    return selected


def build_long_term_context(state: Any, npc_key: str | None, *, max_items: int = 8) -> str:
    ensure_conversation_memory_state(state)
    key = _clean_key(npc_key)
    query = _latest_query_text(state)
    try:
        service = get_memory_service()
        ctx = service.retrieve_context(
            profile_key=_memory_profile_key(state),
            npc_id=key,
            query=query,
            mode="npc",
            short_limit=8,
            long_limit=max(1, max_items),
            retrieved_limit=10,
        )
        if ctx.long_lines:
            return "\n".join(ctx.long_lines[: max(1, max_items)])
    except Exception:
        pass

    bucket = state.conversation_long_term.get(key, [])
    if not isinstance(bucket, list) or not bucket:
        return "(aucune memoire long terme)"
    chosen = _select_long_entries(bucket, max_items=max(1, max_items))
    if not chosen:
        return "(aucune memoire long terme)"
    lines: list[str] = []
    for entry in chosen:
        kind = _clean_text(entry.get("kind"), max_len=16) or "general"
        importance = max(1, min(5, _safe_int(entry.get("importance"), 1)))
        summary = _clean_text(entry.get("summary"), max_len=240)
        if not summary:
            continue
        lines.append(f"- [{kind}|imp{importance}] {summary}")
    return "\n".join(lines) if lines else "(aucune memoire long terme)"


def build_global_memory_context(state: Any, *, max_items: int = 6) -> str:
    ensure_conversation_memory_state(state)
    query = _latest_query_text(state)
    try:
        service = get_memory_service()
        ctx = service.retrieve_context(
            profile_key=_memory_profile_key(state),
            npc_id=str(getattr(state, "selected_npc", "") or ""),
            query=query,
            mode="world",
            short_limit=0,
            long_limit=max(1, max_items),
            retrieved_limit=max(1, max_items),
        )
        text = ctx.world_text()
        if text and text != "(aucune memoire globale)":
            return text
    except Exception:
        pass

    entries = state.conversation_global_long_term
    if not isinstance(entries, list) or not entries:
        return "(aucune memoire globale)"
    chosen = _select_long_entries(entries, max_items=max(1, max_items))
    if not chosen:
        return "(aucune memoire globale)"
    lines: list[str] = []
    for entry in chosen:
        summary = _clean_text(entry.get("summary"), max_len=220)
        if not summary:
            continue
        npc_name = _clean_text(entry.get("npc_name"), max_len=80)
        kind = _clean_text(entry.get("kind"), max_len=16) or "general"
        if npc_name:
            lines.append(f"- ({npc_name}/{kind}) {summary}")
        else:
            lines.append(f"- ({kind}) {summary}")
    return "\n".join(lines) if lines else "(aucune memoire globale)"


def build_retrieved_context(state: Any, npc_key: str | None, *, max_items: int = 10) -> str:
    ensure_conversation_memory_state(state)
    key = _clean_key(npc_key)
    try:
        service = get_memory_service()
        ctx = service.retrieve_context(
            profile_key=_memory_profile_key(state),
            npc_id=key,
            query=_latest_query_text(state),
            mode="both",
            short_limit=8,
            long_limit=12,
            retrieved_limit=max(1, max_items),
        )
        return ctx.retrieved_text()
    except Exception:
        return "(aucun rappel semantique)"
