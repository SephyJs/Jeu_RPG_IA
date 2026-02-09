from __future__ import annotations

import asyncio
from pathlib import Path

from nicegui import ui

from app.gamemaster.location_manager import (
    MAP_ANCHORS,
    is_building_scene_title,
    official_shortest_path,
    scene_open_status,
)
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.components.npc_world import spawn_roaming_known_npcs
from app.ui.state.game_state import Choice, GameState


MAP_IMAGE_FILE = Path("assets/maps/aelyndar.png")
MAP_IMAGE_URL = "/assets/maps/aelyndar.png"
MAP_CANVAS_WIDTH_PX = 1300
MAP_CANVAS_HEIGHT_PX = 860

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


def world_map_panel(state: GameState, on_change) -> None:
    current_anchor = state.current_scene().map_anchor or "inconnu"
    discovered_count = len(state.discovered_anchors)
    move_choices, enter_choices = _classify_scene_choices(state)

    ui.label("Carte d'Aelyndar").classes("text-lg font-semibold")
    ui.separator()
    ui.label(f"Zone actuelle: {current_anchor}").classes("text-sm")
    ui.label("Date du monde: " + format_fantasy_datetime(state.world_time_minutes)).classes("text-xs opacity-80")
    ui.label(f"Zones découvertes: {discovered_count}/{len(MAP_ANCHORS)}").classes("text-xs opacity-70")

    with ui.dialog().props("maximized") as map_dialog:
        with ui.card().classes("w-full h-full").style("margin:0; border-radius:0;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Carte d'Aelyndar (cliquable)").classes("text-lg font-semibold")
                ui.button("Fermer", on_click=map_dialog.close).props("outline")

            _render_clickable_map(
                state,
                on_change,
                on_travel=map_dialog.close,
                canvas_width_px=MAP_CANVAS_WIDTH_PX,
                canvas_height_px=MAP_CANVAS_HEIGHT_PX,
            )

    ui.button("Afficher carte", on_click=map_dialog.open).classes("w-full")

    ui.separator()
    ui.label("Deplacements locaux").classes("text-sm font-semibold")

    if move_choices:
        ui.label("Aller vers").classes("text-xs opacity-80")
        with ui.row().classes("w-full flex-wrap gap-1"):
            for choice in move_choices:
                target = state.scenes.get(str(choice.next_scene_id or ""))
                target_title = _short_scene_title(str(target.title if target else choice.label))
                ui.button(
                    f"➡ Aller vers {target_title}",
                    on_click=lambda c=choice: _apply_local_choice_from_map(state, c, on_change),
                ).props("outline dense size=sm no-caps")

    if enter_choices:
        ui.label("Entrer dans").classes("text-xs opacity-80")
        with ui.row().classes("w-full flex-wrap gap-1"):
            for choice in enter_choices:
                target = state.scenes.get(str(choice.next_scene_id or ""))
                target_title = _short_scene_title(str(target.title if target else choice.label))
                is_open = True
                status_hint = ""
                if target is not None:
                    is_open, status_hint = scene_open_status(target, state.world_time_minutes)
                label = f"Entrer dans {target_title}" if is_open else f"Entrer dans {target_title} (ferme)"
                btn = ui.button(
                    label,
                    on_click=(lambda c=choice: _apply_local_choice_from_map(state, c, on_change)) if is_open else None,
                ).props("dense size=sm no-caps")
                if not is_open:
                    btn.props("disable")
                if status_hint:
                    with btn:
                        ui.tooltip(status_hint)

    if not move_choices and not enter_choices:
        ui.label("Aucun deplacement direct ici. Utilise l'exploration ci-dessous.").classes("text-xs opacity-70")

    ui.separator()
    ui.label("Exploration").classes("text-sm font-semibold")
    if state.location_generation_in_progress:
        ui.button("Exploration en cours...").props("disable flat dense size=sm no-caps").classes("w-full")
    else:
        ui.button(
            "Explorer un nouveau lieu",
            on_click=lambda: _explore_new_location_from_map(state, on_change),
        ).props("outline dense size=sm no-caps").classes("w-full")

    _render_dungeon_controls_from_map(state, on_change)


def _render_clickable_map(
    state: GameState,
    on_change,
    *,
    canvas_width_px: int,
    canvas_height_px: int,
    on_travel=None,
) -> None:
    current_anchor = state.current_scene().map_anchor
    has_bg = MAP_IMAGE_FILE.exists()

    with ui.element("div").classes("w-full").style(
        "flex:1 1 auto; min-height:0; overflow:auto; background:#0d1117; border-radius:8px;"
    ):
        with ui.element("div").style(
            f"position:relative; width:{canvas_width_px}px; height:{canvas_height_px}px; margin:8px auto;"
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
                        if discovered and target_scene_id else None,
                    ).props("dense").style(
                        f"position:absolute; left:{x}%; top:{y}%; transform:translate(-50%, -50%);"
                        f"background:{color}; border:{border}; opacity:{opacity};"
                        "border-radius:999px; font-size:11px; padding:2px 8px;"
                        "max-width:150px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"
                    )
                    if not (discovered and target_scene_id):
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
    if state.current_scene_id == scene_id:
        return

    current_anchor = state.current_scene().map_anchor or "Lumeria"
    route = official_shortest_path(current_anchor, anchor)

    destination = state.scenes[scene_id]
    is_open, status_hint = scene_open_status(destination, state.world_time_minutes)
    if not is_open:
        state.push("Système", f"🚪 {status_hint}", count_for_media=False)
        on_change()
        if on_travel:
            on_travel()
        return

    state.set_scene(scene_id)
    travel_minutes = max(20, (len(route) - 1) * 40)
    state.advance_world_time(travel_minutes)
    spawn_roaming_known_npcs(state)
    _refresh_quests_after_travel(state)
    if len(route) > 1:
        state.push("Système", f"🛣️ Route officielle: {' -> '.join(route)}", count_for_media=False)
    state.push("Système", f"🧭 Vous prenez la route vers {state.current_scene().title} ({anchor}).", count_for_media=False)
    on_change()
    if on_travel:
        on_travel()


def _classify_scene_choices(state: GameState) -> tuple[list[Choice], list[Choice]]:
    scene = state.current_scene()
    move_choices: list[Choice] = []
    enter_choices: list[Choice] = []
    for choice in scene.choices:
        target_id = str(choice.next_scene_id or "").strip()
        if not target_id or target_id not in state.scenes:
            continue
        destination = state.scenes[target_id]
        if is_building_scene_title(destination.title):
            enter_choices.append(choice)
        else:
            move_choices.append(choice)
    return move_choices, enter_choices


def _apply_local_choice_from_map(state: GameState, choice: Choice, on_change) -> None:
    target_id = str(choice.next_scene_id or "").strip()
    if not target_id or target_id not in state.scenes:
        return
    destination = state.scenes[target_id]
    is_open, status_hint = scene_open_status(destination, state.world_time_minutes)
    if not is_open:
        state.push("Système", f"🚪 {status_hint}", count_for_media=False)
        on_change()
        return
    state.push("Joueur", choice.label)
    state.set_scene(target_id)
    if is_building_scene_title(destination.title):
        state.advance_world_time(8)
    else:
        state.advance_world_time(14)
    spawn_roaming_known_npcs(state)
    _refresh_quests_after_travel(state)
    state.push("Système", f"➡️ Vous arrivez : {state.current_scene().title}", count_for_media=False)
    on_change()


def _short_scene_title(title: str) -> str:
    text = str(title or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip()
    return text


def _refresh_quests_after_travel(state: GameState) -> None:
    # Reuse la logique de suivi de quetes existante du dialogue central.
    try:
        from app.ui.components.center_dialogue import _update_quests_and_notify

        _update_quests_and_notify(state)
    except Exception:
        pass


def _explore_new_location_from_map(state: GameState, on_change) -> None:
    try:
        from app.ui.components.center_dialogue import _explore_new_location

        _explore_new_location(state, on_change)
    except Exception as e:
        state.push("Système", f"⚠️ Exploration indisponible: {e}", count_for_media=False)
        on_change()


def _render_dungeon_controls_from_map(state: GameState, on_change) -> None:
    scene = state.current_scene()
    anchor = scene.map_anchor or "Lumeria"
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None

    if run and not bool(run.get("completed", False)):
        floor = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        name = str(run.get("dungeon_name") or "Donjon")
        with ui.row().classes("w-full flex-wrap gap-1"):
            ui.button(
                f"Explorer etage suivant ({floor}/{total})",
                on_click=lambda: _advance_dungeon_from_map(state, on_change),
            ).props("outline dense size=sm no-caps")
            ui.button(
                f"Quitter {name}",
                on_click=lambda: _leave_dungeon_from_map(state, on_change),
            ).props("outline dense size=sm no-caps")
        return

    if state.dungeon_generation_in_progress:
        ui.button("Preparation du donjon...").props("disable flat dense size=sm no-caps").classes("w-full")
        return

    ui.button(
        f"Entrer dans le donjon de {anchor}",
        on_click=lambda: _enter_dungeon_from_map(state, on_change),
    ).props("outline dense size=sm no-caps").classes("w-full")


def _enter_dungeon_from_map(state: GameState, on_change) -> None:
    try:
        from app.ui.components.center_dialogue import _enter_dungeon

        asyncio.create_task(_enter_dungeon(state, on_change))
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()


def _advance_dungeon_from_map(state: GameState, on_change) -> None:
    try:
        from app.ui.components.center_dialogue import _advance_dungeon

        asyncio.create_task(_advance_dungeon(state, on_change))
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()


def _leave_dungeon_from_map(state: GameState, on_change) -> None:
    try:
        from app.ui.components.center_dialogue import _leave_dungeon

        _leave_dungeon(state, on_change)
    except Exception as e:
        state.push("Système", f"⚠️ Donjon indisponible: {e}", count_for_media=False)
        on_change()
