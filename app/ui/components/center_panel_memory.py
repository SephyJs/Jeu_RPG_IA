from __future__ import annotations

import re

from app.gamemaster.conversation_memory import ensure_conversation_memory_state
from app.gamemaster.gm_state_builder import apply_base_gm_state
from app.ui.state.game_state import GameState

_NO_NPC_KEY = "__no_npc__"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_npc_key(value: object) -> str:
    key = re.sub(r"\s+", " ", str(value or "")).strip()
    return key[:160] if key else _NO_NPC_KEY


def _extract_last_exchange_lines(state: GameState, npc_key: str | None) -> tuple[str, str]:
    ensure_conversation_memory_state(state)
    key = _clean_npc_key(npc_key)
    bucket = state.conversation_short_term.get(key, [])
    if not isinstance(bucket, list) or not bucket:
        return "", ""

    last_player = ""
    last_npc = ""
    for entry in reversed(bucket):
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip().casefold()
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        if not last_player and role == "player":
            last_player = text
        elif not last_npc and role == "npc":
            last_npc = text
        if last_player and last_npc:
            break
    return last_player, last_npc


def _guided_training_session_summary(state: GameState, npc_key: str | None) -> str:
    gm_state = state.gm_state if isinstance(state.gm_state, dict) else {}
    flags = gm_state.get("flags") if isinstance(gm_state.get("flags"), dict) else {}
    raw = flags.get("guided_training_session")
    if not isinstance(raw, dict):
        return ""

    current_npc_key = str(npc_key or "").strip()
    session_npc_key = str(raw.get("npc_key") or "").strip()
    if current_npc_key and session_npc_key and current_npc_key != session_npc_key:
        return ""

    skill_name = str(raw.get("skill_name") or "").strip() or "inconnu"
    stage = str(raw.get("stage") or "inconnu").strip()
    intent = str(raw.get("intent") or "").strip() or "general"
    attempts = max(0, _safe_int(raw.get("attempts"), 0))
    return f"actif|competence={skill_name}|etape={stage}|intent={intent}|essais={attempts}"


def prepare_gm_state_for_turn(
    state: GameState,
    *,
    scene,
    npc: str | None,
    npc_key: str | None,
    safe_int,
    economy_manager,
    format_fantasy_datetime,
    experience_tier,
    build_short_term_context,
    build_long_term_context,
    build_global_memory_context,
    build_retrieved_context=None,
) -> None:
    try:
        run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
        in_dungeon = bool(run and not bool(run.get("completed", False)))
        location = state.current_scene().title
        location_id = state.current_scene().id
        map_anchor = state.current_scene().map_anchor
        scene_npcs = list(getattr(scene, "npc_names", []) or [])

        if in_dungeon:
            dungeon_name = str(run.get("dungeon_name") or "Donjon")
            current_floor = max(0, safe_int(run.get("current_floor"), 0))
            total_floors = max(0, safe_int(run.get("total_floors"), 0))
            run_anchor = str(run.get("anchor") or scene.map_anchor or "")
            location = f"{dungeon_name} (etage {current_floor}/{total_floors})"
            location_id = f"dungeon:{run_anchor or 'unknown'}"
            map_anchor = run_anchor
            scene_npcs = []

        selected_profile = state.npc_profiles.get(npc_key) if npc and npc_key else None
        apply_base_gm_state(
            state,
            economy_manager=economy_manager,
            location=location,
            location_id=location_id,
            map_anchor=map_anchor,
            scene_npcs=scene_npcs,
            in_dungeon=in_dungeon,
            selected_npc=npc if npc else None,
            selected_npc_key=npc_key if npc_key else None,
            selected_profile=selected_profile if isinstance(selected_profile, dict) else None,
        )

        state.gm_state["conversation_short_term"] = build_short_term_context(state, npc_key, max_lines=8)
        state.gm_state["conversation_long_term"] = build_long_term_context(state, npc_key, max_items=12)
        state.gm_state["conversation_global_memory"] = build_global_memory_context(state, max_items=12)
        if callable(build_retrieved_context):
            state.gm_state["conversation_retrieved_memory"] = build_retrieved_context(state, npc_key, max_items=10)
        last_player, last_npc = _extract_last_exchange_lines(state, npc_key)
        state.gm_state["conversation_last_player_line"] = last_player or "(aucune)"
        state.gm_state["conversation_last_npc_line"] = last_npc or "(aucune)"
        state.gm_state["guided_training_session"] = _guided_training_session_summary(state, npc_key) or "(inactive)"
    except Exception:
        pass


def record_trade_event_in_memory(
    state: GameState,
    *,
    trade_outcome: dict,
    npc_key: str | None,
    npc_name: str | None,
    scene,
    safe_int,
    remember_system_event_fn,
) -> str:
    if not isinstance(trade_outcome, dict) or not bool(trade_outcome.get("attempted")):
        return ""

    trade_context = trade_outcome.get("trade_context") if isinstance(trade_outcome.get("trade_context"), dict) else {}
    action = str(trade_context.get("action") or "").strip()
    status = str(trade_context.get("status") or "").strip()
    item_id = str(trade_context.get("item_id") or trade_context.get("query") or "").strip()
    qty_done = max(0, safe_int(trade_context.get("qty_done"), 0))
    detail = item_id
    if qty_done > 0:
        detail = f"{item_id} x{qty_done}" if item_id else f"x{qty_done}"
    memory_line = f"Economie ({action or 'trade'}): {status or 'inconnu'}"
    if detail:
        memory_line += f" | {detail}"
    trade_event_hint = f"trade:{action or 'unknown'}:{status or 'unknown'}"
    remember_system_event_fn(
        state,
        fact_text=memory_line,
        npc_key=npc_key,
        npc_name=str(npc_name or ""),
        scene_id=scene.id,
        scene_title=scene.title,
        world_time_minutes=state.world_time_minutes,
        kind="trade",
        importance=4,
    )
    return trade_event_hint


def remember_dialogue_turn_safe(
    state: GameState,
    *,
    npc_key: str | None,
    npc_name: str,
    player_text: str,
    npc_reply: str,
    scene_id: str,
    scene_title: str,
    world_time_minutes: int,
    remember_dialogue_turn_fn,
) -> None:
    try:
        remember_dialogue_turn_fn(
            state,
            npc_key=npc_key,
            npc_name=npc_name,
            player_text=player_text,
            npc_reply=npc_reply,
            scene_id=scene_id,
            scene_title=scene_title,
            world_time_minutes=world_time_minutes,
        )
    except Exception:
        pass
