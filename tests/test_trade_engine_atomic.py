from __future__ import annotations

from app.core.data.item_manager import ItemDef
from app.core.engine import TradeEngine
from app.ui.state.game_state import GameState
from app.ui.state.inventory import ItemStack


def _build_item_defs() -> dict[str, ItemDef]:
    return {
        "potion_soin_01": ItemDef(
            id="potion_soin_01",
            name="Potion de soin",
            stack_max=1,
            type="consumable",
            slot="",
            rarity="common",
            description="",
            stat_bonuses={},
            effects=[],
            value_gold=30,
        ),
        "epee_apprenti": ItemDef(
            id="epee_apprenti",
            name="Epee d'apprenti",
            stack_max=20,
            type="weapon",
            slot="weapon",
            rarity="common",
            description="",
            stat_bonuses={},
            effects=[],
            value_gold=10,
        ),
        "junk": ItemDef(
            id="junk",
            name="Bric-a-brac",
            stack_max=1,
            type="material",
            slot="",
            rarity="common",
            description="",
            stat_bonuses={},
            effects=[],
            value_gold=1,
        ),
    }


def test_buy_is_atomic_when_inventory_is_full() -> None:
    state = GameState()
    state.item_defs = _build_item_defs()
    state.player.gold = 200

    for idx in range(len(state.carried.slots)):
        state.carried.set(idx, ItemStack(item_id="junk", qty=1))
    for idx in range(len(state.storage.slots)):
        state.storage.set(idx, ItemStack(item_id="junk", qty=1))

    engine = TradeEngine()
    session = engine.start_trade("Marchand", "buy", llm_enabled=False)
    session = engine.add_to_cart(
        session=session,
        item_id="potion_soin_01",
        qty=2,
        item_defs=state.item_defs,
        npc_profile={"tension_level": 10},
    )
    session = engine.confirm_trade(session)

    gold_before = state.player.gold
    result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)

    assert result.get("ok") is False
    assert result.get("error") == "inventory_full"
    assert state.player.gold == gold_before
    totals = engine.inventory_totals(state)
    assert totals.get("potion_soin_01", 0) == 0


def test_sell_is_atomic_when_items_are_missing() -> None:
    state = GameState()
    state.item_defs = _build_item_defs()
    state.player.gold = 50
    state.carried.set(0, ItemStack(item_id="epee_apprenti", qty=2))

    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    session = engine.add_to_cart(
        session=session,
        item_id="epee_apprenti",
        qty=5,
        item_defs=state.item_defs,
        npc_profile={"tension_level": 20},
    )
    session = engine.confirm_trade(session)

    gold_before = state.player.gold
    result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)

    assert result.get("ok") is False
    assert result.get("error") == "insufficient_items"
    assert state.player.gold == gold_before
    totals = engine.inventory_totals(state)
    assert totals.get("epee_apprenti", 0) == 2


def test_trade_transaction_log_is_written_with_transaction_id() -> None:
    state = GameState()
    state.item_defs = _build_item_defs()
    state.player.gold = 20
    state.carried.set(0, ItemStack(item_id="epee_apprenti", qty=2))

    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    session = engine.add_to_cart(
        session=session,
        item_id="epee_apprenti",
        qty=2,
        item_defs=state.item_defs,
        npc_profile={"tension_level": 20},
    )
    session = engine.confirm_trade(session)
    result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)

    assert result.get("ok") is True
    trade_context = result.get("trade_context", {})
    tx_id = str(trade_context.get("transaction_id") or "")
    assert tx_id.startswith("tx_")

    transactions = state.gm_state.get("trade_transactions")
    assert isinstance(transactions, list)
    assert transactions
    assert str(transactions[-1].get("transaction_id") or "") == tx_id
