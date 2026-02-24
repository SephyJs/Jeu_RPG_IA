from __future__ import annotations

import json
import random
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .location_manager import canonical_anchor, official_neighbors
from .models import model_for


ALLOWED_OBJECTIVE_TYPES = (
    "talk_to_npc",
    "send_messages",
    "explore_locations",
    "reach_anchor",
    "collect_gold",
    "clear_dungeon_floors",
)


class QuestRewardItemDraft(BaseModel):
    item_id: str
    qty: int = 1


class QuestRewardsDraft(BaseModel):
    gold: int = 0
    items: list[QuestRewardItemDraft] = Field(default_factory=list)
    shop_discount_pct: int = 0
    temple_heal_bonus: int = 0


class QuestBranchOptionDraft(BaseModel):
    id: str = ""
    label: str
    description: str = ""
    objective_delta: int = 0
    rewards_bonus: QuestRewardsDraft = Field(default_factory=QuestRewardsDraft)
    reputation: dict[str, int] = Field(default_factory=dict)


class QuestBranchDraft(BaseModel):
    prompt: str = ""
    options: list[QuestBranchOptionDraft] = Field(default_factory=list)


class QuestDraft(BaseModel):
    title: str
    description: str
    objective_type: Literal[
        "talk_to_npc",
        "send_messages",
        "explore_locations",
        "reach_anchor",
        "collect_gold",
        "clear_dungeon_floors",
    ] = "send_messages"
    target_count: int = 3
    target_npc: str = ""
    target_anchor: str = ""
    progress_hint: str = ""
    quest_intro: str = ""
    rewards: QuestRewardsDraft = Field(default_factory=QuestRewardsDraft)
    deadline_hours: int = 0
    failure_consequence: str = ""
    branching: QuestBranchDraft = Field(default_factory=QuestBranchDraft)


class QuestManager:
    def __init__(self, llm: Any, *, items_dir: str = "data/items") -> None:
        self.llm = llm
        self.items_dir = Path(items_dir)
        self.rng = random.Random(12345)
        self.known_item_ids = self._load_known_item_ids()

    async def generate_quest(
        self,
        *,
        player_name: str,
        npc_name: str,
        location_id: str,
        location_title: str,
        map_anchor: str,
        npc_profile: dict | None = None,
        existing_titles: list[str] | None = None,
    ) -> dict:
        prompt = self._build_prompt(
            player_name=player_name,
            npc_name=npc_name,
            location_id=location_id,
            location_title=location_title,
            map_anchor=map_anchor,
            npc_profile=npc_profile,
            existing_titles=existing_titles or [],
        )

        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.35,
                num_ctx=4096,
                num_predict=900,
                stop=None,
            )
            draft = self._parse_draft(raw)
        except Exception:
            draft = self._fallback_draft(npc_name=npc_name, map_anchor=map_anchor)

        return self._normalize_draft(
            draft=draft,
            npc_name=npc_name,
            map_anchor=map_anchor,
        )

    def _build_prompt(
        self,
        *,
        player_name: str,
        npc_name: str,
        location_id: str,
        location_title: str,
        map_anchor: str,
        npc_profile: dict | None,
        existing_titles: list[str],
    ) -> str:
        anchor = canonical_anchor(map_anchor or location_title)
        neighbors = official_neighbors(anchor)
        schema = {
            "title": "Nom court de quete",
            "description": "Description en 1-3 phrases",
            "objective_type": "talk_to_npc|send_messages|explore_locations|reach_anchor|collect_gold|clear_dungeon_floors",
            "target_count": 3,
            "target_npc": npc_name,
            "target_anchor": "Lumeria",
            "progress_hint": "Conseil de progression en une phrase",
            "quest_intro": "Phrase que le PNJ dit en donnant la quete",
            "deadline_hours": 24,
            "failure_consequence": "Consequence narrative si la quete echoue",
            "rewards": {
                "gold": 10,
                "items": [{"item_id": "pain_01", "qty": 1}],
                "shop_discount_pct": 0,
                "temple_heal_bonus": 0,
            },
            "branching": {
                "prompt": "Choix moral ou tactique",
                "options": [
                    {
                        "id": "diplomatie",
                        "label": "Voie diplomatique",
                        "description": "Prendre plus de temps, moins de violence.",
                        "objective_delta": 1,
                        "rewards_bonus": {"gold": 0, "items": [], "shop_discount_pct": 2, "temple_heal_bonus": 0},
                        "reputation": {"Habitants": 2},
                    },
                    {
                        "id": "coercition",
                        "label": "Voie coercitive",
                        "description": "Regler vite, mais marquer les esprits.",
                        "objective_delta": -1,
                        "rewards_bonus": {"gold": 8, "items": [], "shop_discount_pct": 0, "temple_heal_bonus": 0},
                        "reputation": {"Habitants": -2, "Aventuriers": 1},
                    },
                ],
            },
        }

        profile_hint = json.dumps(npc_profile or {}, ensure_ascii=False)[:1200]
        known_items = self.known_item_ids[:10]
        return (
            "Tu generes UNE quete RPG pour un PNJ. Reponds en JSON valide uniquement, sans markdown.\n"
            f"Joueur: {player_name}\n"
            f"PNJ: {npc_name}\n"
            f"Lieu: {location_title} (id={location_id}, ancrage={anchor})\n"
            f"Voisins officiels de l'ancrage: {', '.join(neighbors) if neighbors else anchor}\n"
            f"Quetes deja existantes (evite doublon exact): {', '.join(existing_titles[:30])}\n"
            "Contraintes:\n"
            "- Quete courte, realisable en session de jeu.\n"
            "- objective_type DOIT etre dans la liste autorisee.\n"
            "- target_count doit etre un entier positif.\n"
            "- Pour talk_to_npc: target_npc doit etre le PNJ source.\n"
            "- Pour reach_anchor: target_anchor doit etre un ancrage officiel.\n"
            f"- Items connus: {', '.join(known_items) if known_items else 'pain_01'}.\n"
            "- Rewards: au moins un benefice concret (or, item, reduction, bonus temple).\n"
            "- deadline_hours: 0 si pas de delai, sinon 6..72 heures.\n"
            "- Ajoute un branching avec 2 options pour donner un vrai choix au joueur.\n"
            "Contexte profil PNJ (optionnel):\n"
            f"{profile_hint}\n"
            "Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _parse_draft(self, raw: str) -> QuestDraft:
        text = self._extract_json(raw)
        try:
            payload = json.loads(text)
            return QuestDraft.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return self._fallback_draft(npc_name="PNJ", map_anchor="Lumeria")

    def _fallback_draft(self, *, npc_name: str, map_anchor: str) -> QuestDraft:
        anchor = canonical_anchor(map_anchor)
        return QuestDraft(
            title=f"Service pour {npc_name}",
            description=f"{npc_name} vous demande de l'aider a mieux connaitre les alentours de {anchor}.",
            objective_type="send_messages",
            target_count=4,
            target_npc=npc_name,
            target_anchor=anchor,
            progress_hint="Discute encore un peu avec les habitants.",
            quest_intro="J'aurais besoin d'un coup de main. Montre-moi ce que tu sais faire.",
            rewards=QuestRewardsDraft(gold=15, items=[QuestRewardItemDraft(item_id="pain_01", qty=1)]),
            deadline_hours=24,
            failure_consequence=f"{npc_name} perd patience et retire son soutien.",
        )

    def _normalize_draft(self, *, draft: QuestDraft, npc_name: str, map_anchor: str) -> dict:
        objective_type = draft.objective_type if draft.objective_type in ALLOWED_OBJECTIVE_TYPES else "send_messages"
        target_count = self._normalize_target_count(objective_type, draft.target_count)
        target_npc = str(draft.target_npc or npc_name).strip() or npc_name

        anchor = canonical_anchor(draft.target_anchor or map_anchor)
        if objective_type == "reach_anchor":
            current = canonical_anchor(map_anchor)
            if anchor == current:
                neighbors = official_neighbors(current)
                if neighbors:
                    anchor = neighbors[0]

        rewards = self._normalize_rewards(draft.rewards)
        if not self._has_any_reward(rewards):
            rewards["gold"] = 12

        title = str(draft.title or "").strip() or f"Mission de {npc_name}"
        description = str(draft.description or "").strip() or f"{npc_name} vous confie une mission."
        progress_hint = str(draft.progress_hint or "").strip()
        quest_intro = str(draft.quest_intro or "").strip() or "J'ai une mission pour toi."
        deadline_hours = self._normalize_deadline_hours(draft.deadline_hours)
        failure_consequence = str(draft.failure_consequence or "").strip()
        if not failure_consequence:
            failure_consequence = f"Echec de mission: {npc_name} vous fera moins confiance."
        branching = self._normalize_branching(
            draft.branching,
            objective_type=objective_type,
            target_count=target_count,
        )

        return {
            "title": title[:90],
            "description": description[:500],
            "objective_type": objective_type,
            "target_count": target_count,
            "target_npc": target_npc[:80],
            "target_anchor": anchor,
            "progress_hint": progress_hint[:220],
            "quest_intro": quest_intro[:220],
            "rewards": rewards,
            "deadline_hours": deadline_hours,
            "failure_consequence": failure_consequence[:220],
            "branching": branching,
        }

    def _normalize_target_count(self, objective_type: str, raw_value: int) -> int:
        value = max(1, int(raw_value or 1))
        if objective_type == "talk_to_npc":
            return min(max(value, 2), 6)
        if objective_type == "send_messages":
            return min(max(value, 3), 10)
        if objective_type == "explore_locations":
            return min(max(value, 1), 4)
        if objective_type == "reach_anchor":
            return 1
        if objective_type == "collect_gold":
            return min(max(value, 8), 250)
        if objective_type == "clear_dungeon_floors":
            return min(max(value, 1), 8)
        return min(max(value, 1), 10)

    def _normalize_rewards(self, rewards: QuestRewardsDraft) -> dict:
        gold = min(max(int(rewards.gold or 0), 0), 300)
        shop_discount_pct = min(max(int(rewards.shop_discount_pct or 0), 0), 35)
        temple_heal_bonus = min(max(int(rewards.temple_heal_bonus or 0), 0), 5)

        items: list[dict] = []
        for item in rewards.items[:6]:
            item_id = str(item.item_id or "").strip()
            if not item_id:
                continue
            if self.known_item_ids and item_id not in self.known_item_ids:
                item_id = self.known_item_ids[0]
            qty = min(max(int(item.qty or 1), 1), 10)
            items.append({"item_id": item_id, "qty": qty})

        return {
            "gold": gold,
            "items": items,
            "shop_discount_pct": shop_discount_pct,
            "temple_heal_bonus": temple_heal_bonus,
        }

    def _normalize_deadline_hours(self, raw_value: int) -> int:
        value = max(0, int(raw_value or 0))
        if value <= 0:
            return 0
        return min(max(value, 6), 96)

    def _normalize_branching(
        self,
        raw: QuestBranchDraft,
        *,
        objective_type: str,
        target_count: int,
    ) -> dict:
        options: list[dict] = []
        source_options = raw.options if isinstance(raw, QuestBranchDraft) else []
        for idx, option in enumerate(source_options[:3]):
            if not isinstance(option, QuestBranchOptionDraft):
                continue
            label = str(option.label or "").strip()
            if not label:
                continue
            option_id = self._slug(str(option.id or "") or label, fallback=f"option_{idx + 1}")
            bonus = self._normalize_rewards(option.rewards_bonus)
            rep_map: dict[str, int] = {}
            if isinstance(option.reputation, dict):
                for key, value in option.reputation.items():
                    faction = re.sub(r"\s+", " ", str(key or "").strip())
                    if not faction:
                        continue
                    try:
                        delta = int(value)
                    except (TypeError, ValueError):
                        continue
                    if delta == 0:
                        continue
                    rep_map[faction[:64]] = max(-15, min(15, delta))
            options.append(
                {
                    "id": option_id[:40],
                    "label": label[:80],
                    "description": str(option.description or "").strip()[:180],
                    "objective_delta": max(-3, min(3, int(option.objective_delta or 0))),
                    "rewards_bonus": bonus,
                    "reputation": rep_map,
                }
            )

        if len(options) < 2:
            options = self._fallback_branching_options(
                objective_type=objective_type,
                target_count=target_count,
            )

        prompt = str(raw.prompt or "").strip() if isinstance(raw, QuestBranchDraft) else ""
        if not prompt:
            prompt = "Comment veux-tu mener cette mission ?"

        return {
            "prompt": prompt[:220],
            "options": options[:3],
        }

    def _fallback_branching_options(self, *, objective_type: str, target_count: int) -> list[dict]:
        diplomacy = {
            "id": "diplomatie",
            "label": "Voie diplomatique",
            "description": "Approche prudente et respectueuse.",
            "objective_delta": 1,
            "rewards_bonus": {
                "gold": 0,
                "items": [],
                "shop_discount_pct": 2,
                "temple_heal_bonus": 0,
            },
            "reputation": {"Habitants": 2},
        }
        coercion = {
            "id": "coercition",
            "label": "Voie coercitive",
            "description": "Approche rapide mais risquee pour ta reputation.",
            "objective_delta": -1,
            "rewards_bonus": {
                "gold": 8,
                "items": [],
                "shop_discount_pct": 0,
                "temple_heal_bonus": 0,
            },
            "reputation": {"Habitants": -2, "Aventuriers": 1},
        }
        if objective_type in {"collect_gold", "clear_dungeon_floors"}:
            coercion["objective_delta"] = -2 if target_count >= 3 else -1
            diplomacy["objective_delta"] = 0
        if objective_type == "talk_to_npc":
            diplomacy["objective_delta"] = 0
            coercion["objective_delta"] = 0
        return [diplomacy, coercion]

    def _slug(self, text: str, *, fallback: str = "option") -> str:
        folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "_", folded.casefold()).strip("_")
        return slug or fallback

    def _has_any_reward(self, rewards: dict) -> bool:
        return bool(
            int(rewards.get("gold") or 0) > 0
            or int(rewards.get("shop_discount_pct") or 0) > 0
            or int(rewards.get("temple_heal_bonus") or 0) > 0
            or bool(rewards.get("items"))
        )

    def _load_known_item_ids(self) -> list[str]:
        if not self.items_dir.exists():
            return ["pain_01"]

        ids: list[str] = []
        for path in sorted(self.items_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                item_id = raw.get("id")
                if isinstance(item_id, str) and item_id.strip():
                    ids.append(item_id.strip())
            except Exception:
                continue

        if not ids:
            ids = ["pain_01"]
        return ids

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"
