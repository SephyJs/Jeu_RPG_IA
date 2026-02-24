from __future__ import annotations

import json
import random
import re
import unicodedata
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
_RARITY_RANK = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 4,
}
_BUFF_STATS = ("force", "dexterite", "agilite", "defense", "sagesse", "intelligence", "magie")


class LootManager:
    def __init__(self, llm: Any, *, data_dir: str = "data") -> None:
        self.llm = llm
        self.items = ItemsManager(data_dir=data_dir)
        self.rng = random.Random(20260209)
        self._last_fallback_item_id = ""
        self._recent_fallback_item_ids: list[str] = []

    def load_item_defs(self) -> dict[str, ItemDef]:
        return self.items.load_all()

    async def generate_loot(
        self,
        *,
        source_type: str,
        floor: int,
        anchor: str,
        known_items: dict[str, ItemDef],
        hint_text: str = "",
    ) -> dict:
        source_key = str(source_type or "").strip().casefold()
        floor_hint = max(1, int(floor or 1))
        fallback = self._fallback_loot(
            source_type=source_key,
            floor=floor_hint,
            known_items=known_items,
            hint_text=hint_text,
        )
        if self.llm is None:
            result = self._apply_diversity_guard(
                fallback,
                source_type=source_key,
                floor=floor_hint,
                known_items=known_items,
                hint_text=hint_text,
            )
            self._remember_recent_loot(str(result.get("item_id") or ""))
            return result

        prompt = self._build_prompt(
            source_type=source_key,
            floor=floor_hint,
            anchor=anchor,
            known_items=known_items,
            hint_text=hint_text,
        )
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
            normalized = self._normalize_loot_payload(
                payload,
                source_type=source_key,
                floor=floor_hint,
                known_items=known_items,
                fallback=fallback,
            )
            result = self._apply_diversity_guard(
                normalized,
                source_type=source_key,
                floor=floor_hint,
                known_items=known_items,
                hint_text=hint_text,
            )
            self._remember_recent_loot(str(result.get("item_id") or ""))
            return result
        except Exception:
            result = self._apply_diversity_guard(
                fallback,
                source_type=source_key,
                floor=floor_hint,
                known_items=known_items,
                hint_text=hint_text,
            )
            self._remember_recent_loot(str(result.get("item_id") or ""))
            return result

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

    def _fallback_loot(
        self,
        *,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        hint_text: str = "",
    ) -> dict:
        hinted = self._fallback_loot_from_hint(
            source_type=source_type,
            floor=floor,
            known_items=known_items,
            hint_text=hint_text,
        )
        if isinstance(hinted, dict):
            return hinted

        potion_roll = self.rng.random()
        if potion_roll <= self._potion_drop_chance(source_type):
            potion_loot = self._fallback_potion_loot(
                source_type=source_type,
                floor=max(1, floor),
                known_items=known_items,
            )
            if isinstance(potion_loot, dict):
                return potion_loot

        existing_ids = list(known_items.keys())
        if existing_ids and self.rng.random() <= self._existing_drop_chance(source_type=source_type, floor=floor):
            chosen = self._pick_existing_item_for_source(known_items, source_type=source_type, floor=floor)
            if chosen and chosen in known_items:
                item = known_items[chosen]
                qty = self._fallback_existing_qty(item, source_type=source_type, floor=floor)
                return {
                    "item_id": chosen,
                    "qty": qty,
                    "rarity": item.rarity or "common",
                    "new_item": None,
                    "floor_hint": max(1, floor),
                }

        new_item = self._fallback_generated_item(
            source_type=source_type,
            floor=max(1, floor),
            known_items=known_items,
            force_non_consumable=str(source_type or "").strip().casefold() in {"mimic", "treasure", "boss"},
        )
        return {
            "item_id": new_item["id"],
            "qty": 1,
            "rarity": new_item["rarity"],
            "new_item": new_item,
            "floor_hint": max(1, floor),
        }

    def _fallback_loot_from_hint(
        self,
        *,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        hint_text: str,
    ) -> dict | None:
        hint_raw = str(hint_text or "").strip()
        if not hint_raw:
            return None

        hint_ascii = unicodedata.normalize("NFKD", hint_raw).encode("ascii", "ignore").decode("ascii").lower()
        hint_slug = re.sub(r"[^a-z0-9]+", "_", hint_ascii).strip("_")
        if not hint_slug:
            return None

        if hint_slug in known_items:
            item = known_items[hint_slug]
            qty = 1 if self._is_equipment(item) else self.rng.randint(1, 3)
            return {
                "item_id": hint_slug,
                "qty": qty,
                "rarity": item.rarity or self._roll_rarity(source_type=source_type, floor=max(1, floor)),
                "new_item": None,
                "floor_hint": max(1, floor),
            }

        rarity = self._roll_rarity(source_type=source_type, floor=max(1, floor))
        item_type = "misc"
        slot = ""
        stack_max = 6
        stats: dict[str, int] = {}
        effects: list[dict] = []
        desc = f"Trouve dans le donjon: {hint_raw}."

        if any(k in hint_ascii for k in ("epee", "lame", "hache", "dague", "arc", "lance", "marteau", "flamberge")):
            item_type = "weapon"
            slot = "weapon"
            stack_max = 1
            stats = {"force": 1 + max(0, floor // 10)}
        elif any(k in hint_ascii for k in ("armure", "bouclier", "heaume", "casque", "plastron")):
            item_type = "armor"
            slot = "armor"
            stack_max = 1
            stats = {"defense": 1 + max(0, floor // 10)}
        elif any(k in hint_ascii for k in ("anneau", "amulette", "talisman", "relique", "fragment", "pierre", "gemme")):
            item_type = "accessory"
            slot = "accessory"
            stack_max = 1
            stats = {"chance": 1}
        elif any(k in hint_ascii for k in ("potion", "elixir", "onguent", "phiole")):
            item_type = "consumable"
            stack_max = 6
            effects = [self._build_potion_effect(rarity=rarity, floor=max(1, floor), hint_text=hint_ascii)]
        elif any(k in hint_ascii for k in ("livre", "grimoire", "tome", "parchemin", "necromancie", "necromancy")):
            item_type = "misc"
            stack_max = 2
            stats = {"intelligence": 1, "magie": 1}
            effects = [{"kind": "mana", "value": max(2, 2 + (floor // 8))}]
        elif any(k in hint_ascii for k in ("bourse", "piece", "pieces", "or", "coffre")):
            item_type = "material"
            stack_max = 12

        new_item = self._normalize_item_payload(
            {
                "id": hint_slug,
                "name": hint_raw[:80] or hint_slug.replace("_", " ").title(),
                "type": item_type,
                "slot": slot,
                "rarity": rarity,
                "description": desc,
                "stack_max": stack_max,
                "stat_bonuses": stats,
                "effects": effects,
                "value_gold": max(6, 8 + (floor * 2)),
            },
            floor=max(1, floor),
            forced_rarity=rarity,
        )
        if new_item["id"] in known_items:
            new_item["id"] = self._make_unique_item_id(new_item["id"], known_items)

        qty = 1 if item_type in {"weapon", "armor", "accessory"} else max(1, min(4, 1 + (floor // 8)))
        return {
            "item_id": new_item["id"],
            "qty": qty,
            "rarity": new_item["rarity"],
            "new_item": new_item,
            "floor_hint": max(1, floor),
        }

    def _fallback_generated_item(
        self,
        *,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        force_non_consumable: bool = False,
    ) -> dict:
        rarity = self._roll_rarity(source_type=source_type, floor=max(1, floor))
        base_pool: dict[str, list[tuple[str, str, float]]] = {
            "monster": [
                ("Lame ebrechee", "weapon", 2.4),
                ("Bouclier fendu", "armor", 2.0),
                ("Talisman de cendre", "accessory", 1.4),
                ("Griffe fossilisee", "material", 1.8),
                ("Os runique", "misc", 1.2),
                ("Phiole guerriere", "consumable", 0.9),
            ],
            "mimic": [
                ("Anneau du piege", "accessory", 2.4),
                ("Dague de coffre", "weapon", 1.9),
                ("Cuirasse grinçante", "armor", 1.3),
                ("Croc de mimic", "material", 1.7),
                ("Idole voleuse", "misc", 1.5),
                ("Phiole d'ombre", "consumable", 1.0),
            ],
            "treasure": [
                ("Grimoire ancestral", "misc", 2.4),
                ("Talisman ancien", "accessory", 2.2),
                ("Sceau antique", "material", 1.6),
                ("Lance de veille", "weapon", 1.4),
                ("Cuirasse d'apparat", "armor", 1.2),
                ("Elixir lunaire", "consumable", 0.8),
            ],
            "boss": [
                ("Lame du souverain", "weapon", 2.6),
                ("Aegis crepusculaire", "armor", 2.5),
                ("Anneau du tyran", "accessory", 2.3),
                ("Grimoire de domination", "misc", 1.9),
                ("Coeur cristallin", "material", 1.7),
                ("Elixir du conquerant", "consumable", 0.7),
            ],
        }
        pool = list(
            base_pool.get(
                source_type,
                [
                    ("Fragment etrange", "accessory", 1.6),
                    ("Parchemin use", "misc", 1.2),
                    ("Potion claire", "consumable", 0.9),
                ],
            )
        )
        if force_non_consumable:
            filtered = [row for row in pool if row[1] != "consumable"]
            if filtered:
                pool = filtered

        weighted_rows = [(name, item_type, max(0.05, float(weight))) for name, item_type, weight in pool]
        name, item_type, _ = self._weighted_choice(weighted_rows)
        slot = ""
        stack_max = 8
        stats: dict[str, int] = {}
        effects: list[dict] = []
        if item_type == "weapon":
            slot = "weapon"
            stack_max = 1
            stats = {"force": 1 + max(0, floor // 12)}
        elif item_type == "armor":
            slot = "armor"
            stack_max = 1
            stats = {"defense": 1 + max(0, floor // 12)}
        elif item_type == "accessory":
            slot = "accessory"
            stack_max = 1
            stats = {"chance": 1}
        elif item_type == "consumable":
            stack_max = 8
            effects = [self._build_potion_effect(rarity=rarity, floor=max(1, floor))]
        elif item_type == "misc":
            stack_max = 4
            effects = [{"kind": "mana", "value": max(2, 2 + (floor // 10))}]
            stats = {"sagesse": 1}

        base_id = re.sub(r"[^a-z0-9_]+", "_", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()).strip("_")
        if not base_id:
            base_id = f"loot_{source_type}_{floor}"
        if base_id in known_items:
            base_id = self._make_unique_item_id(base_id, known_items)

        return self._normalize_item_payload(
            {
                "id": base_id,
                "name": name,
                "type": item_type,
                "slot": slot,
                "rarity": rarity,
                "description": f"Recupere dans un donjon ({source_type}).",
                "stack_max": stack_max,
                "stat_bonuses": stats,
                "effects": effects,
                "value_gold": max(8, 10 + (floor * 2)),
            },
            floor=max(1, floor),
            forced_rarity=rarity,
        )

    def _existing_drop_chance(self, *, source_type: str, floor: int) -> float:
        source_key = str(source_type or "").strip().casefold()
        base = {
            "monster": 0.56,
            "mimic": 0.43,
            "treasure": 0.34,
            "boss": 0.24,
        }.get(source_key, 0.45)
        tier = max(0, (max(1, int(floor)) - 1) // 6)
        adjusted = base - min(0.20, tier * 0.02)
        return max(0.12, min(0.80, adjusted))

    def _item_type_weight(self, *, item_type: str, source_type: str) -> float:
        source_key = str(source_type or "").strip().casefold()
        item_key = str(item_type or "").strip().casefold()
        by_source = {
            "monster": {"weapon": 2.8, "armor": 2.2, "accessory": 1.5, "material": 1.7, "misc": 1.1, "consumable": 0.8},
            "mimic": {"weapon": 1.9, "armor": 1.4, "accessory": 2.6, "material": 1.6, "misc": 1.4, "consumable": 1.0},
            "treasure": {"weapon": 1.3, "armor": 1.2, "accessory": 2.7, "material": 1.5, "misc": 2.3, "consumable": 0.9},
            "boss": {"weapon": 2.5, "armor": 2.3, "accessory": 2.2, "material": 1.6, "misc": 1.8, "consumable": 0.7},
        }
        return max(0.05, float(by_source.get(source_key, by_source["treasure"]).get(item_key, 1.0)))

    def _weighted_choice(self, rows: list[tuple[Any, Any, float]]) -> tuple[Any, Any, float]:
        if not rows:
            return ("Fragment etrange", "misc", 1.0)
        total = sum(max(0.01, float(weight)) for _, _, weight in rows)
        if total <= 0:
            return rows[0]
        roll = self.rng.random() * total
        acc = 0.0
        for row in rows:
            weight = max(0.01, float(row[2]))
            acc += weight
            if roll <= acc:
                return row
        return rows[-1]

    def _pick_existing_item_for_source(
        self,
        known_items: dict[str, ItemDef],
        *,
        source_type: str,
        floor: int,
        exclude_ids: set[str] | None = None,
    ) -> str:
        rows: list[tuple[str, str, float]] = []
        excluded = {str(item_id or "").strip().casefold() for item_id in (exclude_ids or set()) if str(item_id or "").strip()}
        floor_target_rank = max(0, min(4, (max(1, int(floor)) - 1) // 8))
        for item_id, item in known_items.items():
            item_key = str(item_id or "").strip().casefold()
            if not item_key:
                continue
            if item_key in excluded:
                continue
            item_type = str(item.type or "").strip().casefold()
            if item_type not in {"weapon", "armor", "accessory", "consumable", "material", "misc"}:
                item_type = "misc"
            weight = self._item_type_weight(item_type=item_type, source_type=source_type)
            rarity_rank = _RARITY_RANK.get(str(item.rarity or "").strip().casefold(), 0)
            if rarity_rank < floor_target_rank:
                weight *= max(0.35, 1.0 - (0.17 * (floor_target_rank - rarity_rank)))
            else:
                weight *= 1.0 + min(0.18, 0.06 * (rarity_rank - floor_target_rank))

            if item_key == self._last_fallback_item_id:
                weight *= 0.06
            elif item_key in self._recent_fallback_item_ids:
                weight *= 0.30

            rows.append((item_key, item_type, max(0.01, weight)))

        if not rows:
            return ""
        picked_id, _, _ = self._weighted_choice(rows)
        return str(picked_id or "")

    def _fallback_existing_qty(self, item: ItemDef, *, source_type: str, floor: int) -> int:
        if self._is_equipment(item):
            return 1
        item_type = str(item.type or "").strip().casefold()
        if item_type == "consumable":
            bonus = {"monster": 0, "mimic": 1, "treasure": 1, "boss": 1}.get(str(source_type or "").strip().casefold(), 0)
            qty_max = min(7, max(2, 2 + bonus + (max(1, floor) // 12)))
            return self.rng.randint(1, qty_max)
        if item_type == "material":
            qty_max = min(8, max(2, 2 + (max(1, floor) // 10)))
            return self.rng.randint(1, qty_max)
        qty_max = min(4, max(2, 1 + (max(1, floor) // 16)))
        return self.rng.randint(1, qty_max)

    def _loot_item_type(self, loot: dict, known_items: dict[str, ItemDef]) -> str:
        if not isinstance(loot, dict):
            return ""
        new_item = loot.get("new_item") if isinstance(loot.get("new_item"), dict) else None
        if new_item is not None:
            return str(new_item.get("type") or "").strip().casefold()
        item_id = str(loot.get("item_id") or "").strip().casefold()
        item = known_items.get(item_id)
        return str(getattr(item, "type", "") or "").strip().casefold()

    def _looks_like_potion_hint(self, hint_text: str) -> bool:
        hint = unicodedata.normalize("NFKD", str(hint_text or "")).encode("ascii", "ignore").decode("ascii").lower()
        return any(token in hint for token in ("potion", "elixir", "phiole", "tonique", "soin", "mana"))

    def _apply_diversity_guard(
        self,
        loot: dict,
        *,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        hint_text: str,
    ) -> dict:
        if not isinstance(loot, dict):
            return loot
        if self._looks_like_potion_hint(hint_text):
            return loot

        source_key = str(source_type or "").strip().casefold()
        item_type = self._loot_item_type(loot, known_items)
        current_item_id = str(loot.get("item_id") or "").strip().casefold()

        # Empêche les drops en boucle d'un seul item (ex: épée unique), même si l'IA
        # propose toujours le même ID.
        if current_item_id and current_item_id == self._last_fallback_item_id:
            replacement = self._replacement_loot_for_repetition(
                repeated_item_id=current_item_id,
                source_type=source_key,
                floor=max(1, floor),
                known_items=known_items,
                hint_text=hint_text,
            )
            replacement_id = str(replacement.get("item_id") or "").strip().casefold() if isinstance(replacement, dict) else ""
            if replacement_id and replacement_id != current_item_id:
                return replacement

        if source_key in {"mimic", "treasure", "boss"} and item_type == "consumable":
            trigger = {"mimic": 0.70, "treasure": 0.78, "boss": 0.86}.get(source_key, 0.70)
            if self.rng.random() <= trigger:
                repl = self._fallback_generated_item(
                    source_type=source_key,
                    floor=max(1, floor),
                    known_items=known_items,
                    force_non_consumable=True,
                )
                return {
                    "item_id": repl["id"],
                    "qty": 1,
                    "rarity": repl["rarity"],
                    "new_item": repl,
                    "floor_hint": max(1, floor),
                }
        return loot

    def _replacement_loot_for_repetition(
        self,
        *,
        repeated_item_id: str,
        source_type: str,
        floor: int,
        known_items: dict[str, ItemDef],
        hint_text: str,
    ) -> dict:
        repeated_key = str(repeated_item_id or "").strip().casefold()
        source_key = str(source_type or "").strip().casefold()

        hinted = self._fallback_loot_from_hint(
            source_type=source_key,
            floor=max(1, floor),
            known_items=known_items,
            hint_text=hint_text,
        )
        hinted_id = str((hinted or {}).get("item_id") or "").strip().casefold() if isinstance(hinted, dict) else ""
        if hinted_id and hinted_id != repeated_key:
            return hinted

        alt_existing = self._pick_existing_item_for_source(
            known_items,
            source_type=source_key,
            floor=max(1, floor),
            exclude_ids={repeated_key},
        )
        if alt_existing and alt_existing in known_items:
            item = known_items[alt_existing]
            qty = self._fallback_existing_qty(item, source_type=source_key, floor=max(1, floor))
            return {
                "item_id": alt_existing,
                "qty": qty,
                "rarity": str(item.rarity or "common").strip().casefold() or "common",
                "new_item": None,
                "floor_hint": max(1, floor),
            }

        repl = self._fallback_generated_item(
            source_type=source_key,
            floor=max(1, floor),
            known_items=known_items,
            force_non_consumable=source_key in {"mimic", "treasure", "boss"},
        )
        return {
            "item_id": repl["id"],
            "qty": 1,
            "rarity": repl["rarity"],
            "new_item": repl,
            "floor_hint": max(1, floor),
        }

    def _remember_recent_loot(self, item_id: str) -> None:
        key = str(item_id or "").strip().casefold()
        if not key:
            return
        self._last_fallback_item_id = key
        recent = [row for row in self._recent_fallback_item_ids if row != key]
        recent.append(key)
        self._recent_fallback_item_ids = recent[-8:]

    def _build_prompt(
        self,
        *,
        source_type: str,
        floor: int,
        anchor: str,
        known_items: dict[str, ItemDef],
        hint_text: str = "",
    ) -> str:
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
            f"Indice narratif de butin (prioritaire si pertinent): {str(hint_text or '').strip()}\n"
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
            rarity = self._roll_rarity(source_type=source_type, floor=max(1, floor))

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
        effects = self._normalize_effects(effects, item_type=item_type, rarity=rarity, floor=max(1, floor))
        if not effects:
            effects = self._default_effects_for_item(
                item_type=item_type,
                rarity=rarity,
                floor=max(1, floor),
            )

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

    def _potion_drop_chance(self, source_type: str) -> float:
        return {
            "monster": 0.24,
            "mimic": 0.34,
            "treasure": 0.29,
            "boss": 0.45,
        }.get(str(source_type or "").strip().casefold(), 0.22)

    def _fallback_potion_loot(self, *, source_type: str, floor: int, known_items: dict[str, ItemDef]) -> dict | None:
        rarity = self._roll_rarity(source_type=source_type, floor=max(1, floor))
        existing = self._find_existing_potion(known_items, rarity=rarity, floor=floor)
        if existing:
            item = known_items[existing]
            qty_max = min(6, max(2, 2 + floor // 10))
            qty = 1 if self._is_equipment(item) else self.rng.randint(1, qty_max)
            return {
                "item_id": existing,
                "qty": qty,
                "rarity": item.rarity or rarity,
                "new_item": None,
                "floor_hint": max(1, floor),
            }

        effect = self._build_potion_effect(rarity=rarity, floor=max(1, floor))
        item_id = self._make_unique_item_id(
            f"potion_{str(effect.get('kind') or 'mix')}",
            known_items,
        )
        if str(effect.get("kind") or "") == "stat_buff":
            stat = str(effect.get("stat") or "force").strip().casefold()
            stat_label = stat.replace("_", " ")
            name = f"Potion de {stat_label}"
        elif str(effect.get("kind") or "") == "mana":
            name = "Potion de mana"
        else:
            name = "Potion de soin"

        new_item = self._normalize_item_payload(
            {
                "id": item_id,
                "name": name,
                "type": "consumable",
                "slot": "",
                "rarity": rarity,
                "description": "Une potion d'aventurier issue des reserves du donjon.",
                "stack_max": 8,
                "stat_bonuses": {},
                "effects": [effect],
                "value_gold": max(8, 12 + floor * 2),
            },
            floor=max(1, floor),
            forced_rarity=rarity,
        )
        qty = self.rng.randint(1, min(6, max(2, 2 + floor // 10)))
        return {
            "item_id": new_item["id"],
            "qty": qty,
            "rarity": new_item["rarity"],
            "new_item": new_item,
            "floor_hint": max(1, floor),
        }

    def _find_existing_potion(self, known_items: dict[str, ItemDef], *, rarity: str, floor: int) -> str:
        consumables = [
            item_id
            for item_id, item in known_items.items()
            if str(item.type or "").strip().casefold() == "consumable"
        ]
        if not consumables:
            return ""

        min_rank = max(0, min(4, floor // 12))
        candidates = []
        for item_id in consumables:
            item = known_items[item_id]
            item_rank = _RARITY_RANK.get(str(item.rarity or "").strip().casefold(), 0)
            if item_rank >= min_rank:
                candidates.append(item_id)
        if not candidates:
            candidates = consumables
        return str(self.rng.choice(candidates) if candidates else "")

    def _build_potion_effect(self, *, rarity: str, floor: int, hint_text: str = "") -> dict:
        hint = str(hint_text or "").strip().casefold()
        power = max(1, 1 + (floor // 8) + _RARITY_RANK.get(rarity, 0))

        if "mana" in hint:
            return {"kind": "mana", "value": max(3, 3 + power)}
        if any(token in hint for token in ("force", "dexterite", "agilite", "defense", "sagesse", "intelligence", "magie")):
            target = "force"
            for stat in _BUFF_STATS:
                if stat in hint:
                    target = stat
                    break
            return {
                "kind": "stat_buff",
                "stat": target,
                "value": self._stat_buff_value(rarity=rarity, floor=floor),
                "duration_turns": max(2, 2 + _RARITY_RANK.get(rarity, 0)),
            }
        if any(token in hint for token in ("heal", "soin", "vie", "pv", "sante", "sant")):
            return {"kind": "heal", "value": max(4, 4 + power)}

        roll = self.rng.random()
        if roll < 0.45:
            return {"kind": "heal", "value": max(4, 4 + power)}
        if roll < 0.72:
            return {"kind": "mana", "value": max(3, 3 + power)}
        target = str(self.rng.choice(_BUFF_STATS))
        return {
            "kind": "stat_buff",
            "stat": target,
            "value": self._stat_buff_value(rarity=rarity, floor=floor),
            "duration_turns": max(2, 2 + _RARITY_RANK.get(rarity, 0)),
        }

    def _stat_buff_value(self, *, rarity: str, floor: int) -> int:
        base = 1 + _RARITY_RANK.get(rarity, 0)
        if floor >= 12:
            base += 1
        if floor >= 22:
            base += 1
        return max(1, min(base, 6))

    def _normalize_effects(self, effects: list[dict], *, item_type: str, rarity: str, floor: int) -> list[dict]:
        out: list[dict] = []
        for effect in effects[:6]:
            if not isinstance(effect, dict):
                continue
            kind = str(effect.get("kind") or "").strip().casefold()
            if not kind:
                continue
            if kind == "heal":
                out.append({"kind": "heal", "value": max(1, self._safe_int(effect.get("value"), 1))})
                continue
            if kind == "mana":
                out.append({"kind": "mana", "value": max(1, self._safe_int(effect.get("value"), 1))})
                continue
            if kind == "stat_buff":
                stat = str(effect.get("stat") or "").strip().casefold()
                if stat not in _BUFF_STATS:
                    stat = str(self.rng.choice(_BUFF_STATS))
                out.append(
                    {
                        "kind": "stat_buff",
                        "stat": stat,
                        "value": max(1, min(self._safe_int(effect.get("value"), self._stat_buff_value(rarity=rarity, floor=floor)), 9)),
                        "duration_turns": max(1, min(self._safe_int(effect.get("duration_turns"), 3), 12)),
                    }
                )
                continue
            if kind == "passive":
                label = str(effect.get("name") or effect.get("label") or "Effet passif").strip()[:60]
                value = self._safe_int(effect.get("value"), 0)
                out.append({"kind": "passive", "name": label, "value": max(-20, min(value, 20))})
                continue
            label = str(effect.get("label") or kind).strip()[:60]
            value = self._safe_int(effect.get("value"), 0)
            out.append({"kind": "passive", "name": label, "value": max(-20, min(value, 20))})
        return out[:6]

    def _default_effects_for_item(self, *, item_type: str, rarity: str, floor: int) -> list[dict]:
        rarity_rank = _RARITY_RANK.get(rarity, 0)
        if item_type == "consumable":
            return [self._build_potion_effect(rarity=rarity, floor=max(1, floor))]
        if rarity_rank <= 0:
            return []
        if item_type == "weapon":
            return [{"kind": "passive", "name": "Percant", "value": 1 + rarity_rank}]
        if item_type == "armor":
            return [{"kind": "passive", "name": "Robuste", "value": 1 + rarity_rank}]
        if item_type == "accessory":
            return [{"kind": "passive", "name": "Chanceux", "value": max(1, rarity_rank)}]
        if item_type == "misc" and rarity_rank >= 2:
            return [{"kind": "passive", "name": "Resonance", "value": rarity_rank}]
        return []

    def _roll_rarity(self, *, source_type: str, floor: int = 1) -> str:
        table = {
            "monster": [("common", 0.60), ("uncommon", 0.28), ("rare", 0.10), ("epic", 0.02)],
            "mimic": [("common", 0.30), ("uncommon", 0.35), ("rare", 0.25), ("epic", 0.08), ("legendary", 0.02)],
            "treasure": [("common", 0.25), ("uncommon", 0.40), ("rare", 0.22), ("epic", 0.10), ("legendary", 0.03)],
            "boss": [("common", 0.08), ("uncommon", 0.32), ("rare", 0.33), ("epic", 0.20), ("legendary", 0.07)],
        }
        dist = list(table.get(source_type, table["treasure"]))
        tier = max(0, (max(1, int(floor)) - 1) // 5)
        if tier > 0:
            adjusted: list[tuple[str, float]] = []
            for name, base in dist:
                value = float(base)
                if name == "common":
                    value -= 0.03 * tier
                elif name == "uncommon":
                    value += 0.01 * tier
                elif name == "rare":
                    value += 0.012 * tier
                elif name == "epic":
                    value += 0.006 * tier
                elif name == "legendary":
                    value += 0.002 * tier
                adjusted.append((name, max(0.01, value)))
            total = sum(weight for _, weight in adjusted)
            if total > 0:
                dist = [(name, weight / total) for name, weight in adjusted]
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
