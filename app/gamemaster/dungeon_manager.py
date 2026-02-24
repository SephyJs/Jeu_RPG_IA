from __future__ import annotations

import json
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .models import model_for


class DungeonProfileDraft(BaseModel):
    name: str
    theme: str
    entry_text: str
    monster_pool: list[str] = Field(default_factory=list)
    treasure_pool: list[str] = Field(default_factory=list)


class DungeonManager:
    def __init__(self, llm: Any, *, storage_dir: str = "data/dungeons/generated"):
        self.llm = llm
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_dungeon_profile(self, cache: dict[str, dict], anchor: str) -> dict:
        key = self._anchor_key(anchor)
        existing = cache.get(key)
        if isinstance(existing, dict):
            return existing

        loaded = self._load_from_disk(key)
        if loaded:
            cache[key] = loaded
            return loaded

        profile = await self._generate_profile(anchor)
        cache[key] = profile
        self._save_to_disk(key, profile)
        return profile

    def start_run(self, anchor: str, profile: dict) -> dict:
        total_floors = random.randint(10, 25)
        floors = [self._build_floor_event(profile, i + 1) for i in range(total_floors)]
        if floors:
            floors[-1] = self._build_boss_event(profile, total_floors)
        relic = self._roll_run_relic()
        return {
            "anchor": anchor,
            "dungeon_name": str(profile.get("name") or f"Donjon de {anchor}"),
            "theme": str(profile.get("theme") or "sombre"),
            "entry_text": str(profile.get("entry_text") or "Le portail se referme derrière vous."),
            "total_floors": total_floors,
            "current_floor": 0,
            "floors": floors,
            "completed": False,
            "run_relic": relic,
        }

    def advance_floor(self, run: dict) -> dict | None:
        if not isinstance(run, dict):
            return None
        if bool(run.get("completed", False)):
            return None

        current = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        floors = run.get("floors", [])
        if current >= total or not isinstance(floors, list) or current >= len(floors):
            run["completed"] = True
            return None

        event = floors[current]
        run["current_floor"] = current + 1
        if run["current_floor"] >= total:
            run["completed"] = True
        return event if isinstance(event, dict) else None

    async def _generate_profile(self, anchor: str) -> dict:
        prompt = self._profile_prompt(anchor)
        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.35,
                num_ctx=3072,
                num_predict=420,
                stop=None,
            )
            draft = self._parse_profile(raw, anchor)
            monsters = [m.strip() for m in draft.monster_pool if isinstance(m, str) and m.strip()][:8]
            treasures = [t.strip() for t in draft.treasure_pool if isinstance(t, str) and t.strip()][:8]
            if not monsters:
                monsters = self._fallback_profile(anchor)["monster_pool"]
            if not treasures:
                treasures = self._fallback_profile(anchor)["treasure_pool"]
            return {
                "anchor": anchor,
                "name": draft.name.strip() or f"Donjon de {anchor}",
                "theme": draft.theme.strip() or "sombre",
                "entry_text": draft.entry_text.strip() or f"Les portes du Donjon de {anchor} grondent en s'ouvrant.",
                "monster_pool": monsters,
                "treasure_pool": treasures,
            }
        except Exception:
            return self._fallback_profile(anchor)

    def _parse_profile(self, raw: str, anchor: str) -> DungeonProfileDraft:
        text = self._extract_json(raw)
        try:
            data = json.loads(text)
            draft = DungeonProfileDraft.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            fallback = self._fallback_profile(anchor)
            draft = DungeonProfileDraft(
                name=fallback["name"],
                theme=fallback["theme"],
                entry_text=fallback["entry_text"],
                monster_pool=fallback["monster_pool"],
                treasure_pool=fallback["treasure_pool"],
            )

        if not draft.entry_text.startswith("Ataryxia"):
            draft.entry_text = f"Ataryxia : {draft.entry_text}"
        return draft

    def _build_floor_event(self, profile: dict, floor: int) -> dict:
        roll = random.random()
        if roll < 0.10:
            kind = "mimic"
        elif roll < 0.55:
            kind = "monster"
        else:
            kind = "treasure"

        monsters = profile.get("monster_pool") if isinstance(profile.get("monster_pool"), list) else []
        treasures = profile.get("treasure_pool") if isinstance(profile.get("treasure_pool"), list) else []

        if kind == "monster":
            name = random.choice(monsters) if monsters else random.choice([
                "Goule d'obsidienne", "Sentinelle déchue", "Araignée cendreuse", "Chevalier sans visage"
            ])
            monster_id = self._monster_id_from_name(name)
            text = f"Étage {floor}: Un {name} bloque le passage et se jette sur vous."
            return {"floor": floor, "type": "monster", "name": name, "monster_id": monster_id, "text": text}

        if kind == "mimic":
            lure = random.choice(treasures) if treasures else random.choice([
                "coffre runique", "reliquaire doré", "urne scellée", "sac de gemmes"
            ])
            text = f"Étage {floor}: Un {lure} vous attire... puis révèle ses crocs. C'était un mimic."
            return {"floor": floor, "type": "mimic", "name": "Mimic", "monster_id": "mimic", "loot_lure": lure, "text": text}

        loot = random.choice(treasures) if treasures else random.choice([
            "anneau gravé", "potion d'ombre", "bourse de pièces noires", "fragment ancien"
        ])
        text = f"Étage {floor}: Vous découvrez un trésor: {loot}."
        return {"floor": floor, "type": "treasure", "loot": loot, "text": text}

    def _build_boss_event(self, profile: dict, floor: int) -> dict:
        monsters = profile.get("monster_pool") if isinstance(profile.get("monster_pool"), list) else []
        treasures = profile.get("treasure_pool") if isinstance(profile.get("treasure_pool"), list) else []
        base = random.choice(monsters) if monsters else "Seigneur spectral"
        boss_title = random.choice(
            [
                "Seigneur",
                "Gardien",
                "Tyran",
                "Souverain",
                "Archonte",
            ]
        )
        boss_name = f"{boss_title} {base}".strip()
        monster_id = self._monster_id_from_name(base)
        boss_treasure = random.choice(treasures) if treasures else "relique de domination"
        text = (
            f"Étage {floor}: Boss final! {boss_name} vous barre la route. "
            f"Il protège jalousement {boss_treasure}."
        )
        return {
            "floor": floor,
            "type": "boss",
            "name": boss_name,
            "base_monster_name": base,
            "monster_id": monster_id,
            "loot": boss_treasure,
            "text": text,
            "boss": True,
        }

    def _profile_prompt(self, anchor: str) -> str:
        schema = {
            "name": "Nom unique du donjon",
            "theme": "Ambiance courte",
            "entry_text": "Phrase d'entrée immersive (1-2 phrases)",
            "monster_pool": ["Monstre 1", "Monstre 2"],
            "treasure_pool": ["Trésor 1", "Trésor 2"],
        }
        return (
            "Tu génères la fiche d'un donjon dark-fantasy. Réponds en JSON valide uniquement.\n"
            f"Ville/zone: {anchor}.\n"
            "Contraintes: ambiance cohérente, sans méta, sans markdown.\n"
            "Le donjon doit être distinct des autres zones et fidèle à cette ville/zone.\n"
            "Le donjon sera rejoué avec 10-25 étages aléatoires; fournis juste la fiche de thème et les pools.\n"
            f"Schéma:\n{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _fallback_profile(self, anchor: str) -> dict:
        return {
            "anchor": anchor,
            "name": f"Abysses de {anchor}",
            "theme": "pierre humide et magie ancienne",
            "entry_text": f"Ataryxia : Les portes des Abysses de {anchor} grincent, et l'air devient lourd.",
            "monster_pool": [
                "squelette blindé",
                "goule cendreuse",
                "acolyte dément",
                "garde spectral",
            ],
            "treasure_pool": [
                "relique ternie",
                "pierre-mémoire",
                "lame rouillée enchantée",
                "bourse d'or noirci",
            ],
        }

    def _anchor_key(self, anchor: str) -> str:
        return self._slug((anchor or "").strip(), default="zone")

    def _monster_id_from_name(self, name: str) -> str:
        return self._slug((name or "").strip(), default="monster")

    def _slug(self, raw_text: str, *, default: str) -> str:
        raw = unicodedata.normalize("NFKD", raw_text).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        return slug or default

    def _path_for(self, anchor_key: str) -> Path:
        return self.storage_dir / f"{anchor_key}.json"

    def _load_from_disk(self, anchor_key: str) -> dict | None:
        path = self._path_for(anchor_key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            monsters = raw.get("monster_pool") if isinstance(raw.get("monster_pool"), list) else []
            treasures = raw.get("treasure_pool") if isinstance(raw.get("treasure_pool"), list) else []
            return {
                "anchor": str(raw.get("anchor") or ""),
                "name": str(raw.get("name") or "Donjon sans nom"),
                "theme": str(raw.get("theme") or "sombre"),
                "entry_text": str(raw.get("entry_text") or "Ataryxia : Les profondeurs vous observent."),
                "monster_pool": [str(v).strip() for v in monsters if str(v).strip()][:8],
                "treasure_pool": [str(v).strip() for v in treasures if str(v).strip()][:8],
            }
        except Exception:
            return None

    def _save_to_disk(self, anchor_key: str, profile: dict) -> None:
        path = self._path_for(anchor_key)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start:end + 1]
        return "{}"

    def _roll_run_relic(self) -> dict:
        pool = [
            {
                "id": "relique_cendre",
                "name": "Relique de cendre",
                "effect": "attack",
                "bonus": 2,
                "description": "Augmente vos jets offensifs en donjon.",
            },
            {
                "id": "relique_garde",
                "name": "Relique de garde",
                "effect": "defense",
                "bonus": 2,
                "description": "Renforce votre defense contre les monstres.",
            },
            {
                "id": "relique_sang",
                "name": "Relique de sang",
                "effect": "max_hp",
                "bonus": 6,
                "description": "Accorde une reserve de vitalite temporaire.",
            },
            {
                "id": "relique_flux",
                "name": "Relique du flux",
                "effect": "heal",
                "bonus": 2,
                "description": "Ameliore les soins pendant l'expedition.",
            },
        ]
        return dict(random.choice(pool))
