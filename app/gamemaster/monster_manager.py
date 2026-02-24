from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MonsterDef:
    id: str
    name: str
    aliases: tuple[str, ...]
    archetype: str
    tier: int
    description: str
    base_hp: int
    base_dc: int
    base_attack_bonus: int
    base_damage_min: int
    base_damage_max: int
    hp_per_floor: float
    dc_per_5_floors: int
    attack_per_6_floors: int
    damage_per_8_floors: int
    boss_hp_mult: float
    boss_damage_mult: float
    boss_dc_bonus: int
    boss_attack_bonus: int
    media_image: str
    media_clip: str


class MonsterManager:
    def __init__(self, *, data_dir: str = "data/monsters") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, MonsterDef] | None = None
        self._name_index: dict[str, str] | None = None

    def load_catalog(self) -> dict[str, MonsterDef]:
        if isinstance(self._cache, dict):
            return self._cache

        out: dict[str, MonsterDef] = {}
        for path in sorted(self.data_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            monster = self._monster_from_payload(payload)
            if not monster:
                continue
            out[monster.id] = monster

        if not out:
            out = self._fallback_catalog()

        self._cache = out
        self._name_index = self._build_name_index(out)
        return out

    def combat_profile_for_event(self, event: dict) -> dict | None:
        if not isinstance(event, dict):
            return None

        event_type = str(event.get("type") or "monster").strip().casefold() or "monster"
        floor = max(1, self._safe_int(event.get("floor"), 1))
        is_boss = bool(event.get("boss", False)) or event_type == "boss"
        monster_id = str(event.get("monster_id") or "").strip().casefold()
        base_name = str(event.get("base_monster_name") or event.get("name") or "").strip()
        display_name = str(event.get("name") or "").strip() or base_name

        monster = self._resolve_monster(monster_id=monster_id, name=base_name, event_type=event_type)
        if not isinstance(monster, MonsterDef):
            return None

        scaled = self._scaled_combat_values(monster, floor=floor, is_boss=is_boss)
        return {
            "monster_id": monster.id,
            "base_name": monster.name,
            "enemy_name": display_name or monster.name,
            "archetype": monster.archetype,
            "tier": monster.tier,
            "description": monster.description,
            "media_image": monster.media_image,
            "media_clip": monster.media_clip,
            "enemy_hp": scaled["enemy_hp"],
            "dc": scaled["dc"],
            "enemy_attack_bonus": scaled["enemy_attack_bonus"],
            "enemy_damage_min": scaled["enemy_damage_min"],
            "enemy_damage_max": scaled["enemy_damage_max"],
        }

    def _resolve_monster(self, *, monster_id: str, name: str, event_type: str) -> MonsterDef | None:
        catalog = self.load_catalog()
        if monster_id and monster_id in catalog:
            return catalog[monster_id]

        if event_type == "mimic" and "mimic" in catalog:
            return catalog["mimic"]

        name_index = self._name_index if isinstance(self._name_index, dict) else {}
        normalized_name = self._norm(name)
        if normalized_name and normalized_name in name_index:
            resolved_id = name_index[normalized_name]
            return catalog.get(resolved_id)

        if event_type == "boss" and normalized_name:
            parts = normalized_name.split(" ", 1)
            if len(parts) == 2:
                tail = parts[1].strip()
                if tail in name_index:
                    resolved_id = name_index[tail]
                    return catalog.get(resolved_id)

        return None

    def _scaled_combat_values(self, monster: MonsterDef, *, floor: int, is_boss: bool) -> dict[str, int]:
        floor_delta = max(0, int(floor) - 1)
        tier_bonus = max(0, monster.tier - 1)

        hp = monster.base_hp + int(round(floor_delta * monster.hp_per_floor)) + (tier_bonus * 2)
        dc = monster.base_dc + (floor_delta // 5) * monster.dc_per_5_floors + (tier_bonus // 2)
        attack = monster.base_attack_bonus + (floor_delta // 6) * monster.attack_per_6_floors + (tier_bonus // 2)
        damage_bonus = (floor_delta // 8) * monster.damage_per_8_floors + (tier_bonus // 2)
        damage_min = monster.base_damage_min + damage_bonus
        damage_max = monster.base_damage_max + damage_bonus

        if is_boss:
            hp = int(round(hp * monster.boss_hp_mult))
            damage_min = int(round(damage_min * monster.boss_damage_mult))
            damage_max = int(round(damage_max * monster.boss_damage_mult))
            dc += monster.boss_dc_bonus
            attack += monster.boss_attack_bonus

        hp = max(6, hp)
        dc = max(8, dc)
        attack = max(1, attack)
        damage_min = max(1, damage_min)
        damage_max = max(damage_min, damage_max)

        return {
            "enemy_hp": hp,
            "dc": dc,
            "enemy_attack_bonus": attack,
            "enemy_damage_min": damage_min,
            "enemy_damage_max": damage_max,
        }

    def _monster_from_payload(self, payload: object) -> MonsterDef | None:
        if not isinstance(payload, dict):
            return None

        monster_id = self._slug(str(payload.get("id") or ""))
        name = str(payload.get("name") or "").strip()
        if not monster_id or not name:
            return None

        aliases_raw = payload.get("aliases")
        aliases: list[str] = []
        if isinstance(aliases_raw, list):
            for value in aliases_raw[:12]:
                alias = str(value or "").strip()
                if alias:
                    aliases.append(alias[:80])
        aliases.append(name)
        aliases = list(dict.fromkeys(aliases))

        archetype = str(payload.get("archetype") or "brute").strip().casefold()[:32] or "brute"
        tier = max(1, min(self._safe_int(payload.get("tier"), 1), 5))
        description = str(payload.get("description") or "").strip()[:220]

        combat_raw = payload.get("combat")
        combat = combat_raw if isinstance(combat_raw, dict) else {}
        boss_raw = payload.get("boss_modifiers")
        boss = boss_raw if isinstance(boss_raw, dict) else {}
        media_raw = payload.get("media")
        media = media_raw if isinstance(media_raw, dict) else {}

        return MonsterDef(
            id=monster_id,
            name=name[:80],
            aliases=tuple(aliases),
            archetype=archetype,
            tier=tier,
            description=description,
            base_hp=max(6, self._safe_int(combat.get("base_hp"), 18)),
            base_dc=max(8, self._safe_int(combat.get("base_dc"), 12)),
            base_attack_bonus=max(1, self._safe_int(combat.get("base_attack_bonus"), 3)),
            base_damage_min=max(1, self._safe_int(combat.get("base_damage_min"), 3)),
            base_damage_max=max(1, self._safe_int(combat.get("base_damage_max"), 6)),
            hp_per_floor=max(0.3, self._safe_float(combat.get("hp_per_floor"), 1.1)),
            dc_per_5_floors=max(0, self._safe_int(combat.get("dc_per_5_floors"), 1)),
            attack_per_6_floors=max(0, self._safe_int(combat.get("attack_per_6_floors"), 1)),
            damage_per_8_floors=max(0, self._safe_int(combat.get("damage_per_8_floors"), 1)),
            boss_hp_mult=max(1.0, self._safe_float(boss.get("hp_mult"), 1.5)),
            boss_damage_mult=max(1.0, self._safe_float(boss.get("damage_mult"), 1.35)),
            boss_dc_bonus=max(0, self._safe_int(boss.get("dc_bonus"), 2)),
            boss_attack_bonus=max(0, self._safe_int(boss.get("attack_bonus"), 1)),
            media_image=str(media.get("image") or "").strip()[:240],
            media_clip=str(media.get("clip") or "").strip()[:240],
        )

    def _build_name_index(self, catalog: dict[str, MonsterDef]) -> dict[str, str]:
        out: dict[str, str] = {}
        for monster_id, monster in catalog.items():
            keys = [monster.name, *monster.aliases, monster.id]
            for key in keys:
                norm = self._norm(key)
                if not norm or norm in out:
                    continue
                out[norm] = monster_id
        return out

    def _fallback_catalog(self) -> dict[str, MonsterDef]:
        defaults = [
            {
                "id": "mimic",
                "name": "Mimic",
                "aliases": ["Coffre mimic"],
                "archetype": "trickster",
                "tier": 2,
                "description": "Un faux coffre vivant, agressif et imprevisible.",
                "combat": {"base_hp": 20, "base_dc": 13, "base_attack_bonus": 4, "base_damage_min": 4, "base_damage_max": 8, "hp_per_floor": 1.2},
            },
            {
                "id": "goule_obsidienne",
                "name": "Goule d'obsidienne",
                "aliases": ["Goule obsidienne"],
                "archetype": "brute",
                "tier": 2,
                "description": "Masse de chair noire animee par une rage funeste.",
                "combat": {"base_hp": 22, "base_dc": 12, "base_attack_bonus": 3, "base_damage_min": 3, "base_damage_max": 7, "hp_per_floor": 1.4},
            },
        ]
        out: dict[str, MonsterDef] = {}
        for row in defaults:
            monster = self._monster_from_payload(row)
            if monster:
                out[monster.id] = monster
        return out

    def _slug(self, value: str) -> str:
        folded = self._ascii_fold(value).casefold()
        slug = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
        return slug[:80]

    def _norm(self, value: str) -> str:
        folded = self._ascii_fold(value).casefold()
        return re.sub(r"\s+", " ", folded).strip()

    def _ascii_fold(self, value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(ch for ch in text if not unicodedata.combining(ch))

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
