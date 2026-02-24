from __future__ import annotations

from app.core.data.item_manager import ItemDef
from app.gamemaster.craft_manager import CraftManager
from app.ui.state.game_state import GameState
from app.ui.state.inventory import ItemStack


def _count_item(state: GameState, item_id: str) -> int:
    target = str(item_id or "").strip().casefold()
    total = 0
    for grid in (state.carried, state.storage):
        for stack in grid.slots:
            if stack is None:
                continue
            if str(stack.item_id or "").strip().casefold() != target:
                continue
            total += int(getattr(stack, "qty", 0) or 0)
    return total


def test_craft_consumes_inputs_and_adds_outputs() -> None:
    manager = CraftManager()
    state = GameState()
    state.item_defs = {
        "pain_01": ItemDef(id="pain_01", name="Pain", stack_max=20, type="consumable", value_gold=5),
        "potion_soin_01": ItemDef(id="potion_soin_01", name="Potion soin", stack_max=10, type="consumable", value_gold=14),
    }
    state.carried.slots[0] = ItemStack(item_id="pain_01", qty=4)

    outcome = manager.craft(
        state=state,
        recipe_id="infusion_soin",
        qty=2,
        item_defs=state.item_defs,
        scene_title="Laboratoire d'alchimie",
    )

    assert outcome.get("ok") is True
    assert _count_item(state, "pain_01") == 0
    assert _count_item(state, "potion_soin_01") == 2


def test_craft_fails_when_station_missing() -> None:
    manager = CraftManager()
    state = GameState()
    state.item_defs = {
        "pain_01": ItemDef(id="pain_01", name="Pain", stack_max=20, type="consumable", value_gold=5),
        "potion_soin_01": ItemDef(id="potion_soin_01", name="Potion soin", stack_max=10, type="consumable", value_gold=14),
    }
    state.carried.slots[0] = ItemStack(item_id="pain_01", qty=2)

    outcome = manager.craft(
        state=state,
        recipe_id="infusion_soin",
        qty=1,
        item_defs=state.item_defs,
        scene_title="Ruelle des Lanternes",
    )

    assert outcome.get("ok") is False
    assert _count_item(state, "pain_01") == 2
    assert _count_item(state, "potion_soin_01") == 0
