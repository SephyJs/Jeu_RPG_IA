from typing import Callable

from nicegui import ui

from app.ui.state.game_state import GameState
from app.ui.components.left_panel import left_panel
from app.ui.components.center_dialogue import center_dialogue
from app.ui.components.right_narrator import right_narrator, pick_random_video_url
from app.core.data.data_manager import DataManager, DataError
from app.core.data.item_manager import ItemsManager
from app.core.save import SaveManager
from app.gamemaster.npc_manager import NPCProfileManager
from app.gamemaster.location_manager import LocationManager
from app.gamemaster.economy_manager import EconomyManager
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.components.npc_world import ensure_npc_world_state, spawn_roaming_known_npcs, sync_npc_registry_from_profiles


MEDIA_EVERY_X_MESSAGES = 6      # change vidéo après 6 messages
MEDIA_EVERY_Y_SECONDS = 35.0    # ou toutes les 35s si rien ne se passe
MEDIA_DURATION_SECONDS = 8.0     # durée d'une animation avant retour image
SAVE_SLOT_COUNT = 3
_location_seed = LocationManager(None)
_items_manager = ItemsManager(data_dir="data")
_economy_manager = EconomyManager(data_dir="data")


def _sync_gm_state(state: GameState) -> None:
    def _safe_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    state.gm_state["player_name"] = state.player.name
    state.gm_state["location"] = state.current_scene().title
    state.gm_state["location_id"] = state.current_scene().id
    state.gm_state["map_anchor"] = state.current_scene().map_anchor
    state.gm_state["world_time_minutes"] = max(0, _safe_int(getattr(state, "world_time_minutes", 0), 0))
    state.gm_state["world_datetime"] = format_fantasy_datetime(state.gm_state["world_time_minutes"])
    state.gm_state.setdefault("flags", {})
    state.gm_state["npc_profiles"] = state.npc_profiles
    state.gm_state["player_sheet"] = state.player_sheet if isinstance(state.player_sheet, dict) else {}
    state.gm_state["player_sheet_ready"] = bool(state.player_sheet_ready)
    state.gm_state["player_gold"] = max(0, _safe_int(getattr(state.player, "gold", 0), 0))
    state.gm_state["inventory_summary"] = _economy_manager.inventory_summary(state, state.item_defs if isinstance(state.item_defs, dict) else {})
    state.gm_state["skill_points"] = max(0, _safe_int(getattr(state, "skill_points", 0), 0))
    known_skills = state.player_skills if isinstance(state.player_skills, list) else []
    state.gm_state["player_skills"] = [
        {
            "skill_id": str(s.get("skill_id") or ""),
            "name": str(s.get("name") or ""),
            "category": str(s.get("category") or ""),
            "rank": max(1, _safe_int(s.get("rank"), 1)),
            "level": max(1, _safe_int(s.get("level"), 1)),
            "uses": max(0, _safe_int(s.get("uses"), 0)),
        }
        for s in known_skills
        if isinstance(s, dict)
    ]
    state.gm_state["equipped_items"] = dict(state.equipped_items)
    if isinstance(state.player_sheet, dict):
        state.gm_state["effective_stats"] = state.player_sheet.get("effective_stats", {})
    else:
        state.gm_state["effective_stats"] = {}
    active_quests: list[dict] = []
    for q in state.quests:
        if not isinstance(q, dict):
            continue
        if str(q.get("status") or "in_progress") != "in_progress":
            continue
        objective = q.get("objective", {}) if isinstance(q.get("objective"), dict) else {}
        progress = q.get("progress", {}) if isinstance(q.get("progress"), dict) else {}
        active_quests.append(
            {
                "id": str(q.get("id") or ""),
                "title": str(q.get("title") or ""),
                "source_npc_name": str(q.get("source_npc_name") or ""),
                "objective_type": str(objective.get("type") or ""),
                "objective_target": _safe_int(objective.get("target"), 1),
                "progress_current": _safe_int(progress.get("current"), 0),
                "progress_target": _safe_int(progress.get("target"), 1),
            }
        )
    state.gm_state["active_quests"] = active_quests


def build_initial_state() -> GameState:
    state = GameState()

    dm = DataManager(data_dir="data")

    try:
        state.scenes = dm.load_all_location_scenes()
    except DataError as e:
        raise RuntimeError(
            "Erreur de chargement des données. Vérifie le dossier /data et les JSON.\n"
            f"Détail: {e}"
        ) from e

    start_id = dm.choose_start_location_id()
    _location_seed.seed_static_anchors(state.scenes)
    state.set_scene(start_id)
    try:
        state.item_defs = _items_manager.load_all()
    except Exception:
        state.item_defs = {}

    state.push(
        "Système",
        "Avant de commencer: presente ton personnage (pseudo, genre, apparence, atouts).",
        count_for_media=False,
    )
    state.push("Ataryxia", "Je dois savoir qui tu es avant d'ouvrir les routes et les rencontres.", count_for_media=False)

    state.narrator_messages_since_last_media = 0
    state.narrator_media_url = state.narrator_default_image_url

    _sync_gm_state(state)
    return state


def _refresh_static_scenes_from_data(state: GameState) -> None:
    """Force les lieux statiques depuis data/lieux, y compris après chargement d'une sauvegarde ancienne."""
    dm = DataManager(data_dir="data")
    try:
        static_scenes = dm.load_all_location_scenes()
    except DataError:
        return

    for scene_id, scene in static_scenes.items():
        state.scenes[scene_id] = scene

    if state.current_scene_id not in state.scenes:
        state.current_scene_id = dm.choose_start_location_id()

    if state.selected_npc and state.selected_npc not in state.current_scene().npc_names:
        state.selected_npc = None


def maybe_start_random_media(state: GameState) -> bool:
    video = pick_random_video_url()
    if not video:
        return False
    state.set_narrator_video(video, duration_s=MEDIA_DURATION_SECONDS)
    state.narrator_messages_since_last_media = 0
    return True


def _inject_game_page_css() -> None:
    ui.add_head_html(
        """
        <style>
          .save-toolbar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
          }
          .desktop-layout {
            display: flex;
            gap: 16px;
            align-items: stretch;
            flex-wrap: nowrap;
          }
          .mobile-layout {
            display: none;
          }
          .mobile-drawer {
            display: none;
          }
          .desktop-panel-left {
            width: 320px;
            flex: 0 0 320px;
            height: calc(100vh - 96px);
            overflow: hidden;
          }
          .desktop-panel-center {
            flex: 1 1 auto;
            min-width: 420px;
            height: calc(100vh - 96px);
            overflow: hidden;
          }
          .desktop-panel-right {
            width: 320px;
            flex: 0 0 320px;
            height: calc(100vh - 96px);
            display: flex;
            flex-direction: column;
            gap: 12px;
            overflow-y: auto;
            overflow-x: hidden;
            min-height: 0;
          }
          @media (max-width: 1024px) {
            .desktop-layout {
              display: none !important;
            }
            .mobile-layout {
              display: flex !important;
              flex-direction: column;
              gap: 10px;
            }
            .mobile-drawer {
              display: block !important;
              width: min(92vw, 380px) !important;
            }
            html,
            body,
            #q-app,
            .q-layout,
            .q-page-container,
            .q-page {
              overflow-y: auto !important;
              -webkit-overflow-scrolling: touch;
            }
            .save-toolbar > * {
              flex: 1 1 180px;
            }
            .save-toolbar .save-title {
              flex: 1 1 100%;
            }
            .mobile-quick-actions {
              display: flex;
              gap: 8px;
              margin-bottom: 2px;
            }
            .mobile-quick-actions .q-btn {
              flex: 1 1 auto;
            }
            .mobile-panel-card {
              width: 100%;
              min-height: 240px;
              max-height: none;
              overflow-y: auto;
              overflow-x: hidden;
            }
            .mobile-panel-card.center-mobile {
              min-height: 320px;
              max-height: none;
            }
            .dialogue-chat-card {
              height: 40vh !important;
              min-height: 240px;
            }
            .narrator-video-card {
              height: 28vh !important;
            }
            .choices-row,
            .npc-row {
              max-height: 96px;
              overflow-y: auto;
              align-content: flex-start;
            }
            .choice-btn,
            .npc-btn,
            .send-btn {
              min-height: 28px !important;
              padding: 2px 8px !important;
              font-size: 11px !important;
              line-height: 1.2;
              max-width: 100%;
            }
          }
          @media (max-width: 640px) {
            .save-toolbar > * {
              flex: 1 1 calc(50% - 6px);
            }
            .save-toolbar .save-title {
              flex: 1 1 100%;
            }
            .mobile-panel-card {
              min-height: 220px;
              max-height: none;
            }
            .mobile-panel-card.center-mobile {
              min-height: 300px;
              max-height: none;
            }
            .dialogue-chat-card {
              height: 34vh !important;
            }
            .narrator-video-card {
              height: 24vh !important;
            }
            .choices-row,
            .npc-row {
              display: flex !important;
              flex-wrap: nowrap !important;
              overflow-x: auto;
              overflow-y: hidden;
              gap: 6px;
              max-height: none;
              padding-bottom: 4px;
            }
            .choice-btn,
            .npc-btn {
              flex: 0 0 auto;
              width: auto;
              max-width: 210px;
              white-space: nowrap;
              overflow: hidden;
              text-overflow: ellipsis;
            }
          }
        </style>
        """,
    )


@ui.page('/game')
def game_page() -> None:
    dark = ui.dark_mode()
    dark.enable()
    _inject_game_page_css()

    save_manager = SaveManager(slot_count=SAVE_SLOT_COUNT)
    npc_store = NPCProfileManager(None)

    state = build_initial_state()
    active_slot = {"value": save_manager.get_last_slot(default=1)}
    panel_refreshers: list[Callable[[], None]] = []
    right_refreshers: list[Callable[[], None]] = []

    def _normalize_npc_profiles_in_state() -> None:
        if not isinstance(state.npc_profiles, dict):
            state.npc_profiles = {}
        try:
            disk_profiles = npc_store.load_all_profiles()
        except Exception:
            disk_profiles = {}
        if isinstance(disk_profiles, dict):
            for key, profile in disk_profiles.items():
                if key in state.npc_profiles:
                    continue
                if isinstance(profile, dict):
                    state.npc_profiles[key] = profile

        ensure_npc_world_state(state)
        sync_npc_registry_from_profiles(state)
        try:
            npc_store.save_all_profiles(state.npc_profiles)
        except Exception:
            pass

    save_manager.load_slot(active_slot["value"], state)
    _normalize_npc_profiles_in_state()
    _refresh_static_scenes_from_data(state)
    _location_seed.seed_static_anchors(state.scenes)
    spawn_roaming_known_npcs(state)
    _sync_gm_state(state)

    def _refresh_panels() -> None:
        for refresh in panel_refreshers:
            refresh()

    def _refresh_right_panels() -> None:
        for refresh in right_refreshers:
            refresh()

    def _replace_state(new_state: GameState) -> None:
        state.__dict__.clear()
        state.__dict__.update(new_state.__dict__)

    def _persist_current_slot(show_notify: bool = False) -> None:
        _sync_gm_state(state)
        try:
            npc_store.save_all_profiles(state.npc_profiles)
        except Exception:
            pass
        save_manager.save_slot(active_slot["value"], state)
        if show_notify:
            ui.notify(f"Sauvegarde effectuée sur le slot {active_slot['value']}.")

    def _load_current_slot(show_notify: bool = True) -> None:
        fresh = build_initial_state()
        if not save_manager.load_slot(active_slot["value"], fresh):
            if show_notify:
                ui.notify(f"Le slot {active_slot['value']} est vide.")
            return

        _replace_state(fresh)
        _normalize_npc_profiles_in_state()
        _refresh_static_scenes_from_data(state)
        _location_seed.seed_static_anchors(state.scenes)
        spawn_roaming_known_npcs(state)
        _sync_gm_state(state)
        _refresh_panels()
        if show_notify:
            ui.notify(f"Slot {active_slot['value']} chargé.")

    def _new_game_in_slot() -> None:
        fresh = build_initial_state()
        _replace_state(fresh)
        _normalize_npc_profiles_in_state()
        _refresh_static_scenes_from_data(state)
        _location_seed.seed_static_anchors(state.scenes)
        spawn_roaming_known_npcs(state)
        _sync_gm_state(state)
        _persist_current_slot(show_notify=False)
        _refresh_panels()
        ui.notify(f"Nouvelle partie créée dans le slot {active_slot['value']}.")

    def _set_active_slot(slot_value: int) -> None:
        try:
            selected = int(slot_value)
        except Exception:
            selected = 1
        active_slot["value"] = max(1, min(SAVE_SLOT_COUNT, selected))
        save_manager.set_last_slot(active_slot["value"])

    def on_change() -> None:
        if (
            not state.narrator_media_url.endswith(".mp4")
            and state.narrator_messages_since_last_media >= MEDIA_EVERY_X_MESSAGES
        ):
            maybe_start_random_media(state)

        _refresh_panels()
        _persist_current_slot(show_notify=False)

    left_mobile_drawer = ui.left_drawer(value=False).classes('mobile-drawer').props('overlay bordered')
    with left_mobile_drawer:
        with ui.card().classes('mobile-panel-card'):
            @ui.refreshable
            def render_left_mobile() -> None:
                left_panel(state, on_change)

            panel_refreshers.append(render_left_mobile.refresh)
            render_left_mobile()

    right_mobile_drawer = ui.right_drawer(value=False).classes('mobile-drawer').props('overlay bordered')
    with right_mobile_drawer:
        with ui.card().classes('mobile-panel-card'):
            @ui.refreshable
            def render_right_mobile() -> None:
                right_narrator(state)

            panel_refreshers.append(render_right_mobile.refresh)
            right_refreshers.append(render_right_mobile.refresh)
            render_right_mobile()

    with ui.column().classes('w-full gap-3'):
        with ui.card().classes('w-full rounded-2xl').style('margin-bottom: 10px;'):
            with ui.row().classes('w-full items-center gap-2 save-toolbar'):
                ui.label('Sauvegardes').classes('font-semibold save-title')
                ui.switch(
                    'Mode nuit',
                    value=True,
                    on_change=lambda e: dark.enable() if e.value else dark.disable(),
                ).props('dense color=amber')
                ui.select(
                    options={i: f'Slot {i}' for i in range(1, SAVE_SLOT_COUNT + 1)},
                    value=active_slot["value"],
                    on_change=lambda e: _set_active_slot(e.value),
                ).props('outlined dense')
                ui.button('Charger', on_click=lambda: _load_current_slot(show_notify=True)).props('outline')
                ui.button('Sauvegarder', on_click=lambda: _persist_current_slot(show_notify=True))
                ui.button('Nouvelle partie', on_click=_new_game_in_slot).props('outline')
                ui.button('Prototype 2D', on_click=lambda: ui.navigate.to('/prototype-2d')).props('outline dense no-caps')

        with ui.row().classes('w-full desktop-layout'):
            with ui.card().classes('rounded-2xl desktop-panel-left'):
                @ui.refreshable
                def render_left_desktop() -> None:
                    left_panel(state, on_change)

                panel_refreshers.append(render_left_desktop.refresh)
                render_left_desktop()

            with ui.card().classes('rounded-2xl desktop-panel-center'):
                @ui.refreshable
                def render_center_desktop() -> None:
                    center_dialogue(state, on_change)

                panel_refreshers.append(render_center_desktop.refresh)
                render_center_desktop()

            with ui.card().classes('rounded-2xl desktop-panel-right'):
                @ui.refreshable
                def render_right_desktop() -> None:
                    right_narrator(state)

                panel_refreshers.append(render_right_desktop.refresh)
                right_refreshers.append(render_right_desktop.refresh)
                render_right_desktop()

        with ui.column().classes('w-full mobile-layout'):
            with ui.row().classes('w-full items-center mobile-quick-actions'):
                ui.button('Volet gauche', on_click=left_mobile_drawer.toggle).props('outline dense no-caps')
                ui.button('Volet droite', on_click=right_mobile_drawer.toggle).props('outline dense no-caps')

            with ui.card().classes('mobile-panel-card center-mobile'):
                @ui.refreshable
                def render_center_mobile() -> None:
                    center_dialogue(state, on_change)

                panel_refreshers.append(render_center_mobile.refresh)
                render_center_mobile()

    ui.timer(1.0, lambda: (_refresh_right_panels() if state.ensure_narrator_image_if_expired() else None))

    ui.timer(
        MEDIA_EVERY_Y_SECONDS,
        lambda: (maybe_start_random_media(state) and _refresh_right_panels())
        if not state.narrator_media_url.endswith(".mp4") else None
    )

    _persist_current_slot(show_notify=False)
