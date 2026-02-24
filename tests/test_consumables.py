from app.ui.components.consumables import (
    add_consumable_stat_buff,
    get_active_consumable_buffs,
    get_consumable_stat_bonus_totals,
    tick_consumable_buffs,
)
from app.ui.state.game_state import GameState


def test_add_and_tick_consumable_buff_lifecycle() -> None:
    state = GameState()
    added = add_consumable_stat_buff(
        state,
        stat="force",
        value=2,
        duration_turns=3,
        item_id="potion_force_01",
        item_name="Potion de force",
    )
    assert isinstance(added, dict)
    assert get_consumable_stat_bonus_totals(state) == {"force": 2}

    expired = tick_consumable_buffs(state)
    assert expired == []
    assert get_active_consumable_buffs(state)[0]["turns_remaining"] == 2

    tick_consumable_buffs(state)
    expired = tick_consumable_buffs(state)
    assert len(expired) == 1
    assert get_consumable_stat_bonus_totals(state) == {}


def test_reapplying_same_buff_extends_remaining_turns() -> None:
    state = GameState()
    add_consumable_stat_buff(
        state,
        stat="defense",
        value=2,
        duration_turns=2,
        item_id="potion_defense_01",
        item_name="Potion de defense",
    )
    tick_consumable_buffs(state)
    buffs = get_active_consumable_buffs(state)
    assert buffs[0]["turns_remaining"] == 1

    add_consumable_stat_buff(
        state,
        stat="defense",
        value=2,
        duration_turns=4,
        item_id="potion_defense_01",
        item_name="Potion de defense",
    )
    buffs = get_active_consumable_buffs(state)
    assert buffs[0]["turns_remaining"] == 4
