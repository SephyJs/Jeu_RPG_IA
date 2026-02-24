from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.data.data_manager import DataManager


def _write_location(path: Path, *, scene_id: str, title: str) -> None:
    payload = {
        "id": scene_id,
        "title": title,
        "narrator_text": "Une place calme.",
        "npcs": ["Marchand"],
        "choices": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_starting_points(path: Path) -> None:
    payload = {
        "version": 1,
        "default_seed": 7,
        "candidates": [{"location_id": "village_center_01", "weight": 1}],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_location_cache_returns_isolated_copy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    lieux_dir = data_dir / "lieux"
    lieux_dir.mkdir(parents=True)
    _write_location(lieux_dir / "village_center_01.json", scene_id="village_center_01", title="Village")
    _write_starting_points(data_dir / "starting_points.json")

    manager = DataManager(data_dir=str(data_dir))
    first = manager.load_all_location_scenes()
    first["village_center_01"].title = "Mutated"

    second = manager.load_all_location_scenes()
    assert second["village_center_01"].title == "Village"


def test_location_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    lieux_dir = data_dir / "lieux"
    lieux_dir.mkdir(parents=True)
    scene_path = lieux_dir / "village_center_01.json"
    _write_location(scene_path, scene_id="village_center_01", title="Village")
    _write_starting_points(data_dir / "starting_points.json")

    manager = DataManager(data_dir=str(data_dir))
    initial = manager.load_all_location_scenes()
    assert initial["village_center_01"].title == "Village"

    time.sleep(0.002)
    _write_location(scene_path, scene_id="village_center_01", title="Village Renove")
    updated = manager.load_all_location_scenes()
    assert updated["village_center_01"].title == "Village Renove"


def test_starting_points_cache_returns_isolated_copy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    lieux_dir = data_dir / "lieux"
    lieux_dir.mkdir(parents=True)
    _write_location(lieux_dir / "village_center_01.json", scene_id="village_center_01", title="Village")
    _write_starting_points(data_dir / "starting_points.json")

    manager = DataManager(data_dir=str(data_dir))
    _, first_candidates = manager.load_starting_points()
    first_candidates.append(first_candidates[0])

    _, second_candidates = manager.load_starting_points()
    assert len(second_candidates) == 1
