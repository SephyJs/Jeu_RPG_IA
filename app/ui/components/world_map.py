from __future__ import annotations

import asyncio
from collections import deque
from math import cos, pi, sin
from pathlib import Path

from nicegui import ui

from app.core.engine import TravelEngine, normalize_travel_state
from app.gamemaster.location_manager import (
    MAP_ANCHORS,
    is_building_scene_title,
    official_shortest_path,
    scene_open_status,
)
from app.gamemaster.reputation_manager import can_access_scene_by_reputation
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.components.gameplay_hooks import (
    advance_dungeon as _run_advance_dungeon,
    enter_dungeon as _run_enter_dungeon,
    explore_new_location as _run_explore_new_location,
    leave_dungeon as _run_leave_dungeon,
    refresh_quests_and_story as _refresh_quests_and_story,
)
from app.ui.components.npc_world import spawn_roaming_known_npcs
from app.ui.nsfw import (
    contains_nsfw_marker as _contains_nsfw_marker,
    is_nsfw_mode_enabled as _is_nsfw_mode_enabled,
    is_nsfw_scene as _is_nsfw_scene,
)
from app.ui.state.game_state import Choice, GameState, Scene


MAP_IMAGE_FILE = Path("assets/maps/aelyndar.png")
MAP_IMAGE_URL = "/assets/maps/aelyndar.png"
MAP_CANVAS_WIDTH_PX = 1300
MAP_CANVAS_HEIGHT_PX = 860
_MAP_ZOOM_MIN_PCT = 60
_MAP_ZOOM_MAX_PCT = 220
_MAP_ZOOM_STEP_PCT = 10
_WORLD_MAP_ZOOM_FLAG = "world_map_zoom_pct"
_LOCAL_MAP_ZOOM_FLAG = "local_map_zoom_pct"
_TRAVEL_ENGINE = TravelEngine(seed=2048)

ANCHOR_POSITIONS: dict[str, tuple[float, float]] = {
    "Valedor": (18.0, 20.0),
    "Forêt Murmurante": (38.0, 30.0),
    "Brumefeu": (58.0, 20.0),
    "Bois Sépulcral": (82.0, 28.0),
    "Ruines de Lethar": (24.0, 42.0),
    "Lumeria": (56.0, 46.0),
    "Sylve d'Ancaria": (76.0, 48.0),
    "Sylvaën": (74.0, 62.0),
    "Dun'Khar": (50.0, 60.0),
    "Pics de Khar": (56.0, 73.0),
    "Temple Ensablé": (33.0, 60.0),
    "Temple de Cendre": (20.0, 70.0),
    "Ile d'Astra'Nyx": (86.0, 86.0),
}

_LOCAL_ENTRY_TARGET_FLAG = "local_entry_target_scene_id"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _travel_state(state: GameState):
    state.travel_state = normalize_travel_state(getattr(state, "travel_state", None))
    return state.travel_state


def _travel_in_progress(state: GameState) -> bool:
    status = str(_travel_state(state).status or "idle")
    return status in {"traveling", "camping"}


def _scene_title(state: GameState, scene_id: str) -> str:
    sid = str(scene_id or "").strip()
    scene = state.scenes.get(sid)
    if isinstance(scene, Scene):
        return str(scene.title or sid)
    return sid or "inconnu"


def _travel_tension_label(level: int) -> str:
    value = max(0, min(100, int(level)))
    if value >= 75:
        return "haute"
    if value >= 40:
        return "moyenne"
    return "faible"


def _apply_travel_state_patch(state: GameState, patch: dict) -> list[str]:
    if not isinstance(patch, dict):
        return []

    lines: list[str] = []

    world_patch = patch.get("world") if isinstance(patch.get("world"), dict) else {}
    if world_patch:
        time_delta = max(0, _safe_int(world_patch.get("time_passed"), 0))
        if time_delta > 0:
            state.advance_world_time(time_delta)
            lines.append(f"+{time_delta} min")

    player_patch = patch.get("player") if isinstance(patch.get("player"), dict) else {}
    if player_patch:
        hp_delta = _safe_int(player_patch.get("hp_delta"), 0)
        if hp_delta != 0:
            before_hp = max(0, _safe_int(getattr(state.player, "hp", 0), 0))
            max_hp = max(1, _safe_int(getattr(state.player, "max_hp", 1), 1))
            after_hp = max(0, min(max_hp, before_hp + hp_delta))
            state.player.hp = after_hp
            lines.append(f"PV {before_hp}->{after_hp}")
        gold_delta = _safe_int(player_patch.get("gold_delta"), 0)
        if gold_delta != 0:
            before_gold = max(0, _safe_int(getattr(state.player, "gold", 0), 0))
            after_gold = max(0, before_gold + gold_delta)
            state.player.gold = after_gold
            lines.append(f"Or {before_gold}->{after_gold}")
        corruption_delta = _safe_int(player_patch.get("corruption_delta"), 0)
        if corruption_delta != 0:
            before_corr = max(0, min(100, _safe_int(getattr(state, "player_corruption_level", 0), 0)))
            after_corr = max(0, min(100, before_corr + corruption_delta))
            state.player_corruption_level = after_corr
            if isinstance(state.gm_state, dict):
                state.gm_state["player_corruption_level"] = after_corr
            lines.append(f"Corruption {before_corr}->{after_corr}")

    rep_patch = patch.get("reputation") if isinstance(patch.get("reputation"), dict) else {}
    if rep_patch:
        if not isinstance(state.faction_reputation, dict):
            state.faction_reputation = {}
        for faction, delta_raw in rep_patch.items():
            name = str(faction or "").strip()[:80]
            if not name:
                continue
            delta = max(-25, min(25, _safe_int(delta_raw, 0)))
            if delta == 0:
                continue
            before_rep = _safe_int(state.faction_reputation.get(name), 0)
            after_rep = max(-100, min(100, before_rep + delta))
            state.faction_reputation[name] = after_rep
            lines.append(f"Rep {name} {before_rep:+d}->{after_rep:+d}")

    resources_patch = patch.get("resources") if isinstance(patch.get("resources"), dict) else {}
    if resources_patch:
        food_delta = _safe_int(resources_patch.get("food"), 0)
        water_delta = _safe_int(resources_patch.get("water"), 0)
        if food_delta or water_delta:
            lines.append(f"Ressources vivres {food_delta:+d}/{water_delta:+d}")

    flags_patch = patch.get("flags") if isinstance(patch.get("flags"), dict) else {}
    if flags_patch:
        flags = _gm_flags(state)
        for key, value in flags_patch.items():
            k = str(key or "").strip()
            if not k:
                continue
            flags[k[:80]] = value

    return lines


def _estimate_tick_minutes(action: str, distance_gain: int, fatigue_gain: int) -> int:
    act = str(action or "continue").strip().casefold()
    if act == "camp":
        return 45
    base = max(8, (distance_gain * 3) + max(0, fatigue_gain))
    if act == "accelerate":
        base = max(10, base + 8)
    elif act == "detour":
        base = max(12, base + 12)
    return max(8, min(160, base))


def _finalize_travel_arrival(state: GameState) -> None:
    travel = _travel_state(state)
    if str(travel.status or "") != "arrived":
        return

    _TRAVEL_ENGINE.load_state(travel)
    patch = _TRAVEL_ENGINE.arrive()
    state.travel_state = _TRAVEL_ENGINE.export_state()

    destination_id = str(patch.get("location_id") or "").strip() if isinstance(patch, dict) else ""
    if destination_id and destination_id in state.scenes and state.current_scene_id != destination_id:
        state.set_scene(destination_id)
        spawn_roaming_known_npcs(state)
        _refresh_quests_after_travel(state)
    patch_lines = _apply_travel_state_patch(state, patch if isinstance(patch, dict) else {})
    arrival_title = _scene_title(state, destination_id or state.current_scene_id)
    if patch_lines:
        state.push("Système", f"🧭 Arrivée: {arrival_title} ({' | '.join(patch_lines)})", count_for_media=False)
    else:
        state.push("Système", f"🧭 Arrivée: {arrival_title}", count_for_media=False)


def _start_travel_to_scene(state: GameState, destination_scene_id: str) -> tuple[bool, str]:
    target_id = str(destination_scene_id or "").strip()
    if not target_id or target_id not in state.scenes:
        return False, "Destination inconnue."
    if _travel_in_progress(state):
        return False, "Un trajet est déjà en cours."

    if state.current_scene_id == target_id:
        return False, "Vous êtes déjà sur place."

    origin = state.current_scene()
    destination = state.scenes[target_id]
    route = official_shortest_path(origin.map_anchor or "Lumeria", destination.map_anchor or "Lumeria")
    route_distance = max(20, (len(route) - 1) * 30)
    world = state.world_state if isinstance(getattr(state, "world_state", None), dict) else {}
    global_tension = max(0, min(100, _safe_int(world.get("global_tension"), 0)))
    instability = max(0, min(100, _safe_int(world.get("instability_level"), 0)))
    danger = max(10, min(95, 20 + (global_tension // 2) + (instability // 3)))

    _TRAVEL_ENGINE.load_state(_travel_state(state))
    started = _TRAVEL_ENGINE.start_travel(
        from_id=origin.id,
        to_id=destination.id,
        options={
            "route": route,
            "total_distance": route_distance,
            "danger_level": danger,
            "segment_distance": 30,
        },
    )
    state.travel_state = started
    route_txt = " -> ".join(route) if len(route) > 1 else (route[0] if route else destination.map_anchor or destination.title)
    return True, f"Trajet lancé: {origin.title} -> {destination.title} | Route: {route_txt}"


def _start_local_travel_to_scene(state: GameState, destination_scene_id: str) -> tuple[bool, str]:
    target_id = str(destination_scene_id or "").strip()
    if not target_id or target_id not in state.scenes:
        return False, "Destination locale inconnue."
    if _travel_in_progress(state):
        return False, "Un trajet est déjà en cours."
    if state.current_scene_id == target_id:
        return False, "Vous êtes déjà sur place."

    scene_ids = _local_scene_ids_for_current_anchor(state)
    adjacency = _local_directed_adjacency(state, scene_ids)
    path = _shortest_path_directed(adjacency, state.current_scene_id, target_id)
    if not path:
        return False, "Aucun chemin local direct depuis cette position."

    destination = state.scenes[target_id]
    hops = max(1, len(path) - 1)
    per_hop = 8 if is_building_scene_title(destination.title) else 12
    total_distance = max(8, hops * per_hop)
    world = state.world_state if isinstance(getattr(state, "world_state", None), dict) else {}
    global_tension = max(0, min(100, _safe_int(world.get("global_tension"), 0)))
    instability = max(0, min(100, _safe_int(world.get("instability_level"), 0)))
    time_of_day = str(world.get("time_of_day") or "").strip().casefold()
    night_bonus = 4 if time_of_day in {"night", "nightfall"} else 0
    danger = max(6, min(55, 8 + (global_tension // 6) + (instability // 8) + night_bonus))

    _TRAVEL_ENGINE.load_state(_travel_state(state))
    started = _TRAVEL_ENGINE.start_travel(
        from_id=state.current_scene_id,
        to_id=target_id,
        options={
            "route": path,
            "total_distance": total_distance,
            "danger_level": danger,
            "segment_distance": max(6, per_hop),
        },
    )
    state.travel_state = started
    title_path = [str(state.scenes[sid].title) for sid in path if sid in state.scenes]
    route_txt = " -> ".join(title_path[:8]) if title_path else destination.title
    return True, f"Trajet local lancé: {route_txt}"


def _tick_travel(state: GameState, *, action: str) -> tuple[bool, list[str]]:
    travel_before = _travel_state(state)
    if str(travel_before.status or "idle") not in {"traveling", "camping"}:
        return False, ["Aucun trajet actif."]

    if isinstance(travel_before.pending_event, dict):
        return False, ["Un événement de route attend votre choix."]

    before_progress = max(0, _safe_int(travel_before.progress, 0))
    before_fatigue = max(0, _safe_int(travel_before.fatigue, 0))

    _TRAVEL_ENGINE.load_state(travel_before)
    updated, event = _TRAVEL_ENGINE.tick_travel(
        state.world_state if isinstance(getattr(state, "world_state", None), dict) else {},
        {"world_time_minutes": max(0, _safe_int(getattr(state, "world_time_minutes", 0), 0))},
        action=action,
    )
    state.travel_state = updated

    after_progress = max(0, _safe_int(updated.progress, 0))
    after_fatigue = max(0, _safe_int(updated.fatigue, 0))
    progress_gain = max(0, after_progress - before_progress)
    fatigue_gain = max(0, after_fatigue - before_fatigue)

    minutes = _estimate_tick_minutes(action, progress_gain, fatigue_gain)
    state.advance_world_time(minutes)

    lines: list[str] = [
        f"Voyage: +{progress_gain} progression ({after_progress}/{max(1, _safe_int(updated.total_distance, 1))})",
        f"Fatigue: {after_fatigue}/100",
    ]
    if str(action).strip().casefold() == "camp":
        lines[0] = "Camp établi: récupération en cours."

    if after_fatigue >= 85:
        hp_loss = 2 if str(action).strip().casefold() == "accelerate" else 1
        before_hp = max(0, _safe_int(getattr(state.player, "hp", 0), 0))
        max_hp = max(1, _safe_int(getattr(state.player, "max_hp", 1), 1))
        state.player.hp = max(0, min(max_hp, before_hp - hp_loss))
        lines.append(f"Épuisement: -{hp_loss} PV")

    if isinstance(event, dict):
        evt_text = str(event.get("short_text") or "").strip()
        if evt_text:
            lines.append(f"Événement: {evt_text}")
        patch_lines = _apply_travel_state_patch(
            state,
            event.get("state_patch") if isinstance(event.get("state_patch"), dict) else {},
        )
        if patch_lines:
            lines.append("Impact: " + " | ".join(patch_lines))

    if str(updated.status or "") == "arrived":
        _finalize_travel_arrival(state)
        lines.append("Destination atteinte.")

    return True, lines


def _resolve_travel_event_choice(state: GameState, choice_id: str) -> tuple[bool, list[str]]:
    travel = _travel_state(state)
    if not isinstance(travel.pending_event, dict):
        return False, ["Aucun choix de route actif."]

    _TRAVEL_ENGINE.load_state(travel)
    patch = _TRAVEL_ENGINE.resolve_travel_choice(choice_id)
    state.travel_state = _TRAVEL_ENGINE.export_state()
    patch_lines = _apply_travel_state_patch(state, patch)
    lines = ["Choix appliqué."]
    if patch_lines:
        lines.append("Impact: " + " | ".join(patch_lines))

    updated = _travel_state(state)
    if str(updated.status or "") == "arrived":
        _finalize_travel_arrival(state)
        lines.append("Destination atteinte.")
    return True, lines


def _abort_travel(state: GameState, *, return_back: bool = False) -> tuple[bool, str]:
    if not _travel_in_progress(state):
        return False, "Aucun trajet en cours."
    _TRAVEL_ENGINE.load_state(_travel_state(state))
    if return_back:
        state.travel_state = _TRAVEL_ENGINE.return_back()
        return True, "Demi-tour effectué. Trajet interrompu."
    state.travel_state = _TRAVEL_ENGINE.abort_travel()
    return True, "Trajet abandonné."


def _render_travel_panel(state: GameState, on_change) -> None:
    travel = _travel_state(state)
    status = str(travel.status or "idle")
    if status not in {"traveling", "camping"}:
        return

    from_title = _scene_title(state, travel.from_location_id)
    to_title = _scene_title(state, travel.to_location_id)
    total = max(1, _safe_int(travel.total_distance, 1))
    progress = max(0, min(total, _safe_int(travel.progress, 0)))
    progress_ratio = max(0.0, min(1.0, progress / total))
    supplies = travel.supplies_used if isinstance(travel.supplies_used, dict) else {}
    pending_event = travel.pending_event if isinstance(travel.pending_event, dict) else None
    danger = max(0, min(100, _safe_int(travel.danger_level, 0)))
    fatigue = max(0, min(100, _safe_int(travel.fatigue, 0)))

    with ui.card().classes("w-full rounded-xl shadow-sm").style(
        "margin-top:10px; padding:12px; border:1px solid rgba(255,255,255,0.14); background:rgba(255,255,255,0.03);"
    ):
        ui.label("Voyage").classes("text-sm font-semibold")
        ui.label(f"{from_title} -> {to_title}").classes("text-xs opacity-80")
        ui.linear_progress(value=progress_ratio).classes("w-full").style("margin-top:6px;")
        ui.label(f"Progression: {progress}/{total}").classes("text-[11px] opacity-75")
        with ui.row().classes("w-full flex-wrap gap-2").style("margin-top:6px;"):
            ui.label(f"Danger: {_travel_tension_label(danger)} ({danger})").classes("text-xs opacity-80")
            ui.label(f"Fatigue: {_travel_tension_label(fatigue)} ({fatigue})").classes("text-xs opacity-80")
            ui.label(
                f"Vivres: nourriture {max(0, _safe_int(supplies.get('food'), 0))} | eau {max(0, _safe_int(supplies.get('water'), 0))}"
            ).classes("text-xs opacity-80")
        if pending_event:
            event_text = str(pending_event.get("short_text") or "").strip()
            if event_text:
                ui.separator().style("margin:8px 0;")
                ui.label("Événement de route").classes("text-xs font-semibold")
                ui.label(event_text).classes("text-xs opacity-80")
            choices = pending_event.get("choices") if isinstance(pending_event.get("choices"), list) else []
            with ui.row().classes("w-full flex-wrap gap-2").style("margin-top:8px;"):
                for row in choices[:3]:
                    if not isinstance(row, dict):
                        continue
                    option_id = str(row.get("id") or "").strip()
                    text = str(row.get("text") or option_id).strip()[:64]
                    risk = str(row.get("risk_tag") or "").strip()
                    hint = str(row.get("effects_hint") or "").strip()
                    if not option_id:
                        continue

                    def _pick_choice(oid=option_id):
                        ok, lines = _resolve_travel_event_choice(state, oid)
                        for line in lines:
                            state.push("Narration système", line, count_for_media=False)
                        if ok:
                            _refresh_quests_after_travel(state)
                        on_change()

                    btn_label = text if not risk else f"{text} [{risk}]"
                    ui.button(btn_label, on_click=_pick_choice).props("outline dense no-caps").classes("flex-1 min-w-[120px]")
                    if hint:
                        ui.label(hint[:120]).classes("text-[11px] opacity-65")
            ui.label("Choisis une option pour reprendre le trajet.").classes("text-[11px] opacity-70")
            return

        with ui.row().classes("w-full flex-wrap gap-2").style("margin-top:8px;"):
            def _travel_action(action_key: str) -> None:
                ok, lines = _tick_travel(state, action=action_key)
                for line in lines:
                    state.push("Narration système", line, count_for_media=False)
                if ok:
                    _refresh_quests_after_travel(state)
                on_change()

            ui.button("Continuer", on_click=lambda: _travel_action("continue")).props("dense no-caps").classes("flex-1 min-w-[100px]")
            ui.button("Accélérer", on_click=lambda: _travel_action("accelerate")).props("outline dense no-caps").classes("flex-1 min-w-[100px]")
            ui.button("Camper", on_click=lambda: _travel_action("camp")).props("outline dense no-caps").classes("flex-1 min-w-[100px]")
            ui.button("Détour", on_click=lambda: _travel_action("detour")).props("outline dense no-caps").classes("flex-1 min-w-[100px]")
            ui.button(
                "Rebrousser",
                on_click=lambda: (
                    state.push("Système", _abort_travel(state, return_back=True)[1], count_for_media=False),
                    on_change(),
                ),
            ).props("outline dense no-caps").classes("flex-1 min-w-[100px]")
            ui.button(
                "Abandonner",
                on_click=lambda: (
                    state.push("Système", _abort_travel(state, return_back=False)[1], count_for_media=False),
                    on_change(),
                ),
            ).props("outline dense no-caps color=negative").classes("flex-1 min-w-[100px]")

def world_map_panel(state: GameState, on_change) -> None:
    if str(_travel_state(state).status or "") == "arrived":
        _finalize_travel_arrival(state)
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    in_dungeon = bool(run and not bool(run.get("completed", False)))
    current_anchor = (
        str(run.get("anchor") or "").strip()
        if in_dungeon
        else (state.current_scene().map_anchor or "inconnu")
    )
    if not current_anchor:
        current_anchor = state.current_scene().map_anchor or "inconnu"
    discovered_count = len(state.discovered_anchors)
    _sanitize_local_entry_target(state)

    ui.label("Carte d'Aelyndar").classes("text-lg font-semibold")
    with ui.row().classes("w-full flex-wrap gap-2").style("margin: 2px 0 8px 0;"):
        ui.label(f"Zone: {current_anchor}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        if in_dungeon:
            ui.label(
                f"Donjon: {run.get('dungeon_name', 'Donjon')} ({int(run.get('current_floor', 0))}/{int(run.get('total_floors', 0))})"
            ).classes("text-sm rounded-md").style(
                "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
            )
        ui.label(f"Monde: {format_fantasy_datetime(state.world_time_minutes)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Découvertes: {discovered_count}/{len(MAP_ANCHORS)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
    _render_travel_panel(state, on_change)

    with ui.dialog() as local_map_dialog:
        with ui.card().classes("w-[min(1100px,96vw)]").style("max-height:92vh; overflow:auto;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Map locale (graphe)").classes("text-lg font-semibold")
                ui.button("Fermer", on_click=local_map_dialog.close).props("outline")

            local_refresh_holder: dict[str, object] = {"refresh": None}

            def _change_local_zoom(delta: int) -> None:
                current = _get_map_zoom_pct(state, _LOCAL_MAP_ZOOM_FLAG, default=100)
                _set_map_zoom_pct(state, _LOCAL_MAP_ZOOM_FLAG, current + int(delta))
                refresh = local_refresh_holder.get("refresh")
                if callable(refresh):
                    refresh()

            def _reset_local_zoom() -> None:
                _set_map_zoom_pct(state, _LOCAL_MAP_ZOOM_FLAG, 100)
                refresh = local_refresh_holder.get("refresh")
                if callable(refresh):
                    refresh()

            @ui.refreshable
            def _render_local_map_body() -> None:
                zoom_pct = _get_map_zoom_pct(state, _LOCAL_MAP_ZOOM_FLAG, default=100)
                with ui.row().classes("w-full items-center justify-between").style("margin-top:6px;"):
                    ui.label(f"Zoom: {zoom_pct}%").classes("text-xs opacity-70")
                    with ui.row().classes("items-center gap-2"):
                        ui.button("-", on_click=lambda: _change_local_zoom(-_MAP_ZOOM_STEP_PCT)).props(
                            "outline dense no-caps"
                        )
                        ui.button("+", on_click=lambda: _change_local_zoom(_MAP_ZOOM_STEP_PCT)).props(
                            "outline dense no-caps"
                        )
                        ui.button("100%", on_click=_reset_local_zoom).props("outline dense no-caps")
                _render_local_graph_navigation(
                    state,
                    on_change,
                    zoom_pct=zoom_pct,
                    on_zoom_delta=_change_local_zoom,
                )

            local_refresh_holder["refresh"] = _render_local_map_body.refresh
            _render_local_map_body()

    with ui.dialog().props("maximized") as world_map_dialog:
        with ui.card().classes("w-full h-full").style("margin:0; border-radius:0;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Carte d'Aelyndar (cliquable)").classes("text-lg font-semibold")
                ui.button("Fermer", on_click=world_map_dialog.close).props("outline")
            world_refresh_holder: dict[str, object] = {"refresh": None}

            def _change_world_zoom(delta: int) -> None:
                current = _get_map_zoom_pct(state, _WORLD_MAP_ZOOM_FLAG, default=100)
                _set_map_zoom_pct(state, _WORLD_MAP_ZOOM_FLAG, current + int(delta))
                refresh = world_refresh_holder.get("refresh")
                if callable(refresh):
                    refresh()

            def _reset_world_zoom() -> None:
                _set_map_zoom_pct(state, _WORLD_MAP_ZOOM_FLAG, 100)
                refresh = world_refresh_holder.get("refresh")
                if callable(refresh):
                    refresh()

            @ui.refreshable
            def _render_world_map_body() -> None:
                zoom_pct = _get_map_zoom_pct(state, _WORLD_MAP_ZOOM_FLAG, default=100)
                with ui.row().classes("w-full items-center justify-between").style("padding:6px 0 2px 0;"):
                    ui.label(f"Zoom: {zoom_pct}%").classes("text-xs opacity-70")
                    with ui.row().classes("items-center gap-2"):
                        ui.button("-", on_click=lambda: _change_world_zoom(-_MAP_ZOOM_STEP_PCT)).props(
                            "outline dense no-caps"
                        )
                        ui.button("+", on_click=lambda: _change_world_zoom(_MAP_ZOOM_STEP_PCT)).props(
                            "outline dense no-caps"
                        )
                        ui.button("100%", on_click=_reset_world_zoom).props("outline dense no-caps")

                _render_clickable_map(
                    state,
                    on_change,
                    on_travel=world_map_dialog.close,
                    canvas_width_px=MAP_CANVAS_WIDTH_PX,
                    canvas_height_px=MAP_CANVAS_HEIGHT_PX,
                    zoom_pct=zoom_pct,
                    on_zoom_delta=_change_world_zoom,
                )

            world_refresh_holder["refresh"] = _render_world_map_body.refresh
            _render_world_map_body()

    with ui.row().classes("w-full gap-2").style("margin-top:4px;"):
        ui.button("Afficher map", on_click=local_map_dialog.open).props("outline no-caps").classes("flex-1").style(
            "min-height:40px;"
        )
        ui.button("Carte", on_click=world_map_dialog.open).props("outline no-caps").classes("flex-1").style(
            "min-height:40px;"
        )

    with ui.card().classes("w-full rounded-xl shadow-sm").style("margin-top:10px; padding:12px;"):
        ui.label("Exploration").classes("text-sm font-semibold")
        if state.location_generation_in_progress:
            ui.button("Exploration en cours...").props("disable flat no-caps").classes("w-full").style("min-height:36px;")
        else:
            ui.button(
                "Explorer un nouveau lieu",
                on_click=lambda: _explore_new_location_from_map(state, on_change),
            ).props("outline no-caps").classes("w-full").style("min-height:36px;")

        ui.separator().style("margin:10px 0;")
        _render_dungeon_controls_from_map(state, on_change)


def _gm_flags(state: GameState) -> dict:
    gm_state = state.gm_state if isinstance(state.gm_state, dict) else {}
    flags = gm_state.get("flags")
    if isinstance(flags, dict):
        return flags
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    state.gm_state["flags"] = {}
    return state.gm_state["flags"]


def _clamp_map_zoom_pct(value: object, *, default: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(_MAP_ZOOM_MIN_PCT, min(_MAP_ZOOM_MAX_PCT, parsed))


def _get_map_zoom_pct(state: GameState, flag_key: str, *, default: int = 100) -> int:
    return _clamp_map_zoom_pct(_gm_flags(state).get(flag_key, default), default=default)


def _set_map_zoom_pct(state: GameState, flag_key: str, value: object) -> int:
    clamped = _clamp_map_zoom_pct(value)
    _gm_flags(state)[str(flag_key)] = clamped
    return clamped


def _zoom_delta_from_wheel_args(args: object) -> int:
    payload = args if isinstance(args, dict) else {}
    try:
        delta_y = float(payload.get("deltaY", 0.0))
    except Exception:
        delta_y = 0.0
    if delta_y < 0:
        return _MAP_ZOOM_STEP_PCT
    if delta_y > 0:
        return -_MAP_ZOOM_STEP_PCT
    return 0


def _handle_zoom_wheel_event(event_args, on_zoom_delta) -> None:
    if not callable(on_zoom_delta):
        return
    step = _zoom_delta_from_wheel_args(getattr(event_args, "args", None))
    if step:
        on_zoom_delta(step)


def _scene_icon_name(scene: Scene) -> str:
    text = f"{str(scene.id or '').lower()} {str(scene.title or '').lower()}"

    def has(*tokens: str) -> bool:
        return any(token in text for token in tokens)

    if _contains_nsfw_marker(text):
        return "nightlife"
    if has("taverne", "auberge"):
        return "local_bar"
    if has("forge", "armurerie", "atelier", "terrain d'entrainement", "terrain d'entraînement"):
        return "build"
    if has("boutique", "marche", "marché", "guilde", "guildes", "banque", "monnaies"):
        return "store"
    if has("infirmerie", "hospice", "herboristerie"):
        return "medical_services"
    if has("prison", "caserne", "citadelle", "tour de guet", "tours de guet"):
        return "security"
    if has("palais", "conseil", "tribunal", "archives", "manoir", "villas"):
        return "account_balance"
    if has("academie", "académie", "laboratoire", "observatoire", "scriptoria", "menagerie", "ménagerie"):
        return "auto_fix_high"
    if has("temple", "sanctuaire", "monastere", "monastère", "necropole", "nécropole"):
        return "self_improvement"
    return "business"


def _get_local_entry_target(state: GameState) -> str:
    return str(_gm_flags(state).get(_LOCAL_ENTRY_TARGET_FLAG) or "").strip()


def _set_local_entry_target(state: GameState, scene_id: str) -> None:
    flags = _gm_flags(state)
    clean = str(scene_id or "").strip()
    if clean:
        flags[_LOCAL_ENTRY_TARGET_FLAG] = clean
    else:
        flags.pop(_LOCAL_ENTRY_TARGET_FLAG, None)


def _local_scene_ids_for_current_anchor(state: GameState) -> list[str]:
    anchor = str(state.current_scene().map_anchor or "").strip()
    scene_ids: list[str] = []
    for scene_id, scene in state.scenes.items():
        if str(scene.map_anchor or "").strip() == anchor:
            scene_ids.append(scene_id)

    if state.current_scene_id not in scene_ids:
        scene_ids.append(state.current_scene_id)

    scene_ids.sort(key=lambda sid: (state.scenes[sid].title.casefold(), sid))
    return scene_ids


def _sanitize_local_entry_target(state: GameState) -> None:
    target_id = _get_local_entry_target(state)
    if not target_id:
        return
    target = state.scenes.get(target_id)
    if not isinstance(target, Scene):
        _set_local_entry_target(state, "")
        return
    if str(target.map_anchor or "").strip() != str(state.current_scene().map_anchor or "").strip():
        _set_local_entry_target(state, "")
        return
    if not is_building_scene_title(target.title):
        _set_local_entry_target(state, "")
        return
    if _is_nsfw_scene(target) and not _is_nsfw_mode_enabled(state):
        _set_local_entry_target(state, "")


def _local_graph_edges(state: GameState, scene_ids: list[str]) -> list[tuple[str, str]]:
    allowed = set(scene_ids)
    edges: set[tuple[str, str]] = set()
    for scene_id in scene_ids:
        scene = state.scenes.get(scene_id)
        if not isinstance(scene, Scene):
            continue
        for choice in scene.choices:
            target_id = str(choice.next_scene_id or "").strip()
            if not target_id or target_id not in allowed:
                continue
            a, b = sorted((scene_id, target_id))
            edges.add((a, b))
    return sorted(edges)


def _local_directed_adjacency(state: GameState, scene_ids: list[str]) -> dict[str, list[str]]:
    allowed = set(scene_ids)
    adjacency: dict[str, list[str]] = {scene_id: [] for scene_id in scene_ids}
    for scene_id in scene_ids:
        scene = state.scenes.get(scene_id)
        if not isinstance(scene, Scene):
            continue
        for choice in scene.choices:
            target_id = str(choice.next_scene_id or "").strip()
            if not target_id or target_id not in allowed:
                continue
            if target_id not in adjacency[scene_id]:
                adjacency[scene_id].append(target_id)
    return adjacency


def _local_graph_positions(state: GameState, scene_ids: list[str]) -> dict[str, tuple[float, float]]:
    if not scene_ids:
        return {}

    if len(scene_ids) == 1:
        return {scene_ids[0]: (50.0, 50.0)}

    non_buildings = [sid for sid in scene_ids if not is_building_scene_title(state.scenes[sid].title)]
    if state.current_scene_id in non_buildings:
        hub_id = state.current_scene_id
    elif non_buildings:
        hub_id = non_buildings[0]
    else:
        hub_id = scene_ids[0]

    positions: dict[str, tuple[float, float]] = {hub_id: (50.0, 50.0)}
    others = [sid for sid in scene_ids if sid != hub_id]
    count = len(others)
    if count <= 10:
        ring_radii = (34.0,)
    elif count <= 24:
        ring_radii = (26.0, 40.0)
    else:
        ring_radii = (20.0, 32.0, 43.0)

    ring_count = len(ring_radii)
    ring_sizes = [0 for _ in range(ring_count)]
    for idx in range(count):
        ring_sizes[idx % ring_count] += 1

    ring_offsets = [0 for _ in range(ring_count)]
    for idx, scene_id in enumerate(others):
        ring = idx % ring_count
        slot = ring_offsets[ring]
        ring_offsets[ring] += 1
        slots_in_ring = max(1, ring_sizes[ring])
        angle = (2.0 * pi * slot) / slots_in_ring
        radius = ring_radii[ring]

        x = 50.0 + (cos(angle) * radius)
        y = 50.0 + (sin(angle) * radius)
        positions[scene_id] = (max(6.0, min(94.0, x)), max(8.0, min(92.0, y)))

    return positions


def _render_local_graph_navigation(
    state: GameState,
    on_change,
    *,
    zoom_pct: int = 100,
    on_zoom_delta=None,
) -> None:
    scene_ids = _local_scene_ids_for_current_anchor(state)
    if not scene_ids:
        return

    edge_pairs = _local_graph_edges(state, scene_ids)
    positions = _local_graph_positions(state, scene_ids)
    zoom_factor = _clamp_map_zoom_pct(zoom_pct, default=100) / 100.0
    graph_height_px = 320
    if len(scene_ids) > 20:
        graph_height_px = 430
    if len(scene_ids) > 32:
        graph_height_px = 520
    base_width_px = 980
    graph_width_px = max(560, int(base_width_px * zoom_factor))
    graph_scaled_height_px = max(220, int(graph_height_px * zoom_factor))
    node_size_px = max(12, int(round(16 * min(1.35, zoom_factor))))
    label_offset_px = max(10, int(round(12 * min(1.4, zoom_factor))))
    label_width_px = max(90, int(round(120 * min(1.5, zoom_factor))))

    with ui.card().classes("w-full rounded-xl shadow-sm").style("margin-top:10px; padding:12px;"):
        ui.label("Navigation locale (graphe)").classes("text-sm font-semibold")
        ui.label(
            "Clique un point pour lancer un trajet local. Le déplacement est progressif, pas instantané."
        ).classes("text-xs opacity-70")

        graph_wrap = ui.element("div").classes("w-full").style(
            "margin-top:8px; overflow:auto; border:1px solid rgba(255,255,255,0.12);"
            "border-radius:10px; background:rgba(255,255,255,0.02);"
        )
        if callable(on_zoom_delta):
            graph_wrap.on(
                "wheel",
                handler=lambda e: _handle_zoom_wheel_event(e, on_zoom_delta),
                throttle=0.08,
                js_handler=(
                    "(e) => {"
                    "if (!e || !e.ctrlKey) { return; }"
                    "e.preventDefault();"
                    "emit({deltaY: e.deltaY});"
                    "}"
                ),
            )

        with graph_wrap:
            with ui.element("div").style(
                f"position:relative; width:{graph_width_px}px; height:{graph_scaled_height_px}px; margin:0 auto;"
            ):
                line_segments: list[str] = []
                for source_id, target_id in edge_pairs:
                    p1 = positions.get(source_id)
                    p2 = positions.get(target_id)
                    if p1 is None or p2 is None:
                        continue
                    line_segments.append(
                        f'<line x1="{p1[0]:.2f}%" y1="{p1[1]:.2f}%" x2="{p2[0]:.2f}%" y2="{p2[1]:.2f}%" '
                        'stroke="rgba(255,255,255,0.30)" stroke-width="2" />'
                    )

                ui.html(
                    '<svg viewBox="0 0 100 100" preserveAspectRatio="none" '
                    'style="position:absolute; inset:0; width:100%; height:100%; pointer-events:none;">'
                    + "".join(line_segments)
                    + "</svg>"
                ).style("position:absolute; inset:0;")

                for scene_id in scene_ids:
                    scene = state.scenes.get(scene_id)
                    point = positions.get(scene_id)
                    if not isinstance(scene, Scene) or point is None:
                        continue

                    is_building = is_building_scene_title(scene.title)
                    is_current = scene_id == state.current_scene_id
                    is_selected_entry = scene_id == _get_local_entry_target(state)
                    fill = "#f4b942" if is_building else "#4ecdc4"
                    border = "2px solid #ffffff" if is_current else "1px solid rgba(0,0,0,0.55)"
                    if is_selected_entry:
                        border = "2px solid #ff5d5d"
                    opacity = "0.60" if (_is_nsfw_scene(scene) and not _is_nsfw_mode_enabled(state)) else "1"

                    btn = ui.button(
                        "",
                        on_click=lambda sid=scene_id: _on_local_graph_node_click(state, sid, on_change),
                    ).style(
                        f"position:absolute; left:{point[0]:.2f}%; top:{point[1]:.2f}%; transform:translate(-50%, -50%);"
                        f"width:{node_size_px}px; height:{node_size_px}px; min-width:{node_size_px}px; min-height:{node_size_px}px;"
                        f"padding:0; border-radius:999px; background:{fill}; border:{border}; opacity:{opacity};"
                        "display:flex; align-items:center; justify-content:center;"
                    )
                    with btn:
                        if is_building:
                            icon_name = _scene_icon_name(scene)
                            ui.icon(icon_name).style(
                                f"font-size:{max(8, int(round(node_size_px * 0.70)))}px;"
                                "color:rgba(15,23,42,0.78); pointer-events:none;"
                            )
                        ui.tooltip(scene.title)

                    is_nsfw = _is_nsfw_scene(scene)
                    show_label = len(scene_ids) <= 20 or (not is_building) or is_current or is_selected_entry or is_nsfw
                    if show_label:
                        label_prefix = "18+ " if is_nsfw else ""
                        ui.label(f"{label_prefix}{_short_scene_title(scene.title)}").classes("text-[10px] opacity-90").style(
                            f"position:absolute; left:{point[0]:.2f}%; top:calc({point[1]:.2f}% + {label_offset_px}px);"
                            f"transform:translateX(-50%); max-width:{label_width_px}px; text-align:center; white-space:nowrap;"
                            "overflow:hidden; text-overflow:ellipsis;"
                        )

        entry_target_id = _get_local_entry_target(state)
        entry_target = state.scenes.get(entry_target_id) if entry_target_id else None
        if isinstance(entry_target, Scene) and is_building_scene_title(entry_target.title):
            is_open, status_hint = scene_open_status(entry_target, state.world_time_minutes)
            can_access_rep, rep_hint = can_access_scene_by_reputation(
                state,
                scene_id=entry_target.id,
                scene_title=entry_target.title,
            )
            entry_choice = _find_choice_to_scene(state.current_scene(), entry_target_id)
            can_enter = (entry_choice is not None) and is_open and can_access_rep
            label = f"Entrée : {_short_scene_title(entry_target.title)}"
            if not is_open:
                label += " (fermé)"
            elif not can_access_rep:
                label += " (bloqué)"
            btn = ui.button(
                label,
                on_click=(lambda: _enter_selected_local_building(state, on_change)) if can_enter else None,
            ).props("outline no-caps").classes("w-full").style("margin-top:8px; min-height:36px;")
            if not can_enter:
                btn.props("disable")
            if status_hint and not can_access_rep:
                with btn:
                    ui.tooltip(f"{status_hint} | {rep_hint}")
            elif status_hint:
                with btn:
                    ui.tooltip(status_hint)
            elif rep_hint:
                with btn:
                    ui.tooltip(rep_hint)
            if entry_choice is None:
                ui.label("Approche-toi d'une rue connectée à ce bâtiment pour entrer.").classes("text-xs opacity-70")
        else:
            ui.button("Entrée", on_click=None).props("disable flat no-caps").classes("w-full").style(
                "margin-top:8px; min-height:36px;"
            )

        with ui.row().classes("w-full items-center gap-3").style("margin-top:8px;"):
            ui.label("● Rue / extérieur").classes("text-xs opacity-70").style("color:#4ecdc4;")
            ui.label("● Bâtiment").classes("text-xs opacity-70").style("color:#f4b942;")
            ui.label("Ctrl + molette = zoom").classes("text-xs opacity-60")
            ui.label("Bord blanc = position actuelle").classes("text-xs opacity-60")


def _on_local_graph_node_click(state: GameState, target_scene_id: str, on_change) -> None:
    target = state.scenes.get(str(target_scene_id or "").strip())
    if not isinstance(target, Scene):
        return

    if _is_nsfw_scene(target) and not _is_nsfw_mode_enabled(state):
        state.push("Système", "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.", count_for_media=False)
        on_change()
        return

    if is_building_scene_title(target.title):
        _set_local_entry_target(state, "")
        moved = _move_to_local_scene(state, target.id, on_change)
        if moved and state.current_scene_id == target.id and not _travel_in_progress(state):
            state.push("Système", f"➡️ Vous êtes déjà dans {_short_scene_title(target.title)}.", count_for_media=False)
            on_change()
        return

    _set_local_entry_target(state, "")
    _move_to_local_scene(state, target.id, on_change)


def _move_to_local_scene(state: GameState, target_scene_id: str, on_change) -> bool:
    if _travel_in_progress(state):
        state.push("Système", "Trajet actif: impossible de changer de zone locale.", count_for_media=False)
        on_change()
        return False
    target_id = str(target_scene_id or "").strip()
    if not target_id or target_id not in state.scenes:
        return False
    if state.current_scene_id == target_id:
        return True

    target = state.scenes[target_id]
    can_access, rep_hint = can_access_scene_by_reputation(
        state,
        scene_id=target.id,
        scene_title=target.title,
    )
    if not can_access:
        state.push("Système", f"🚫 {rep_hint}", count_for_media=False)
        on_change()
        return False
    is_open, status_hint = scene_open_status(target, state.world_time_minutes)
    if not is_open:
        state.push("Système", f"🚪 {status_hint}", count_for_media=False)
        on_change()
        return False

    started, message = _start_local_travel_to_scene(state, target_id)
    if not started:
        state.push("Système", f"⚠️ {message}", count_for_media=False)
        on_change()
        return False
    state.push("Système", f"🧭 {message}", count_for_media=False)
    on_change()
    return True


def _shortest_path_directed(adjacency: dict[str, list[str]], start: str, goal: str) -> list[str]:
    source = str(start or "").strip()
    target = str(goal or "").strip()
    if not source or not target:
        return []
    if source == target:
        return [source]

    queue = deque([source])
    previous: dict[str, str | None] = {source: None}

    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt in previous:
                continue
            previous[nxt] = node
            if nxt == target:
                queue.clear()
                break
            queue.append(nxt)

    if target not in previous:
        return []

    out: list[str] = []
    cursor: str | None = target
    while cursor is not None:
        out.append(cursor)
        cursor = previous.get(cursor)
    out.reverse()
    return out


def _find_choice_to_scene(scene: Scene, target_scene_id: str) -> Choice | None:
    target_id = str(target_scene_id or "").strip()
    for choice in scene.choices:
        if str(choice.next_scene_id or "").strip() == target_id:
            return choice
    return None


def _enter_selected_local_building(state: GameState, on_change) -> None:
    target_id = _get_local_entry_target(state)
    if not target_id or target_id not in state.scenes:
        _set_local_entry_target(state, "")
        on_change()
        return

    target = state.scenes[target_id]
    if _is_nsfw_scene(target) and not _is_nsfw_mode_enabled(state):
        state.push("Système", "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.", count_for_media=False)
        on_change()
        return

    entry_choice = _find_choice_to_scene(state.current_scene(), target_id)
    if entry_choice is None:
        state.push("Système", "Aucune entrée accessible depuis cette position.", count_for_media=False)
        on_change()
        return

    _set_local_entry_target(state, "")
    _apply_local_choice_from_map(state, entry_choice, on_change, user_label="Entrée")


def _render_clickable_map(
    state: GameState,
    on_change,
    *,
    canvas_width_px: int,
    canvas_height_px: int,
    zoom_pct: int = 100,
    on_zoom_delta=None,
    on_travel=None,
) -> None:
    current_anchor = state.current_scene().map_anchor
    travel_locked = _travel_in_progress(state)
    has_bg = MAP_IMAGE_FILE.exists()
    zoom_factor = _clamp_map_zoom_pct(zoom_pct, default=100) / 100.0
    scaled_width_px = max(720, int(canvas_width_px * zoom_factor))
    scaled_height_px = max(480, int(canvas_height_px * zoom_factor))
    btn_font_px = max(10, int(round(12 * min(1.4, zoom_factor))))
    btn_pad_y_px = max(3, int(round(4 * min(1.4, zoom_factor))))
    btn_pad_x_px = max(8, int(round(10 * min(1.4, zoom_factor))))
    btn_max_width_px = max(120, int(round(150 * min(1.5, zoom_factor))))

    map_wrap = ui.element("div").classes("w-full").style(
        "flex:1 1 auto; min-height:0; overflow:auto; background:#0d1117; border-radius:8px;"
    )
    if callable(on_zoom_delta):
        map_wrap.on(
            "wheel",
            handler=lambda e: _handle_zoom_wheel_event(e, on_zoom_delta),
            throttle=0.08,
            js_handler=(
                "(e) => {"
                "if (!e || !e.ctrlKey) { return; }"
                "e.preventDefault();"
                "emit({deltaY: e.deltaY});"
                "}"
            ),
        )

    with map_wrap:
        with ui.element("div").style(
            f"position:relative; width:{scaled_width_px}px; height:{scaled_height_px}px; margin:8px auto;"
        ):
            if has_bg:
                ui.image(MAP_IMAGE_URL).classes("w-full h-full").style("object-fit: contain; display:block;")
            else:
                with ui.element("div").classes("w-full h-full").style(
                    "background: radial-gradient(circle at 30% 20%, #2f7c4f 0%, #1f4d35 45%, #153526 100%);"
                    "display:flex; align-items:center; justify-content:center; color:#f3e6c8;"
                    "font-size:14px; text-align:center; padding:12px;"
                ):
                    ui.label("Ajoute ta carte dans assets/maps/aelyndar.png pour afficher le fond.")

            with ui.element("div").classes("w-full h-full").style("position:absolute; inset:0;"):
                for anchor in MAP_ANCHORS:
                    x, y = ANCHOR_POSITIONS.get(anchor, (50.0, 50.0))
                    discovered = anchor in state.discovered_anchors
                    is_current = anchor == current_anchor
                    target_scene_id = _scene_for_anchor(state, anchor)
                    route = official_shortest_path(current_anchor or "Lumeria", anchor)

                    color = "#f6c453" if discovered else "#57606a"
                    border = "2px solid #f5e3b5" if is_current else "1px solid #111"
                    opacity = "1" if discovered else "0.55"

                    label = anchor if discovered else "???"
                    if discovered and target_scene_id:
                        route_txt = " -> ".join(route)
                        tip = f"Voyager vers {anchor} (route: {route_txt})"
                    else:
                        tip = f"Zone non découverte: {anchor}"

                    btn = ui.button(
                        label,
                        on_click=(
                            lambda sid=target_scene_id, a=anchor: _travel_to_anchor(state, sid, a, on_change, on_travel)
                        )
                        if discovered and target_scene_id and not travel_locked else None,
                    ).style(
                        f"position:absolute; left:{x}%; top:{y}%; transform:translate(-50%, -50%);"
                        f"background:{color}; border:{border}; opacity:{opacity};"
                        f"border-radius:999px; font-size:{btn_font_px}px; padding:{btn_pad_y_px}px {btn_pad_x_px}px;"
                        f"max-width:{btn_max_width_px}px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"
                    )
                    if not (discovered and target_scene_id) or travel_locked:
                        btn.props("disable")
                    with btn:
                        ui.tooltip(tip)


def _scene_for_anchor(state: GameState, anchor: str) -> str | None:
    sid = state.anchor_last_scene.get(anchor)
    if sid and sid in state.scenes:
        return sid

    for scene_id, scene in state.scenes.items():
        if scene.map_anchor == anchor:
            return scene_id
    return None


def _travel_to_anchor(state: GameState, scene_id: str | None, anchor: str, on_change, on_travel=None) -> None:
    if not scene_id or scene_id not in state.scenes:
        return
    if _travel_in_progress(state):
        state.push("Système", "🧭 Un trajet est déjà en cours. Termine-le avant d'en lancer un autre.", count_for_media=False)
        on_change()
        if on_travel:
            on_travel()
        return
    if state.current_scene_id == scene_id:
        return

    current_anchor = state.current_scene().map_anchor or "Lumeria"
    route = official_shortest_path(current_anchor, anchor)

    destination = state.scenes[scene_id]
    if _is_nsfw_scene(destination) and not _is_nsfw_mode_enabled(state):
        state.push("Système", "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.", count_for_media=False)
        on_change()
        if on_travel:
            on_travel()
        return
    can_access, rep_hint = can_access_scene_by_reputation(
        state,
        scene_id=destination.id,
        scene_title=destination.title,
    )
    if not can_access:
        state.push("Système", f"🚫 {rep_hint}", count_for_media=False)
        on_change()
        if on_travel:
            on_travel()
        return
    is_open, status_hint = scene_open_status(destination, state.world_time_minutes)
    if not is_open:
        state.push("Système", f"🚪 {status_hint}", count_for_media=False)
        on_change()
        if on_travel:
            on_travel()
        return

    if len(route) > 1:
        state.push("Système", f"🛣️ Route officielle prévue: {' -> '.join(route)}", count_for_media=False)
    started, message = _start_travel_to_scene(state, scene_id)
    if started:
        destination = state.scenes.get(scene_id)
        dest_label = destination.title if isinstance(destination, Scene) else str(scene_id)
        state.push("Système", f"🧭 {message}", count_for_media=False)
        state.push("Système", f"Objectif: {dest_label} ({anchor}).", count_for_media=False)
        _set_local_entry_target(state, "")
    else:
        state.push("Système", f"⚠️ {message}", count_for_media=False)
    on_change()
    if on_travel:
        on_travel()


def _apply_local_choice_from_map(state: GameState, choice: Choice, on_change, *, user_label: str | None = None) -> None:
    if _travel_in_progress(state):
        state.push("Système", "Trajet actif: impossible de se déplacer localement pour l'instant.", count_for_media=False)
        on_change()
        return
    target_id = str(choice.next_scene_id or "").strip()
    if not target_id or target_id not in state.scenes:
        return
    destination = state.scenes[target_id]
    if _is_nsfw_scene(destination) and not _is_nsfw_mode_enabled(state):
        state.push("Système", "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.", count_for_media=False)
        on_change()
        return
    can_access, rep_hint = can_access_scene_by_reputation(
        state,
        scene_id=destination.id,
        scene_title=destination.title,
    )
    if not can_access:
        state.push("Système", f"🚫 {rep_hint}", count_for_media=False)
        on_change()
        return
    is_open, status_hint = scene_open_status(destination, state.world_time_minutes)
    if not is_open:
        state.push("Système", f"🚪 {status_hint}", count_for_media=False)
        on_change()
        return
    state.push("Joueur", str(user_label or choice.label or "Se déplacer"))
    started, message = _start_local_travel_to_scene(state, target_id)
    if not started:
        state.push("Système", f"⚠️ {message}", count_for_media=False)
        on_change()
        return
    _set_local_entry_target(state, "")
    state.push("Système", f"🧭 {message}", count_for_media=False)
    on_change()


def _short_scene_title(title: str) -> str:
    text = str(title or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip()
    return text


def _refresh_quests_after_travel(state: GameState) -> None:
    try:
        _refresh_quests_and_story(state)
    except Exception:
        pass


def _explore_new_location_from_map(state: GameState, on_change) -> None:
    if _travel_in_progress(state):
        state.push("Système", "Trajet actif: termine le voyage avant d'explorer.", count_for_media=False)
        on_change()
        return
    try:
        _run_explore_new_location(state, on_change)
    except Exception as e:
        state.push("Système", f"⚠️ Exploration indisponible: {e}", count_for_media=False)
        on_change()


def _render_dungeon_controls_from_map(state: GameState, on_change) -> None:
    if _travel_in_progress(state):
        ui.button("Voyage en cours...").props("disable flat no-caps").classes("w-full").style("min-height:34px;")
        return
    scene = state.current_scene()
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    anchor = (str(run.get("anchor") or "").strip() if isinstance(run, dict) else "") or scene.map_anchor or "Lumeria"

    if run and not bool(run.get("completed", False)):
        floor = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        name = str(run.get("dungeon_name") or "Donjon")
        with ui.row().classes("w-full flex-wrap gap-2"):
            ui.button(
                f"Explorer etage suivant ({floor}/{total})",
                on_click=lambda: _advance_dungeon_from_map(state, on_change),
            ).props("outline no-caps").style("min-height:34px; padding:4px 10px;")
            ui.button(
                f"Quitter {name}",
                on_click=lambda: _leave_dungeon_from_map(state, on_change),
            ).props("outline no-caps").style("min-height:34px; padding:4px 10px;")
        return

    if state.dungeon_generation_in_progress:
        ui.button("Preparation du donjon...").props("disable flat no-caps").classes("w-full").style("min-height:34px;")
        return

    ui.button(
        f"Entrer dans le donjon de {anchor}",
        on_click=lambda: _enter_dungeon_from_map(state, on_change),
    ).props("outline no-caps").classes("w-full").style("min-height:34px;")


def _enter_dungeon_from_map(state: GameState, on_change) -> None:
    async def _run() -> None:
        try:
            await _run_enter_dungeon(state, on_change)
        except Exception as e:
            state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
            on_change()

    try:
        asyncio.create_task(_run())
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()


def _advance_dungeon_from_map(state: GameState, on_change) -> None:
    async def _run() -> None:
        try:
            await _run_advance_dungeon(state, on_change)
        except Exception as e:
            state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
            on_change()

    try:
        asyncio.create_task(_run())
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()


def _leave_dungeon_from_map(state: GameState, on_change) -> None:
    try:
        _run_leave_dungeon(state, on_change)
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()
