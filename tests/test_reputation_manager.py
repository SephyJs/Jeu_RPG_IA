from __future__ import annotations

from app.gamemaster.reputation_manager import (
    apply_dungeon_reputation,
    apply_quest_branch_reputation,
    apply_quest_completion_reputation,
    apply_trade_reputation,
    can_access_scene_by_reputation,
    ensure_reputation_state,
    load_reputation_rules,
    merchant_price_multiplier_from_reputation,
    reputation_tier,
)
from app.ui.state.game_state import GameState


def test_ensure_reputation_state_sanitizes_and_clamps() -> None:
    state = GameState()
    state.faction_reputation = {
        " Marchands!!! ": 250,
        "": 12,
        "Peuple": -999,
    }
    state.faction_reputation_log = [
        {
            "at": "2026-01-01T00:00:00+00:00",
            "faction": "Peuple",
            "delta": 5,
            "before": -200,
            "after": 120,
            "reason": "x" * 400,
            "source": "trade",
        },
        {"faction": "", "delta": 1},
    ]

    ensure_reputation_state(state)

    assert state.faction_reputation == {"Marchands": 100, "Peuple": -100}
    assert len(state.faction_reputation_log) == 1
    assert state.faction_reputation_log[0]["before"] == -100
    assert state.faction_reputation_log[0]["after"] == 100
    assert len(state.faction_reputation_log[0]["reason"]) <= 140


def test_apply_trade_reputation_charity_updates_peuple() -> None:
    state = GameState()

    lines = apply_trade_reputation(
        state,
        trade_context={
            "action": "give",
            "status": "ok",
            "qty_done": 2,
            "target_is_beggar": True,
        },
        npc_name="Mendiant",
        npc_profile={"role": "mendiant"},
        map_anchor="Lumeria",
    )

    assert lines == ["Peuple +2 (2)"]
    assert state.faction_reputation.get("Peuple") == 2
    assert len(state.faction_reputation_log) == 1
    assert state.faction_reputation_log[0]["source"] == "trade"


def test_apply_quest_completion_reputation_is_idempotent() -> None:
    state = GameState()
    quest = {
        "status": "completed",
        "source_npc_name": "Forgeron Brak",
        "objective": {"type": "send_messages"},
    }

    first = apply_quest_completion_reputation(state, quest=quest)
    second = apply_quest_completion_reputation(state, quest=quest)

    assert first == ["Marchands +2 (2)"]
    assert second == []
    assert bool(quest.get("reputation_claimed")) is True
    assert state.faction_reputation.get("Marchands") == 2


def test_apply_dungeon_reputation_only_for_supported_events() -> None:
    state = GameState()

    ignored = apply_dungeon_reputation(state, floor=3, event_type="treasure")
    boss = apply_dungeon_reputation(state, floor=12, event_type="boss")

    assert ignored == []
    assert boss == ["Aventuriers +3 (3)"]
    assert state.faction_reputation.get("Aventuriers") == 3


def test_load_reputation_rules_returns_defaults_when_missing(tmp_path) -> None:
    rules = load_reputation_rules(tmp_path / "missing_rules.json")

    assert rules["trade"]["merchant_faction"] == "Marchands"
    assert rules["quest"]["default_delta"] == 2
    assert rules["dungeon"]["faction"] == "Aventuriers"


def test_apply_trade_reputation_uses_custom_rules() -> None:
    state = GameState()
    custom_rules = {
        "trade": {
            "merchant_faction": "Guilde Marchande",
            "merchant_delta_small": 4,
            "merchant_delta_large": 6,
            "merchant_large_qty_threshold": 1,
            "charity_faction": "Citoyens",
            "charity_delta_small": 5,
            "charity_delta_large": 7,
            "charity_large_qty_threshold": 1,
            "generic_give_delta": 2,
        }
    }

    merchant_lines = apply_trade_reputation(
        state,
        trade_context={"action": "buy", "status": "ok", "qty_done": 1},
        rules=custom_rules,
    )
    charity_lines = apply_trade_reputation(
        state,
        trade_context={"action": "give", "status": "ok", "qty_done": 1, "target_is_beggar": True},
        rules=custom_rules,
    )

    assert merchant_lines == ["Guilde Marchande +4 (4)"]
    assert charity_lines == ["Citoyens +5 (5)"]
    assert state.faction_reputation.get("Guilde Marchande") == 4
    assert state.faction_reputation.get("Citoyens") == 5


def test_apply_dungeon_reputation_uses_custom_rules() -> None:
    state = GameState()
    custom_rules = {
        "dungeon": {
            "faction": "Veilleurs",
            "default_delta": 2,
            "high_floor_delta": 4,
            "high_floor_threshold": 5,
            "boss_delta": 8,
            "eligible_event_types": ["treasure", "boss"],
        }
    }

    treasure = apply_dungeon_reputation(state, floor=3, event_type="treasure", rules=custom_rules)
    high_floor = apply_dungeon_reputation(state, floor=6, event_type="treasure", rules=custom_rules)
    boss = apply_dungeon_reputation(state, floor=6, event_type="boss", rules=custom_rules)

    assert treasure == ["Veilleurs +2 (2)"]
    assert high_floor == ["Veilleurs +4 (6)"]
    assert boss == ["Veilleurs +8 (14)"]
    assert state.faction_reputation.get("Veilleurs") == 14


def test_apply_quest_reputation_uses_custom_rules_without_source_npc() -> None:
    state = GameState()
    quest = {
        "status": "completed",
        "source_npc_name": "",
        "objective": {"type": "send_messages"},
    }
    custom_rules = {
        "quest": {
            "default_faction": "Habitants",
            "default_delta": 1,
            "objective_deltas": {"send_messages": 5},
            "objective_factions": {"send_messages": "Messagers"},
        }
    }

    lines = apply_quest_completion_reputation(state, quest=quest, rules=custom_rules)

    assert lines == ["Messagers +5 (5)"]
    assert state.faction_reputation.get("Messagers") == 5


def test_reputation_tier_and_scene_access_gate() -> None:
    state = GameState()
    state.faction_reputation = {"Autorites": -20}
    ensure_reputation_state(state)

    can_access, hint = can_access_scene_by_reputation(
        state,
        scene_id="palais_royal_01",
        scene_title="Lumeria - Palais Royal",
    )
    assert can_access is False
    assert "Autorites" in hint
    assert reputation_tier(-20) in {"mefiant", "hostile"}


def test_merchant_multiplier_varies_with_reputation() -> None:
    state = GameState()
    state.faction_reputation = {"Marchands": 70}
    ensure_reputation_state(state)
    assert merchant_price_multiplier_from_reputation(state) < 1.0

    state.faction_reputation = {"Marchands": -80}
    ensure_reputation_state(state)
    assert merchant_price_multiplier_from_reputation(state) > 1.0


def test_apply_quest_branch_reputation_once() -> None:
    state = GameState()
    quest = {
        "id": "quest_0009",
        "status": "completed",
        "branching": {
            "selected_option_id": "coercition",
            "options": [
                {"id": "coercition", "reputation": {"Habitants": -2, "Aventuriers": 1}},
            ],
        },
    }

    first = apply_quest_branch_reputation(state, quest=quest)
    second = apply_quest_branch_reputation(state, quest=quest)

    assert first
    assert second == []
    assert state.faction_reputation.get("Habitants") == -2
