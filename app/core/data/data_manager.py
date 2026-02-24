from __future__ import annotations

from copy import deepcopy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.models import Choice, Scene


class DataError(RuntimeError):
    """Erreur de données (JSON manquant, invalide, etc.)."""


@dataclass(frozen=True)
class StartingPointCandidate:
    location_id: str
    weight: int


class DataManager:
    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = Path(data_dir)
        self.lieux_dir = self.data_dir / "lieux"
        self.starting_points_path = self.data_dir / "starting_points.json"
        self._starting_points_cache_key: tuple[int, int] | None = None
        self._starting_points_cache_value: tuple[Optional[int], List[StartingPointCandidate]] | None = None
        self._scenes_cache_key: tuple[tuple[str, int, int], ...] | None = None
        self._scenes_cache_value: Dict[str, Scene] | None = None

    # ---------- JSON helpers ----------
    def _read_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise DataError(f"Fichier introuvable: {path.as_posix()}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise DataError(f"JSON invalide dans {path.as_posix()} : {e}") from e

    def _starting_points_stamp(self) -> tuple[int, int]:
        stat = self.starting_points_path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)

    def _locations_stamp(self) -> tuple[tuple[str, int, int], ...]:
        files = sorted(self.lieux_dir.glob("*.json"))
        rows: list[tuple[str, int, int]] = []
        for path in files:
            stat = path.stat()
            rows.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(rows)

    # ---------- Starting points ----------
    def load_starting_points(self) -> Tuple[Optional[int], List[StartingPointCandidate]]:
        cache_key: tuple[int, int] | None = None
        if self.starting_points_path.exists():
            cache_key = self._starting_points_stamp()
            if (
                cache_key == self._starting_points_cache_key
                and isinstance(self._starting_points_cache_value, tuple)
            ):
                default_seed, candidates = self._starting_points_cache_value
                return default_seed, deepcopy(candidates)

        data = self._read_json(self.starting_points_path)

        if data.get("version") != 1:
            raise DataError("starting_points.json: 'version' doit être 1")

        default_seed = data.get("default_seed", None)
        if default_seed is not None and not isinstance(default_seed, int):
            raise DataError("starting_points.json: 'default_seed' doit être un entier ou null")

        candidates_raw = data.get("candidates", [])
        if not isinstance(candidates_raw, list) or not candidates_raw:
            raise DataError("starting_points.json: 'candidates' doit être une liste non vide")

        candidates: List[StartingPointCandidate] = []
        for c in candidates_raw:
            if not isinstance(c, dict):
                raise DataError("starting_points.json: chaque candidate doit être un objet")
            loc = c.get("location_id")
            w = c.get("weight", 1)
            if not isinstance(loc, str) or not loc:
                raise DataError("starting_points.json: 'location_id' doit être une string non vide")
            if not isinstance(w, int) or w <= 0:
                raise DataError("starting_points.json: 'weight' doit être un entier > 0")
            candidates.append(StartingPointCandidate(location_id=loc, weight=w))

        if cache_key is None:
            cache_key = self._starting_points_stamp()
        self._starting_points_cache_key = cache_key
        self._starting_points_cache_value = (default_seed, deepcopy(candidates))
        return default_seed, candidates

    def choose_start_location_id(self, seed: Optional[int] = None) -> str:
        default_seed, candidates = self.load_starting_points()
        rng = random.Random(seed if seed is not None else default_seed)

        population = [c.location_id for c in candidates]
        weights = [c.weight for c in candidates]
        return rng.choices(population=population, weights=weights, k=1)[0]

    # ---------- Locations ----------
    def load_location_scene(self, location_id: str) -> Scene:
        path = self.lieux_dir / f"{location_id}.json"
        data = self._read_json(path)

        scene_id = data.get("id")
        title = data.get("title")
        narrator_text = data.get("narrator_text")

        if scene_id != location_id:
            raise DataError(f"{path.as_posix()}: 'id' doit correspondre au nom du fichier ({location_id})")
        if not isinstance(title, str) or not title:
            raise DataError(f"{path.as_posix()}: 'title' doit être une string non vide")
        if not isinstance(narrator_text, str) or not narrator_text:
            raise DataError(f"{path.as_posix()}: 'narrator_text' doit être une string non vide")

        npcs = data.get("npcs", [])
        if not isinstance(npcs, list) or any(not isinstance(x, str) for x in npcs):
            raise DataError(f"{path.as_posix()}: 'npcs' doit être une liste de strings")

        choices_raw = data.get("choices", [])
        if not isinstance(choices_raw, list):
            raise DataError(f"{path.as_posix()}: 'choices' doit être une liste")

        choices: List[Choice] = []
        for ch in choices_raw:
            if not isinstance(ch, dict):
                raise DataError(f"{path.as_posix()}: chaque choice doit être un objet")
            cid = ch.get("id")
            label = ch.get("label")
            next_loc = ch.get("next_location_id")  # on accepte ce nom dans les JSON

            if not isinstance(cid, str) or not cid:
                raise DataError(f"{path.as_posix()}: choice.id doit être une string non vide")
            if not isinstance(label, str) or not label:
                raise DataError(f"{path.as_posix()}: choice.label doit être une string non vide")

            if next_loc is not None and (not isinstance(next_loc, str) or not next_loc):
                raise DataError(f"{path.as_posix()}: next_location_id doit être une string ou absent")

            # On mappe vers ton modèle existant: next_scene_id
            choices.append(Choice(id=cid, label=label, next_scene_id=next_loc))

        return Scene(
            id=scene_id,
            title=title,
            narrator_text=narrator_text,
            npc_names=npcs,
            choices=choices,
        )

    def load_all_location_scenes(self) -> Dict[str, Scene]:
        if not self.lieux_dir.exists():
            raise DataError(f"Dossier introuvable: {self.lieux_dir.as_posix()}")

        cache_key = self._locations_stamp()
        if cache_key == self._scenes_cache_key and isinstance(self._scenes_cache_value, dict):
            return deepcopy(self._scenes_cache_value)

        scenes: Dict[str, Scene] = {}
        for p in sorted(self.lieux_dir.glob("*.json")):
            loc_id = p.stem
            scenes[loc_id] = self.load_location_scene(loc_id)
        if not scenes:
            raise DataError("Aucun lieu trouvé dans data/lieux/*.json")

        self._scenes_cache_key = cache_key
        self._scenes_cache_value = deepcopy(scenes)
        return scenes
