import json

from app.core.data.item_manager import ItemDef
from app.gamemaster.economy_manager import EconomyManager
from app.ui.state.game_state import GameState
from app.ui.state.game_state import Scene
from app.ui.state.inventory import ItemStack


def _merchant_profile() -> dict:
    return {"role": "forgeron", "label": "Forgeron"}


def test_buy_question_is_not_treated_as_player_sell(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 10
    state.item_defs = {}

    outcome = manager.process_trade_message(
        state=state,
        user_text="j'aimerais allez dans le donjon, mais je n'ai pas d'arme, est ce que vous en vendez ?",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )

    assert outcome.get("attempted") is True
    assert outcome.get("applied") is False
    ctx = outcome.get("trade_context", {})
    assert isinstance(ctx, dict)
    assert ctx.get("action") == "buy"
    assert ctx.get("status") == "offer_pending"
    text = " ".join(str(x) for x in outcome.get("system_lines", []))
    assert "Tu ne possedes pas" not in text


def test_buy_requires_confirmation_then_updates_inventory_and_gold(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 100
    state.item_defs = {}

    offer = manager.process_trade_message(
        state=state,
        user_text="combien coute une arme ?",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert offer.get("trade_context", {}).get("status") == "offer_pending"
    assert isinstance(state.gm_state.get("pending_trade"), dict)

    before_gold = state.player.gold
    confirm = manager.process_trade_message(
        state=state,
        user_text="oui je prends",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert confirm.get("applied") is True
    assert confirm.get("trade_context", {}).get("status") == "ok"
    assert state.player.gold < before_gold

    bought_item_id = str(confirm.get("trade_context", {}).get("item_id") or "")
    totals = manager.inventory_totals(state)
    assert bought_item_id in totals
    assert totals[bought_item_id] >= 1
    assert "pending_trade" not in state.gm_state


def test_sentence_with_object_pronoun_confirms_pending_buy(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 20
    state.item_defs = {}

    offer = manager.process_trade_message(
        state=state,
        user_text="j'aimerais allez dans le donjon, mais je n'ai pas d'arme, est ce que vous en vendez ?",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert offer.get("trade_context", {}).get("status") == "offer_pending"
    assert isinstance(state.gm_state.get("pending_trade"), dict)

    before_gold = state.player.gold
    confirm = manager.process_trade_message(
        state=state,
        user_text="Je vous achete l'epee",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert confirm.get("attempted") is True
    assert confirm.get("applied") is True
    assert confirm.get("trade_context", {}).get("status") == "ok"
    assert state.player.gold < before_gold
    assert "pending_trade" not in state.gm_state


def test_question_with_infinitive_buy_is_detected(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 10
    state.item_defs = {}

    outcome = manager.process_trade_message(
        state=state,
        user_text="Est il possible d'acheter l'arme a 8 or ?",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )

    assert outcome.get("attempted") is True
    assert outcome.get("applied") is False
    assert outcome.get("trade_context", {}).get("action") == "buy"
    assert outcome.get("trade_context", {}).get("status") in {"offer_pending", "insufficient_gold"}


def test_buy_price_hint_is_used_for_offer_and_confirmation(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 10
    state.item_defs = {}

    offer = manager.process_trade_message(
        state=state,
        user_text="Est il possible d'acheter l'arme a 8 or ?",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert offer.get("attempted") is True
    assert offer.get("trade_context", {}).get("status") == "offer_pending"
    assert offer.get("trade_context", {}).get("unit_price") == 8
    assert state.gm_state.get("pending_trade", {}).get("unit_price") == 8

    confirm = manager.process_trade_message(
        state=state,
        user_text="Je vous achete l'epee",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert confirm.get("applied") is True
    assert confirm.get("trade_context", {}).get("status") == "ok"
    assert confirm.get("trade_context", {}).get("qty_done") == 1
    assert confirm.get("trade_context", {}).get("unit_price") == 8
    assert state.player.gold == 2


def test_price_in_confirmation_does_not_turn_into_quantity(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 100
    state.item_defs = {}

    offer = manager.process_trade_message(
        state=state,
        user_text="je voudrais acheter une epee",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert offer.get("trade_context", {}).get("status") == "offer_pending"
    assert offer.get("trade_context", {}).get("qty_offer") == 1
    assert offer.get("trade_context", {}).get("unit_price") == 10

    confirm = manager.process_trade_message(
        state=state,
        user_text="oui a 8 or",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert confirm.get("applied") is True
    assert confirm.get("trade_context", {}).get("qty_done") == 1
    assert confirm.get("trade_context", {}).get("unit_price") == 8


def test_cancel_pending_buy_keeps_state_unchanged(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 50
    state.item_defs = {}

    _ = manager.process_trade_message(
        state=state,
        user_text="je voudrais acheter une epee",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert isinstance(state.gm_state.get("pending_trade"), dict)

    before_gold = state.player.gold
    cancel = manager.process_trade_message(
        state=state,
        user_text="non annule",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert cancel.get("trade_context", {}).get("status") == "canceled"
    assert "pending_trade" not in state.gm_state
    assert state.player.gold == before_gold
    assert manager.inventory_totals(state) == {}


def test_explicit_sell_applies_immediately(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 0
    state.item_defs = {
        "pain_01": ItemDef(
            id="pain_01",
            name="Pain",
            stack_max=20,
            type="consumable",
            slot="",
            rarity="common",
            description="Pain simple.",
            stat_bonuses={},
            effects=[],
            value_gold=6,
        )
    }
    state.carried.slots[0] = ItemStack(item_id="pain_01", qty=2)

    outcome = manager.process_trade_message(
        state=state,
        user_text="je vends 1 pain",
        selected_npc_name="Marchande",
        selected_npc_profile={"role": "marchande", "label": "Marchande"},
        item_defs=state.item_defs,
    )

    assert outcome.get("applied") is True
    assert outcome.get("trade_context", {}).get("action") == "sell"
    assert outcome.get("trade_context", {}).get("status") == "ok"
    assert state.player.gold > 0
    assert manager.inventory_totals(state).get("pain_01") == 1


def test_sell_intent_with_je_viens_is_detected(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 0
    state.item_defs = {
        "epee_apprenti": ItemDef(
            id="epee_apprenti",
            name="Epee d'apprenti",
            stack_max=1,
            type="weapon",
            slot="weapon",
            rarity="common",
            description="Lame d'entrainement.",
            stat_bonuses={},
            effects=[],
            value_gold=10,
        )
    }
    state.carried.slots[0] = ItemStack(item_id="epee_apprenti", qty=1)

    outcome = manager.process_trade_message(
        state=state,
        user_text="bonjour, je viens vous vendre des épées d'apprenti",
        selected_npc_name="Forgeron",
        selected_npc_profile={"role": "forgeron", "label": "Forgeron"},
        item_defs=state.item_defs,
    )

    assert outcome.get("attempted") is True
    assert outcome.get("applied") is False
    assert outcome.get("trade_context", {}).get("action") == "sell"
    assert outcome.get("trade_context", {}).get("status") == "offer_pending"
    assert isinstance(state.gm_state.get("pending_trade"), dict)


def test_pending_trade_without_confirm_returns_reminder(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 50
    state.item_defs = {}

    offer = manager.process_trade_message(
        state=state,
        user_text="je voudrais acheter une epee",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )
    assert offer.get("trade_context", {}).get("status") == "offer_pending"
    assert isinstance(state.gm_state.get("pending_trade"), dict)

    follow_up = manager.process_trade_message(
        state=state,
        user_text="5 pieces d or par epee cela vous convient",
        selected_npc_name="Forgeron",
        selected_npc_profile=_merchant_profile(),
        item_defs=state.item_defs,
    )

    assert follow_up.get("attempted") is True
    assert follow_up.get("applied") is False
    assert follow_up.get("trade_context", {}).get("status") == "offer_pending"
    assert isinstance(state.gm_state.get("pending_trade"), dict)
    lines = " ".join(str(x) for x in follow_up.get("system_lines", []))
    assert "offre en attente" in lines.lower()


def test_named_merchant_stock_limits_buy_and_persists(tmp_path) -> None:
    merchants_dir = tmp_path / "merchants"
    merchants_dir.mkdir(parents=True, exist_ok=True)
    (merchants_dir / "marchand_local.json").write_text(
        json.dumps(
            {
                "id": "marchand_local",
                "name": "Marchand local",
                "location": {"location_id": "boutique_01", "location_title": "La Boutique"},
                "npc_profile": {"label": "Marchand local", "role": "Marchand"},
                "inventory": [{"item_id": "pain_01", "stock": 1, "price_multiplier": 1.0}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manager = EconomyManager(data_dir=str(tmp_path))
    state = GameState()
    state.player.gold = 100
    state.scenes = {
        "boutique_01": Scene(
            id="boutique_01",
            title="Lumeria - Boutique",
            narrator_text="Ataryxia : Etals en bois.",
            map_anchor="Lumeria",
            npc_names=["Marchand local"],
            choices=[],
        )
    }
    state.current_scene_id = "boutique_01"
    state.item_defs = {
        "pain_01": ItemDef(
            id="pain_01",
            name="Pain",
            stack_max=20,
            type="consumable",
            slot="",
            rarity="common",
            description="Pain simple.",
            stat_bonuses={},
            effects=[],
            value_gold=6,
        )
    }

    first = manager.process_trade_message(
        state=state,
        user_text="j'achete 2 pain",
        selected_npc_name="Marchand local",
        selected_npc_profile={"role": "marchand", "label": "Marchand local"},
        item_defs=state.item_defs,
    )
    assert first.get("applied") is True
    assert first.get("trade_context", {}).get("qty_done") == 1
    assert manager.inventory_totals(state).get("pain_01") == 1

    second = manager.process_trade_message(
        state=state,
        user_text="j'achete 1 pain",
        selected_npc_name="Marchand local",
        selected_npc_profile={"role": "marchand", "label": "Marchand local"},
        item_defs=state.item_defs,
    )
    assert second.get("applied") is False
    assert second.get("trade_context", {}).get("status") == "out_of_stock"
