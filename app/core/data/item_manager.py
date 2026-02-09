from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
import re


class DataError(RuntimeError):
    pass


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    stack_max: int
    type: str = "misc"
    slot: str = ""
    rarity: str = "common"
    description: str = ""
    stat_bonuses: dict[str, int] | None = None
    effects: list[dict] | None = None
    value_gold: int = 0


class ItemsManager:
    def __init__(self, data_dir: str = "data") -> None:
        self.items_dir = Path(data_dir) / "items"
        self.items_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, ItemDef]:
        if not self.items_dir.exists():
            return {}

        items: dict[str, ItemDef] = {}
        for p in sorted(self.items_dir.glob("*.json")):
            raw = json.loads(p.read_text(encoding="utf-8"))
            item_id = raw.get("id")
            name = raw.get("name")
            stack_max = raw.get("stack_max", 1)
            item_type = str(raw.get("type") or "misc").strip().casefold()
            slot = str(raw.get("slot") or "").strip().casefold()
            rarity = str(raw.get("rarity") or "common").strip().casefold()
            description = str(raw.get("description") or "").strip()
            stat_bonuses_raw = raw.get("stat_bonuses")
            effects_raw = raw.get("effects")
            value_gold_raw = raw.get("value_gold", 0)

            if not isinstance(item_id, str) or not item_id:
                raise DataError(f"{p.as_posix()}: id invalide")
            if not isinstance(name, str) or not name:
                raise DataError(f"{p.as_posix()}: name invalide")
            if not isinstance(stack_max, int) or stack_max <= 0:
                raise DataError(f"{p.as_posix()}: stack_max invalide")

            stat_bonuses: dict[str, int] = {}
            if isinstance(stat_bonuses_raw, dict):
                for key, value in stat_bonuses_raw.items():
                    if not isinstance(key, str):
                        continue
                    try:
                        v = int(value)
                    except (TypeError, ValueError):
                        continue
                    if v == 0:
                        continue
                    stat_bonuses[key] = v

            effects: list[dict] = []
            if isinstance(effects_raw, list):
                for effect in effects_raw[:8]:
                    if isinstance(effect, dict):
                        effects.append(effect)

            try:
                value_gold = int(value_gold_raw)
            except (TypeError, ValueError):
                value_gold = 0
            value_gold = max(0, value_gold)

            items[item_id] = ItemDef(
                id=item_id,
                name=name,
                stack_max=stack_max,
                type=item_type or "misc",
                slot=slot,
                rarity=rarity or "common",
                description=description,
                stat_bonuses=stat_bonuses,
                effects=effects,
                value_gold=value_gold,
            )

        return items

    def save_item(self, payload: dict) -> ItemDef:
        item_id_raw = str(payload.get("id") or "").strip().casefold()
        item_id = re.sub(r"[^a-z0-9_]+", "_", item_id_raw).strip("_")
        if not item_id:
            raise DataError("item id invalide")

        name = str(payload.get("name") or "").strip()
        if not name:
            raise DataError("item name invalide")

        stack_max_raw = payload.get("stack_max", 1)
        try:
            stack_max = int(stack_max_raw)
        except (TypeError, ValueError):
            stack_max = 1
        stack_max = max(1, min(stack_max, 999))

        item_type = str(payload.get("type") or "misc").strip().casefold() or "misc"
        slot = str(payload.get("slot") or "").strip().casefold()
        rarity = str(payload.get("rarity") or "common").strip().casefold() or "common"
        description = str(payload.get("description") or "").strip()
        value_gold_raw = payload.get("value_gold", 0)
        try:
            value_gold = int(value_gold_raw)
        except (TypeError, ValueError):
            value_gold = 0
        value_gold = max(0, min(value_gold, 99999))

        stat_bonuses_raw = payload.get("stat_bonuses")
        stat_bonuses: dict[str, int] = {}
        if isinstance(stat_bonuses_raw, dict):
            for key, value in stat_bonuses_raw.items():
                if not isinstance(key, str):
                    continue
                try:
                    delta = int(value)
                except (TypeError, ValueError):
                    continue
                if delta == 0:
                    continue
                stat_bonuses[key] = max(-999, min(delta, 999))

        effects_raw = payload.get("effects")
        effects: list[dict] = []
        if isinstance(effects_raw, list):
            for effect in effects_raw[:8]:
                if isinstance(effect, dict):
                    effects.append(effect)

        normalized = {
            "id": item_id,
            "name": name,
            "stack_max": stack_max,
            "type": item_type,
            "slot": slot,
            "rarity": rarity,
            "description": description,
            "stat_bonuses": stat_bonuses,
            "effects": effects,
            "value_gold": value_gold,
        }
        path = self.items_dir / f"{item_id}.json"
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

        return ItemDef(
            id=item_id,
            name=name,
            stack_max=stack_max,
            type=item_type,
            slot=slot,
            rarity=rarity,
            description=description,
            stat_bonuses=stat_bonuses,
            effects=effects,
            value_gold=value_gold,
        )
