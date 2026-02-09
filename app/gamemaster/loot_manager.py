from __future__ import annotations

import json
import random
import re
from dataclasses import asdict
from typing import Any

from app.core.data.item_manager import ItemsManager, ItemDef

from .models import model_for


RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")
EQUIPMENT_SLOTS = ("weapon", "armor", "accessory_1", "accessory_2")
STAT_KEYS = (
    "pv_max",
    "mana_max",
    "force",
    "intelligence",
    "magie",
    "defense",
    "sagesse",
    "agilite",
    "dexterite",
    "chance",
    "charisme",
)


class LootManager:
    def __init__(self, llm: Any, *, data_dir: str = "data") -> None:
        self.llm = llm
        self.items = ItemsManager(data_dir=data_dir)
        self.rng = random.Random(20260209)

    def load_item_defs(self) -> dict[str, ItemDef]:
        return self.items.load_all()

    async def generate_loot(
        self,
        *,
        source_type: str,
        floor: int,
        anchor: str,
        known_items: dict[str, ItemDef],
    ) -> dict:
        fallback = self._fallback_loot(source_type=source_type, floor=floor, known_items=known_items)
        if self.llm is None:
            return fallback

        prompt = self._build_prompt(source_type=source_type, floor=floor, anchor=anchor, known_items=known_items)
        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.25,
                num_ctx=4096,
                num_predict=750,
                stop=None,
            )
            payload = json.loads(self._extract_json(raw))
            return self._normalize_loot_payload(payload, source_type=source_type, floor=floor, known_items=known_items, fallback=fallback)
        except Exception:
            return fallback

    def ensure_item_exists(self, loot: dict, known_items: dict[str, ItemDef]) -> tuple[str, dict[str, ItemDef], bool]:
        item_id = str(loot.get("item_id") or "").strip().casefold()
        if item_id and item_id in known_items:
            return item_id, known_items, False

        new_item = loot.get("new_item") if isinstance(loot.get("new_item"), dict) else None
        if new_item is None:
            # dernier recours: créer un item misc minimal si ID demandé inexistant
            new_item = {
                "id": item_id or f"item_{self.rng.randint(1000, 9999)}",
                "name": str(loot.get("item_name") or "Objet inconnu"),
                "type": "misc",
                "slot": "",
                "rarity": str(loot.get("rarity") or "common"),
                "description": "Un objet recupere lors d'une expedition.",
                "stack_max": 8,
                "stat_bonuses": {},
                "effects": [],
                "value_gold": 5,
            }

        normalized_item = self._normalize_item_payload(new_item, floor=max(1, int(loot.get("floor_hint", 1) or 1)))
        if normalized_item["id"] in known_items:
            normalized_item["id"] = self._make_unique_item_id(normalized_item["id"], known_items)

        saved = self.items.save_item(normalized_item)
        updated = dict(known_items)
        updated[saved.id] = saved
        return saved.id, updated, True

    def normalize_equip_state(self, equipped_items: object) -> dict[str, str]:
        out = {slot: "" for slot in EQUIPMENT_SLOTS}
        if not isinstance(equipped_items, dict):
            return out
        for slot in EQUIPMENT_SLOTS:
            value = equipped_items.get(slot)
            out[slot] = str(value).strip().casefold() if isinstance(value, str) else ""
        return out

    def compute_equipment_bonus(self, item_defs: dict[str, ItemDef], equipped_items: dict[str, str]) -> dict[str, int]:
        totals = {k: 0 for k in STAT_KEYS}
        for slot in EQUIPMENT_SLOTS:
            item_id = str(equipped_items.get(slot) or "").strip().casefold()
            if not item_id:
                continue
            item = item_defs.get(item_id)
            if not item:
                continue
            bonuses = item.stat_bonuses or {}
            for stat, value in bonuses.items():
                if stat not in totals:
                    continue
                try:
                    delta = int(value)
                except (TypeError, ValueError):
                    continue
                totals[stat] += delta
        return totals

    def apply_equipment_to_sheet(self, sheet: dict, item_defs: dict[str, ItemDef], equipped_items: dict[str, str]) -> dict:
        out = dict(sheet) if isinstance(sheet, dict) else {}
        base_stats = out.get("stats")
        if not isinstance(base_stats, dict):
            return out

        bonuses = self.compute_equipment_bonus(item_defs, equipped_items)
        effective = dict(base_stats)
        for key in STAT_KEYS:
            try:
                base_value = int(base_stats.get(key, 0))
            except (TypeError, ValueError):
                base_value = 0
            effective[key] = max(0, base_value + int(bonuses.get(key, 0)))

        # cohérence dérivées
        try:
            effective["pv"] = min(int(base_stats.get("pv", effective.get("pv_max", 0))), int(effective.get("pv_max", 0)))
        except (TypeError, ValueError):
            pass
        try:
            effective["mana"] = min(int(base_stats.get("mana", effective.get("mana_max", 0))), int(effective.get("mana_max", 0)))
        except (TypeError, ValueError):
            pass

        out["effective_stats"] = effective
        out["equipment_bonuses"] = bonuses
        out["equipment_runtime"] = self._render_equipment_view(equipped_items, item_defs)
        return out

    def choose_equipment_slot(self, item: ItemDef, equipped_items: dict[str, str]) -> str | None:
        slot = str(item.slot or "").strip().casefold()
        item_type = str(item.type or "").strip().casefold()

        if slot in {"weapon", "armor"}:
            return slot
        if slot in {"accessory_1", "accessory_2"}:
            return slot
        if slot in {"accessory", "ring", "amulet", "trinket"} or item_type in {"accessory", "trinket"}:
            if not equipped_items.get("accessory_1"):
                return "accessory_1"
            return "accessory_2"
        if item_type in {"weapon"}:
            return "weapon"
        if item_type in {"armor", "shield", "helm"}:
            return "armor"
        return None

    def can_equip(self, item: ItemDef) -> bool:
        return self.choose_equipment_slot(item, {"accessory_1": "", "accessory_2": "", "weapon": "", "armor": ""}) is not None

    def _render_equipment_view(self, equipped_items: dict[str, str], item_defs: dict[str, ItemDef]) -> dict:
        out: dict[str, dict] = {}
        for slot in EQUIPMENT_SLOTS:
            item_id = str(equipped_items.get(slot) or "").strip().casefold()
            if not item_id:
                out[slot] = {"item_id": "", "name": "", "rarity": ""}
                continue
            item = item_defs.get(item_id)
            if not item:
                out[slot] = {"item_id": item_id, "name": item_id, "rarity": ""}
            else:
                out[slot] = {"item_id": item.id, "name": item.name, "rarity": item.rarity}
        return out

    def _fallback_loot(self, *, source_type: str, floor: int, known_items: dict[str, ItemDef]) -> dict:
        existing_ids = list(known_items.keys())
        if existing_ids:
            chosen = self.rng.choice(existing_ids)
            qty = 1 if self._is_equipment(known_items[chosen]) else self.rng.randint(1, 3)
            return {
                "item_id": chosen,
                "qty": qty,
                "rarity": known_items[chosen].rarity or "common",
                "new_item": None,
                "floor_hint": max(1, floor),
            }

        new_item = self._normalize_item_payload(
            {
                "id": "fragment_etrange",
                "name": "Fragment etrange",
                "type": "accessory",
                "slot": "accessory",
                "rarity": "common",
                "description": "Un fragment charge d'une energie faible.",
                "stack_max": 6,
                "stat_bonuses": {"chance": 1},
                "effects": [{"kind": "flavor", "value": 1}],
                "value_gold": 12,
            },
            floor=max(1, floor),
        )
        return {
            "item_id": new_item["id"],
            "qty": 1,
            "rarity": new_item["rarity"],
            "new_item": new_item,
            "floor_hint": max(1, floor),
        }

    def _build_prompt(self, *, source_type: str, floor: int, anchor: str, known_items: dict[str, ItemDef]) -> str:
        sample_existing = []
        for item in list(known_items.values())[:20]:
            sample_existing.append({"id": item.id, "name": item.name, "type": item.type, "rarity": item.rarity})

        schema = {
            "item_id": "id_existant_ou_nouvel_id",
            "qty": 1,
            "rarity": "common|uncommon|rare|epic|legendary",
            "new_item": {
                "id": "id_item",
                "name": "Nom lisible",
                "type": "weapon|armor|accessory|consumable|material|misc",
                "slot": "weapon|armor|accessory|accessory_1|accessory_2|",
                "rarity": "common",
                "description": "Description courte",
                "stack_max": 1,
                "stat_bonuses": {"force": 1},
                "effects": [{"kind": "heal", "value": 2}],
                "value_gold": 20,
            },
        }
        return (
            "Tu es un moteur de loot RPG. Reponds en JSON valide UNIQUEMENT.\n"
            "Objectif: loot aleatoire mais juste, adapte a la progression.\n"
            "Contraintes de justice:\n"
            "- pas d'objet absurdement fort pour le niveau\n"
            "- pour equipment: total de bonus modere\n"
            "- pour consumable/material: stack raisonnable\n"
            "- si item_id existe deja dans la liste, new_item peut etre null\n"
            "- si item_id n'existe pas, remplir new_item complet pour creation.\n"
            f"Contexte: source={source_type}, etage={max(1, floor)}, zone={anchor}\n"
            f"Items existants (extraits): {json.dumps(sample_existing, ensure_ascii=False)}\n"
            "Schema attendu:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _normalize_loot_payload(
        self,
        payload: object,
        *,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        fallback: dict,
    ) -> dict:
        if not isinstance(payload, dict):
            return fallback

        item_id = str(payload.get("item_id") or "").strip().casefold()
        rarity = str(payload.get("rarity") or "").strip().casefold()
        if rarity not in RARITY_ORDER:
            rarity = self._roll_rarity(source_type=source_type)

        new_item = payload.get("new_item") if isinstance(payload.get("new_item"), dict) else None
        if not item_id and new_item:
            item_id = str(new_item.get("id") or "").strip().casefold()

        if not item_id:
            return fallback

        qty = self._safe_int(payload.get("qty"), 1)
        qty = max(1, min(qty, 12))

        if item_id in known_items:
            if self._is_equipment(known_items[item_id]):
                qty = 1
        elif new_item is not None:
            new_item = self._normalize_item_payload(new_item, floor=max(1, floor), forced_rarity=rarity)
            item_id = new_item["id"]
            if new_item.get("type") in {"weapon", "armor", "accessory"}:
                qty = 1
        else:
            return fallback

        return {
            "item_id": item_id,
            "qty": qty,
            "rarity": rarity,
            "new_item": new_item,
            "floor_hint": max(1, floor),
        }

    def _normalize_item_payload(self, raw: dict, *, floor: int, forced_rarity: str = "") -> dict:
        item_id = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("id") or "").strip().casefold()).strip("_")
        if not item_id:
            item_id = f"item_{self.rng.randint(1000, 9999)}"

        name = str(raw.get("name") or item_id.replace("_", " ").title()).strip()[:80]
        item_type = str(raw.get("type") or "misc").strip().casefold()
        if item_type not in {"weapon", "armor", "accessory", "consumable", "material", "misc"}:
            item_type = "misc"

        slot = str(raw.get("slot") or "").strip().casefold()
        if item_type == "weapon":
            slot = "weapon"
        elif item_type == "armor":
            slot = "armor"
        elif item_type == "accessory":
            if slot not in {"accessory", "accessory_1", "accessory_2"}:
                slot = "accessory"
        else:
            slot = ""

        rarity = forced_rarity or str(raw.get("rarity") or "common").strip().casefold()
        if rarity not in RARITY_ORDER:
            rarity = "common"

        desc = str(raw.get("description") or "").strip()

        stack_max = self._safe_int(raw.get("stack_max"), 1)
        if item_type in {"weapon", "armor", "accessory"}:
            stack_max = 1
        else:
            stack_max = max(1, min(stack_max, 64))

        stat_bonuses_raw = raw.get("stat_bonuses")
        stat_bonuses: dict[str, int] = {}
        if isinstance(stat_bonuses_raw, dict):
            for key, value in stat_bonuses_raw.items():
                if str(key) not in STAT_KEYS:
                    continue
                delta = max(-20, min(self._safe_int(value), 20))
                if delta != 0:
                    stat_bonuses[str(key)] = delta

        stat_budget = self._stat_budget(rarity=rarity, floor=floor, item_type=item_type)
        stat_bonuses = self._limit_stat_bonuses(stat_bonuses, stat_budget)

        effects_raw = raw.get("effects")
        effects: list[dict] = []
        if isinstance(effects_raw, list):
            for effect in effects_raw[:6]:
                if isinstance(effect, dict):
                    effects.append(effect)

        value_gold = self._safe_int(raw.get("value_gold"), 0)
        value_gold = max(1, min(value_gold, 50000))

        return {
            "id": item_id,
            "name": name,
            "type": item_type,
            "slot": slot,
            "rarity": rarity,
            "description": desc,
            "stack_max": stack_max,
            "stat_bonuses": stat_bonuses,
            "effects": effects,
            "value_gold": value_gold,
        }

    def _roll_rarity(self, *, source_type: str) -> str:
        table = {
            "monster": [("common", 0.60), ("uncommon", 0.28), ("rare", 0.10), ("epic", 0.02)],
            "mimic": [("common", 0.30), ("uncommon", 0.35), ("rare", 0.25), ("epic", 0.08), ("legendary", 0.02)],
            "treasure": [("common", 0.25), ("uncommon", 0.40), ("rare", 0.22), ("epic", 0.10), ("legendary", 0.03)],
        }
        dist = table.get(source_type, table["treasure"])
        r = self.rng.random()
        cumulative = 0.0
        for name, weight in dist:
            cumulative += weight
            if r <= cumulative:
                return name
        return dist[-1][0]

    def _stat_budget(self, *, rarity: str, floor: int, item_type: str) -> int:
        base = {
            "common": 1,
            "uncommon": 3,
            "rare": 5,
            "epic": 8,
            "legendary": 12,
        }.get(rarity, 2)
        progression = max(0, floor // 6)
        if item_type in {"consumable", "material", "misc"}:
            return 0
        return min(20, base + progression)

    def _limit_stat_bonuses(self, stats: dict[str, int], budget: int) -> dict[str, int]:
        if budget <= 0 or not stats:
            return {}
        filtered = {k: max(-8, min(v, 8)) for k, v in stats.items() if k in STAT_KEYS and v != 0}
        current = sum(abs(v) for v in filtered.values())
        if current <= budget:
            return filtered
        ratio = budget / float(current)
        scaled: dict[str, int] = {}
        for key, value in filtered.items():
            new_v = int(value * ratio)
            if new_v == 0:
                new_v = 1 if value > 0 else -1
            scaled[key] = new_v
        # second pass strict budget
        while sum(abs(v) for v in scaled.values()) > budget:
            key = max(scaled, key=lambda k: abs(scaled[k]))
            if scaled[key] > 0:
                scaled[key] -= 1
            elif scaled[key] < 0:
                scaled[key] += 1
            if scaled[key] == 0:
                del scaled[key]
                if not scaled:
                    break
        return scaled

    def _make_unique_item_id(self, base_id: str, known_items: dict[str, ItemDef]) -> str:
        idx = 2
        candidate = f"{base_id}_{idx}"
        while candidate in known_items:
            idx += 1
            candidate = f"{base_id}_{idx}"
        return candidate

    def _is_equipment(self, item: ItemDef) -> bool:
        t = str(item.type or "").strip().casefold()
        return t in {"weapon", "armor", "accessory"} or str(item.slot or "").strip().casefold() in {
            "weapon",
            "armor",
            "accessory",
            "accessory_1",
            "accessory_2",
        }

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def item_def_to_dict(self, item: ItemDef) -> dict:
        return asdict(item)
