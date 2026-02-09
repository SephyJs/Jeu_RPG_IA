from __future__ import annotations

from collections import deque
import json
import random
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.ui.state.game_state import Choice, Scene
from .models import model_for
from .world_time import format_hour_label, minute_of_day


MAP_ANCHORS = [
    "Valedor",
    "Forêt Murmurante",
    "Brumefeu",
    "Bois Sépulcral",
    "Ruines de Lethar",
    "Lumeria",
    "Sylve d'Ancaria",
    "Sylvaën",
    "Dun'Khar",
    "Pics de Khar",
    "Temple Ensablé",
    "Temple de Cendre",
    "Ile d'Astra'Nyx",
]

ANCHOR_NEIGHBORS = {
    "Lumeria": ["Forêt Murmurante", "Sylve d'Ancaria", "Sylvaën", "Dun'Khar", "Ruines de Lethar"],
    "Forêt Murmurante": ["Valedor", "Brumefeu", "Lumeria", "Ruines de Lethar"],
    "Brumefeu": ["Forêt Murmurante", "Bois Sépulcral"],
    "Bois Sépulcral": ["Brumefeu", "Sylve d'Ancaria"],
    "Ruines de Lethar": ["Forêt Murmurante", "Lumeria", "Temple Ensablé"],
    "Sylve d'Ancaria": ["Bois Sépulcral", "Lumeria", "Sylvaën"],
    "Sylvaën": ["Sylve d'Ancaria", "Dun'Khar", "Pics de Khar"],
    "Dun'Khar": ["Lumeria", "Sylvaën", "Temple Ensablé", "Pics de Khar"],
    "Temple Ensablé": ["Ruines de Lethar", "Dun'Khar", "Temple de Cendre"],
    "Temple de Cendre": ["Temple Ensablé", "Dun'Khar"],
    "Pics de Khar": ["Dun'Khar", "Sylvaën", "Ile d'Astra'Nyx"],
    "Ile d'Astra'Nyx": ["Pics de Khar"],
    "Valedor": ["Forêt Murmurante"],
}

_URBAN_ANCHORS = {
    "Lumeria",
    "Valedor",
    "Brumefeu",
    "Sylvaën",
    "Dun'Khar",
}

_CITY_LAYOUT_PRESETS: dict[str, dict[str, object]] = {
    "Lumeria": {
        "center_scene_id": "village_center_01",
        # Effet ruelles: tout n'est pas joignable directement depuis la place.
        "edges": [
            ("village_center_01", "taverne_01"),
            ("village_center_01", "forge_01"),
            ("taverne_01", "boutique_01"),
            ("forge_01", "infirmerie_01"),
            ("infirmerie_01", "temple_01"),
            ("temple_01", "prison_01"),
        ],
    }
}

_CITY_DISTRICT_TEMPLATES: list[dict[str, object]] = [
    {
        "title": "Ruelle des Lanternes",
        "narrator": "Ataryxia : Des lanternes de cuivre balancent au-dessus d'une rue étroite, entre ombre et rumeurs.",
        "npcs": ["Passant pressé", "Rôdeur de quartier"],
    },
    {
        "title": "Auberge du Carrefour",
        "narrator": "Ataryxia : Une auberge serrée entre deux murs de pierre, où le bois humide craque sous les bottes.",
        "npcs": ["Aubergiste", "Serveur"],
    },
    {
        "title": "Forge de Quartier",
        "narrator": "Ataryxia : Le fer rouge pulse dans la nuit comme un coeur battant sous la pluie.",
        "npcs": ["Forgeron", "Apprenti forgeron"],
    },
    {
        "title": "Marché Couvert",
        "narrator": "Ataryxia : Sous des toiles sombres, les étals bruissent de marchandages et de promesses douteuses.",
        "npcs": ["Marchand", "Cliente encapuchonnée"],
    },
    {
        "title": "Sanctuaire de Rue",
        "narrator": "Ataryxia : Une chapelle discrète, noyée d'encens froid, protège les âmes fatiguées.",
        "npcs": ["Acolyte", "Pèlerin silencieux"],
    },
    {
        "title": "Cour des Artisans",
        "narrator": "Ataryxia : Des ateliers ouvrent sur une cour pavée, frappée de marteaux et de poussière claire.",
        "npcs": ["Artisane", "Livreur"],
    },
]

_BUILDING_TITLE_TOKENS = (
    "taverne",
    "auberge",
    "forge",
    "boutique",
    "temple",
    "prison",
    "infirmerie",
    "sanctuaire",
    "atelier",
    "maison",
    "caserne",
    "bibliotheque",
    "bibliothèque",
    "comptoir",
    "salle",
    "marche couvert",
    "marché couvert",
)

_STREET_TITLE_TOKENS = (
    "ruelle",
    "rue",
    "allee",
    "allée",
    "carrefour",
    "place",
    "quartier",
    "porte",
    "sentier",
    "chemin",
    "avenue",
    "village",
)

_ROAMING_STREET_NPCS = [
    "Marchand ambulant",
    "Colporteuse",
    "Messager essoufflé",
    "Mendiant",
    "Passante pressée",
    "Vieil ouvrier",
    "Garde en ronde",
    "Barde itinérant",
    "Enfant des rues",
    "Artisane de passage",
    "Chasseur urbain",
    "SDF du quartier",
]

_SCENE_HOURS_BY_ID: dict[str, tuple[int, int, str]] = {
    "boutique_01": (8, 18, "La boutique"),
    "forge_01": (7, 19, "La forge"),
    "taverne_01": (6, 2, "La taverne"),
    "temple_01": (6, 22, "Le temple"),
    "infirmerie_01": (0, 0, "L'infirmerie"),
    "prison_01": (0, 0, "La prison"),
}

_SCENE_HOURS_BY_TITLE: list[tuple[tuple[str, ...], tuple[int, int, str]]] = [
    (("boutique", "marchand", "comptoir", "marche couvert", "marché couvert"), (8, 18, "Le commerce")),
    (("forge", "atelier"), (7, 19, "La forge")),
    (("auberge", "taverne"), (6, 2, "L'auberge")),
    (("temple", "sanctuaire"), (6, 22, "Le sanctuaire")),
    (("infirmerie",), (0, 0, "L'infirmerie")),
    (("prison", "caserne"), (0, 0, "Le poste de garde")),
]


def _norm_anchor_token(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")


_ANCHOR_BY_NORM = {_norm_anchor_token(anchor): anchor for anchor in MAP_ANCHORS}


def canonical_anchor(anchor: str, *, default: str = "Lumeria") -> str:
    return _ANCHOR_BY_NORM.get(_norm_anchor_token(anchor), default)


def official_neighbors(anchor: str) -> list[str]:
    current = canonical_anchor(anchor)
    neighbors: set[str] = set(ANCHOR_NEIGHBORS.get(current, []))

    # Tolère les graphes non strictement symétriques.
    for source, targets in ANCHOR_NEIGHBORS.items():
        if current in targets:
            neighbors.add(source)

    ordered: list[str] = []
    for candidate in MAP_ANCHORS:
        if candidate in neighbors:
            ordered.append(candidate)
    return ordered


def official_shortest_path(start_anchor: str, end_anchor: str) -> list[str]:
    start = canonical_anchor(start_anchor)
    goal = canonical_anchor(end_anchor)
    if start == goal:
        return [start]

    queue: deque[list[str]] = deque([[start]])
    visited = {start}

    while queue:
        path = queue.popleft()
        node = path[-1]
        for nxt in official_neighbors(node):
            if nxt in visited:
                continue
            next_path = [*path, nxt]
            if nxt == goal:
                return next_path
            visited.add(nxt)
            queue.append(next_path)

    return [start, goal]


def _norm_text_token(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", raw.lower()).strip()


def is_building_scene_title(title: str) -> bool:
    norm = _norm_text_token(title)
    return any(token in norm for token in _BUILDING_TITLE_TOKENS)


def is_street_scene(scene: Scene) -> bool:
    if not isinstance(scene, Scene):
        return False
    title_norm = _norm_text_token(scene.title)
    if is_building_scene_title(scene.title):
        return False
    return any(token in title_norm for token in _STREET_TITLE_TOKENS)


def refresh_roaming_street_npcs(
    scene: Scene,
    *,
    max_total: int = 5,
    roaming_candidates: list[str] | None = None,
) -> bool:
    if not is_street_scene(scene):
        return False

    pool: list[str] = []
    if isinstance(roaming_candidates, list):
        for row in roaming_candidates:
            name = str(row or "").strip()
            if name and name not in pool:
                pool.append(name)
    for name in _ROAMING_STREET_NPCS:
        if name not in pool:
            pool.append(name)

    if not pool:
        return False

    pool_set = set(pool)
    fixed = [name for name in scene.npc_names if name not in _ROAMING_STREET_NPCS and name not in pool_set]
    room = max(0, max_total - len(fixed))
    if room <= 0:
        return False

    extra_count = random.randint(0, min(room, 2))
    extras = random.sample(pool, k=min(extra_count, len(pool))) if extra_count > 0 else []

    merged: list[str] = []
    for name in [*fixed, *extras]:
        clean = str(name or "").strip()
        if not clean or clean in merged:
            continue
        merged.append(clean)

    merged = merged[:max_total]
    if merged == scene.npc_names:
        return False
    scene.npc_names = merged
    return True


def scene_opening_window(scene: Scene) -> tuple[int, int, str] | None:
    if not isinstance(scene, Scene):
        return None
    if not is_building_scene_title(scene.title):
        return None

    scene_id = str(scene.id or "").strip()
    if scene_id in _SCENE_HOURS_BY_ID:
        return _SCENE_HOURS_BY_ID[scene_id]

    title_norm = _norm_text_token(scene.title)
    for tokens, window in _SCENE_HOURS_BY_TITLE:
        if any(token in title_norm for token in tokens):
            return window
    return None


def _is_open_now(*, open_hour: int, close_hour: int, world_time_minutes: int) -> bool:
    start = (int(open_hour) % 24) * 60
    end = (int(close_hour) % 24) * 60
    now = minute_of_day(world_time_minutes)

    # Meme heure de debut/fin => ouvert en continu.
    if start == end:
        return True
    if start < end:
        return start <= now < end
    # Fenetre de nuit (ex: 18h -> 02h).
    return now >= start or now < end


def scene_open_status(scene: Scene, world_time_minutes: int) -> tuple[bool, str]:
    window = scene_opening_window(scene)
    if window is None:
        return True, ""

    open_hour, close_hour, label = window
    status = _is_open_now(
        open_hour=open_hour,
        close_hour=close_hour,
        world_time_minutes=world_time_minutes,
    )

    if int(open_hour) % 24 == int(close_hour) % 24:
        schedule = "ouvert en permanence"
    else:
        schedule = f"ouvert de {format_hour_label(open_hour)} a {format_hour_label(close_hour)}"

    if status:
        return True, f"{label} est {schedule}."
    return False, f"{label} est ferme ({schedule})."


class LocationDraft(BaseModel):
    title: str
    narrator_text: str
    npcs: list[str] = Field(default_factory=list)
    travel_label_from_current: str = ""


class LocationManager:
    def __init__(self, llm: Any):
        self.llm = llm

    def is_city_anchor(self, anchor: str) -> bool:
        return canonical_anchor(anchor) in _URBAN_ANCHORS

    def seed_static_anchors(self, scenes: dict[str, Scene]) -> None:
        static_map = {
            "village_center_01": "Lumeria",
            "taverne_01": "Lumeria",
            "forge_01": "Lumeria",
            "boutique_01": "Lumeria",
            "infirmerie_01": "Lumeria",
            "temple_01": "Lumeria",
            "prison_01": "Lumeria",
        }
        for scene_id, anchor in static_map.items():
            scene = scenes.get(scene_id)
            if scene and not scene.map_anchor:
                scene.map_anchor = anchor
        self.apply_city_street_layouts(scenes)

    def apply_city_street_layouts(self, scenes: dict[str, Scene]) -> None:
        anchors = sorted(
            {
                scene.map_anchor
                for scene in scenes.values()
                if scene.map_anchor in MAP_ANCHORS and self.is_city_anchor(scene.map_anchor)
            }
        )
        for anchor in anchors:
            self.apply_city_street_layout(scenes, anchor)

    def apply_city_street_layout(
        self,
        scenes: dict[str, Scene],
        anchor: str,
        *,
        prefer_center_scene_id: str | None = None,
    ) -> None:
        anchor_name = canonical_anchor(anchor)
        if not self.is_city_anchor(anchor_name) and anchor_name not in _CITY_LAYOUT_PRESETS:
            return
        local_ids = [sid for sid, scene in scenes.items() if scene.map_anchor == anchor_name]
        if len(local_ids) < 2:
            return

        local_set = set(local_ids)
        external_choices: dict[str, list[Choice]] = {}
        for sid in local_ids:
            scene = scenes[sid]
            kept: list[Choice] = []
            for choice in scene.choices:
                nxt = choice.next_scene_id
                if not nxt or nxt not in local_set:
                    kept.append(choice)
            external_choices[sid] = self._dedupe_choices(kept)

        center_id = self._pick_city_center_scene_id(
            scenes,
            local_ids,
            prefer_center_scene_id=prefer_center_scene_id,
        )
        edges = self._build_local_edges(
            scenes,
            anchor_name,
            local_ids,
            center_id=center_id,
        )

        adjacency: dict[str, set[str]] = {sid: set() for sid in local_ids}
        for a, b in edges:
            if a not in local_set or b not in local_set or a == b:
                continue
            adjacency[a].add(b)
            adjacency[b].add(a)

        for sid in local_ids:
            scene = scenes[sid]
            merged = list(external_choices.get(sid, []))
            for target_id in sorted(adjacency.get(sid, set()), key=lambda t: scenes[t].title.casefold()):
                merged.append(
                    Choice(
                        id=f"street_{target_id}",
                        label=self._street_label(
                            source=scene,
                            target=scenes[target_id],
                            center_scene_id=center_id,
                        ),
                        next_scene_id=target_id,
                    )
                )
            scene.choices = self._dedupe_choices(merged)

    def generate_city_map_for_new_anchor(
        self,
        *,
        anchor: str,
        center_scene: Scene,
        existing_scenes: dict[str, Scene],
    ) -> list[Scene]:
        anchor_name = canonical_anchor(anchor)
        if not self.is_city_anchor(anchor_name):
            return []

        rng = random.Random(f"city_layout::{anchor_name}")
        templates = list(_CITY_DISTRICT_TEMPLATES)
        rng.shuffle(templates)

        existing_ids = set(existing_scenes.keys())
        existing_titles = {scene.title for scene in existing_scenes.values()}

        # Ville inconnue: on crée plusieurs points de rue dès l'arrivée.
        district_count = 4
        new_scenes: list[Scene] = []
        for template in templates[:district_count]:
            title = self._unique_title(f"{anchor_name} - {str(template['title'])}", existing_titles)
            scene_id = self._unique_scene_id(anchor_name, title, existing_ids)
            existing_ids.add(scene_id)
            existing_titles.add(title)

            narrator = str(template.get("narrator") or "").strip()
            fallback_narrator = "Une rue nouvelle s'ouvre sous vos pas."
            if not narrator.startswith("Ataryxia"):
                narrator = f"Ataryxia : {narrator or fallback_narrator}"
            npcs_raw = template.get("npcs")
            npc_names = [str(n).strip() for n in npcs_raw if isinstance(n, str) and str(n).strip()] if isinstance(npcs_raw, list) else []

            new_scenes.append(
                Scene(
                    id=scene_id,
                    title=title,
                    narrator_text=narrator,
                    map_anchor=anchor_name,
                    generated=True,
                    npc_names=npc_names[:4],
                    choices=[],
                )
            )
        return new_scenes


    async def generate_next_scene(self, current_scene: Scene, existing_scenes: dict[str, Scene]) -> tuple[Scene, str]:
        current_anchor = canonical_anchor(self._infer_anchor(current_scene))
        neighbors = official_neighbors(current_anchor)
        target_anchor = self._pick_target_anchor(current_anchor, neighbors, existing_scenes)
        existing_titles = [s.title for s in existing_scenes.values()]
        existing_ids = set(existing_scenes.keys())

        prompt = self._build_prompt(
            current_scene=current_scene,
            current_anchor=current_anchor,
            target_anchor=target_anchor,
            existing_titles=existing_titles,
        )

        raw = await self.llm.generate(
            model=model_for("rules"),
            prompt=prompt,
            temperature=0.35,
            num_ctx=4096,
            num_predict=700,
            stop=None,
        )

        draft = self._parse_draft(raw, target_anchor=target_anchor)
        location_id = self._unique_scene_id(target_anchor, draft.title, existing_ids)
        title = self._unique_title(draft.title, set(existing_titles))
        narration = draft.narrator_text.strip() or f"Ataryxia : Le vent tourne à {title}, et la route se fait plus lourde."
        npcs = [n.strip() for n in draft.npcs if isinstance(n, str) and n.strip()][:4]

        scene = Scene(
            id=location_id,
            title=title,
            narrator_text=narration,
            map_anchor=target_anchor,
            generated=True,
            npc_names=npcs,
            choices=[],
        )

        travel_label = draft.travel_label_from_current.strip() or f"Prendre la route vers {title}"
        return scene, travel_label

    def _dedupe_choices(self, choices: list[Choice]) -> list[Choice]:
        out: list[Choice] = []
        seen_targets: set[str] = set()
        seen_labels: set[str] = set()
        for choice in choices:
            if not isinstance(choice, Choice):
                continue
            label = str(choice.label or "").strip()
            target = str(choice.next_scene_id or "").strip()
            if target:
                if target in seen_targets:
                    continue
                seen_targets.add(target)
            if not target:
                key = label.casefold()
                if key in seen_labels:
                    continue
                seen_labels.add(key)
            out.append(choice)
        return out

    def _pick_city_center_scene_id(
        self,
        scenes: dict[str, Scene],
        local_ids: list[str],
        *,
        prefer_center_scene_id: str | None,
    ) -> str:
        if prefer_center_scene_id and prefer_center_scene_id in local_ids:
            return prefer_center_scene_id

        sample_scene = scenes[local_ids[0]]
        anchor = canonical_anchor(sample_scene.map_anchor)
        preset = _CITY_LAYOUT_PRESETS.get(anchor)
        preset_center = str((preset or {}).get("center_scene_id") or "").strip()
        if preset_center and preset_center in local_ids:
            return preset_center

        center_tokens = ("centre", "center", "place", "plaza", "carrefour", "coeur", "cœur")
        scored: list[tuple[int, str]] = []
        for sid in local_ids:
            scene = scenes[sid]
            title_norm = self._norm(scene.title)
            sid_norm = self._norm(sid)
            score = 0
            if not scene.generated:
                score += 2
            if any(tok in title_norm or tok in sid_norm for tok in center_tokens):
                score += 6
            if "village_center" in sid_norm:
                score += 8
            scored.append((score, sid))
        scored.sort(key=lambda row: (-row[0], scenes[row[1]].title.casefold(), row[1]))
        return scored[0][1]

    def _build_local_edges(
        self,
        scenes: dict[str, Scene],
        anchor: str,
        local_ids: list[str],
        *,
        center_id: str,
    ) -> set[tuple[str, str]]:
        local_set = set(local_ids)
        preset = _CITY_LAYOUT_PRESETS.get(anchor)
        if preset and isinstance(preset.get("edges"), list):
            out: set[tuple[str, str]] = set()
            for row in preset["edges"]:
                if not (isinstance(row, (list, tuple)) and len(row) == 2):
                    continue
                a = str(row[0] or "").strip()
                b = str(row[1] or "").strip()
                if a in local_set and b in local_set and a != b:
                    out.add(tuple(sorted((a, b))))
            if out and self._all_nodes_reachable(local_ids, out, center_id=center_id):
                return out

        others = [sid for sid in local_ids if sid != center_id]
        others.sort(key=lambda sid: (scenes[sid].generated, scenes[sid].title.casefold(), sid))

        out: set[tuple[str, str]] = set()

        # Depuis le centre, on n'expose que 2 points max pour garder l'effet "ruelles".
        fanout = min(2, len(others))
        for sid in others[:fanout]:
            out.add(tuple(sorted((center_id, sid))))

        # Le reste se découvre en progressant dans les rues.
        for idx in range(len(others) - 1):
            out.add(tuple(sorted((others[idx], others[idx + 1]))))

        # Petite boucle secondaire pour éviter un tracé trop linéaire.
        if len(others) >= 4:
            out.add(tuple(sorted((others[1], others[3]))))

        return out

    def _all_nodes_reachable(
        self,
        local_ids: list[str],
        edges: set[tuple[str, str]],
        *,
        center_id: str,
    ) -> bool:
        if len(local_ids) < 2:
            return True
        adjacency: dict[str, set[str]] = {sid: set() for sid in local_ids}
        for a, b in edges:
            if a in adjacency and b in adjacency:
                adjacency[a].add(b)
                adjacency[b].add(a)
        if not adjacency.get(center_id):
            return False
        stack = [center_id]
        seen = {center_id}
        while stack:
            node = stack.pop()
            for nxt in adjacency.get(node, set()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return len(seen) == len(local_ids)

    def _street_label(self, *, source: Scene, target: Scene, center_scene_id: str) -> str:
        if target.id == center_scene_id:
            return "Revenir vers le centre"
        if source.id == center_scene_id:
            return f"Prendre la ruelle vers {self._short_scene_title(target.title)}"
        return f"Continuer vers {self._short_scene_title(target.title)}"

    def _short_scene_title(self, title: str) -> str:
        text = str(title or "").strip()
        if " - " in text:
            return text.split(" - ", 1)[1].strip()
        return text

    def _build_prompt(
        self,
        *,
        current_scene: Scene,
        current_anchor: str,
        target_anchor: str,
        existing_titles: list[str],
    ) -> str:
        schema = {
            "title": "Nom du nouveau lieu",
            "narrator_text": "Texte narrateur 1-3 phrases",
            "npcs": ["Nom PNJ 1", "Nom PNJ 2"],
            "travel_label_from_current": "Libellé du choix de voyage",
        }
        return (
            "Tu génères un nouveau lieu d'exploration pour un RPG dark-fantasy.\n"
            "Réponds en JSON valide uniquement, sans markdown.\n"
            "Le monde est AELYNDAR. Tu dois rester STRICTEMENT cohérent avec cette carte et ses routes officielles.\n"
            f"- Ancrages: {', '.join(MAP_ANCHORS)}\n"
            f"- Zone actuelle: {current_scene.title} (ancrage: {current_anchor})\n"
            f"- Destination imposée sur la route officielle: {target_anchor}\n"
            f"- Voisins officiels depuis {current_anchor}: {', '.join(official_neighbors(current_anchor))}\n"
            f"- Le lieu généré doit être un point du trajet entre {current_anchor} et {target_anchor}, pas ailleurs.\n"
            "- Evite de régénérer un lieu déjà existant.\n"
            f"- Lieux existants: {', '.join(existing_titles[:60])}\n"
            "Contraintes:\n"
            "- N'invente JAMAIS de nouvel ancrage de carte.\n"
            "- N'invente JAMAIS un chemin hors graphe officiel.\n"
            "- title: court, évocateur, pas de doublon exact avec les lieux existants.\n"
            "- narrator_text: commence par 'Ataryxia :', 1 à 3 phrases, ambiance sombre.\n"
            "- npcs: 0 à 4 PNJ crédibles pour le lieu.\n"
            "- travel_label_from_current: phrase actionnable pour un bouton.\n"
            "Schéma:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _parse_draft(self, raw: str, *, target_anchor: str) -> LocationDraft:
        json_str = self._extract_json(raw)
        try:
            data = json.loads(json_str)
            draft = LocationDraft.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            draft = LocationDraft(
                title=f"Sentier Oublié vers {target_anchor}",
                narrator_text=f"Ataryxia : La piste se déchire sous vos pas, et la route vers {target_anchor} semble retenir son souffle.",
                npcs=[],
                travel_label_from_current="Explorer un sentier oublié",
            )

        if not draft.title.strip():
            draft.title = f"Sentier Oublié vers {target_anchor}"

        if not draft.narrator_text.strip().startswith("Ataryxia"):
            draft.narrator_text = f"Ataryxia : {draft.narrator_text.strip() or 'Un nouveau lieu s’ouvre devant vous.'}"

        return draft

    def _pick_target_anchor(self, current_anchor: str, neighbors: list[str], existing_scenes: dict[str, Scene]) -> str:
        if not neighbors:
            return current_anchor

        scene_counts: dict[str, int] = {}
        for scene in existing_scenes.values():
            if scene.map_anchor:
                scene_counts[scene.map_anchor] = scene_counts.get(scene.map_anchor, 0) + 1

        unseen = [a for a in neighbors if scene_counts.get(a, 0) == 0]
        if unseen:
            return random.choice(unseen)

        return min(neighbors, key=lambda a: (scene_counts.get(a, 0), a))

    def _infer_anchor(self, scene: Scene) -> str:
        if scene.map_anchor in MAP_ANCHORS:
            return scene.map_anchor

        title_norm = self._norm(scene.title)
        for anchor in MAP_ANCHORS:
            if self._norm(anchor) in title_norm or title_norm in self._norm(anchor):
                return anchor

        if any(k in title_norm for k in ("village", "taverne", "prison", "temple")):
            return "Lumeria"

        return "Lumeria"

    def _unique_scene_id(self, anchor: str, title: str, existing_ids: set[str]) -> str:
        base = f"gen_{self._slug(anchor)}_{self._slug(title)}"
        candidate = base
        index = 2
        while candidate in existing_ids:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _unique_title(self, title: str, existing_titles: set[str]) -> str:
        candidate = title.strip()
        if candidate not in existing_titles:
            return candidate
        index = 2
        while f"{candidate} ({index})" in existing_titles:
            index += 1
        return f"{candidate} ({index})"

    def _slug(self, text: str) -> str:
        raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        return slug or "lieu"

    def _norm(self, text: str) -> str:
        return self._slug(text)

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"
