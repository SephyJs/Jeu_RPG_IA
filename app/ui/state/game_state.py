from __future__ import annotations
from app.ui.state.inventory import InventoryGrid, ItemStack

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    next_scene_id: Optional[str] = None


@dataclass
class Scene:
    id: str
    title: str
    narrator_text: str
    map_anchor: str = ""
    generated: bool = False
    npc_names: List[str] = field(default_factory=list)
    choices: List[Choice] = field(default_factory=list)


@dataclass
class ChatMessage:
    speaker: str
    text: str


@dataclass
class PlayerProfile:
    name: str = "L'Éveillé"
    hp: int = 20
    max_hp: int = 20
    gold: int = 10


@dataclass
class GameState:
    player: PlayerProfile = field(default_factory=PlayerProfile)
    scenes: Dict[str, Scene] = field(default_factory=dict)
    current_scene_id: str = "city"
    chat: List[ChatMessage] = field(default_factory=list)
    chat_draft: str = ""
    selected_npc: Optional[str] = None
    npc_profiles: Dict[str, dict] = field(default_factory=dict)
    npc_registry: Dict[str, dict] = field(default_factory=dict)
    npc_scene_bindings: Dict[str, str] = field(default_factory=dict)
    npc_generation_in_progress: set[str] = field(default_factory=set)
    location_generation_in_progress: bool = False
    discovered_scene_ids: set[str] = field(default_factory=set)
    discovered_anchors: set[str] = field(default_factory=set)
    anchor_last_scene: Dict[str, str] = field(default_factory=dict)
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

    def advance_world_time(self, minutes: int) -> int:
        delta = max(0, int(minutes))
        if delta <= 0:
            return 0
        self.world_time_minutes = max(0, int(self.world_time_minutes) + delta)
        return delta

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
