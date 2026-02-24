from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
    npc_names: list[str] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)


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
