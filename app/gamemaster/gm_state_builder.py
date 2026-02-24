from __future__ import annotations

from typing import Any

from app.core.engine import normalize_trade_session, normalize_travel_state, trade_session_to_dict, travel_state_to_dict
from app.gamemaster.reputation_manager import ensure_reputation_state, reputation_summary
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.state.game_state import GameState


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def experience_tier(level: int, skill_count: int) -> str:
    if level <= 2 and skill_count <= 2:
        return "debutant"
    if level <= 5 or skill_count <= 6:
        return "intermediaire"
    return "avance"


def build_player_skills_payload(player_skills: object) -> list[dict[str, Any]]:
    rows = player_skills if isinstance(player_skills, list) else []
    return [
        {
            "skill_id": str(row.get("skill_id") or ""),
            "name": str(row.get("name") or ""),
            "category": str(row.get("category") or ""),
            "rank": max(1, safe_int(row.get("rank"), 1)),
            "level": max(1, safe_int(row.get("level"), 1)),
            "uses": max(0, safe_int(row.get("uses"), 0)),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def extract_effective_stats(player_sheet: object) -> dict[str, Any]:
    if not isinstance(player_sheet, dict):
        return {}

    raw_effective = player_sheet.get("effective_stats")
    if isinstance(raw_effective, dict):
        return dict(raw_effective)

    raw_stats = player_sheet.get("stats")
    if isinstance(raw_stats, dict):
        return dict(raw_stats)

    return {}


def apply_base_gm_state(
    state: GameState,
    *,
    economy_manager,
    location: str | None = None,
    location_id: str | None = None,
    map_anchor: str | None = None,
    scene_npcs: list[str] | None = None,
    in_dungeon: bool = False,
    selected_npc: str | None = None,
    selected_npc_key: str | None = None,
    selected_profile: dict | None = None,
) -> None:
    ensure_reputation_state(state)
    state.sync_world_state()
    gm_state = state.gm_state if isinstance(state.gm_state, dict) else {}
    state.gm_state = gm_state

    scene = state.current_scene()
    resolved_location = str(location if location is not None else scene.title)
    resolved_location_id = str(location_id if location_id is not None else scene.id)
    resolved_map_anchor = str(map_anchor if map_anchor is not None else scene.map_anchor)
    resolved_scene_npcs = scene_npcs if isinstance(scene_npcs, list) else list(getattr(scene, "npc_names", []) or [])

    gm_state["player_name"] = str(getattr(state.player, "name", "l'Éveillé") or "l'Éveillé")
    gm_state["location"] = resolved_location
    gm_state["location_id"] = resolved_location_id
    gm_state["map_anchor"] = resolved_map_anchor
    gm_state["scene_npcs"] = resolved_scene_npcs
    gm_state["world_time_minutes"] = max(0, safe_int(getattr(state, "world_time_minutes", 0), 0))
    gm_state["world_datetime"] = format_fantasy_datetime(gm_state["world_time_minutes"])
    world_state = dict(state.world_state) if isinstance(getattr(state, "world_state", None), dict) else {}
    gm_state["world_state"] = world_state
    gm_state["time_of_day"] = str(world_state.get("time_of_day") or "morning")
    gm_state["day_counter"] = max(1, safe_int(world_state.get("day_counter"), 1))
    gm_state["global_tension"] = max(0, min(100, safe_int(world_state.get("global_tension"), 0)))
    gm_state["instability_level"] = max(0, min(100, safe_int(world_state.get("instability_level"), 0)))
    gm_state.setdefault("flags", {})
    gm_state["in_dungeon"] = bool(in_dungeon)

    gm_state["npc_profiles"] = state.npc_profiles
    gm_state["player_sheet"] = state.player_sheet if isinstance(state.player_sheet, dict) else {}
    gm_state["player_sheet_ready"] = bool(state.player_sheet_ready)
    gm_state["player_gold"] = max(0, safe_int(getattr(state.player, "gold", 0), 0))
    gm_state["inventory_summary"] = economy_manager.inventory_summary(
        state,
        state.item_defs if isinstance(state.item_defs, dict) else {},
    )
    gm_state["skill_points"] = max(0, safe_int(getattr(state, "skill_points", 0), 0))
    gm_state["player_corruption_level"] = max(0, min(100, safe_int(getattr(state, "player_corruption_level", 0), 0)))
    gm_state["player_skills"] = build_player_skills_payload(getattr(state, "player_skills", []))
    gm_state["equipped_items"] = (
        dict(state.equipped_items)
        if isinstance(getattr(state, "equipped_items", None), dict)
        else {}
    )

    effective_stats = extract_effective_stats(state.player_sheet)
    gm_state["effective_stats"] = effective_stats

    player_level = max(1, safe_int(effective_stats.get("niveau"), 1))
    skill_count = len(gm_state["player_skills"]) if isinstance(gm_state.get("player_skills"), list) else 0
    equipped_weapon = str(gm_state["equipped_items"].get("weapon") or "").strip()

    gm_state["player_level"] = player_level
    gm_state["player_skill_count"] = max(0, skill_count)
    gm_state["player_weapon_equipped"] = equipped_weapon
    gm_state["player_experience_tier"] = experience_tier(player_level, skill_count)
    gm_state["faction_reputation"] = dict(state.faction_reputation)
    gm_state["faction_reputation_summary"] = reputation_summary(state, limit=6)
    gm_state["faction_states"] = dict(state.faction_states) if isinstance(getattr(state, "faction_states", None), dict) else {}
    travel_state = normalize_travel_state(getattr(state, "travel_state", None))
    gm_state["travel_state"] = travel_state_to_dict(travel_state)
    gm_state["travel_status"] = str(travel_state.status or "idle")
    gm_state["travel_from"] = str(travel_state.from_location_id or "")
    gm_state["travel_to"] = str(travel_state.to_location_id or "")
    gm_state["travel_progress"] = max(0, safe_int(travel_state.progress, 0))
    gm_state["travel_total_distance"] = max(0, safe_int(travel_state.total_distance, 0))
    gm_state["travel_danger_level"] = max(0, min(100, safe_int(travel_state.danger_level, 0)))
    gm_state["travel_fatigue"] = max(0, min(100, safe_int(travel_state.fatigue, 0)))
    trade_session = normalize_trade_session(getattr(state, "trade_session", None))
    gm_state["trade_session"] = trade_session_to_dict(trade_session)
    gm_state["trade_status"] = str(trade_session.status or "idle")
    gm_state["trade_mode"] = str(trade_session.mode or "sell")
    gm_state["trade_turn_id"] = max(0, safe_int(trade_session.turn_id, 0))

    npc_name = str(selected_npc if selected_npc is not None else getattr(state, "selected_npc", "") or "").strip()
    npc_key = str(selected_npc_key or "").strip()
    if npc_name:
        gm_state["selected_npc"] = npc_name
        if npc_key:
            gm_state["selected_npc_key"] = npc_key

        if isinstance(selected_profile, dict):
            gm_state["selected_npc_profile"] = selected_profile
        elif npc_key and isinstance(state.npc_profiles.get(npc_key), dict):
            gm_state["selected_npc_profile"] = state.npc_profiles[npc_key]
        elif not npc_key and isinstance(gm_state.get("selected_npc_key"), str):
            maybe_key = str(gm_state.get("selected_npc_key") or "").strip()
            if maybe_key and isinstance(state.npc_profiles.get(maybe_key), dict):
                gm_state["selected_npc_profile"] = state.npc_profiles[maybe_key]
            else:
                gm_state.pop("selected_npc_profile", None)
        else:
            gm_state.pop("selected_npc_profile", None)
    else:
        gm_state.pop("selected_npc", None)
        gm_state.pop("selected_npc_key", None)
        gm_state.pop("selected_npc_profile", None)
