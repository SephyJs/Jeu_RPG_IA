from __future__ import annotations

from app.gamemaster.economy_manager import EconomyManager
from app.ui.state.game_state import GameState


def test_world_event_restock_bonus_increases_refill(tmp_path) -> None:
    manager = EconomyManager(data_dir=str(tmp_path))
    manager._merchant_catalog_cache = {  # noqa: SLF001 - targeted unit test
        "marchand_local": {
            "id": "marchand_local",
            "inventory": {"pain_01": {"stock": 9}},
        }
    }

    state = GameState()
    flags = state.gm_state.setdefault("flags", {})
    flags["merchant_runtime_stock"] = {"marchand_local": {"pain_01": 3}}
    state.world_state["merchant_restock_bonus_pct"] = 60
    state.world_time_minutes = 7 * 24 * 60

    manager._restock_merchants_if_needed(state)  # noqa: SLF001 - targeted behavior test

    stock = flags["merchant_runtime_stock"]["marchand_local"]["pain_01"]
    assert stock >= 8
