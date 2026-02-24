from __future__ import annotations
from app.ui.state.inventory import InventoryGrid

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time

from app.core.engine import (
    TradeSession,
    TravelState,
    idle_trade_session,
    idle_travel_state,
    normalize_trade_session,
    normalize_travel_state,
)
from app.core.models import ChatMessage, Choice, PlayerProfile, Scene

CHAT_HISTORY_MAX_ITEMS = 600
_DAY_MINUTES = 24 * 60

__all__ = [
    "GameState",
    "Scene",
    "Choice",
    "ChatMessage",
    "PlayerProfile",
]


@dataclass
class GameState:
    player: PlayerProfile = field(default_factory=PlayerProfile)
    scenes: Dict[str, Scene] = field(default_factory=dict)
    current_scene_id: str = "city"
    chat: List[ChatMessage] = field(default_factory=list)
    chat_draft: str = ""
    chat_turn_in_progress: bool = False
    selected_npc: Optional[str] = None
    pending_choice_options: List[dict] = field(default_factory=list)
    pending_choice_prompt: str = ""
    pending_choice_source_npc_key: str = ""
    pending_choice_created_at: str = ""
    npc_profiles: Dict[str, dict] = field(default_factory=dict)
    npc_registry: Dict[str, dict] = field(default_factory=dict)
    npc_scene_bindings: Dict[str, str] = field(default_factory=dict)
    npc_generation_in_progress: set[str] = field(default_factory=set)
    location_generation_in_progress: bool = False
    discovered_scene_ids: set[str] = field(default_factory=set)
    discovered_anchors: set[str] = field(default_factory=set)
    anchor_last_scene: Dict[str, str] = field(default_factory=dict)
    world_state: dict = field(
        default_factory=lambda: {
            "time_of_day": "morning",
            "day_counter": 1,
            "global_tension": 18,
            "instability_level": 12,
        }
    )
    dungeon_profiles: Dict[str, dict] = field(default_factory=dict)
    active_dungeon_run: dict | None = None
    dungeon_generation_in_progress: bool = False
    gm_state: dict = field(
        default_factory=lambda: {
            "player_name": "l'Éveillé",
            "location": "inconnu",
            "location_id": "inconnu",
            "flags": {},
        }
    )
    left_panel_tab: str = "carte"
    player_sheet: dict = field(default_factory=dict)
    player_sheet_ready: bool = False
    player_sheet_missing: List[str] = field(default_factory=list)
    player_sheet_generation_in_progress: bool = False
    player_progress_log: List[dict] = field(default_factory=list)
    player_skills: List[dict] = field(default_factory=list)
    skill_points: int = 1
    player_corruption_level: int = 0
    skill_training_in_progress: bool = False
    skill_training_log: List[dict] = field(default_factory=list)
    skill_passive_practice: Dict[str, dict] = field(default_factory=dict)
    equipped_items: Dict[str, str] = field(
        default_factory=lambda: {
            "weapon": "",
            "armor": "",
            "accessory_1": "",
            "accessory_2": "",
        }
    )
    selected_equipped_slot: str = ""
    quests: List[dict] = field(default_factory=list)
    quest_seq: int = 0
    npc_dialogue_counts: Dict[str, int] = field(default_factory=dict)
    npc_quests_given: Dict[str, int] = field(default_factory=dict)
    quest_generation_in_progress: set[str] = field(default_factory=set)
    quest_counters: dict = field(
        default_factory=lambda: {
            "player_messages_sent": 0,
            "dungeon_floors_cleared": 0,
        }
    )
    conversation_short_term: Dict[str, List[dict]] = field(default_factory=dict)
    conversation_long_term: Dict[str, List[dict]] = field(default_factory=dict)
    conversation_global_long_term: List[dict] = field(default_factory=list)
    faction_reputation: Dict[str, int] = field(default_factory=dict)
    faction_reputation_log: List[dict] = field(default_factory=list)
    faction_states: Dict[str, dict] = field(default_factory=dict)
    trade_session: TradeSession = field(default_factory=idle_trade_session)
    travel_state: TravelState = field(default_factory=idle_travel_state)
    # Temps de monde persistant (minutes écoulées depuis l'epoch fantasy du jeu).
    world_time_minutes: int = (2 * 24 * 60) + (8 * 60)

    # Inventaire
    carried: InventoryGrid = field(default_factory=lambda: InventoryGrid.empty(6, 4))
    storage: InventoryGrid = field(default_factory=lambda: InventoryGrid.empty(10, 6))
    selected_slot: tuple[str, int] | None = None  # ("carried"|"storage", idx)
    item_defs: dict[str, object] = field(default_factory=dict)  # on précisera après
    skill_defs: dict[str, object] = field(default_factory=dict)

    # --- Narrator media (image par défaut + vidéos ponctuelles) ---
    narrator_default_image_url: str = "/assets/ataryxia.png"
    narrator_media_url: str = "/assets/ataryxia.png"  # peut devenir un .mp4
    narrator_media_expires_at: float = 0.0  # quand revenir à l'image fixe
    narrator_messages_since_last_media: int = 0

    def current_scene(self) -> Scene:
        return self.scenes[self.current_scene_id]

    def push(self, speaker: str, text: str, *, count_for_media: bool = True) -> None:
        self.chat.append(ChatMessage(speaker=speaker, text=text))
        if len(self.chat) > CHAT_HISTORY_MAX_ITEMS:
            del self.chat[:-CHAT_HISTORY_MAX_ITEMS]
        if count_for_media:
            self.narrator_messages_since_last_media += 1

    def set_scene(self, scene_id: str) -> None:
        if scene_id not in self.scenes:
            return
        self.current_scene_id = scene_id
        self.selected_npc = None
        scene = self.current_scene()
        self.discovered_scene_ids.add(scene_id)
        if scene.map_anchor:
            self.discovered_anchors.add(scene.map_anchor)
            self.anchor_last_scene[scene.map_anchor] = scene_id
        self.sync_world_state()

    def advance_world_time(self, minutes: int) -> int:
        delta = max(0, int(minutes))
        if delta <= 0:
            return 0
        self.world_time_minutes = max(0, int(self.world_time_minutes) + delta)
        self.sync_world_state(drift_minutes=delta)
        return delta

    def sync_travel_state(self) -> None:
        self.travel_state = normalize_travel_state(getattr(self, "travel_state", None))

    def sync_trade_session(self) -> None:
        self.trade_session = normalize_trade_session(getattr(self, "trade_session", None))

    def sync_world_state(self, *, drift_minutes: int = 0) -> None:
        if not isinstance(self.world_state, dict):
            self.world_state = {}
        ws = self.world_state

        total_minutes = max(0, int(self.world_time_minutes))
        minute_of_day = total_minutes % _DAY_MINUTES
        hour = minute_of_day // 60
        if 5 <= hour < 12:
            ws["time_of_day"] = "morning"
        elif 12 <= hour < 18:
            ws["time_of_day"] = "afternoon"
        elif 18 <= hour < 23:
            ws["time_of_day"] = "nightfall"
        else:
            ws["time_of_day"] = "night"

        ws["day_counter"] = max(1, (total_minutes // _DAY_MINUTES) + 1)
        ws["global_tension"] = max(0, min(100, int(ws.get("global_tension") or 18)))
        ws["instability_level"] = max(0, min(100, int(ws.get("instability_level") or 12)))

        drift = max(0, int(drift_minutes))
        if drift > 0:
            instability_gain = min(4, max(0, drift // 180))
            ws["instability_level"] = max(0, min(100, int(ws["instability_level"]) + instability_gain))
            tension_gain = 1 if drift >= 180 else 0
            ws["global_tension"] = max(0, min(100, int(ws["global_tension"]) + tension_gain))

    def set_narrator_video(self, video_url: str, duration_s: float = 8.0) -> None:
        self.narrator_media_url = video_url
        self.narrator_media_expires_at = time.time() + duration_s

    def ensure_narrator_image_if_expired(self) -> bool:
        """Revient à l'image fixe si la vidéo a expiré. Retourne True si changement."""
        if self.narrator_media_url.endswith(".mp4") and time.time() >= self.narrator_media_expires_at:
            self.narrator_media_url = self.narrator_default_image_url
            self.narrator_media_expires_at = 0.0
            return True
        return False
