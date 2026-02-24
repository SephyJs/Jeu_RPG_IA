from __future__ import annotations

from nicegui import ui

from app.gamemaster.npc_manager import (
    profile_corruption_level,
    profile_display_name,
    profile_tension_level,
    resolve_profile_role,
    tension_tier_label,
)
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

    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if run and not bool(run.get("completed", False)):
        state.selected_npc = None
        floor = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        name = str(run.get("dungeon_name") or "Donjon")
        ui.label("PNJ locaux").classes("text-lg font-semibold")
        with ui.row().classes("w-full flex-wrap gap-2").style("margin:2px 0 8px 0;"):
            ui.label(f"Lieu: {name}").classes("text-sm rounded-md").style(
                "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
            )
            ui.label(f"Etage: {floor}/{total}").classes("text-sm rounded-md").style(
                "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
            )
            ui.label("Presents: 0").classes("text-sm rounded-md").style(
                "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
            )
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px;"):
            ui.label("Aucun PNJ accessible pendant l'exploration du donjon.").classes("opacity-70")
        return

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
    with ui.row().classes("w-full flex-wrap gap-2").style("margin:2px 0 8px 0;"):
        ui.label(f"Lieu: {scene.title}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Presents: {len(npc_names)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )

    if not npc_names:
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px;"):
            ui.label("Aucun PNJ visible ici pour le moment.").classes("opacity-70")
        return

    with ui.column().classes("w-full gap-3"):
        for npc in npc_names:
            npc_key = resolve_scene_npc_key(state, npc, scene.id)
            profile = state.npc_profiles.get(npc_key)
            if isinstance(profile, dict):
                display_name = profile_display_name(profile, npc)
                role = resolve_profile_role(profile, npc)
                subtitle = display_name
                tension = profile_tension_level(profile)
                corruption = profile_corruption_level(profile)
                dominance = str(profile.get("dominance_style") or "soft").strip()
                role = f"{role} | tension: {tension_tier_label(tension)} | corr: {corruption} | style: {dominance}"
            else:
                subtitle = npc
                role = "Fiche a generer"

            with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px;"):
                with ui.row().classes("w-full items-center justify-between gap-3").style("flex-wrap: nowrap;"):
                    with ui.column().classes("min-w-0 gap-1"):
                        ui.label(subtitle).classes("text-sm font-semibold").style(
                            "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                        )
                        ui.label(role).classes("text-xs opacity-75").style(
                            "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                        )
                    ui.button(
                        "Parler",
                        on_click=lambda npc_name=npc: select_npc_for_dialogue(state, npc_name, on_change),
                    ).props("outline no-caps").style("min-height:34px; min-width:86px;")
