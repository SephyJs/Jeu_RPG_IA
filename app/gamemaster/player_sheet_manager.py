from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import uuid
from pathlib import Path
from typing import Any

from .models import model_for


_PLACEHOLDER_TOKENS = {
    "",
    "inconnu",
    "unknown",
    "nom du personnage",
    "genre",
    "description visuelle courte",
    "description visuelle detaillee",
    "l'eveille",
    "l'éveillé",
    "eveille",
}

_CREATION_MISSING_LABELS = {
    "char_name": "Pseudo / nom du personnage",
    "gender": "Genre (homme, femme, non-binaire, ...)",
    "appearance": "Description physique",
    "strengths": "Atouts / talents",
    "persona": "Trait de caractere principal",
}

_STAT_KEYS = (
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

_CREATION_BASE_STAT_VALUE = 5
_CREATION_BONUS_POOL = 15
_CREATION_MIN_BONUS = 2
_CREATION_MAX_BONUS = 5


class PlayerSheetManager:
    def __init__(self, llm: Any, *, template_path: str = "data/player_sheet_template.json") -> None:
        self.llm = llm
        self.template_path = Path(template_path)
        self.template = self._load_template()

    @staticmethod
    def creation_missing_labels() -> dict[str, str]:
        return dict(_CREATION_MISSING_LABELS)

    def create_initial_sheet(self, *, fallback_name: str = "") -> dict:
        sheet = copy.deepcopy(self.template)
        metadata = sheet.setdefault("metadata", {})
        metadata["version"] = str(metadata.get("version") or "1.0")
        metadata["char_id"] = str(metadata.get("char_id") or uuid.uuid4())
        if fallback_name and self._is_value_missing(str(sheet.get("char_name") or "")):
            sheet["char_name"] = fallback_name.strip()
        return self._sanitize_sheet(sheet)

    def ensure_sheet(self, sheet: object, *, fallback_name: str = "") -> dict:
        base = self.create_initial_sheet(fallback_name=fallback_name)
        if not isinstance(sheet, dict):
            return base
        merged = self._deep_merge(base, sheet)
        if fallback_name and self._is_value_missing(str(merged.get("char_name") or "")):
            merged["char_name"] = fallback_name.strip()
        return self._sanitize_sheet(merged)

    def missing_creation_fields(self, sheet: dict) -> list[str]:
        missing: list[str] = []

        name = str(sheet.get("char_name") or "").strip()
        if self._is_value_missing(name) or len(name) < 2:
            missing.append("char_name")

        identity = sheet.get("identity", {}) if isinstance(sheet.get("identity"), dict) else {}
        gender = str(identity.get("gender") or "").strip()
        if self._is_value_missing(gender):
            missing.append("gender")

        visual = sheet.get("description_visuelle", {}) if isinstance(sheet.get("description_visuelle"), dict) else {}
        appearance_short = str(visual.get("courte") or "").strip()
        if self._is_value_missing(appearance_short) or len(appearance_short) < 8:
            missing.append("appearance")

        lore = sheet.get("lore_details", {}) if isinstance(sheet.get("lore_details"), dict) else {}
        passives = lore.get("passives")
        if not self._has_valid_passive(passives):
            missing.append("strengths")

        persona = str(sheet.get("char_persona") or "").strip()
        if self._is_value_missing(persona) or len(persona) < 10:
            missing.append("persona")

        return missing

    def next_creation_question(self, missing_fields: list[str]) -> str:
        if not missing_fields:
            return "Fiche complete."
        top = missing_fields[0]
        if top == "char_name":
            return "Avant de commencer, donne ton pseudo (nom du personnage)."
        if top == "gender":
            return "Precise ton genre (homme, femme, non-binaire, etc.)."
        if top == "appearance":
            return "Decris ton apparence en une ou deux phrases (taille, visage, tenue, signe distinctif)."
        if top == "strengths":
            return "Indique au moins un atout ou talent de ton personnage (ex: force brute, magie, discretion)."
        if top == "persona":
            return "Quel est ton temperament principal (calme, impulsif, calculateur, protecteur, etc.) ?"
        return "Donne plus de details sur ton personnage."

    async def ingest_creation_message(
        self,
        *,
        sheet: dict,
        user_message: str,
        recent_chat_lines: list[str],
    ) -> dict:
        current = self.ensure_sheet(sheet)
        patch = await self._extract_creation_patch(
            current_sheet=current,
            user_message=user_message,
            recent_chat_lines=recent_chat_lines,
        )
        updated_sheet, updated_fields = self._apply_creation_patch(current, patch)
        updated_sheet, creation_bonus = await self._apply_creation_stat_distribution(
            updated_sheet,
            user_message=user_message,
            recent_chat_lines=recent_chat_lines,
        )
        missing = self.missing_creation_fields(updated_sheet)
        ready = not missing

        if updated_fields:
            ack = "Infos fiche prises en compte: " + ", ".join(updated_fields[:4]) + "."
        else:
            ack = "Je n'ai pas encore assez d'informations utilisables."
        bonus_summary = self._format_creation_bonus_summary(creation_bonus)
        if bonus_summary:
            ack += f" Repartition atouts: {bonus_summary}."

        return {
            "sheet": updated_sheet,
            "updated_fields": updated_fields,
            "missing_fields": missing,
            "ready": ready,
            "ack_text": ack,
            "next_question": self.next_creation_question(missing),
        }

    async def infer_progression_update(
        self,
        *,
        sheet: dict,
        user_message: str,
        npc_reply: str,
        narration: str,
    ) -> dict:
        if self.llm is None:
            return self._empty_progression()

        stats = self._safe_stats(sheet.get("stats"))
        schema = {
            "xp_gain": 0,
            "reason": "raison courte",
            "stat_deltas": {
                "force": 0,
                "intelligence": 0,
                "magie": 0,
                "defense": 0,
                "sagesse": 0,
                "agilite": 0,
                "dexterite": 0,
                "chance": 0,
                "charisme": 0,
                "pv_max": 0,
                "mana_max": 0,
            },
        }
        prompt = (
            "Tu es un moteur de progression RPG.\n"
            "Reponds en JSON valide UNIQUEMENT.\n"
            "Tu n'accordes des gains que si l'action le justifie vraiment.\n"
            "Si c'est juste du bavardage sans enjeu, donne 0 partout.\n"
            "Gains tres modestes: xp_gain 0..6, chaque stat_delta 0..2.\n"
            "Pas de valeurs negatives.\n"
            "Stats actuelles:\n"
            f"{json.dumps(stats, ensure_ascii=False)}\n"
            "Contexte tour:\n"
            f"- Joueur: {user_message}\n"
            f"- Reponse PNJ: {npc_reply}\n"
            f"- Narration: {narration}\n"
            "Schema attendu:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.1,
                num_ctx=2048,
                num_predict=240,
                stop=None,
            )
            payload = json.loads(self._extract_json(raw))
            return self._normalize_progression(payload)
        except Exception:
            return self._empty_progression()

    def apply_progression_update(self, sheet: dict, progression: dict) -> tuple[dict, list[str]]:
        current = self.ensure_sheet(sheet)
        stats = self._safe_stats(current.get("stats"))
        lines: list[str] = []

        xp_gain = max(0, min(self._safe_int(progression.get("xp_gain"), 0), 12))
        if xp_gain > 0:
            stats["experience"] += xp_gain
            lines.append(f"+{xp_gain} XP")

        deltas_raw = progression.get("stat_deltas") if isinstance(progression.get("stat_deltas"), dict) else {}
        for key in _STAT_KEYS:
            delta = max(0, min(self._safe_int(deltas_raw.get(key), 0), 3))
            if delta <= 0:
                continue
            stats[key] = min(999, max(1, self._safe_int(stats.get(key), 1) + delta))
            lines.append(f"+{delta} {key}")

        pv_max_delta = max(0, min(self._safe_int(deltas_raw.get("pv_max"), 0), 6))
        mana_max_delta = max(0, min(self._safe_int(deltas_raw.get("mana_max"), 0), 6))
        if pv_max_delta > 0:
            stats["pv_max"] = min(999, self._safe_int(stats.get("pv_max"), 20) + pv_max_delta)
            lines.append(f"+{pv_max_delta} pv_max")
        if mana_max_delta > 0:
            stats["mana_max"] = min(999, self._safe_int(stats.get("mana_max"), 10) + mana_max_delta)
            lines.append(f"+{mana_max_delta} mana_max")

        level_ups = 0
        while stats["experience"] >= self._xp_needed_for_next_level(stats["niveau"]):
            stats["experience"] -= self._xp_needed_for_next_level(stats["niveau"])
            stats["niveau"] += 1
            level_ups += 1
            stats["pv_max"] = min(999, stats["pv_max"] + 3)
            stats["mana_max"] = min(999, stats["mana_max"] + 2)
            stats["force"] = min(999, stats["force"] + 1)
            stats["intelligence"] = min(999, stats["intelligence"] + 1)

        if level_ups > 0:
            stats["pv"] = stats["pv_max"]
            stats["mana"] = stats["mana_max"]
            lines.append(f"Niveau +{level_ups}")

        stats["pv"] = min(max(1, self._safe_int(stats.get("pv"), 20)), self._safe_int(stats.get("pv_max"), 20))
        stats["mana"] = min(max(0, self._safe_int(stats.get("mana"), 10)), self._safe_int(stats.get("mana_max"), 10))

        current["stats"] = stats
        reason = str(progression.get("reason") or "").strip()
        if reason and lines:
            lines.append(f"({reason[:120]})")
        return current, lines

    def sync_player_basics(self, sheet: dict, player: object) -> None:
        if not isinstance(sheet, dict):
            return
        effective = sheet.get("effective_stats")
        stats = self._safe_stats(effective if isinstance(effective, dict) else sheet.get("stats"))
        char_name = str(sheet.get("char_name") or "").strip()
        if char_name and not self._is_value_missing(char_name):
            try:
                player.name = char_name
            except Exception:
                pass
        try:
            player.max_hp = max(1, self._safe_int(stats.get("pv_max"), 20))
            player.hp = min(max(1, self._safe_int(stats.get("pv"), player.max_hp)), player.max_hp)
        except Exception:
            pass

    async def _extract_creation_patch(
        self,
        *,
        current_sheet: dict,
        user_message: str,
        recent_chat_lines: list[str],
    ) -> dict:
        heuristic_patch = self._heuristic_creation_patch(user_message)
        if self.llm is None:
            return heuristic_patch

        schema = {
            "char_name": "",
            "identity": {
                "gender": "",
                "social_class": "",
                "age_apparent": "",
                "family": {"origin": "", "reputation": ""},
            },
            "description_visuelle": {"courte": "", "longue": "", "aura": ""},
            "char_persona": "",
            "lore_details": {"passives": [{"nom": "", "effet": ""}]},
            "world_logic": {"goals": []},
            "speech_style": {"pronouns": ""},
        }
        recent = "\n".join(recent_chat_lines[-10:])
        prompt = (
            "Tu extrais des informations de creation de personnage depuis le message du joueur.\n"
            "Reponds en JSON valide UNIQUEMENT.\n"
            "N'invente pas. Si une info n'est pas explicitement presente: chaine vide ou liste vide.\n"
            "Ne modifie que les informations du joueur.\n"
            "Etat actuel fiche (resume):\n"
            f"{json.dumps(current_sheet, ensure_ascii=False)[:1800]}\n"
            "Contexte recent:\n"
            f"{recent}\n"
            "Dernier message joueur:\n"
            f"{user_message}\n"
            "Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )
        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.1,
                num_ctx=4096,
                num_predict=500,
                stop=None,
            )
            payload = json.loads(self._extract_json(raw))
            if isinstance(payload, dict):
                cleaned_payload = self._drop_empty_patch_values(payload)
                if isinstance(cleaned_payload, dict):
                    return self._deep_merge(heuristic_patch, cleaned_payload)
                return heuristic_patch
            return heuristic_patch
        except Exception:
            return heuristic_patch

    def _apply_creation_patch(self, sheet: dict, patch: dict) -> tuple[dict, list[str]]:
        out = self.ensure_sheet(sheet)
        if not isinstance(patch, dict):
            return out, []

        touched: list[str] = []
        char_name = str(patch.get("char_name") or "").strip()
        if char_name and not self._is_value_missing(char_name):
            out["char_name"] = char_name[:60]
            touched.append("nom")

        identity_patch = patch.get("identity") if isinstance(patch.get("identity"), dict) else {}
        identity = out.setdefault("identity", {})
        gender = str(identity_patch.get("gender") or "").strip()
        if gender and not self._is_value_missing(gender):
            identity["gender"] = gender[:40]
            touched.append("genre")
        social_class = str(identity_patch.get("social_class") or "").strip()
        if social_class and not self._is_value_missing(social_class):
            identity["social_class"] = social_class[:60]
            touched.append("classe sociale")
        age_apparent = str(identity_patch.get("age_apparent") or "").strip()
        if age_apparent and not self._is_value_missing(age_apparent):
            identity["age_apparent"] = age_apparent[:40]
            touched.append("age apparent")
        fam_patch = identity_patch.get("family") if isinstance(identity_patch.get("family"), dict) else {}
        family = identity.setdefault("family", {})
        origin = str(fam_patch.get("origin") or "").strip()
        if origin and not self._is_value_missing(origin):
            family["origin"] = origin[:80]
            touched.append("origine")
        reputation = str(fam_patch.get("reputation") or "").strip()
        if reputation and not self._is_value_missing(reputation):
            family["reputation"] = reputation[:80]

        visual_patch = patch.get("description_visuelle") if isinstance(patch.get("description_visuelle"), dict) else {}
        visual = out.setdefault("description_visuelle", {})
        short_desc = str(visual_patch.get("courte") or "").strip()
        if short_desc and not self._is_value_missing(short_desc):
            visual["courte"] = short_desc[:220]
            touched.append("apparence")
        long_desc = str(visual_patch.get("longue") or "").strip()
        if long_desc and not self._is_value_missing(long_desc):
            visual["longue"] = long_desc[:700]
        aura = str(visual_patch.get("aura") or "").strip()
        if aura and not self._is_value_missing(aura):
            visual["aura"] = aura[:180]

        persona = str(patch.get("char_persona") or "").strip()
        if persona and not self._is_value_missing(persona):
            out["char_persona"] = persona[:350]
            touched.append("personnalite")

        lore = out.setdefault("lore_details", {})
        lore_patch = patch.get("lore_details") if isinstance(patch.get("lore_details"), dict) else {}
        passives_patch = lore_patch.get("passives")
        if isinstance(passives_patch, list):
            existing = lore.get("passives")
            if not isinstance(existing, list):
                existing = []
            seen = {str((p or {}).get("nom") or "").strip().casefold() for p in existing if isinstance(p, dict)}
            for p in passives_patch:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("nom") or "").strip()
                effect = str(p.get("effet") or "").strip()
                if not name or self._is_value_missing(name):
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                existing.append({"nom": name[:80], "effet": effect[:220]})
            lore["passives"] = existing[:8]
            if existing:
                touched.append("atouts")

        world_logic_patch = patch.get("world_logic") if isinstance(patch.get("world_logic"), dict) else {}
        goals_patch = world_logic_patch.get("goals")
        if isinstance(goals_patch, list):
            goals = [str(g).strip() for g in goals_patch if isinstance(g, str) and str(g).strip()]
            if goals:
                world_logic = out.setdefault("world_logic", {})
                old_goals = world_logic.get("goals") if isinstance(world_logic.get("goals"), list) else []
                merged_goals = [*old_goals, *goals]
                dedup: list[str] = []
                seen = set()
                for g in merged_goals:
                    k = g.casefold()
                    if k in seen:
                        continue
                    seen.add(k)
                    dedup.append(g[:120])
                world_logic["goals"] = dedup[:8]

        speech_patch = patch.get("speech_style") if isinstance(patch.get("speech_style"), dict) else {}
        pronouns = str(speech_patch.get("pronouns") or "").strip()
        if pronouns and not self._is_value_missing(pronouns):
            speech = out.setdefault("speech_style", {})
            speech["pronouns"] = pronouns[:24]

        return self._sanitize_sheet(out), sorted(set(touched))

    async def _apply_creation_stat_distribution(
        self,
        sheet: dict,
        *,
        user_message: str,
        recent_chat_lines: list[str],
    ) -> tuple[dict, dict[str, int]]:
        out = self.ensure_sheet(sheet)
        bonus = {key: 0 for key in _STAT_KEYS}
        lore = out.get("lore_details", {}) if isinstance(out.get("lore_details"), dict) else {}
        passives = lore.get("passives")
        if self._has_valid_passive(passives):
            bonus = await self._infer_creation_stat_bonus(
                sheet=out,
                user_message=user_message,
                recent_chat_lines=recent_chat_lines,
            )
        stats = self._safe_stats(out.get("stats"))
        for key in _STAT_KEYS:
            stats[key] = _CREATION_BASE_STAT_VALUE + max(0, min(_CREATION_MAX_BONUS, self._safe_int(bonus.get(key), 0)))
        out["stats"] = stats

        metadata = out.setdefault("metadata", {})
        metadata["creation_stat_bonus"] = dict(bonus)
        metadata["creation_points_total"] = (_CREATION_BASE_STAT_VALUE * len(_STAT_KEYS)) + sum(bonus.values())
        return self._sanitize_sheet(out), bonus

    async def _infer_creation_stat_bonus(
        self,
        *,
        sheet: dict,
        user_message: str,
        recent_chat_lines: list[str],
    ) -> dict[str, int]:
        context_text = self._build_strength_context(
            sheet=sheet,
            user_message=user_message,
            recent_chat_lines=recent_chat_lines,
        )
        heuristic_weights = self._strength_weights_from_text(context_text)
        llm_weights = {key: 0 for key in _STAT_KEYS}
        if self.llm is not None:
            schema = {
                "stat_bonuses": {key: 0 for key in _STAT_KEYS},
                "reason": "",
            }
            prompt = (
                "Tu ajustes des statistiques de creation RPG selon les atouts du joueur.\n"
                "Reponds en JSON valide UNIQUEMENT.\n"
                "Contraintes: total bonus exact = 15 points.\n"
                "Chaque statistique bonussee doit etre entre 2 et 5.\n"
                "Les stats sans bonus restent a 0.\n"
                "Evite une repartition uniforme, privilegie la coherence avec les atouts.\n"
                "Contexte joueur:\n"
                f"{context_text[:1800]}\n"
                "Schema attendu:\n"
                f"{json.dumps(schema, ensure_ascii=False)}\n"
            )
            try:
                raw = await self.llm.generate(
                    model=model_for("rules"),
                    prompt=prompt,
                    temperature=0.25,
                    num_ctx=4096,
                    num_predict=260,
                    stop=None,
                )
                payload = json.loads(self._extract_json(raw))
                candidate = {}
                if isinstance(payload, dict):
                    if isinstance(payload.get("stat_bonuses"), dict):
                        candidate = payload.get("stat_bonuses") or {}
                    elif isinstance(payload.get("bonuses"), dict):
                        candidate = payload.get("bonuses") or {}
                if isinstance(candidate, dict):
                    for key in _STAT_KEYS:
                        llm_weights[key] = max(0, min(_CREATION_MAX_BONUS, self._safe_int(candidate.get(key), 0)))
            except Exception:
                llm_weights = {key: 0 for key in _STAT_KEYS}

        merged_weights: dict[str, int] = {}
        for key in _STAT_KEYS:
            merged_weights[key] = max(0, self._safe_int(heuristic_weights.get(key), 0) + (self._safe_int(llm_weights.get(key), 0) * 4))
        return self._allocate_creation_bonus(merged_weights, seed_text=context_text)

    def _build_strength_context(self, *, sheet: dict, user_message: str, recent_chat_lines: list[str]) -> str:
        chunks: list[str] = []
        lore = sheet.get("lore_details", {}) if isinstance(sheet.get("lore_details"), dict) else {}
        passives = lore.get("passives") if isinstance(lore.get("passives"), list) else []
        for passive in passives[:8]:
            if not isinstance(passive, dict):
                continue
            name = str(passive.get("nom") or "").strip()
            effect = str(passive.get("effet") or "").strip()
            if name or effect:
                chunks.append(f"{name}: {effect}".strip(": "))
        visual = sheet.get("description_visuelle", {}) if isinstance(sheet.get("description_visuelle"), dict) else {}
        short_desc = str(visual.get("courte") or "").strip()
        if short_desc:
            chunks.append(short_desc[:220])
        persona = str(sheet.get("char_persona") or "").strip()
        if persona:
            chunks.append(persona[:220])
        if user_message:
            chunks.append(user_message[:280])
        recent = [line for line in recent_chat_lines[-8:] if isinstance(line, str) and line.strip()]
        chunks.extend(recent)
        return "\n".join(chunks)

    def _strength_weights_from_text(self, text: str) -> dict[str, int]:
        lower = (text or "").casefold()
        weights = {key: 0 for key in _STAT_KEYS}
        rules: list[tuple[str, dict[str, int]]] = [
            (r"\b(magie|mage|magique|arcane|sort|sorcier|mana|rituel|enchant)\b", {"magie": 4, "intelligence": 3, "sagesse": 2}),
            (r"\b(intelligent|strategie|strat[eé]gie|analytique|calcul)\b", {"intelligence": 4, "sagesse": 2}),
            (r"\b(sage|sagesse|spirituel|meditation|priere|pr[iî]ere)\b", {"sagesse": 4, "magie": 2, "charisme": 1}),
            (r"\b(vitesse|rapide|agile|agilit[eé]|esquive|mobile)\b", {"agilite": 4, "dexterite": 2, "chance": 1}),
            (r"\b(dexterit[eé]|precis|pr[eé]cis|adresse|furtif|discret|voleur)\b", {"dexterite": 4, "agilite": 2, "chance": 1}),
            (r"\b(force|puissant|muscl|brute|berserk)\b", {"force": 4, "defense": 2}),
            (r"\b(defense|armure|robuste|endurant|tank|resistant|r[eé]sistant)\b", {"defense": 4, "force": 2}),
            (r"\b(chance|chanceux|fortune)\b", {"chance": 5}),
            (r"\b(charisme|diplomat|orateur|persuas|leader|noble)\b", {"charisme": 4, "sagesse": 1, "intelligence": 1}),
        ]
        for pattern, bonus in rules:
            if not re.search(pattern, lower):
                continue
            for stat_key, value in bonus.items():
                weights[stat_key] = max(0, self._safe_int(weights.get(stat_key), 0) + value)
        return weights

    def _allocate_creation_bonus(self, weights: dict[str, int], *, seed_text: str) -> dict[str, int]:
        token = (seed_text or "creation").encode("utf-8", errors="ignore")
        seed = int(hashlib.sha256(token).hexdigest()[:16], 16)
        rng = random.Random(seed)

        scored: list[tuple[float, str]] = []
        for idx, key in enumerate(_STAT_KEYS):
            base = max(0, self._safe_int(weights.get(key), 0))
            jitter = rng.random() * 0.75
            tie_break = (len(_STAT_KEYS) - idx) / 100.0
            scored.append((base + jitter + tie_break, key))
        scored.sort(reverse=True)
        ordered = [key for _, key in scored]

        positive_count = sum(1 for key in _STAT_KEYS if self._safe_int(weights.get(key), 0) > 0)
        slot_count = min(5, max(3, positive_count))
        selected = ordered[:slot_count]

        bonus = {key: 0 for key in _STAT_KEYS}
        for key in selected:
            bonus[key] = _CREATION_MIN_BONUS
        remaining = _CREATION_BONUS_POOL - (slot_count * _CREATION_MIN_BONUS)

        while remaining > 0:
            choices = [key for key in selected if bonus[key] < _CREATION_MAX_BONUS]
            if not choices:
                break
            choice_weights = []
            for key in choices:
                weight = max(1, self._safe_int(weights.get(key), 0))
                choice_weights.append(weight)
            pick = rng.choices(choices, weights=choice_weights, k=1)[0]
            bonus[pick] += 1
            remaining -= 1

        return bonus

    def _format_creation_bonus_summary(self, bonus: dict[str, int]) -> str:
        if not isinstance(bonus, dict):
            return ""
        parts: list[str] = []
        for key in _STAT_KEYS:
            value = self._safe_int(bonus.get(key), 0)
            if value > 0:
                parts.append(f"+{value} {key}")
        return ", ".join(parts)

    def _heuristic_creation_patch(self, text: str) -> dict:
        msg = (text or "").strip()
        lower = msg.casefold()
        patch: dict[str, Any] = {
            "char_name": "",
            "identity": {"gender": "", "social_class": "", "age_apparent": "", "family": {"origin": "", "reputation": ""}},
            "description_visuelle": {"courte": "", "longue": "", "aura": ""},
            "char_persona": "",
            "lore_details": {"passives": []},
            "world_logic": {"goals": []},
            "speech_style": {"pronouns": ""},
        }

        name_patterns = [
            r"\bje m(?:'| )appelle\s+([a-zA-Z0-9_\-']{2,30})",
            r"\bmon pseudo est\s+([a-zA-Z0-9_\-']{2,30})",
            r"\bpseudo\s*[:=]\s*([a-zA-Z0-9_\-']{2,30})",
            r"\bnom\s*[:=]\s*([a-zA-Z0-9_\-']{2,30})",
        ]
        for pat in name_patterns:
            m = re.search(pat, msg, flags=re.IGNORECASE)
            if m:
                patch["char_name"] = m.group(1).strip()
                break

        if re.search(r"\bhomme\b", lower):
            patch["identity"]["gender"] = "homme"
        elif re.search(r"\bfemme\b", lower):
            patch["identity"]["gender"] = "femme"
        elif re.search(r"non[- ]?binaire|nb\b", lower):
            patch["identity"]["gender"] = "non-binaire"

        if re.search(r"\b(vous? ressemble|ressemble|cheveux|yeux|taille|barbe|cicatrice|tenue|armure)\b", lower):
            patch["description_visuelle"]["courte"] = msg[:220]
            patch["description_visuelle"]["longue"] = msg[:700]

        if re.search(r"\b(calme|impulsif|froid|charism|timide|agressif|prudent|protecteur|calculateur)\b", lower):
            patch["char_persona"] = msg[:220]

        if re.search(r"\b(atout|atouts|talent|talents|specialite|specialites|spécialité|spécialités|fort en|bonne en|competence|competences|compétence|compétences)\b", lower):
            patch["lore_details"]["passives"] = [{"nom": "Atout declare", "effet": msg[:220]}]

        return patch

    def _load_template(self) -> dict:
        if self.template_path.exists():
            try:
                raw = json.loads(self.template_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                pass
        return {
            "metadata": {"version": "1.0", "char_id": ""},
            "char_name": "",
            "identity": {"social_class": "", "age_apparent": "inconnu", "gender": "", "family": {"origin": "", "relatives": [], "reputation": ""}},
            "speech_style": {"register": "neutre", "verbosity": "equilibre", "max_sentences_per_reply": 3, "vocabulary": "simple", "pronouns": "vouvoiement", "dialogue_tag": ""},
            "char_persona": "",
            "first_message": "",
            "lore_details": {"passives": [], "backstory": ""},
            "description_visuelle": {"courte": "", "longue": "", "aura": ""},
            "equipment": {"tenue": "", "arme": "", "accessoires": [], "objets_portes": []},
            "world_logic": {"knowledge_base": [], "goals": [], "secrets": []},
            "stats": {
                "niveau": 1,
                "experience": 0,
                "pv": 20,
                "pv_max": 20,
                "mana": 10,
                "mana_max": 10,
                "force": 5,
                "intelligence": 5,
                "magie": 5,
                "defense": 5,
                "sagesse": 5,
                "agilite": 5,
                "dexterite": 5,
                "chance": 5,
                "charisme": 5,
            },
            "dynamic_flags": {"is_met": False, "is_angry": False, "current_mood": "neutre"},
            "relations": {"inconnu": {"affinite": 0, "confiance": 5, "desir": 0, "statut": "etranger"}},
        }

    def _sanitize_sheet(self, sheet: dict) -> dict:
        out = self._deep_merge(copy.deepcopy(self.template), sheet if isinstance(sheet, dict) else {})
        out.setdefault("metadata", {})
        out["metadata"]["version"] = str(out["metadata"].get("version") or "1.0")
        out["metadata"]["char_id"] = str(out["metadata"].get("char_id") or uuid.uuid4())

        out["char_name"] = str(out.get("char_name") or "").strip()
        if len(out["char_name"]) > 60:
            out["char_name"] = out["char_name"][:60]

        stats = self._safe_stats(out.get("stats"))
        out["stats"] = stats

        lore = out.get("lore_details", {})
        if not isinstance(lore, dict):
            lore = {}
        passives = lore.get("passives")
        if isinstance(passives, list):
            normalized: list[dict] = []
            for p in passives[:8]:
                if not isinstance(p, dict):
                    continue
                nom = str(p.get("nom") or "").strip()
                eff = str(p.get("effet") or "").strip()
                if not nom:
                    continue
                normalized.append({"nom": nom[:80], "effet": eff[:220]})
            lore["passives"] = normalized
        else:
            lore["passives"] = []
        out["lore_details"] = lore

        return out

    def _safe_stats(self, raw_stats: object) -> dict:
        stats = raw_stats if isinstance(raw_stats, dict) else {}
        safe = {
            "niveau": max(1, self._safe_int(stats.get("niveau"), 1)),
            "experience": max(0, self._safe_int(stats.get("experience"), 0)),
            "pv": max(1, self._safe_int(stats.get("pv"), 20)),
            "pv_max": max(1, self._safe_int(stats.get("pv_max"), 20)),
            "mana": max(0, self._safe_int(stats.get("mana"), 10)),
            "mana_max": max(0, self._safe_int(stats.get("mana_max"), 10)),
        }
        for key in _STAT_KEYS:
            safe[key] = max(1, self._safe_int(stats.get(key), 5))
        safe["pv"] = min(safe["pv"], safe["pv_max"])
        safe["mana"] = min(safe["mana"], safe["mana_max"])
        return safe

    def _empty_progression(self) -> dict:
        return {
            "xp_gain": 0,
            "reason": "",
            "stat_deltas": {
                "force": 0,
                "intelligence": 0,
                "magie": 0,
                "defense": 0,
                "sagesse": 0,
                "agilite": 0,
                "dexterite": 0,
                "chance": 0,
                "charisme": 0,
                "pv_max": 0,
                "mana_max": 0,
            },
        }

    def _normalize_progression(self, raw: object) -> dict:
        if not isinstance(raw, dict):
            return self._empty_progression()
        out = self._empty_progression()
        out["xp_gain"] = max(0, min(self._safe_int(raw.get("xp_gain"), 0), 12))
        out["reason"] = str(raw.get("reason") or "").strip()
        deltas = raw.get("stat_deltas")
        if isinstance(deltas, dict):
            for key in out["stat_deltas"].keys():
                out["stat_deltas"][key] = max(0, min(self._safe_int(deltas.get(key), 0), 6))
        return out

    def _xp_needed_for_next_level(self, level: int) -> int:
        lvl = max(1, int(level))
        return 60 + ((lvl - 1) * 40)

    def _has_valid_passive(self, passives: object) -> bool:
        if not isinstance(passives, list):
            return False
        for p in passives:
            if not isinstance(p, dict):
                continue
            nom = str(p.get("nom") or "").strip()
            if nom and not self._is_value_missing(nom):
                return True
        return False

    def _is_value_missing(self, value: str) -> bool:
        compact = re.sub(r"\s+", " ", (value or "").strip()).casefold()
        return compact in _PLACEHOLDER_TOKENS

    def _deep_merge(self, base: object, patch: object) -> object:
        if not isinstance(base, dict) or not isinstance(patch, dict):
            return copy.deepcopy(patch) if patch is not None else copy.deepcopy(base)
        out = copy.deepcopy(base)
        for key, value in patch.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = self._deep_merge(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
        return out

    def _drop_empty_patch_values(self, value: object) -> object | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if self._is_value_missing(text):
                return None
            return text
        if isinstance(value, dict):
            out: dict[str, object] = {}
            for key, item in value.items():
                cleaned = self._drop_empty_patch_values(item)
                if cleaned is None:
                    continue
                if isinstance(cleaned, dict) and not cleaned:
                    continue
                if isinstance(cleaned, list) and not cleaned:
                    continue
                out[str(key)] = cleaned
            return out
        if isinstance(value, list):
            out_list: list[object] = []
            for item in value:
                cleaned = self._drop_empty_patch_values(item)
                if cleaned is None:
                    continue
                if isinstance(cleaned, dict) and not cleaned:
                    continue
                if isinstance(cleaned, list) and not cleaned:
                    continue
                out_list.append(cleaned)
            return out_list
        return copy.deepcopy(value)

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
