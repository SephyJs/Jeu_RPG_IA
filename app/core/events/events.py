from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OnNpcTensionChanged:
    npc_key: str
    npc_name: str
    old_value: int
    new_value: int
    reason: str = ""


@dataclass(frozen=True)
class OnQuestUpdated:
    quest_id: str
    status: str
    source_npc_key: str = ""
    source_npc_name: str = ""


@dataclass(frozen=True)
class OnTradeCompleted:
    npc_key: str
    npc_name: str
    item_id: str
    qty_done: int
    gold_delta: int
    action: str


@dataclass(frozen=True)
class OnLocationEntered:
    scene_id: str
    scene_title: str
    map_anchor: str = ""
    context: dict[str, Any] = field(default_factory=dict)
