from app.gamemaster.dungeon_combat import (
    build_gore_defeat_lines,
    build_combat_state,
    is_combat_event,
    resolve_combat_turn,
    wants_repeat_heal_until_full,
)


class SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        if self._values:
            value = int(self._values.pop(0))
            return max(a, min(b, value))
        return a


class _FakeMonsterManager:
    def combat_profile_for_event(self, event: dict) -> dict:
        return {
            "monster_id": "golem_test",
            "base_name": "Golem de test",
            "enemy_name": str(event.get("name") or "Golem de test"),
            "archetype": "tank",
            "tier": 3,
            "description": "Monstre de test",
            "media_image": "",
            "media_clip": "",
            "enemy_hp": 44,
            "dc": 15,
            "enemy_attack_bonus": 6,
            "enemy_damage_min": 5,
            "enemy_damage_max": 9,
        }


def _base_combat() -> dict:
    return {
        "active": True,
        "event_type": "monster",
        "floor": 1,
        "enemy_name": "Goule",
        "enemy_hp": 30,
        "enemy_max_hp": 30,
        "dc": 12,
        "enemy_attack_bonus": 3,
        "enemy_damage_min": 3,
        "enemy_damage_max": 5,
        "guard": 0,
        "round": 1,
    }


def _base_sheet() -> dict:
    return {
        "stats": {
            "force": 5,
            "intelligence": 5,
            "magie": 5,
            "defense": 5,
            "sagesse": 5,
            "agilite": 5,
            "dexterite": 5,
            "chance": 5,
            "charisme": 5,
            "pv": 20,
            "pv_max": 20,
        }
    }


def test_is_combat_event_detects_monster_boss() -> None:
    assert is_combat_event({"type": "monster"})
    assert is_combat_event({"type": "boss"})
    assert not is_combat_event({"type": "treasure"})
    assert not is_combat_event(None)


def test_build_combat_state_generates_enemy_values() -> None:
    combat = build_combat_state({"type": "boss", "name": "Dragon noir", "floor": 9}, rng=SequenceRng([32]))
    assert combat["event_type"] == "boss"
    assert combat["enemy_name"] == "Dragon noir"
    assert combat["enemy_hp"] >= 20
    assert combat["enemy_max_hp"] == combat["enemy_hp"]
    assert combat["dc"] >= 12


def test_build_combat_state_uses_monster_manager_profile() -> None:
    combat = build_combat_state(
        {"type": "monster", "name": "Golem de test", "floor": 12, "monster_id": "golem_test"},
        rng=SequenceRng([10]),
        monster_manager=_FakeMonsterManager(),
    )
    assert combat["enemy_name"] == "Golem de test"
    assert combat["enemy_hp"] == 44
    assert combat["dc"] == 15
    assert combat["enemy_attack_bonus"] == 6
    assert combat["monster_id"] == "golem_test"
    assert combat["monster_archetype"] == "tank"


def test_resolve_combat_turn_outcomes_from_d20() -> None:
    for raw_roll, expected in (
        (20, "critical_success"),
        (15, "success"),
        (5, "failure"),
        (1, "critical_failure"),
    ):
        result = resolve_combat_turn(
            combat_state=_base_combat(),
            action_text="j'attaque",
            player_hp=20,
            player_max_hp=20,
            player_sheet=_base_sheet(),
            known_skills=[],
            skill_manager=None,
            rng=SequenceRng([raw_roll, 4, 10, 4]),
        )
        assert result["outcome"] == expected


def test_skill_bonus_can_turn_failure_into_success() -> None:
    known_skills = [
        {
            "skill_id": "frappe_precise",
            "name": "Frappe precise",
            "category": "combat",
            "level": 20,
            "rank": 3,
        }
    ]
    combat = _base_combat()
    combat["dc"] = 13
    result = resolve_combat_turn(
        combat_state=combat,
        action_text="j'utilise frappe precise et j'attaque",
        player_hp=20,
        player_max_hp=20,
        player_sheet=_base_sheet(),
        known_skills=known_skills,
        skill_manager=None,
        rng=SequenceRng([10, 4, 10, 4]),
    )
    assert result["outcome"] == "success"
    assert "frappe_precise" in result["used_skill_ids"]


def test_heal_action_restores_hp_and_stays_capped() -> None:
    result = resolve_combat_turn(
        combat_state=_base_combat(),
        action_text="je lance un soin",
        player_hp=5,
        player_max_hp=20,
        player_sheet=_base_sheet(),
        known_skills=[],
        skill_manager=None,
        rng=SequenceRng([20, 12, 1]),
    )
    assert result["action_kind"] == "heal"
    assert result["outcome"] == "critical_success"
    assert 5 < result["player_hp"] <= 20


def test_runtime_stat_bonuses_apply_to_rolls() -> None:
    combat = _base_combat()
    combat["dc"] = 12
    result = resolve_combat_turn(
        combat_state=combat,
        action_text="j'attaque",
        player_hp=20,
        player_max_hp=20,
        player_sheet=_base_sheet(),
        known_skills=[],
        runtime_stat_bonuses={
            "force": 4,
            "dexterite": 4,
            "agilite": 4,
        },
        skill_manager=None,
        rng=SequenceRng([10, 4, 10, 4]),
    )
    assert result["outcome"] == "success"


def test_mixed_attack_and_heal_words_prefers_attack_action() -> None:
    result = resolve_combat_turn(
        combat_state=_base_combat(),
        action_text="je tape puis je soigne",
        player_hp=20,
        player_max_hp=20,
        player_sheet=_base_sheet(),
        known_skills=[],
        skill_manager=None,
        rng=SequenceRng([10, 4, 10, 4]),
    )
    assert result["action_kind"] == "attack"


def test_wants_repeat_heal_until_full_detects_clear_intent() -> None:
    assert wants_repeat_heal_until_full("Je lance des sorts de soin jusqu a etre completement soigne.")
    assert wants_repeat_heal_until_full("Je veux me soigner completement, heal jusqu'au maximum.")
    assert wants_repeat_heal_until_full("heal me until full")
    assert not wants_repeat_heal_until_full("Je lance un sort de soin.")
    assert not wants_repeat_heal_until_full("J'attaque jusqu'au bout.")


def test_build_gore_defeat_lines_is_descriptive() -> None:
    lines = build_gore_defeat_lines(enemy_name="Goule", event_type="monster", rng=SequenceRng([0, 0]))
    assert len(lines) >= 2
    joined = " ".join(lines).casefold()
    assert "goule" in joined
    assert "sang" in joined or "demembre" in joined or "devore" in joined


def test_resolve_combat_turn_defeat_uses_gore_lines() -> None:
    combat = _base_combat()
    result = resolve_combat_turn(
        combat_state=combat,
        action_text="j'attaque",
        player_hp=2,
        player_max_hp=20,
        player_sheet=_base_sheet(),
        known_skills=[],
        skill_manager=None,
        rng=SequenceRng([5, 20, 9, 0, 0]),
    )
    assert result["defeat"] is True
    joined = " ".join([str(x) for x in result.get("lines", [])]).casefold()
    assert "vous etes mis a terre." not in joined
    assert "sang" in joined or "demembre" in joined or "devore" in joined
