from __future__ import annotations

from app.core.events import EventBus, OnTradeCompleted
from app.gamemaster.gamemaster import GameMaster
from app.gamemaster.ollama_client import OllamaClient
from app.gamemaster.runtime import get_runtime_services, reset_runtime_services


def teardown_function() -> None:
    reset_runtime_services()


def test_gamemaster_close_unsubscribes_handlers() -> None:
    bus = EventBus()
    gm = GameMaster(OllamaClient(), event_bus=bus)

    bus.publish(
        OnTradeCompleted(
            npc_key="npc_forgeron",
            npc_name="Forgeron",
            item_id="epee_apprenti",
            qty_done=1,
            gold_delta=10,
            action="sell",
        )
    )
    assert len(gm._pending_events) == 1

    gm.close()
    bus.publish(
        OnTradeCompleted(
            npc_key="npc_forgeron",
            npc_name="Forgeron",
            item_id="epee_apprenti",
            qty_done=1,
            gold_delta=10,
            action="sell",
        )
    )
    assert len(gm._pending_events) == 1


def test_runtime_services_are_singleton_and_resettable() -> None:
    reset_runtime_services()
    first = get_runtime_services()
    second = get_runtime_services()
    assert first is second

    reset_runtime_services()
    third = get_runtime_services()
    assert third is not first
