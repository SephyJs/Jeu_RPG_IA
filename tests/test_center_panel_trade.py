from __future__ import annotations

import asyncio

from app.core.data.item_manager import ItemDef
from app.gamemaster.economy_manager import EconomyManager
from app.ui.components.center_panel_trade import (
    apply_trade_from_player_message,
    render_trade_dialogue,
)
from app.ui.state.game_state import GameState, Scene
from app.ui.state.inventory import ItemStack


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_state() -> GameState:
    state = GameState()
    state.scenes = {
        "forge_01": Scene(
            id="forge_01",
            title="Forge - Atelier de braise",
            narrator_text="",
            map_anchor="Lumeria",
            npc_names=["Forgeron"],
            choices=[],
        )
    }
    state.current_scene_id = "forge_01"
    state.selected_npc = "Forgeron"
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
    state.carried.slots[0] = ItemStack(item_id="epee_apprenti", qty=7)
    return state


def _noop(*args, **kwargs):
    return None


def _no_rep(*args, **kwargs) -> list[str]:
    return []


def test_trade_slash_command_mode_sell_is_detected(tmp_path) -> None:
    state = _build_state()
    manager = EconomyManager(data_dir=str(tmp_path))

    outcome = apply_trade_from_player_message(
        state,
        user_text="/trade mode sell",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )

    assert outcome.get("attempted") is True
    assert outcome.get("trade_context", {}).get("status") == "started"
    assert state.trade_session.status == "selecting"
    assert state.trade_session.mode == "sell"
    assert state.trade_session.llm_enabled is False


def test_trade_full_flow_with_commands_executes_sale(tmp_path) -> None:
    state = _build_state()
    manager = EconomyManager(data_dir=str(tmp_path))

    _ = apply_trade_from_player_message(
        state,
        user_text="/trade mode sell",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    _ = apply_trade_from_player_message(
        state,
        user_text="je vends des epees d apprenti",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    assert state.trade_session.pending_question is not None

    _ = apply_trade_from_player_message(
        state,
        user_text="/trade all",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    assert state.trade_session.status == "confirming"
    assert state.trade_session.cart
    assert state.trade_session.cart[0].qty == 7

    gold_before = state.player.gold
    final = apply_trade_from_player_message(
        state,
        user_text="/trade confirm",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )

    assert final.get("attempted") is True
    assert final.get("applied") is True
    assert final.get("trade_context", {}).get("status") == "ok"
    assert state.player.gold > gold_before
    totals = manager.inventory_totals(state)
    assert totals.get("epee_apprenti", 0) == 0


def test_trade_render_done_uses_core_execution_anchor(tmp_path) -> None:
    state = _build_state()
    manager = EconomyManager(data_dir=str(tmp_path))

    _ = apply_trade_from_player_message(
        state,
        user_text="/trade mode sell",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    _ = apply_trade_from_player_message(
        state,
        user_text="je vends des epees d apprenti",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    _ = apply_trade_from_player_message(
        state,
        user_text="/trade all",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    _ = apply_trade_from_player_message(
        state,
        user_text="/trade confirm",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )

    line = asyncio.run(
        render_trade_dialogue(
            state=state,
            selected_npc="Forgeron",
            selected_profile={"role": "forgeron", "label": "Forgeron"},
            llm_client=None,
        )
    )
    assert "Vente executee:" in line
    assert "On continue le commerce ?" in line


def test_trade_render_with_llm_keeps_core_anchor_prefix(tmp_path) -> None:
    state = _build_state()
    manager = EconomyManager(data_dir=str(tmp_path))
    _ = apply_trade_from_player_message(
        state,
        user_text="/trade mode sell",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    _ = apply_trade_from_player_message(
        state,
        user_text="je vends 2 epees d apprenti",
        selected_npc="Forgeron",
        npc_key="forge_01__forgeron",
        selected_profile={"role": "forgeron", "label": "Forgeron"},
        ensure_quest_state_fn=_noop,
        ensure_item_state_fn=_noop,
        economy_manager=manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_noop,
        apply_trade_reputation_fn=_no_rep,
    )
    state.trade_session.llm_enabled = True

    class _FakeLLM:
        async def generate(self, **kwargs):
            return "Je serre les prix, mais je reste ouvert."

    line = asyncio.run(
        render_trade_dialogue(
            state=state,
            selected_npc="Forgeron",
            selected_profile={"role": "forgeron", "label": "Forgeron"},
            llm_client=_FakeLLM(),
        )
    )
    assert line.startswith("Recapitulatif:")
    assert "Total" in line
