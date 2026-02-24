from app.core.events import EventBus, OnQuestUpdated


def test_event_bus_publish_subscribe_and_unsubscribe() -> None:
    bus = EventBus()
    seen: list[str] = []

    def _handler(event: OnQuestUpdated) -> None:
        seen.append(f"{event.quest_id}:{event.status}")

    unsubscribe = bus.subscribe(OnQuestUpdated, _handler)
    bus.publish(OnQuestUpdated(quest_id="quest_001", status="completed"))
    unsubscribe()
    bus.publish(OnQuestUpdated(quest_id="quest_002", status="failed"))

    assert seen == ["quest_001:completed"]
