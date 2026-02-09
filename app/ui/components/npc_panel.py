from __future__ import annotations

from nicegui import ui

from app.gamemaster.npc_manager import profile_display_name, resolve_profile_role
from app.ui.components.center_dialogue import select_npc_for_dialogue
from app.ui.components.npc_world import (
    ensure_npc_world_state,
    resolve_scene_npc_key,
    sync_npc_registry_from_profiles,
)
from app.ui.state.game_state import GameState


def npc_panel(state: GameState, on_change) -> None:
    ensure_npc_world_state(state)
    sync_npc_registry_from_profiles(state)

    scene = state.current_scene()
    npc_names = []
    seen: set[str] = set()
    for row in scene.npc_names:
        name = str(row or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        npc_names.append(name)

    ui.label("PNJ locaux").classes("text-lg font-semibold")
    ui.separator()
    ui.label(f"Lieu: {scene.title}").classes("text-sm")
    ui.label(f"PNJ presents: {len(npc_names)}").classes("text-xs opacity-70")

    if not npc_names:
        ui.label("Aucun PNJ visible ici pour le moment.").classes("opacity-70")
        return

    with ui.column().classes("w-full gap-2"):
        for npc in npc_names:
            npc_key = resolve_scene_npc_key(state, npc, scene.id)
            profile = state.npc_profiles.get(npc_key)
            if isinstance(profile, dict):
                display_name = profile_display_name(profile, npc)
                role = resolve_profile_role(profile, npc)
                subtitle = f"{display_name} ({role})"
            else:
                subtitle = f"{npc} (fiche a generer)"

            with ui.row().classes("w-full items-center justify-between gap-2").style("flex-wrap: nowrap;"):
                ui.label(subtitle).classes("text-sm").style("overflow:hidden; text-overflow:ellipsis; white-space:nowrap;")
                ui.button(
                    "Parler",
                    on_click=lambda npc_name=npc: select_npc_for_dialogue(state, npc_name, on_change),
                ).props("outline dense size=sm no-caps")
