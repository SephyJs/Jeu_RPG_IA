from __future__ import annotations

import json
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import model_for


_ROLE_PLACEHOLDERS = {
    "metier/fonction",
    "metier fonction",
    "metier",
    "fonction",
    "role",
    "rôle",
    "profession",
    "job",
    "occupation",
    "n/a",
    "na",
    "inconnu",
    "unknown",
    "...",
}

_MISSING_IDENTITY_VALUES = {
    "",
    "inconnu",
    "unknown",
    "n/a",
    "na",
    "?",
    "...",
}

_GENDER_ALIASES = {
    "homme": {
        "homme",
        "masculin",
        "male",
        "man",
        "boy",
        "garcon",
        "gars",
        "m",
    },
    "femme": {
        "femme",
        "feminin",
        "feminine",
        "female",
        "woman",
        "girl",
        "f",
    },
    "non-binaire": {
        "non binaire",
        "non-binaire",
        "nonbinary",
        "non binary",
        "nb",
        "androgyne",
        "genderfluid",
        "agenre",
    },
}

_GENDER_HINTS = {
    "femme": {
        "pretresse",
        "prêtresse",
        "sorciere",
        "sorcière",
        "reine",
        "duchesse",
        "baronne",
        "madame",
        "dame",
        "servante",
    },
    "homme": {
        "forgeron",
        "pretre",
        "prêtre",
        "roi",
        "duc",
        "baron",
        "monsieur",
        "seigneur",
        "soldat",
    },
}

_NAME_GENDER_HINTS = {
    "femme": {
        "mirelle",
        "lysa",
        "ysra",
        "elyra",
        "aelene",
        "selene",
        "claire",
        "alice",
        "luna",
    },
    "homme": {
        "aldric",
        "dorian",
        "ronan",
        "alain",
        "marc",
        "luc",
        "thomas",
        "arthur",
        "gabin",
    },
}

_SPECIES_ALIASES = {
    "humain": {"humain", "humaine", "human"},
    "elfe": {"elfe", "elf", "high elf", "wood elf"},
    "nain": {"nain", "naine", "dwarf"},
    "fée": {"fee", "fée", "fairy", "fae"},
    "homme-bête": {"homme bete", "homme-bête", "beastman", "beastfolk", "lycan"},
    "drakéide": {"drakeide", "drakéide", "draconique", "draconic", "dragonborn"},
    "dragon": {"dragon", "wyrm"},
    "orc": {"orc", "orque"},
    "gobelin": {"gobelin", "goblin"},
    "démonide": {"demonide", "démonide", "demon", "tieffelin", "tiefling", "infernal"},
}

_SPECIES_DEFAULT_WEIGHTS = [
    ("humain", 38),
    ("elfe", 14),
    ("nain", 12),
    ("homme-bête", 10),
    ("fée", 8),
    ("drakéide", 7),
    ("orc", 5),
    ("gobelin", 3),
    ("démonide", 2),
    ("dragon", 1),
]

_SPECIES_BY_ROLE_HINTS = (
    ({"forgeron", "forge", "mineur"}, [("nain", 45), ("humain", 30), ("elfe", 10), ("orc", 10), ("drakéide", 5)]),
    ({"pretresse", "prêtre", "mage", "arcaniste", "druide"}, [("elfe", 30), ("humain", 30), ("fée", 20), ("drakéide", 10), ("démonide", 10)]),
    ({"garde", "mercenaire", "soldat", "chasseur"}, [("humain", 35), ("homme-bête", 25), ("orc", 15), ("elfe", 15), ("drakéide", 10)]),
    ({"aubergiste", "marchand", "tavernier", "taverne"}, [("humain", 50), ("nain", 15), ("elfe", 15), ("homme-bête", 10), ("gobelin", 10)]),
)


def normalize_npc_key(name: str) -> str:
    raw = unicodedata.normalize("NFKD", (name or "").strip()).encode("ascii", "ignore").decode("ascii")
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return key or "npc"


def npc_profile_key(name: str, location_id: str) -> str:
    return f"{normalize_npc_key(location_id)}__{normalize_npc_key(name)}"


def _normalize_role_text(value: str) -> str:
    plain = unicodedata.normalize("NFKD", (value or "").strip()).encode("ascii", "ignore").decode("ascii")
    plain = re.sub(r"[_-]+", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip().casefold()
    return plain


def resolve_profile_role(profile: dict, fallback_label: str) -> str:
    fallback = str(fallback_label or "").strip() or "PNJ"
    if not isinstance(profile, dict):
        return fallback

    label = str(profile.get("label") or "").strip() or fallback
    role = str(profile.get("role") or "").strip()
    if not role:
        return label

    role_norm = _normalize_role_text(role)
    if not role_norm or role_norm in _ROLE_PLACEHOLDERS:
        return label
    return role


def normalize_profile_role_in_place(profile: dict, fallback_label: str) -> bool:
    if not isinstance(profile, dict):
        return False
    resolved = resolve_profile_role(profile, fallback_label)
    current = str(profile.get("role") or "").strip()
    if resolved == current:
        return False
    profile["role"] = resolved
    return True


def profile_display_name(profile: dict, fallback_label: str) -> str:
    identity = profile.get("identity", {}) if isinstance(profile, dict) else {}
    first = str(identity.get("first_name", "")).strip()
    last = str(identity.get("last_name", "")).strip()
    full_name = " ".join(part for part in (first, last) if part)
    return full_name or fallback_label


def profile_summary_line(profile: dict, fallback_label: str) -> str:
    name = profile_display_name(profile, fallback_label)
    role = resolve_profile_role(profile, fallback_label)
    identity = profile.get("identity", {}) if isinstance(profile, dict) else {}
    species = str(identity.get("species") or "").strip()
    gender = str(identity.get("gender") or "").strip()
    flags = profile.get("dynamic_flags", {}) if isinstance(profile, dict) else {}
    mood = str(flags.get("current_mood") or "neutre").strip()
    identity_bits = [x for x in (species, gender) if x]
    identity_suffix = f" - {', '.join(identity_bits)}" if identity_bits else ""
    return f"{name} ({role}{identity_suffix}) | humeur: {mood}"


class NPCIdentity(BaseModel):
    first_name: str
    last_name: str = ""
    alias: str = ""
    social_class: str = "commun"
    age_apparent: str = "inconnu"
    gender: str = "homme"
    species: str = "humain"
    origin: str = "inconnu"
    reputation: str = "ordinaire"


class NPCSpeechStyle(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    register_style: str = Field(default="neutre", alias="register")
    ton: str = "neutre"
    verbosity: str = "équilibré"
    max_sentences_per_reply: int = 3
    vocabulary: str = "simple"
    pronouns: str = "vouvoiement"


class NPCDynamicFlags(BaseModel):
    is_met: bool = False
    relation_score: int = 0
    is_angry: bool = False
    current_mood: str = "neutre"
    is_hostile: bool = False
    is_bribeable: bool = False
    is_quest_giver: bool = False


class NPCWorldAnchor(BaseModel):
    location_id: str
    location_title: str


class NPCProfile(BaseModel):
    template_version: str = "1.0"
    npc_key: str
    label: str
    role: str
    world_anchor: NPCWorldAnchor
    identity: NPCIdentity
    speech_style: NPCSpeechStyle = Field(default_factory=NPCSpeechStyle)
    char_persona: str
    trait_sombre: str = ""
    first_message: str
    backstory: str = ""
    knowledge_base: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    quest_hooks: list[str] = Field(default_factory=list)
    relations: dict[str, list[str]] = Field(default_factory=lambda: {"allies": [], "enemies": []})
    dynamic_flags: NPCDynamicFlags = Field(default_factory=NPCDynamicFlags)


class NPCProfileManager:
    def __init__(self, llm: Any, *, storage_dir: str = "data/npcs/generated"):
        self.llm = llm
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_profile(
        self,
        cache: dict[str, dict],
        npc_label: str,
        location_id: str,
        location_title: str,
    ) -> dict:
        key = npc_profile_key(npc_label, location_id)
        existing = cache.get(key)
        if isinstance(existing, dict):
            self._normalize_profile_in_place(
                existing,
                fallback_label=npc_label,
                npc_key=key,
                location_id=location_id,
                location_title=location_title,
            )
            return existing

        loaded = self._load_from_disk(
            key,
            fallback_label=npc_label,
            location_id=location_id,
            location_title=location_title,
        )
        if loaded:
            cache[key] = loaded
            return loaded

        legacy_key = normalize_npc_key(npc_label)
        legacy = self._load_from_disk(
            legacy_key,
            fallback_label=npc_label,
            location_id=location_id,
            location_title=location_title,
        )
        if legacy and self._legacy_profile_matches_location(legacy, location_title):
            legacy["npc_key"] = key
            legacy["world_anchor"] = {"location_id": location_id, "location_title": location_title}
            self._normalize_profile_in_place(
                legacy,
                fallback_label=npc_label,
                npc_key=key,
                location_id=location_id,
                location_title=location_title,
            )
            validated = NPCProfile.model_validate(legacy).model_dump(by_alias=True)
            cache[key] = validated
            self._save_to_disk(key, validated)
            return validated

        generated = await self._generate_with_llm(
            npc_label=npc_label,
            npc_key=key,
            location_id=location_id,
            location_title=location_title,
        )
        cache[key] = generated
        self._save_to_disk(key, generated)
        return generated

    def save_profile(self, npc_label: str, profile: dict, location_id: str | None = None) -> None:
        npc_key = str(profile.get("npc_key") or "").strip()
        if not npc_key:
            if location_id is None:
                raise ValueError("location_id est requis pour sauvegarder un profil sans npc_key")
            npc_key = npc_profile_key(npc_label, location_id)
            profile["npc_key"] = npc_key
        world_anchor = profile.get("world_anchor", {}) if isinstance(profile.get("world_anchor"), dict) else {}
        anchor_id = str(world_anchor.get("location_id") or location_id or "").strip()
        anchor_title = str(world_anchor.get("location_title") or "").strip()
        self._normalize_profile_in_place(
            profile,
            fallback_label=npc_label,
            npc_key=npc_key,
            location_id=anchor_id,
            location_title=anchor_title,
        )
        self._save_to_disk(npc_key, profile)

    def save_all_profiles(self, profiles: dict[str, dict]) -> None:
        for key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            npc_key = str(profile.get("npc_key") or key).strip()
            if not npc_key:
                continue
            profile["npc_key"] = npc_key
            fallback_label = str(profile.get("label") or "").strip()
            if not fallback_label:
                fallback_label = npc_key.split("__")[-1].replace("_", " ").strip() or "PNJ"
            world_anchor = profile.get("world_anchor", {}) if isinstance(profile.get("world_anchor"), dict) else {}
            self._normalize_profile_in_place(
                profile,
                fallback_label=fallback_label,
                npc_key=npc_key,
                location_id=str(world_anchor.get("location_id") or "").strip(),
                location_title=str(world_anchor.get("location_title") or "").strip(),
            )
            self._save_to_disk(npc_key, profile)

    def load_profile_by_key(
        self,
        npc_key: str,
        *,
        fallback_label: str = "",
        location_id: str = "",
        location_title: str = "",
    ) -> dict | None:
        key = str(npc_key or "").strip()
        if not key:
            return None
        return self._load_from_disk(
            key,
            fallback_label=fallback_label,
            location_id=location_id,
            location_title=location_title,
        )

    def load_all_profiles(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for path in self.storage_dir.glob("*.json"):
            if not path.is_file():
                continue
            key = str(path.stem or "").strip()
            if not key:
                continue
            profile = self._load_from_disk(key)
            if isinstance(profile, dict):
                out[key] = profile
        return out

    def _path_for(self, npc_key: str) -> Path:
        return self.storage_dir / f"{npc_key}.json"

    def _load_from_disk(
        self,
        npc_key: str,
        *,
        fallback_label: str = "",
        location_id: str = "",
        location_title: str = "",
    ) -> dict | None:
        path = self._path_for(npc_key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("npc_key", npc_key)
                if "world_anchor" not in raw:
                    identity = raw.get("identity", {}) if isinstance(raw.get("identity"), dict) else {}
                    raw["world_anchor"] = {
                        "location_id": "",
                        "location_title": str(identity.get("origin") or ""),
                    }
                label = str(raw.get("label") or "").strip()
                if not label:
                    label = fallback_label.strip() or str(raw.get("npc_key") or npc_key).split("__")[-1].replace("_", " ")
                world_anchor = raw.get("world_anchor", {}) if isinstance(raw.get("world_anchor"), dict) else {}
                anchor_id = str(world_anchor.get("location_id") or location_id or "").strip()
                anchor_title = str(world_anchor.get("location_title") or location_title or "").strip()
                self._normalize_profile_in_place(
                    raw,
                    fallback_label=label,
                    npc_key=str(raw.get("npc_key") or npc_key),
                    location_id=anchor_id,
                    location_title=anchor_title,
                )
            return NPCProfile.model_validate(raw).model_dump(by_alias=True)
        except Exception:
            return None

    def _save_to_disk(self, npc_key: str, profile: dict) -> None:
        path = self._path_for(npc_key)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _generate_with_llm(
        self,
        *,
        npc_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> dict:
        prompt = self._prompt_npc_profile_json(
            npc_label=npc_label,
            npc_key=npc_key,
            location_id=location_id,
            location_title=location_title,
        )
        raw = await self.llm.generate(
            model=model_for("rules"),
            prompt=prompt,
            temperature=0.3,
            num_ctx=4096,
            num_predict=700,
            stop=None,
        )
        json_str = self._extract_json(raw)

        try:
            candidate = json.loads(json_str)
            profile = self._coerce_profile(
                candidate,
                npc_label=npc_label,
                npc_key=npc_key,
                location_id=location_id,
                location_title=location_title,
            )
            return NPCProfile.model_validate(profile).model_dump(by_alias=True)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return self._fallback_profile(
                npc_label=npc_label,
                npc_key=npc_key,
                location_id=location_id,
                location_title=location_title,
            ).model_dump(by_alias=True)

    def _coerce_profile(
        self,
        raw: Any,
        *,
        npc_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> dict:
        if isinstance(raw, dict) and isinstance(raw.get("zone_pnj"), list) and raw["zone_pnj"]:
            z = raw["zone_pnj"][0] if isinstance(raw["zone_pnj"][0], dict) else {}
            name = str(z.get("name") or npc_label).strip()
            first, last = self._split_name(name)
            style = z.get("dialogue_style", {}) if isinstance(z.get("dialogue_style"), dict) else {}
            flags = z.get("flags", {}) if isinstance(z.get("flags"), dict) else {}
            relations = z.get("relations", {}) if isinstance(z.get("relations"), dict) else {}
            candidate = {
                "npc_key": npc_key,
                "label": npc_label,
                "role": resolve_profile_role(
                    {"label": npc_label, "role": str(z.get("role") or "")},
                    npc_label,
                ),
                "world_anchor": {"location_id": location_id, "location_title": location_title},
                "identity": {
                    "first_name": first,
                    "last_name": last,
                    "alias": str(z.get("alias") or ""),
                    "social_class": "commun",
                    "age_apparent": "adulte",
                    "gender": str(z.get("gender") or z.get("genre") or z.get("sexe") or ""),
                    "species": str(z.get("species") or z.get("race") or z.get("espece") or z.get("espèce") or ""),
                    "origin": location_title,
                    "reputation": "locale",
                },
                "speech_style": {
                    "register": str(style.get("register") or "neutre"),
                    "ton": str(style.get("ton") or "neutre"),
                    "verbosity": "équilibré",
                    "max_sentences_per_reply": 3,
                    "vocabulary": "simple",
                    "pronouns": "vouvoiement",
                },
                "char_persona": str(z.get("persona") or f"{npc_label} est attentif et prudent."),
                "trait_sombre": str(z.get("trait_sombre") or ""),
                "first_message": f"{first} vous observe puis incline la tête.",
                "backstory": "",
                "knowledge_base": [],
                "goals": [],
                "secrets": [],
                "quest_hooks": z.get("quest_hooks") if isinstance(z.get("quest_hooks"), list) else [],
                "relations": {
                    "allies": relations.get("alliés") if isinstance(relations.get("alliés"), list) else [],
                    "enemies": relations.get("ennemis") if isinstance(relations.get("ennemis"), list) else [],
                },
                "dynamic_flags": {
                    "is_met": False,
                    "relation_score": 0,
                    "is_angry": False,
                    "current_mood": "neutre",
                    "is_hostile": bool(flags.get("is_hostile", False)),
                    "is_bribeable": bool(flags.get("is_bribeable", False)),
                    "is_quest_giver": bool(flags.get("is_quest_giver", False)),
                },
            }
            self._normalize_profile_in_place(
                candidate,
                fallback_label=npc_label,
                npc_key=npc_key,
                location_id=location_id,
                location_title=location_title,
            )
            return candidate

        if isinstance(raw, dict):
            candidate = dict(raw)
            candidate.setdefault("npc_key", npc_key)
            candidate.setdefault("label", npc_label)
            candidate.setdefault("role", npc_label)
            candidate.setdefault("world_anchor", {"location_id": location_id, "location_title": location_title})
            self._normalize_profile_in_place(
                candidate,
                fallback_label=npc_label,
                npc_key=npc_key,
                location_id=location_id,
                location_title=location_title,
            )
            return candidate

        raise ValueError("Format de profil PNJ non reconnu")

    def _fallback_profile(
        self,
        *,
        npc_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> NPCProfile:
        first_names = {
            "homme": ["Aldric", "Dorian", "Ronan", "Garen", "Alain", "Theron"],
            "femme": ["Mirelle", "Ysra", "Lysa", "Aelene", "Naerys", "Selene"],
            "non-binaire": ["Seren", "Vael", "Nyx", "Kael", "Eris", "Soren"],
        }
        last_names = ["Vael", "Morn", "Dusken", "Ardent", "Korr", "Ilven", "Rive"]
        rng = random.Random(f"{npc_key}|{location_id}")
        gender = self._default_gender(
            rng,
            role_hint=npc_label,
            first_name="",
            char_persona="",
        )
        species = self._default_species(
            rng,
            role_hint=npc_label,
            char_persona="",
        )
        first_pool = first_names.get(gender, first_names["non-binaire"])
        first = first_pool[rng.randrange(len(first_pool))]
        last = last_names[rng.randrange(len(last_names))]
        return NPCProfile(
            npc_key=npc_key,
            label=npc_label,
            role=npc_label,
            world_anchor=NPCWorldAnchor(location_id=location_id, location_title=location_title),
            identity=NPCIdentity(
                first_name=first,
                last_name=last,
                social_class="commun",
                age_apparent="adulte",
                gender=gender,
                species=species,
                origin=location_title,
                reputation="locale",
            ),
            speech_style=NPCSpeechStyle(register="neutre", ton="mesuré", pronouns="vouvoiement"),
            char_persona=f"{npc_label} est un(e) {species} prudent(e), observateur(trice) et professionnel(le).",
            trait_sombre="Cache un arrangement dont il préfère ne pas parler.",
            first_message=f"{first} {last} vous jauge, puis accepte la discussion.",
            backstory=f"Installé à {location_title}, {npc_label} a appris à survivre entre rumeurs et dettes.",
            knowledge_base=[f"Connaît bien {location_title}."],
            goals=["Protéger ses intérêts", "Éviter les ennuis"],
            secrets=["Garde une information compromettante sur un notable local."],
            quest_hooks=["Peut orienter le joueur vers une piste en échange d'un service."],
            relations={"allies": [], "enemies": []},
            dynamic_flags=NPCDynamicFlags(current_mood="neutre"),
        )

    def _prompt_npc_profile_json(
        self,
        *,
        npc_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> str:
        schema = {
            "template_version": "1.0",
            "npc_key": npc_key,
            "label": npc_label,
            "role": npc_label,
            "world_anchor": {"location_id": location_id, "location_title": location_title},
            "identity": {
                "first_name": "Prénom",
                "last_name": "Nom",
                "alias": "",
                "social_class": "commun|noble|marginal|...",
                "age_apparent": "adulte",
                "gender": "homme|femme|non-binaire",
                "species": "humain|elfe|nain|fée|homme-bête|drakéide|dragon|orc|gobelin|démonide|...",
                "origin": location_title,
                "reputation": "réputation locale",
            },
            "speech_style": {
                "register": "familier|neutre|soutenu",
                "ton": "jovial|froid|mystérieux|agressif|prudent",
                "verbosity": "faible|équilibré|élevé",
                "max_sentences_per_reply": 3,
                "vocabulary": "style de vocabulaire",
                "pronouns": "vouvoiement|tutoiement",
            },
            "char_persona": "Description psychologique concise",
            "trait_sombre": "Secret/aspect sombre",
            "first_message": "Première réplique en situation de rencontre",
            "backstory": "Passé du PNJ en 2-3 phrases",
            "knowledge_base": ["connaissance_1"],
            "goals": ["objectif_1"],
            "secrets": ["secret_1"],
            "quest_hooks": ["hook_1"],
            "relations": {"allies": ["Nom"], "enemies": ["Nom"]},
            "dynamic_flags": {
                "is_met": False,
                "relation_score": 0,
                "is_angry": False,
                "current_mood": "neutre",
                "is_hostile": False,
                "is_bribeable": False,
                "is_quest_giver": False,
            },
        }
        return (
            "Tu es un générateur de fiches PNJ dark-fantasy.\n"
            "Réponds en JSON valide UNIQUEMENT, sans markdown ni commentaire.\n"
            "Base-toi sur ces inspirations de templates: identité, style de parole, persona, secret, relations, hooks.\n"
            f"Contexte: lieu_id={location_id}, lieu={location_title}, rôle affiché={npc_label}, clé={npc_key}.\n"
            "Le champ role doit être un vrai métier/fonction (ex: Aubergiste), jamais un placeholder.\n"
            "Le champ identity.gender doit toujours être explicite (pas 'inconnu').\n"
            "Le champ identity.species doit refléter un style fantasy cohérent avec le PNJ.\n"
            "Varie les espèces fantasy d'un PNJ à l'autre, évite de répondre toujours 'humain'.\n"
            "Contraintes: garder un ton cohérent jeu médiéval sombre, pas de contenu méta.\n"
            "Schéma attendu:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _normalize_profile_in_place(
        self,
        profile: dict,
        *,
        fallback_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> None:
        if not isinstance(profile, dict):
            return

        normalize_profile_role_in_place(profile, fallback_label)

        world_anchor = profile.get("world_anchor", {}) if isinstance(profile.get("world_anchor"), dict) else {}
        identity = profile.get("identity", {}) if isinstance(profile.get("identity"), dict) else {}
        origin = str(identity.get("origin") or "").strip()

        anchor_id = str(world_anchor.get("location_id") or location_id or "").strip()
        anchor_title = str(world_anchor.get("location_title") or location_title or origin).strip()
        world_anchor["location_id"] = anchor_id
        world_anchor["location_title"] = anchor_title
        profile["world_anchor"] = world_anchor

        self._normalize_identity_in_place(
            profile,
            fallback_label=fallback_label,
            npc_key=npc_key,
            location_id=anchor_id,
            location_title=anchor_title,
        )

    def _normalize_identity_in_place(
        self,
        profile: dict,
        *,
        fallback_label: str,
        npc_key: str,
        location_id: str,
        location_title: str,
    ) -> None:
        identity = profile.get("identity", {}) if isinstance(profile.get("identity"), dict) else {}
        role_hint = str(profile.get("role") or fallback_label or "").strip()
        char_persona = str(profile.get("char_persona") or "").strip()
        speech_style = profile.get("speech_style", {}) if isinstance(profile.get("speech_style"), dict) else {}

        first_name = str(identity.get("first_name") or "").strip()
        last_name = str(identity.get("last_name") or "").strip()
        if not first_name:
            inferred_first, inferred_last = self._split_name(str(profile.get("label") or fallback_label or "Inconnu"))
            first_name = inferred_first
            if not last_name:
                last_name = inferred_last
        identity["first_name"] = first_name
        identity["last_name"] = last_name

        if not str(identity.get("origin") or "").strip():
            identity["origin"] = location_title or "inconnu"

        rng = random.Random(f"{npc_key}|{location_id}|{role_hint}|{first_name}|{last_name}")

        gender = self._canonical_gender(identity.get("gender"))
        if not gender:
            gender = self._infer_gender_from_context(
                role_hint=role_hint,
                first_name=first_name,
                char_persona=char_persona,
                speech_style=speech_style,
            )
        if not gender:
            gender = self._default_gender(
                rng,
                role_hint=role_hint,
                first_name=first_name,
                char_persona=char_persona,
            )
        identity["gender"] = gender

        raw_species = identity.get("species")
        if raw_species is None or not str(raw_species).strip():
            for alt in ("race", "espece", "espèce", "lineage", "heritage", "folk", "folk_style", "style"):
                alt_value = identity.get(alt)
                if isinstance(alt_value, str) and alt_value.strip():
                    raw_species = alt_value
                    break
        species = self._canonical_species(raw_species)
        if not species:
            species = self._default_species(
                rng,
                role_hint=role_hint,
                char_persona=char_persona,
            )
        identity["species"] = species
        profile["identity"] = identity

    def _canonical_gender(self, value: object) -> str | None:
        raw = str(value or "").strip()
        norm = _normalize_role_text(raw)
        if not norm or norm in _MISSING_IDENTITY_VALUES:
            return None

        for canonical, aliases in _GENDER_ALIASES.items():
            for alias in aliases:
                if norm == _normalize_role_text(alias):
                    return canonical

        if "non binaire" in norm or "nonbinary" in norm:
            return "non-binaire"
        if norm.startswith("fem"):
            return "femme"
        if norm.startswith("masc"):
            return "homme"
        return None

    def _infer_gender_from_context(
        self,
        *,
        role_hint: str,
        first_name: str,
        char_persona: str,
        speech_style: dict,
    ) -> str | None:
        role_norm = _normalize_role_text(role_hint)
        for canonical, hints in _GENDER_HINTS.items():
            for hint in hints:
                if _normalize_role_text(hint) and _normalize_role_text(hint) in role_norm:
                    return canonical

        first_norm = _normalize_role_text(first_name)
        for canonical, names in _NAME_GENDER_HINTS.items():
            if first_norm in {_normalize_role_text(x) for x in names}:
                return canonical

        persona_norm = _normalize_role_text(char_persona)
        if " elle " in f" {persona_norm} " and " il " not in f" {persona_norm} ":
            return "femme"
        if " il " in f" {persona_norm} " and " elle " not in f" {persona_norm} ":
            return "homme"

        pronouns = _normalize_role_text(str(speech_style.get("pronouns") or ""))
        if pronouns in {"elle", "her", "she"}:
            return "femme"
        if pronouns in {"il", "him", "he"}:
            return "homme"
        return None

    def _default_gender(self, rng: random.Random, *, role_hint: str, first_name: str, char_persona: str) -> str:
        inferred = self._infer_gender_from_context(
            role_hint=role_hint,
            first_name=first_name,
            char_persona=char_persona,
            speech_style={},
        )
        if inferred:
            return inferred
        return self._weighted_choice(rng, [("homme", 46), ("femme", 46), ("non-binaire", 8)])

    def _canonical_species(self, value: object) -> str | None:
        raw = str(value or "").strip()
        norm = _normalize_role_text(raw)
        if not norm or norm in _MISSING_IDENTITY_VALUES:
            return None

        for canonical, aliases in _SPECIES_ALIASES.items():
            for alias in aliases:
                if norm == _normalize_role_text(alias):
                    return canonical

        if "dragon" in norm:
            return "dragon"
        if "elf" in norm:
            return "elfe"
        if "dwarf" in norm or "nain" in norm:
            return "nain"
        if "orc" in norm:
            return "orc"
        if "gob" in norm:
            return "gobelin"
        if "fee" in norm or "fey" in norm:
            return "fée"
        return raw[:40]

    def _default_species(self, rng: random.Random, *, role_hint: str, char_persona: str) -> str:
        role_norm = _normalize_role_text(f"{role_hint} {char_persona}")
        for keywords, weights in _SPECIES_BY_ROLE_HINTS:
            if any(_normalize_role_text(k) in role_norm for k in keywords):
                return self._weighted_choice(rng, weights)
        return self._weighted_choice(rng, _SPECIES_DEFAULT_WEIGHTS)

    def _weighted_choice(self, rng: random.Random, weighted_values: list[tuple[str, int]]) -> str:
        total = 0
        sanitized: list[tuple[str, int]] = []
        for value, weight in weighted_values:
            w = max(0, int(weight))
            if not value or w <= 0:
                continue
            sanitized.append((value, w))
            total += w
        if not sanitized:
            return "humain"
        pick = rng.randint(1, total)
        cumulative = 0
        for value, weight in sanitized:
            cumulative += weight
            if pick <= cumulative:
                return value
        return sanitized[-1][0]

    def _legacy_profile_matches_location(self, profile: dict, location_title: str) -> bool:
        world_anchor = profile.get("world_anchor", {})
        if isinstance(world_anchor, dict):
            if str(world_anchor.get("location_title", "")).strip() == location_title:
                return True

        identity = profile.get("identity", {})
        if isinstance(identity, dict):
            return str(identity.get("origin", "")).strip() == location_title
        return False

    def _split_name(self, value: str) -> tuple[str, str]:
        cleaned = re.sub(r"\s+", " ", (value or "").strip())
        if not cleaned:
            return "Inconnu", ""
        parts = cleaned.split(" ")
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"
