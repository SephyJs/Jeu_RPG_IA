from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from app.core.events import EventBus, get_global_event_bus

from .craft_manager import CraftManager
from .dungeon_manager import DungeonManager
from .economy_manager import EconomyManager
from .gamemaster import GameMaster
from .location_manager import LocationManager
from .loot_manager import LootManager
from .monster_manager import MonsterManager
from .npc_manager import NPCProfileManager
from .ollama_client import OllamaClient
from .player_sheet_manager import PlayerSheetManager
from .quest_manager import QuestManager
from .skill_manager import SkillManager


@dataclass(frozen=True)
class GameRuntimeServices:
    event_bus: EventBus
    llm: OllamaClient
    gm: GameMaster
    npc_manager: NPCProfileManager
    location_manager: LocationManager
    dungeon_manager: DungeonManager
    quest_manager: QuestManager
    player_sheet_manager: PlayerSheetManager
    loot_manager: LootManager
    skill_manager: SkillManager
    monster_manager: MonsterManager
    economy_manager: EconomyManager
    craft_manager: CraftManager


def build_runtime_services(
    *,
    data_dir: str = "data",
    monsters_dir: str = "data/monsters",
    skills_catalog_path: str = "data/skills_catalog.json",
    crafting_data_path: str = "data/crafting_recipes.json",
    event_bus: EventBus | None = None,
    llm: OllamaClient | None = None,
) -> GameRuntimeServices:
    bus = event_bus if isinstance(event_bus, EventBus) else get_global_event_bus()
    llm_client = llm if isinstance(llm, OllamaClient) else OllamaClient()

    return GameRuntimeServices(
        event_bus=bus,
        llm=llm_client,
        gm=GameMaster(llm_client, seed=123, event_bus=bus),
        npc_manager=NPCProfileManager(llm_client),
        location_manager=LocationManager(llm_client),
        dungeon_manager=DungeonManager(llm_client),
        quest_manager=QuestManager(llm_client),
        player_sheet_manager=PlayerSheetManager(llm_client),
        loot_manager=LootManager(llm_client, data_dir=data_dir),
        skill_manager=SkillManager(llm_client, data_path=skills_catalog_path),
        monster_manager=MonsterManager(data_dir=monsters_dir),
        economy_manager=EconomyManager(data_dir=data_dir),
        craft_manager=CraftManager(data_path=crafting_data_path),
    )


_runtime_lock = Lock()
_runtime_services: GameRuntimeServices | None = None


def get_runtime_services() -> GameRuntimeServices:
    global _runtime_services
    if isinstance(_runtime_services, GameRuntimeServices):
        return _runtime_services

    with _runtime_lock:
        if not isinstance(_runtime_services, GameRuntimeServices):
            _runtime_services = build_runtime_services()
    return _runtime_services


def set_runtime_services(services: GameRuntimeServices | None) -> None:
    global _runtime_services
    with _runtime_lock:
        _runtime_services = services


def reset_runtime_services() -> None:
    global _runtime_services
    with _runtime_lock:
        previous = _runtime_services
        _runtime_services = None

    if isinstance(previous, GameRuntimeServices):
        try:
            previous.gm.close()
        except Exception:
            pass
