from __future__ import annotations

import asyncio

from nicegui import ui

from app.gamemaster.dungeon_combat import is_combat_event
from app.ui.state.game_state import GameState


def sync_dungeon_gm_context(state: GameState, run: dict | None) -> None:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    if isinstance(run, dict) and not bool(run.get("completed", False)):
        state.gm_state["dungeon_context"] = {
            "active": True,
            "anchor": str(run.get("anchor") or ""),
            "dungeon_name": str(run.get("dungeon_name") or "Donjon"),
            "current_floor": int(run.get("current_floor", 0) or 0),
            "total_floors": int(run.get("total_floors", 0) or 0),
        }
    else:
        state.gm_state["dungeon_context"] = {
            "active": False,
            "anchor": "",
            "dungeon_name": "",
            "current_floor": 0,
            "total_floors": 0,
        }


def _sync_dungeon_gm_context(state: GameState, run: dict | None) -> None:
    sync_dungeon_gm_context(state, run)


def render_dungeon_actions(
    state: GameState,
    on_change,
    *,
    advance_dungeon_fn,
    leave_dungeon_fn,
    enter_dungeon_fn,
) -> None:
    scene = state.current_scene()
    anchor = scene.map_anchor or "Lumeria"
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None

    if run and not bool(run.get("completed", False)):
        floor = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        name = str(run.get("dungeon_name") or "Donjon")
        combat = run.get("combat") if isinstance(run.get("combat"), dict) else None
        combat_active = bool(combat and bool(combat.get("active", True)))
        if combat_active:
            enemy = str(combat.get("enemy_name") or "Adversaire")
            enemy_hp = int(combat.get("enemy_hp", 0) or 0)
            enemy_max_hp = int(combat.get("enemy_max_hp", enemy_hp) or enemy_hp)
            ui.button(
                f"Combat en cours: {enemy} ({enemy_hp}/{enemy_max_hp})",
            ).props("disable flat dense no-caps").classes("choice-btn")
        else:
            ui.button(
                f"Explorer étage suivant ({floor}/{total})",
                on_click=lambda: asyncio.create_task(advance_dungeon_fn(state, on_change)),
            ).props("outline dense no-caps").classes("choice-btn")
        ui.button(f"Quitter {name}", on_click=lambda: leave_dungeon_fn(state, on_change)).props("outline dense no-caps").classes("choice-btn")
        return

    if state.dungeon_generation_in_progress:
        ui.button("Préparation du donjon...").props("disable flat dense no-caps").classes("choice-btn")
        return

    ui.button(
        f"Entrer dans le donjon de {anchor}",
        on_click=lambda: enter_dungeon_fn(state, on_change),
    ).props("outline dense no-caps").classes("choice-btn")


async def enter_dungeon(
    state: GameState,
    on_change,
    *,
    dungeon_manager,
    refresh_chat_messages_view,
) -> None:
    if state.dungeon_generation_in_progress:
        return

    scene = state.current_scene()
    anchor = scene.map_anchor or "Lumeria"

    state.dungeon_generation_in_progress = True
    state.push("Système", f"Vous cherchez l'entrée du donjon de {anchor}...", count_for_media=False)
    on_change()
    refresh_chat_messages_view()

    try:
        profile = await dungeon_manager.ensure_dungeon_profile(state.dungeon_profiles, anchor)
        run = dungeon_manager.start_run(anchor, profile)
        state.active_dungeon_run = run
        state.selected_npc = None
        sync_dungeon_gm_context(state, run)
        state.advance_world_time(18)
        state.push("Ataryxia", str(run.get("entry_text") or "Le donjon s'ouvre devant vous."), count_for_media=False)
        state.push(
            "Système",
            f"{run.get('dungeon_name', 'Donjon')} : {run.get('total_floors', 0)} étages pour cette expédition.",
            count_for_media=False,
        )
        relic = run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None
        if isinstance(relic, dict):
            relic_name = str(relic.get("name") or "Relique").strip()
            relic_desc = str(relic.get("description") or "").strip()
            if relic_desc:
                state.push("Système", f"Relique de run: {relic_name} - {relic_desc}", count_for_media=False)
            else:
                state.push("Système", f"Relique de run: {relic_name}", count_for_media=False)
        state.push("Système", "📍 Vous êtes maintenant en exploration de donjon.", count_for_media=False)
    except Exception as e:
        state.push("Système", f"⚠️ Échec de l'ouverture du donjon: {e}", count_for_media=False)
    finally:
        state.dungeon_generation_in_progress = False

    on_change()
    refresh_chat_messages_view()


async def advance_dungeon(
    state: GameState,
    on_change,
    *,
    dungeon_manager,
    start_dungeon_combat_fn,
    maybe_award_loot_from_dungeon_event_fn,
    update_quests_and_notify_fn,
    safe_int,
    refresh_chat_messages_view,
) -> None:
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if not run:
        return

    combat = run.get("combat") if isinstance(run.get("combat"), dict) else None
    if combat and bool(combat.get("active", True)):
        enemy = str(combat.get("enemy_name") or "Adversaire")
        state.push("Système", f"Combat en cours contre {enemy}: utilise le chat pour agir.", count_for_media=False)
        on_change()
        refresh_chat_messages_view()
        return

    state.selected_npc = None
    event = dungeon_manager.advance_floor(run)
    if not event:
        state.push("Système", "Le donjon est vidé pour cette expédition.", count_for_media=False)
        state.active_dungeon_run = None
        on_change()
        refresh_chat_messages_view()
        return

    floor = int(event.get("floor", run.get("current_floor", 0)))
    total = int(run.get("total_floors", 0))
    state.advance_world_time(35)
    state.push("Système", f"[Donjon] Étage {floor}/{total}", count_for_media=False)
    state.push("Ataryxia", str(event.get("text") or "L'étage est silencieux."), count_for_media=False)

    if is_combat_event(event):
        started = False
        try:
            started = bool(start_dungeon_combat_fn(state, run, event))
        except Exception as e:
            state.push("Système", f"⚠️ Echec de preparation du combat: {e}", count_for_media=False)
            started = False
        if started:
            sync_dungeon_gm_context(state, run)
            on_change()
            refresh_chat_messages_view()
            return

    if str(event.get("type") or "").strip().casefold() == "boss":
        state.push("Système", "⚔️ Boss de fin vaincu!", count_for_media=False)

    state.quest_counters["dungeon_floors_cleared"] = max(
        0,
        safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0) + 1,
    )
    sync_dungeon_gm_context(state, run)

    try:
        await maybe_award_loot_from_dungeon_event_fn(state, event)
    except Exception as e:
        state.push("Système", f"Loot indisponible sur cet etage: {e}", count_for_media=False)

    if bool(run.get("completed", False)):
        state.push("Système", "Vous atteignez la fin du donjon et ressortez chargé d'histoires.", count_for_media=False)
        state.active_dungeon_run = None
        sync_dungeon_gm_context(state, None)

    update_quests_and_notify_fn(state)
    on_change()
    refresh_chat_messages_view()


def leave_dungeon(state: GameState, on_change, *, refresh_chat_messages_view) -> None:
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if not run:
        return

    name = str(run.get("dungeon_name") or "le donjon")
    state.active_dungeon_run = None
    sync_dungeon_gm_context(state, None)
    state.selected_npc = None
    state.advance_world_time(10)
    state.push("Système", f"Vous quittez {name} avant d'atteindre les profondeurs.", count_for_media=False)
    on_change()
    refresh_chat_messages_view()
