import json

from app.gamemaster.monster_manager import MonsterManager


def _write_monster(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_catalog_from_json_files(tmp_path) -> None:
    _write_monster(
        tmp_path / "golem.json",
        {
            "id": "golem_rouille",
            "name": "Golem rouille",
            "aliases": ["golem rouille", "construct"],
            "archetype": "tank",
            "tier": 3,
            "description": "Construct de fer",
            "combat": {
                "base_hp": 30,
                "base_dc": 13,
                "base_attack_bonus": 4,
                "base_damage_min": 4,
                "base_damage_max": 8,
                "hp_per_floor": 1.3,
                "dc_per_5_floors": 1,
                "attack_per_6_floors": 1,
                "damage_per_8_floors": 1,
            },
            "boss_modifiers": {
                "hp_mult": 1.7,
                "damage_mult": 1.4,
                "dc_bonus": 2,
                "attack_bonus": 1,
            },
            "media": {
                "image": "assets/monsters/golem.png",
                "clip": "assets/monsters/golem.mp4",
            },
        },
    )

    manager = MonsterManager(data_dir=str(tmp_path))
    catalog = manager.load_catalog()
    assert "golem_rouille" in catalog
    monster = catalog["golem_rouille"]
    assert monster.name == "Golem rouille"
    assert monster.tier == 3


def test_combat_profile_scales_with_floor_and_boss(tmp_path) -> None:
    _write_monster(
        tmp_path / "spectre.json",
        {
            "id": "spectre_test",
            "name": "Spectre test",
            "aliases": ["spectre test"],
            "archetype": "caster",
            "tier": 2,
            "description": "Mage spectral",
            "combat": {
                "base_hp": 20,
                "base_dc": 12,
                "base_attack_bonus": 4,
                "base_damage_min": 3,
                "base_damage_max": 6,
                "hp_per_floor": 1.0,
                "dc_per_5_floors": 1,
                "attack_per_6_floors": 1,
                "damage_per_8_floors": 1,
            },
            "boss_modifiers": {
                "hp_mult": 1.5,
                "damage_mult": 1.4,
                "dc_bonus": 2,
                "attack_bonus": 1,
            },
            "media": {},
        },
    )

    manager = MonsterManager(data_dir=str(tmp_path))
    regular = manager.combat_profile_for_event(
        {"type": "monster", "name": "Spectre test", "monster_id": "spectre_test", "floor": 12}
    )
    boss = manager.combat_profile_for_event(
        {"type": "boss", "name": "Seigneur Spectre test", "base_monster_name": "Spectre test", "monster_id": "spectre_test", "floor": 12}
    )

    assert isinstance(regular, dict)
    assert isinstance(boss, dict)
    assert boss["enemy_hp"] > regular["enemy_hp"]
    assert boss["enemy_damage_max"] >= regular["enemy_damage_max"]
    assert boss["dc"] >= regular["dc"]
