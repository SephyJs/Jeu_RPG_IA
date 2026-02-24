from __future__ import annotations

from difflib import SequenceMatcher
import json
import random
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import model_for


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

_INTENT_HINT_MAP: dict[str, tuple[str, ...]] = {
    "forge": ("forge", "forgeron", "metal", "acier", "marteau"),
    "balayage": ("balay", "auberge", "taverne", "service", "menage", "ménage"),
    "soin": ("soin", "heal", "temple", "medec", "méd", "infirm"),
    "magie": ("magie", "sort", "arcane", "runique", "rituel"),
    "cuisine": ("cuisine", "cuisin", "plat", "taverne"),
    "alchimie": ("alchim", "potion", "distill"),
    "marchandage": ("marchand", "commerce", "negoci", "vente"),
    "escrime": ("epee", "épée", "lame", "escrime", "duel"),
    "furtivite": ("furtif", "discret", "ombre", "infiltr"),
}

_ACTION_VERBS = (
    "utilise",
    "lance",
    "applique",
    "emploie",
    "travaille",
    "pratique",
    "pratiquer",
    "execute",
    "exécute",
    "fais",
    "fait",
    "passe",
    "balaye",
    "forge",
    "soigne",
    "attaque",
    "apprend",
    "entraîne",
    "entraine",
    "entrainer",
    "entrainement",
    "exerce",
    "exercer",
    "repete",
    "repetition",
    "drill",
    "sparring",
)

_INTENT_STOPWORDS = {
    "je",
    "tu",
    "il",
    "elle",
    "on",
    "nous",
    "vous",
    "ils",
    "elles",
    "dans",
    "avec",
    "pour",
    "de",
    "du",
    "des",
    "le",
    "la",
    "les",
    "un",
    "une",
    "et",
    "ou",
    "mais",
    "donc",
    "or",
    "ni",
    "car",
    "que",
    "qui",
    "quoi",
    "sur",
    "sous",
    "chez",
    "vers",
    "a",
    "au",
    "aux",
    "se",
    "me",
    "te",
    "mon",
    "ma",
    "mes",
    "ton",
    "ta",
    "tes",
    "son",
    "sa",
    "ses",
    "notre",
    "votre",
    "leur",
    "leurs",
    "tout",
    "tous",
    "toute",
    "toutes",
    "comme",
    "alors",
    "tres",
    "très",
    "encore",
    "souvent",
    "toujours",
    "entraine",
    "entrainer",
    "entrainement",
    "pratique",
    "pratiquer",
    "exerce",
    "exercer",
    "repete",
    "repetition",
    "drill",
    "sparring",
    "ennemi",
    "ennemis",
    "adversaire",
    "adversaires",
    "cible",
    "cibles",
    "monstre",
    "monstres",
    "creature",
    "creatures",
    "boss",
    "vase",
    "mur",
    "porte",
    "objet",
}


@dataclass(frozen=True)
class SkillDef:
    skill_id: str
    name: str
    category: str
    description: str
    difficulty: int
    primary_stats: tuple[str, ...]
    trainer_roles: tuple[str, ...]
    effects: tuple[str, ...]


class SkillManager:
    def __init__(self, llm: Any, *, data_path: str = "data/skills_catalog.json") -> None:
        self.llm = llm
        self.data_path = Path(data_path)
        self.rng = random.Random(20260209)

    def load_catalog(self) -> dict[str, SkillDef]:
        payload = self._read_catalog_payload()
        raw_skills = payload.get("skills")
        if not isinstance(raw_skills, list) or not raw_skills:
            raw_skills = self._fallback_catalog()
            payload["skills"] = raw_skills
            self._write_catalog_payload(payload)

        catalog: dict[str, SkillDef] = {}
        for row in raw_skills:
            if not isinstance(row, dict):
                continue
            skill = self._skill_from_row(row)
            if skill is None:
                continue
            catalog[skill.skill_id] = skill
        return catalog

    def normalize_known_skills(self, raw_skills: object, catalog: dict[str, SkillDef]) -> list[dict]:
        if not isinstance(raw_skills, list):
            return []
        normalized: list[dict] = []
        seen: set[str] = set()
        for row in raw_skills:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or "").strip().casefold()
            if not skill_id or skill_id in seen:
                continue
            skill = catalog.get(skill_id)
            if not skill:
                continue
            seen.add(skill_id)
            level = max(1, min(99, self._safe_int(row.get("level"), 1)))
            xp = max(0, self._safe_int(row.get("xp"), 0))
            uses = max(0, self._safe_int(row.get("uses"), 0))
            xp_to_next = self.xp_needed_for_next_level(level)
            if xp >= xp_to_next:
                while xp >= xp_to_next and level < 99:
                    xp -= xp_to_next
                    level += 1
                    xp_to_next = self.xp_needed_for_next_level(level)
            xp = min(xp, max(0, xp_to_next - 1)) if level < 99 else 0
            normalized.append(
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "category": skill.category,
                    "description": skill.description,
                    "difficulty": skill.difficulty,
                    "primary_stats": list(skill.primary_stats),
                    "rank": max(1, min(5, self._safe_int(row.get("rank"), 1))),
                    "level": level,
                    "xp": xp,
                    "xp_to_next": xp_to_next if level < 99 else 0,
                    "uses": uses,
                    "trainer_npc": str(row.get("trainer_npc") or "").strip()[:80],
                    "learned_at": str(row.get("learned_at") or "").strip()[:40],
                    "last_used_at": str(row.get("last_used_at") or "").strip()[:40],
                }
            )
        return normalized

    async def suggest_or_create_skill(
        self,
        *,
        catalog: dict[str, SkillDef],
        known_skill_ids: set[str],
        player_stats: dict[str, int],
        npc_name: str,
        npc_role: str,
        player_context: str,
        recent_chat_lines: list[str] | None = None,
    ) -> dict | None:
        if not isinstance(catalog, dict):
            return None

        all_skills = list(catalog.values())
        if not all_skills:
            seed = self._fallback_generated_skill(
                npc_name=npc_name,
                npc_role=npc_role,
                player_stats=player_stats,
                training_context=player_context,
            )
            created = self._register_skill(catalog, seed)
            return {
                "skill": created,
                "created": True,
                "reason": "Le PNJ improvise une nouvelle discipline adaptee.",
            }

        scored = sorted(
            all_skills,
            key=lambda s: self._suggestion_score(s, player_stats, npc_role, known_skill_ids),
            reverse=True,
        )
        top = scored[: min(20, len(scored))]
        training_context = self._build_training_context(player_context, recent_chat_lines)

        decision = await self._llm_select_or_create(
            top=top,
            known_skill_ids=known_skill_ids,
            player_stats=player_stats,
            npc_name=npc_name,
            npc_role=npc_role,
            training_context=training_context,
        )

        if isinstance(decision, dict):
            action = str(decision.get("action") or "").strip().casefold()
            reason = str(decision.get("reason") or "").strip()[:220]

            if action == "select":
                selected = self._resolve_selected_skill(decision, catalog)
                if isinstance(selected, SkillDef):
                    return {
                        "skill": selected,
                        "created": False,
                        "reason": reason or "Entrainement base sur une competence deja connue dans le monde.",
                    }

            if action == "create":
                generated = self._build_generated_skill(
                    raw=decision.get("new_skill"),
                    catalog=catalog,
                    player_stats=player_stats,
                    npc_role=npc_role,
                    training_context=training_context,
                    npc_name=npc_name,
                )
                final_skill, created = self._resolve_or_register_generated(catalog, generated)
                return {
                    "skill": final_skill,
                    "created": created,
                    "reason": reason or "Nouvelle competence formalisee a partir de cet apprentissage.",
                }

        if self._should_create_from_context(training_context, top):
            generated = self._fallback_generated_skill(
                npc_name=npc_name,
                npc_role=npc_role,
                player_stats=player_stats,
                training_context=training_context,
            )
            final_skill, created = self._resolve_or_register_generated(catalog, generated)
            return {
                "skill": final_skill,
                "created": created,
                "reason": "Aucune competence existante ne collait bien: creation d'une nouvelle discipline.",
            }

        weights = [max(1.0, self._suggestion_score(s, player_stats, npc_role, known_skill_ids)) for s in top]
        picked = self.rng.choices(top, weights=weights, k=1)[0]
        return {
            "skill": picked,
            "created": False,
            "reason": "Proposition adaptee a vos statistiques et au formateur.",
        }

    def attempt_learning(
        self,
        *,
        skill: SkillDef,
        player_stats: dict[str, int],
        npc_role: str,
        skill_points: int,
    ) -> dict:
        points_before = max(0, int(skill_points))
        if points_before <= 0:
            return {
                "success": False,
                "reason": "Aucun point de competence disponible.",
                "skill_points_after": 0,
                "chance": 0,
                "roll": 0,
            }

        primary_values = [max(1, self._safe_int(player_stats.get(key), 5)) for key in skill.primary_stats if key in _STAT_KEYS]
        average = sum(primary_values) / float(len(primary_values) or 1)
        stat_bonus = int(round((average - 5.0) * 5.0))
        stat_bonus = max(-22, min(32, stat_bonus))

        base_chance = 84 - ((skill.difficulty - 1) * 14)
        role_bonus = 10 if self._role_matches(skill, npc_role) else 0
        chance = max(8, min(95, base_chance + stat_bonus + role_bonus))

        roll = self.rng.randint(1, 100)
        success = roll <= chance
        points_after = max(0, points_before - 1)

        return {
            "success": success,
            "skill_points_after": points_after,
            "chance": chance,
            "roll": roll,
            "reason": self._learning_reason(skill, stat_bonus, role_bonus, success),
        }

    def xp_needed_for_next_level(self, level: int) -> int:
        lvl = max(1, min(99, int(level)))
        # Courbe exponentielle: progression de plus en plus exigeante.
        return max(12, int(round(24 * (1.45 ** (lvl - 1)))))

    def detect_used_skill_ids(self, text: str, known_skills: list[dict]) -> list[str]:
        if not text or not isinstance(known_skills, list):
            return []
        lower = self._norm(text)
        words = self._split_tokens(lower)
        found: list[str] = []
        for row in known_skills:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or "").strip().casefold()
            if not skill_id:
                continue
            name = self._norm(str(row.get("name") or ""))
            id_phrase = self._norm(skill_id.replace("_", " "))
            if (len(name) >= 4 and name in lower) or (len(id_phrase) >= 4 and id_phrase in lower):
                if skill_id not in found:
                    found.append(skill_id)
                continue

            # Fuzzy tolerant for typos on skill names.
            name_tokens = [tok for tok in self._split_tokens(name) if len(tok) >= 4]
            if name_tokens and words:
                matched = 0
                for token in name_tokens:
                    if any(self._fuzzy_ratio(token, w) >= 0.82 for w in words if len(w) >= 3):
                        matched += 1
                if matched >= max(1, len(name_tokens) - 1):
                    if skill_id not in found:
                        found.append(skill_id)

        # Detection intentionnelle "j'utilise/lance/applique ..." + categorie.
        action_hit = any(verb in lower for verb in _ACTION_VERBS)
        if not action_hit:
            return found[:3]

        hinted_intents = self._extract_intent_hints(text)
        if not hinted_intents:
            return found[:3]

        for row in known_skills:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or "").strip().casefold()
            if not skill_id:
                continue
            if any(self.skill_matches_intent(row, intent) for intent in hinted_intents):
                if skill_id not in found:
                    found.append(skill_id)
                    continue
            category = self._norm(str(row.get("category") or ""))
            if category and any(self._fuzzy_ratio(category, intent) >= 0.82 for intent in hinted_intents):
                found.append(skill_id)
        return found[:3]

    def estimate_usage_xp_gain(self, skill_entry: dict, text: str) -> int:
        if not isinstance(skill_entry, dict):
            return 0
        level = max(1, min(99, self._safe_int(skill_entry.get("level"), 1)))
        difficulty = max(1, min(5, self._safe_int(skill_entry.get("difficulty"), 2)))
        rank = max(1, min(5, self._safe_int(skill_entry.get("rank"), 1)))
        base = 3 + difficulty + (rank - 1)

        lower = self._norm(text)
        quality_bonus = 0
        if len(lower) >= 80:
            quality_bonus += 1
        if len(lower) >= 160:
            quality_bonus += 1
        if re.search(r"\b(combine|combo|precision|précision|canalise|optimise|forge|rituel)\b", lower):
            quality_bonus += 1
        # Léger amortissement à haut niveau pour forcer l'effort.
        level_penalty = (level - 1) // 10
        gain = max(1, base + quality_bonus - level_penalty)
        return min(gain, 18)

    def estimate_training_xp_gain(self, skill_entry: dict, *, success: bool) -> int:
        if not success or not isinstance(skill_entry, dict):
            return 0
        level = max(1, min(99, self._safe_int(skill_entry.get("level"), 1)))
        difficulty = max(1, min(5, self._safe_int(skill_entry.get("difficulty"), 2)))
        rank = max(1, min(5, self._safe_int(skill_entry.get("rank"), 1)))
        base = 8 + (difficulty * 2) + rank
        level_penalty = (level - 1) // 8
        return max(3, min(28, base - level_penalty))

    async def infer_intent_hints(
        self,
        text: str,
        *,
        existing_intents: list[str] | None = None,
        known_categories: list[str] | None = None,
        known_skill_names: list[str] | None = None,
    ) -> list[str]:
        base_candidates = self._extract_intent_hints(text)

        llm_candidates: list[str] = []
        if self.llm is not None and str(text or "").strip():
            schema = {"intents": ["forge", "balayage"], "reason": ""}
            prompt = (
                "Tu identifies les intentions de pratique/competence dans une phrase joueur RPG.\n"
                "Reponds en JSON valide UNIQUEMENT.\n"
                "Corrige les fautes de frappe si possible.\n"
                "Ne limite pas les styles: toute competence est possible.\n"
                "Retourne 1 a 4 labels courts (intents) qui representent ce que le joueur pratique.\n"
                f"Phrase: {str(text)[:1200]}\n"
                f"Intents existants: {existing_intents or []}\n"
                f"Categories connues: {known_categories or []}\n"
                f"Competences connues: {known_skill_names or []}\n"
                f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
            )
            try:
                raw = await self.llm.generate(
                    model=model_for("rules"),
                    prompt=prompt,
                    temperature=0.2,
                    num_ctx=4096,
                    num_predict=200,
                    stop=None,
                )
                payload = json.loads(self._extract_json(raw))
                intents_raw = None
                if isinstance(payload, dict):
                    intents_raw = payload.get("intents")
                elif isinstance(payload, list):
                    intents_raw = payload
                if isinstance(intents_raw, list):
                    llm_candidates = [str(x).strip() for x in intents_raw if isinstance(x, str) and str(x).strip()]
            except Exception:
                llm_candidates = []

        all_candidates = [*llm_candidates, *base_candidates]
        if not all_candidates:
            return []

        out: list[str] = []
        for raw in all_candidates:
            label = self.canonicalize_intent_label(
                raw,
                existing_intents=existing_intents,
                known_categories=known_categories,
                known_skill_names=known_skill_names,
            )
            if label and label not in out:
                out.append(label)
        return out[:4]

    def extract_intent_hints(self, text: str) -> list[str]:
        return self._extract_intent_hints(text)

    def canonicalize_intent_label(
        self,
        raw_label: str,
        *,
        existing_intents: list[str] | None = None,
        known_categories: list[str] | None = None,
        known_skill_names: list[str] | None = None,
    ) -> str:
        raw = self._norm(raw_label)
        if not raw:
            return ""

        tokens = [t for t in self._split_tokens(raw) if t not in _INTENT_STOPWORDS and len(t) >= 3]
        if not tokens:
            return ""
        compact = "_".join(tokens[:3]).strip("_")
        if not compact:
            return ""

        candidates: list[str] = []
        for value in (existing_intents or []):
            if isinstance(value, str):
                norm = self._norm(value)
                if norm:
                    candidates.append(norm)
        for value in (known_categories or []):
            if isinstance(value, str):
                norm = self._norm(value)
                if norm:
                    candidates.append(norm)
        if isinstance(known_skill_names, list):
            for value in known_skill_names:
                if not isinstance(value, str):
                    continue
                norm = self._norm(value)
                if norm:
                    candidates.append(norm)
        candidates.extend(_INTENT_HINT_MAP.keys())

        best = compact
        best_score = 0.0
        for candidate in candidates:
            cand_norm = "_".join(self._split_tokens(candidate)[:3]).strip("_")
            if not cand_norm:
                continue
            score = self._fuzzy_ratio(compact, cand_norm)
            if score > best_score:
                best_score = score
                best = cand_norm

        if best_score >= 0.82:
            return best[:40]
        return compact[:40]

    def skill_matches_intent(self, skill_like: object, intent: str) -> bool:
        intent_key = self.canonicalize_intent_label(intent)
        if not intent_key:
            return False
        if isinstance(skill_like, SkillDef):
            blob = " ".join(
                [
                    skill_like.name,
                    skill_like.category,
                    skill_like.description,
                    " ".join(skill_like.effects),
                    " ".join(skill_like.trainer_roles),
                ]
            )
        elif isinstance(skill_like, dict):
            blob = " ".join(
                [
                    str(skill_like.get("name") or ""),
                    str(skill_like.get("category") or ""),
                    str(skill_like.get("description") or ""),
                    " ".join(str(x) for x in (skill_like.get("effects") or []) if isinstance(x, str)),
                    " ".join(str(x) for x in (skill_like.get("trainer_roles") or []) if isinstance(x, str)),
                ]
            )
        else:
            return False
        skill_tokens = self._split_tokens(blob)
        intent_tokens = self._split_tokens(intent_key)
        if not intent_tokens:
            return False

        for token in intent_tokens:
            if token in skill_tokens:
                return True
            if any(self._fuzzy_ratio(token, st) >= 0.82 for st in skill_tokens if len(st) >= 3):
                return True
        return False

    def apply_skill_xp(self, skill_entry: dict, *, xp_gain: int, used_at_iso: str = "") -> dict:
        if not isinstance(skill_entry, dict):
            return {
                "xp_gain": 0,
                "levels_gained": 0,
                "level_before": 1,
                "level_after": 1,
                "xp_after": 0,
                "xp_to_next": self.xp_needed_for_next_level(1),
                "uses_after": 0,
            }

        level_before = max(1, min(99, self._safe_int(skill_entry.get("level"), 1)))
        level = level_before
        xp = max(0, self._safe_int(skill_entry.get("xp"), 0))
        uses = max(0, self._safe_int(skill_entry.get("uses"), 0))
        gain = max(0, self._safe_int(xp_gain, 0))
        xp += gain
        if gain > 0:
            uses += 1

        levels_gained = 0
        while level < 99:
            needed = self.xp_needed_for_next_level(level)
            if xp < needed:
                break
            xp -= needed
            level += 1
            levels_gained += 1

        xp_to_next = self.xp_needed_for_next_level(level) if level < 99 else 0
        if level >= 99:
            xp = 0
        else:
            xp = min(max(0, xp), max(0, xp_to_next - 1))

        skill_entry["level"] = level
        skill_entry["xp"] = xp
        skill_entry["xp_to_next"] = xp_to_next
        skill_entry["uses"] = uses
        if used_at_iso:
            skill_entry["last_used_at"] = str(used_at_iso)[:40]

        return {
            "xp_gain": gain,
            "levels_gained": levels_gained,
            "level_before": level_before,
            "level_after": level,
            "xp_after": xp,
            "xp_to_next": xp_to_next,
            "uses_after": uses,
        }

    async def _llm_select_or_create(
        self,
        *,
        top: list[SkillDef],
        known_skill_ids: set[str],
        player_stats: dict[str, int],
        npc_name: str,
        npc_role: str,
        training_context: str,
    ) -> dict | None:
        if self.llm is None:
            return None

        schema = {
            "action": "select|create",
            "skill_id": "",
            "reason": "",
            "new_skill": {
                "id": "",
                "name": "",
                "category": "",
                "description": "",
                "difficulty": 2,
                "primary_stats": ["force", "dexterite"],
                "trainer_roles": [npc_role],
                "effects": ["effet narratif court"],
            },
        }
        candidates = [
            {
                "id": s.skill_id,
                "name": s.name,
                "category": s.category,
                "difficulty": s.difficulty,
                "primary_stats": list(s.primary_stats),
            }
            for s in top
        ]
        prompt = (
            "Tu geres un systeme de competences RPG.\n"
            "Reponds en JSON valide UNIQUEMENT.\n"
            "Tu as deux options:\n"
            "- action=select: choisir une competence existante\n"
            "- action=create: creer une nouvelle competence et remplir new_skill\n"
            "Il n'y a AUCUNE limite de style (magie, forge, soins, artisanat, service d'auberge, balayage, etc.).\n"
            "Si la demande semble specifique et absente des candidates, choisis action=create.\n"
            "Evite les doublons de nom/id.\n"
            f"PNJ formateur: {npc_name} ({npc_role})\n"
            f"Stats joueur: {json.dumps(player_stats, ensure_ascii=False)}\n"
            f"Competences deja apprises: {sorted(known_skill_ids)}\n"
            f"Contexte d'entrainement: {training_context[:1500]}\n"
            f"Candidates existantes: {json.dumps(candidates, ensure_ascii=False)}\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        )
        try:
            raw = await self.llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.35,
                num_ctx=4096,
                num_predict=420,
                stop=None,
            )
            payload = json.loads(self._extract_json(raw))
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    def _resolve_selected_skill(self, payload: dict, catalog: dict[str, SkillDef]) -> SkillDef | None:
        skill_id = str(payload.get("skill_id") or "").strip().casefold()
        if skill_id and skill_id in catalog:
            return catalog[skill_id]
        name = str(payload.get("skill_name") or "").strip()
        if name:
            name_norm = self._norm(name)
            for skill in catalog.values():
                if self._norm(skill.name) == name_norm:
                    return skill
        return None

    def _build_generated_skill(
        self,
        *,
        raw: object,
        catalog: dict[str, SkillDef],
        player_stats: dict[str, int],
        npc_role: str,
        training_context: str,
        npc_name: str,
    ) -> SkillDef:
        row = raw if isinstance(raw, dict) else {}
        name = str(row.get("name") or "").strip()
        if (not name) or self._is_generic_generated_name(name):
            name = self._fallback_skill_name(npc_role=npc_role, training_context=training_context, npc_name=npc_name)
        category = str(row.get("category") or "").strip().casefold()[:48] or "hybride"
        description = str(row.get("description") or "").strip()[:360]
        if not description:
            description = f"Technique transmise par {npc_name} autour de {npc_role}."
        difficulty = max(1, min(5, self._safe_int(row.get("difficulty"), self._infer_difficulty(training_context))))

        primary_stats = self._normalize_primary_stats(
            raw=row.get("primary_stats"),
            training_context=training_context,
            player_stats=player_stats,
            category=category,
        )

        trainer_roles = self._normalize_roles(row.get("trainer_roles"), npc_role)
        effects = self._normalize_effects(row.get("effects"), training_context)

        skill_id_raw = str(row.get("id") or row.get("skill_id") or "").strip().casefold()
        if not skill_id_raw:
            skill_id_raw = self._slugify(name) or self._slugify(f"{category}_{npc_role}") or "competence_custom"
        skill_id = self._ensure_unique_skill_id(skill_id_raw, set(catalog.keys()))

        return SkillDef(
            skill_id=skill_id,
            name=name[:80],
            category=category,
            description=description,
            difficulty=difficulty,
            primary_stats=tuple(primary_stats[:3]),
            trainer_roles=tuple(trainer_roles[:8]),
            effects=tuple(effects[:8]),
        )

    def _is_generic_generated_name(self, name: str) -> bool:
        norm = self._norm(name)
        if not norm:
            return True
        generic_names = {
            "nouveau sort",
            "new spell",
            "new skill",
            "nouvelle competence",
            "nouvelle technique",
            "competence",
            "technique",
            "skill",
            "spell",
        }
        return norm in generic_names

    def _resolve_or_register_generated(self, catalog: dict[str, SkillDef], generated: SkillDef) -> tuple[SkillDef, bool]:
        for skill in catalog.values():
            if self._norm(skill.name) == self._norm(generated.name):
                return skill, False
        if generated.skill_id in catalog:
            return catalog[generated.skill_id], False

        created = self._register_skill(catalog, generated)
        return created, True

    def _register_skill(self, catalog: dict[str, SkillDef], skill: SkillDef) -> SkillDef:
        catalog[skill.skill_id] = skill
        payload = self._read_catalog_payload()
        skills = payload.get("skills")
        if not isinstance(skills, list):
            skills = []
            payload["skills"] = skills

        for row in skills:
            if not isinstance(row, dict):
                continue
            existing_id = str(row.get("id") or row.get("skill_id") or "").strip().casefold()
            if existing_id == skill.skill_id:
                return skill
            existing_name = str(row.get("name") or "").strip()
            if existing_name and self._norm(existing_name) == self._norm(skill.name):
                return skill

        skills.append(self._skill_to_row(skill))
        skills.sort(key=lambda row: str(row.get("id") or "").casefold())
        payload["skills"] = skills
        self._write_catalog_payload(payload)
        return skill

    def _should_create_from_context(self, training_context: str, top: list[SkillDef]) -> bool:
        hints = self._extract_intent_hints(training_context)
        if not hints:
            return False
        covered: set[str] = set()
        for hint in hints:
            for skill in top:
                if self.skill_matches_intent(skill, hint):
                    covered.add(hint)
                    break
        # Si le contexte contient des intentions partiellement couvertes
        # (ex: "sort de balayage" -> magie couverte mais pas "balayage"),
        # on préfère créer une compétence dédiée.
        return len(covered) < len(hints)

    def _extract_intent_hints(self, text: str) -> list[str]:
        lower = self._norm(text)
        words = self._split_tokens(lower)
        found: list[str] = []

        # 1) Base hints (fuzzy-friendly).
        for label, tokens in _INTENT_HINT_MAP.items():
            hit = False
            for token in tokens:
                tok = self._norm(token)
                if not tok:
                    continue
                if tok in lower:
                    hit = True
                    break
                if any(self._fuzzy_ratio(tok, w) >= 0.82 for w in words if len(w) >= 4):
                    hit = True
                    break
            if hit:
                found.append(label)

        # 2) Freeform fallback when action verbs are present.
        action_present = any(verb in lower for verb in _ACTION_VERBS)
        if action_present:
            for idx, word in enumerate(words):
                if word in _INTENT_STOPWORDS or len(word) < 4:
                    continue
                # ignore very common verbs likely not an intent
                if word in {
                    "utilise",
                    "lance",
                    "applique",
                    "emploie",
                    "pratique",
                    "pratiquer",
                    "travaille",
                    "fais",
                    "fait",
                    "entraine",
                    "entrainer",
                    "entrainement",
                    "exerce",
                    "exercer",
                    "repete",
                    "repetition",
                }:
                    continue
                # preferentially keep words after "de/du/des/d" patterns
                if idx > 0 and words[idx - 1] in {"de", "du", "des", "d", "a", "au", "aux", "avec", "sur"}:
                    if word not in found:
                        found.append(word)
                # also keep nouns after action verbs
                if idx > 0 and words[idx - 1] in _ACTION_VERBS and word not in found:
                    found.append(word)

            # broad fallback: first informative words
            if not found:
                for word in words:
                    if word in _INTENT_STOPWORDS or len(word) < 4:
                        continue
                    found.append(word)
                    if len(found) >= 2:
                        break

        dedup: list[str] = []
        for label in found:
            normalized = self.canonicalize_intent_label(label)
            if normalized and normalized not in dedup:
                dedup.append(normalized)
        return dedup[:4]

    def _fallback_generated_skill(
        self,
        *,
        npc_name: str,
        npc_role: str,
        player_stats: dict[str, int],
        training_context: str,
    ) -> SkillDef:
        name = self._fallback_skill_name(npc_role=npc_role, training_context=training_context, npc_name=npc_name)
        category = self._fallback_category(training_context, npc_role)
        stats = self._normalize_primary_stats(
            raw=[],
            training_context=training_context,
            player_stats=player_stats,
            category=category,
        )
        description = f"Technique developpee avec {npc_name} ({npc_role})."
        base_id = self._slugify(name) or "competence_custom"
        return SkillDef(
            skill_id=base_id,
            name=name[:80],
            category=category,
            description=description[:360],
            difficulty=max(1, min(5, self._infer_difficulty(training_context))),
            primary_stats=tuple(stats[:3]),
            trainer_roles=tuple(self._normalize_roles([], npc_role)[:8]),
            effects=tuple(self._normalize_effects([], training_context)[:8]),
        )

    def _fallback_skill_name(self, *, npc_role: str, training_context: str, npc_name: str) -> str:
        lower = self._norm(training_context)
        if "forge" in lower or "forger" in lower:
            return "Forge artisanale"
        if "balay" in lower or "auberge" in lower or "taverne" in lower:
            return "Balayage tactique"
        if "soin" in lower or "temple" in lower:
            return "Soin applique"
        if "magie" in lower or "sort" in lower:
            return "Canalisation improvisee"
        role_clean = str(npc_role or "").strip()
        if role_clean:
            return f"Discipline de {role_clean[:40]}"
        return f"Technique de {npc_name[:40]}"

    def _fallback_category(self, training_context: str, npc_role: str) -> str:
        lower = self._norm(f"{training_context} {npc_role}")
        if any(token in lower for token in ("forge", "forger", "metal")):
            return "artisanat"
        if any(token in lower for token in ("magie", "sort", "arcane")):
            return "magie"
        if any(token in lower for token in ("soin", "temple", "medec")):
            return "soin"
        if any(token in lower for token in ("balay", "auberge", "taverne", "service")):
            return "service"
        if any(token in lower for token in ("combat", "epee", "lame", "attaque")):
            return "combat"
        return "hybride"

    def _normalize_primary_stats(
        self,
        *,
        raw: object,
        training_context: str,
        player_stats: dict[str, int],
        category: str,
    ) -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                key = str(value or "").strip().casefold()
                if key in _STAT_KEYS and key not in out:
                    out.append(key)

        if not out:
            lower = self._norm(f"{training_context} {category}")
            if any(token in lower for token in ("magie", "sort", "arcane", "runique")):
                out = ["magie", "intelligence"]
            elif any(token in lower for token in ("forge", "metal", "marteau", "artisan")):
                out = ["force", "dexterite"]
            elif any(token in lower for token in ("soin", "temple", "medec", "infirm")):
                out = ["sagesse", "magie"]
            elif any(token in lower for token in ("balay", "auberge", "service", "taverne", "cuisine")):
                out = ["dexterite", "charisme"]
            elif any(token in lower for token in ("combat", "attaque", "epee", "lame")):
                out = ["force", "agilite"]
            else:
                ranked = sorted(_STAT_KEYS, key=lambda k: self._safe_int(player_stats.get(k), 5), reverse=True)
                out = ranked[:2]

        if len(out) == 1:
            ranked = sorted(_STAT_KEYS, key=lambda k: self._safe_int(player_stats.get(k), 5), reverse=True)
            for key in ranked:
                if key != out[0]:
                    out.append(key)
                    break
        return out[:3] if out else ["intelligence", "sagesse"]

    def _normalize_roles(self, raw: object, npc_role: str) -> list[str]:
        roles: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                role = self._norm(str(value or ""))
                if role and role not in roles:
                    roles.append(role[:40])
        role_base = self._norm(npc_role)
        if role_base and role_base not in roles:
            roles.insert(0, role_base[:40])
        if not roles:
            roles = ["formateur"]
        return roles[:8]

    def _normalize_effects(self, raw: object, training_context: str) -> list[str]:
        effects: list[str] = []
        if isinstance(raw, list):
            effects = [str(value).strip()[:140] for value in raw if isinstance(value, str) and str(value).strip()]
        if not effects:
            hints = self._extract_intent_hints(training_context)
            if hints:
                effects = [f"effet_{hints[0]}"]
            else:
                effects = ["effet_polyvalent"]
        return effects[:8]

    def _build_training_context(self, player_context: str, recent_chat_lines: list[str] | None) -> str:
        chunks: list[str] = []
        base = str(player_context or "").strip()
        if base:
            chunks.append(base[:900])
        if isinstance(recent_chat_lines, list):
            for line in recent_chat_lines[-12:]:
                if isinstance(line, str) and line.strip():
                    chunks.append(line[:180])
        return "\n".join(chunks)

    def _infer_difficulty(self, training_context: str) -> int:
        lower = self._norm(training_context)
        if any(token in lower for token in ("ultime", "maitre", "mythique", "legenda")):
            return 5
        if any(token in lower for token in ("avance", "expert", "complexe")):
            return 4
        if any(token in lower for token in ("intermediaire", "intermedia", "discipline")):
            return 3
        return 2

    def _learning_reason(self, skill: SkillDef, stat_bonus: int, role_bonus: int, success: bool) -> str:
        parts: list[str] = []
        if stat_bonus > 0:
            parts.append("vos stats aident l'apprentissage")
        elif stat_bonus < 0:
            parts.append("vos stats rendent l'exercice plus difficile")
        else:
            parts.append("niveau de base neutre")
        if role_bonus > 0:
            parts.append("le PNJ maitrise bien cette discipline")
        if success:
            return f"{skill.name} acquis: " + ", ".join(parts) + "."
        return f"{skill.name} non acquis: " + ", ".join(parts) + "."

    def _suggestion_score(
        self,
        skill: SkillDef,
        player_stats: dict[str, int],
        npc_role: str,
        known_skill_ids: set[str],
    ) -> float:
        values = [max(1, self._safe_int(player_stats.get(key), 5)) for key in skill.primary_stats if key in _STAT_KEYS]
        avg_stat = sum(values) / float(len(values) or 1)
        role_bonus = 1.25 if self._role_matches(skill, npc_role) else 1.0
        difficulty_factor = max(0.7, 1.3 - (skill.difficulty * 0.1))
        novelty = 1.15 if skill.skill_id not in known_skill_ids else 0.9
        return max(0.1, avg_stat * role_bonus * difficulty_factor * novelty)

    def _role_matches(self, skill: SkillDef, npc_role: str) -> bool:
        role = self._norm(npc_role)
        if not role:
            return False
        for token in skill.trainer_roles:
            if token and token in role:
                return True
        return False

    def _read_catalog_payload(self) -> dict:
        default = {
            "version": "1.0",
            "skills": [dict(row) for row in self._fallback_catalog()],
        }
        if not self.data_path.exists():
            return default
        try:
            payload = json.loads(self.data_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                skills = payload.get("skills")
                if isinstance(skills, list):
                    return payload
            if isinstance(payload, list):
                return {"version": "1.0", "skills": payload}
        except Exception:
            return default
        return default

    def _write_catalog_payload(self, payload: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _skill_from_row(self, row: dict) -> SkillDef | None:
        skill_id = str(row.get("id") or row.get("skill_id") or "").strip().casefold()
        if not skill_id:
            return None
        name = str(row.get("name") or skill_id).strip()[:80]
        category = str(row.get("category") or "general").strip().casefold()[:48] or "general"
        description = str(row.get("description") or "").strip()[:360]
        difficulty = max(1, min(5, self._safe_int(row.get("difficulty"), 2)))

        primary_stats = self._normalize_primary_stats(
            raw=row.get("primary_stats"),
            training_context=f"{name} {description}",
            player_stats={},
            category=category,
        )
        trainer_roles = self._normalize_roles(row.get("trainer_roles"), npc_role="")
        effects = self._normalize_effects(row.get("effects"), training_context=f"{name} {category}")

        return SkillDef(
            skill_id=skill_id,
            name=name,
            category=category,
            description=description,
            difficulty=difficulty,
            primary_stats=tuple(primary_stats[:3]),
            trainer_roles=tuple(trainer_roles[:8]),
            effects=tuple(effects[:8]),
        )

    def _skill_to_row(self, skill: SkillDef) -> dict:
        return {
            "id": skill.skill_id,
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
            "difficulty": skill.difficulty,
            "primary_stats": list(skill.primary_stats),
            "trainer_roles": list(skill.trainer_roles),
            "effects": list(skill.effects),
        }

    def _ensure_unique_skill_id(self, base: str, existing_ids: set[str]) -> str:
        clean = self._slugify(base) or "competence_custom"
        if clean not in existing_ids:
            return clean
        idx = 2
        while f"{clean}_{idx}" in existing_ids:
            idx += 1
        return f"{clean}_{idx}"

    def _slugify(self, value: str) -> str:
        compact = self._norm(value)
        compact = re.sub(r"[^a-z0-9]+", "_", compact)
        compact = compact.strip("_")
        return compact[:64]

    def _ascii_fold(self, value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(ch for ch in text if not unicodedata.combining(ch))

    def _split_tokens(self, value: str) -> list[str]:
        folded = self._ascii_fold(value).casefold()
        chunks = re.split(r"[^a-z0-9]+", folded)
        return [c for c in chunks if c]

    def _fuzzy_ratio(self, a: str, b: str) -> float:
        a_norm = self._norm(a)
        b_norm = self._norm(b)
        if not a_norm or not b_norm:
            return 0.0
        if a_norm == b_norm:
            return 1.0
        return SequenceMatcher(None, a_norm, b_norm).ratio()

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"

    def _norm(self, value: str) -> str:
        folded = self._ascii_fold(value)
        return re.sub(r"\s+", " ", folded.strip()).casefold()

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _fallback_catalog(self) -> list[dict]:
        return [
            {"id": "frappe_precise", "name": "Frappe precise", "category": "combat", "description": "Attaque melee plus stable.", "difficulty": 1, "primary_stats": ["force", "dexterite"], "trainer_roles": ["forgeron", "garde", "mercenaire"], "effects": ["bonus_melee_legere"]},
            {"id": "garde_fermee", "name": "Garde fermee", "category": "defense", "description": "Renforce la posture defensive.", "difficulty": 1, "primary_stats": ["defense", "sagesse"], "trainer_roles": ["garde", "templier", "soldat"], "effects": ["bonus_parade"]},
            {"id": "pas_silencieux", "name": "Pas silencieux", "category": "furtivite", "description": "Deplacements plus discrets.", "difficulty": 2, "primary_stats": ["agilite", "dexterite"], "trainer_roles": ["rodeur", "voleur", "eclaireur"], "effects": ["bonus_furtivite"]},
            {"id": "etincelle", "name": "Etincelle", "category": "magie", "description": "Sort mineur offensif.", "difficulty": 2, "primary_stats": ["magie", "intelligence"], "trainer_roles": ["mage", "arcaniste", "sorcier"], "effects": ["sort_offensif_novice"]},
            {"id": "soin_leger", "name": "Soin leger", "category": "magie_sacree", "description": "Canalise un soin basique.", "difficulty": 2, "primary_stats": ["sagesse", "magie"], "trainer_roles": ["pretre", "temple", "soigneur"], "effects": ["sort_soin_novice"]},
            {"id": "analyse_arcane", "name": "Analyse arcane", "category": "connaissance", "description": "Detecte des residus magiques.", "difficulty": 2, "primary_stats": ["intelligence", "sagesse"], "trainer_roles": ["mage", "erudit", "alchimiste"], "effects": ["analyse_objet"]},
            {"id": "charge_brutale", "name": "Charge brutale", "category": "combat", "description": "Percussion puissante au corps-a-corps.", "difficulty": 3, "primary_stats": ["force", "defense"], "trainer_roles": ["mercenaire", "soldat", "garde"], "effects": ["attaque_lourde"]},
            {"id": "tir_instinctif", "name": "Tir instinctif", "category": "distance", "description": "Tir rapide sur cible mobile.", "difficulty": 3, "primary_stats": ["dexterite", "agilite"], "trainer_roles": ["chasseur", "rodeur", "garde"], "effects": ["attaque_distance"]},
            {"id": "sceau_de_givre", "name": "Sceau de givre", "category": "magie", "description": "Ralentit la cible un court instant.", "difficulty": 3, "primary_stats": ["magie", "sagesse"], "trainer_roles": ["mage", "arcaniste"], "effects": ["controle_legere"]},
            {"id": "mot_dautorite", "name": "Mot d'autorite", "category": "social", "description": "Presence et persuasion accrues.", "difficulty": 3, "primary_stats": ["charisme", "intelligence"], "trainer_roles": ["noble", "marchand", "officier"], "effects": ["bonus_persuasion"]},
            {"id": "bouclier_arcane", "name": "Bouclier arcane", "category": "magie", "description": "Barriere magique temporaire.", "difficulty": 4, "primary_stats": ["magie", "intelligence"], "trainer_roles": ["mage", "arcaniste"], "effects": ["protection_magique"]},
            {"id": "rage_controlee", "name": "Rage controlee", "category": "combat", "description": "Boost physique sous pression.", "difficulty": 4, "primary_stats": ["force", "sagesse"], "trainer_roles": ["gladiateur", "mercenaire"], "effects": ["bonus_force_temporaire"]},
            {"id": "ombre_furtive", "name": "Ombre furtive", "category": "furtivite", "description": "Infiltration avancee hors combat.", "difficulty": 4, "primary_stats": ["agilite", "chance"], "trainer_roles": ["assassin", "rodeur", "voleur"], "effects": ["furtivite_avancee"]},
            {"id": "nova_runique", "name": "Nova runique", "category": "magie", "description": "Explosion magique de zone.", "difficulty": 5, "primary_stats": ["magie", "intelligence"], "trainer_roles": ["archimage", "mage"], "effects": ["sort_zone_avance"]},
            {"id": "volonte_implacable", "name": "Volonte implacable", "category": "mental", "description": "Resiste mieux aux effets mentaux.", "difficulty": 5, "primary_stats": ["sagesse", "defense"], "trainer_roles": ["templier", "pretre", "maitre"], "effects": ["resistance_mentale"]},
        ]
