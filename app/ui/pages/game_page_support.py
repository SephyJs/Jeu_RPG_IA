from __future__ import annotations

import json
import re
import time

from nicegui import ui

from app.core.data.data_manager import DataError, DataManager
from app.core.data.item_manager import ItemsManager
from app.gamemaster.economy_manager import EconomyManager
from app.gamemaster.gm_state_builder import apply_base_gm_state
from app.gamemaster.location_manager import LocationManager
from app.ui.components.right_narrator import pick_random_video_url
from app.ui.state.game_state import GameState


PROFILE_STOP_WORDS = {
    "bonjour",
    "bonsoir",
    "salut",
    "coucou",
    "hello",
    "hey",
    "yo",
    "je",
    "suis",
    "moi",
    "cest",
    "c",
    "est",
    "mon",
    "pseudo",
    "appelle",
    "mappelle",
}


def extract_profile_name(raw_text: str) -> str:
    text = re.sub(r"\s+", " ", str(raw_text or "").replace("’", "'")).strip()
    if not text:
        return ""

    pattern = re.compile(
        r"(?:\bje\s+m'?appelle\b|\bje\s+suis\b|\bmoi\s+c'?est\b|\bmon\s+pseudo(?:\s+est)?\b|\bpseudo(?:\s+est)?\b|\bc'?est\b)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9_-]{2,32})",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if m:
        return m.group(1)

    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_-]{2,32}", text)
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]

    for token in tokens:
        cleaned = token.replace("'", "").casefold()
        if cleaned not in PROFILE_STOP_WORDS:
            return token
    return tokens[-1]


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AutosaveController:
    def __init__(self, *, default_delay_s: float) -> None:
        self._default_delay_s = max(0.1, float(default_delay_s))
        self._dirty = False
        self._due_at = 0.0

    def mark_dirty(self, *, delay_s: float | None = None) -> None:
        delay = self._default_delay_s if delay_s is None else max(0.1, float(delay_s))
        self._dirty = True
        self._due_at = time.monotonic() + delay

    def clear(self) -> None:
        self._dirty = False
        self._due_at = 0.0

    def is_dirty(self) -> bool:
        return bool(self._dirty)

    def is_due(self) -> bool:
        if not self._dirty:
            return False
        return time.monotonic() >= float(self._due_at)


class NPCProfileTracker:
    def __init__(self, *, npc_store) -> None:
        self._npc_store = npc_store
        self._signatures: dict[str, str] = {}

    @staticmethod
    def _signature(profile: dict) -> str:
        try:
            return json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return str(profile)

    def merge_missing_from_disk(self, npc_profiles: object) -> dict:
        if not isinstance(npc_profiles, dict):
            return {}

        try:
            disk_profiles = self._npc_store.load_all_profiles()
        except Exception:
            disk_profiles = {}

        if isinstance(disk_profiles, dict):
            for key, profile in disk_profiles.items():
                if key in npc_profiles:
                    continue
                if isinstance(profile, dict):
                    npc_profiles[key] = profile

        return npc_profiles

    def rebuild_signatures(self, npc_profiles: object) -> None:
        self._signatures.clear()
        profiles = npc_profiles if isinstance(npc_profiles, dict) else {}
        for key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            npc_key = str(profile.get("npc_key") or key).strip()
            if not npc_key:
                continue
            profile["npc_key"] = npc_key
            self._signatures[npc_key] = self._signature(profile)

    def save_dirty_profiles(self, npc_profiles: object) -> None:
        profiles = npc_profiles if isinstance(npc_profiles, dict) else {}
        live_keys: set[str] = set()
        for key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            npc_key = str(profile.get("npc_key") or key).strip()
            if not npc_key:
                continue
            profile["npc_key"] = npc_key
            live_keys.add(npc_key)
            signature = self._signature(profile)
            if self._signatures.get(npc_key) == signature:
                continue
            label = str(profile.get("label") or npc_key).strip() or npc_key
            try:
                self._npc_store.save_profile(label, profile)
                self._signatures[npc_key] = self._signature(profile)
            except Exception:
                continue

        stale_keys = [key for key in self._signatures if key not in live_keys]
        for key in stale_keys:
            self._signatures.pop(key, None)


def sync_gm_state(state: GameState, *, economy_manager: EconomyManager) -> None:
    apply_base_gm_state(state, economy_manager=economy_manager)

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


def build_initial_state(
    *,
    location_seed: LocationManager,
    items_manager: ItemsManager,
    economy_manager: EconomyManager,
) -> GameState:
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
    location_seed.seed_static_anchors(state.scenes)
    state.set_scene(start_id)
    try:
        state.item_defs = items_manager.load_all()
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
    sync_gm_state(state, economy_manager=economy_manager)
    return state


def refresh_static_scenes_from_data(state: GameState) -> None:
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


def maybe_start_random_media(state: GameState, *, duration_seconds: float) -> bool:
    video = pick_random_video_url()
    if not video:
        return False
    state.set_narrator_video(video, duration_s=duration_seconds)
    state.narrator_messages_since_last_media = 0
    return True


def inject_game_page_css() -> None:
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
          .desktop-panel-left {
            width: 320px;
            flex: 0 0 320px;
            height: calc(100vh - 96px);
            overflow-y: auto !important;
            overflow-x: hidden !important;
            min-height: 0;
          }
          .mobile-layout {
            display: none;
          }
          .mobile-drawer {
            display: none;
          }
          .mobile-panel-menu-btn {
            display: none !important;
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
            }
            .mobile-drawer-content {
              width: 100%;
              min-height: 100%;
              box-sizing: border-box;
              padding: 10px;
              padding-bottom: 20px;
              overflow-y: auto;
              overflow-x: hidden;
              -webkit-overflow-scrolling: touch;
            }
            .mobile-drawer .q-drawer__content {
              overflow-y: auto !important;
              -webkit-overflow-scrolling: touch;
            }
            .mobile-panel-menu-btn {
              display: inline-flex !important;
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
              min-height: 44px !important;
              padding: 8px 12px !important;
              font-size: 13px !important;
              line-height: 1.25;
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
