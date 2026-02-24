import random

from app.gamemaster.dungeon_manager import DungeonManager


def _profile() -> dict:
    return {
        "name": "Caveau de test",
        "theme": "ombre",
        "entry_text": "Ataryxia : test",
        "monster_pool": ["Chevalier spectral", "Goule blindee"],
        "treasure_pool": ["Grimoire interdit", "Bourse scellee"],
    }


def test_start_run_injects_boss_on_last_floor(monkeypatch) -> None:
    monkeypatch.setattr(random, "randint", lambda a, b: 10)
    manager = DungeonManager(None)
    run = manager.start_run("Lumeria", _profile())

    floors = run.get("floors")
    assert isinstance(floors, list)
    assert len(floors) == 10
    assert floors[-1].get("type") == "boss"
    assert isinstance(floors[-1].get("name"), str) and str(floors[-1].get("name")).strip()
    assert isinstance(floors[-1].get("monster_id"), str) and str(floors[-1].get("monster_id")).strip()
    assert isinstance(floors[-1].get("base_monster_name"), str) and str(floors[-1].get("base_monster_name")).strip()


def test_advance_floor_reaches_boss_then_completes(monkeypatch) -> None:
    monkeypatch.setattr(random, "randint", lambda a, b: 10)
    manager = DungeonManager(None)
    run = manager.start_run("Lumeria", _profile())

    last_event = None
    for _ in range(10):
        last_event = manager.advance_floor(run)

    assert isinstance(last_event, dict)
    assert last_event.get("type") == "boss"
    assert bool(run.get("completed", False)) is True
    assert manager.advance_floor(run) is None


def test_floor_monster_event_contains_monster_id(monkeypatch) -> None:
    monkeypatch.setattr(random, "random", lambda: 0.20)
    manager = DungeonManager(None)
    event = manager._build_floor_event(_profile(), 3)  # noqa: SLF001 - targeted unit test

    assert event.get("type") == "monster"
    assert isinstance(event.get("monster_id"), str)
    assert str(event.get("monster_id")).strip()
