from __future__ import annotations

from app.core.data.item_manager import ItemDef
from app.core.engine import TradeEngine
from app.ui.state.game_state import GameState
from app.ui.state.inventory import ItemStack


def _build_state_with_swords(qty: int = 8) -> GameState:
    state = GameState()
    state.item_defs = {
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
        )
    }
    state.carried.slots[0] = ItemStack(item_id="epee_apprenti", qty=max(1, qty))
    return state


def test_sell_intent_with_multiple_items_creates_pending_question() -> None:
    state = _build_state_with_swords(qty=8)
    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    inventory = engine.inventory_totals(state)

    intent = engine.detect_sell_intent("je vends des epees d apprenti", inventory, state.item_defs)
    assert intent is not None
    assert intent.item_id == "epee_apprenti"
    pending = engine.propose_bundle_options(intent, inventory)
    assert isinstance(pending, dict)
    assert pending.get("type") == "choose_quantity"
    option_ids = {str(row.get("id") or "") for row in pending.get("options", []) if isinstance(row, dict)}
    assert {"sell_all", "set_qty", "sell_one", "cancel"}.issubset(option_ids)

    session.pending_question = pending
    assert session.pending_question is not None


def test_sell_all_executes_and_removes_inventory() -> None:
    state = _build_state_with_swords(qty=8)
    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    inventory = engine.inventory_totals(state)
    intent = engine.detect_sell_intent("je vends des epees", inventory, state.item_defs)
    assert intent is not None
    pending = engine.propose_bundle_options(intent, inventory)
    assert isinstance(pending, dict)
    session.pending_question = pending

    session, _ = engine.apply_quantity_choice(
        session=session,
        option_id="sell_all",
        quantity=None,
        item_defs=state.item_defs,
        npc_profile={"tension_level": 20},
    )
    assert session.status == "confirming"
    assert session.cart
    assert session.cart[0].qty == 8

    result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)
    assert result.get("ok") is True
    assert result.get("trade_context", {}).get("status") == "ok"
    assert result.get("trade_context", {}).get("qty_done") == 8
    assert result.get("trade_context", {}).get("gold_delta") > 0
    totals = engine.inventory_totals(state)
    assert totals.get("epee_apprenti", 0) == 0


def test_buy_lot_discount_applies_for_large_qty() -> None:
    engine = TradeEngine()
    item = ItemDef(
        id="potion_soin_01",
        name="Potion de soin",
        stack_max=20,
        type="consumable",
        slot="",
        rarity="common",
        description="",
        stat_bonuses={},
        effects=[],
        value_gold=40,
    )
    negotiation = {"mood": 50, "trust": 50, "greed": 50, "rep_bonus": 0}
    unit_qty_1 = engine.price_item(item, {"tension_level": 0}, negotiation, mode="buy", qty=1)
    unit_qty_10 = engine.price_item(item, {"tension_level": 0}, negotiation, mode="buy", qty=10)
    assert unit_qty_10 < unit_qty_1


def test_negotiation_bounds_are_clamped_to_plus_minus_20() -> None:
    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    session = engine.apply_markup_discount(
        session,
        rules={"negotiated_pct": 80, "lot_discount_pct": -80, "lot_bonus_pct": 100},
    )
    assert int(session.proposed_terms.get("negotiated_pct") or 0) == 20
    assert int(session.proposed_terms.get("lot_discount_pct") or 0) == -20
    assert int(session.proposed_terms.get("lot_bonus_pct") or 0) == 20


def test_same_action_fingerprint_is_not_processed_twice() -> None:
    engine = TradeEngine()
    session = engine.start_trade("Forgeron", "sell", llm_enabled=False)
    first_turn = session.turn_id

    session, duplicate_1 = engine.run_action_guard(session, "selecting|je vends des epees")
    turn_after_first = session.turn_id
    session, duplicate_2 = engine.run_action_guard(session, "selecting|je vends des epees")

    assert duplicate_1 is False
    assert duplicate_2 is True
    assert turn_after_first > first_turn
    assert session.turn_id == turn_after_first
