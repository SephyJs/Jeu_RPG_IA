from app.ui.state.game_state import CHAT_HISTORY_MAX_ITEMS, GameState


def test_chat_history_is_capped() -> None:
    state = GameState()

    overflow = 37
    for i in range(CHAT_HISTORY_MAX_ITEMS + overflow):
        state.push("System", f"line-{i}", count_for_media=False)

    assert len(state.chat) == CHAT_HISTORY_MAX_ITEMS
    assert state.chat[0].text == f"line-{overflow}"
    assert state.chat[-1].text == f"line-{CHAT_HISTORY_MAX_ITEMS + overflow - 1}"


def test_media_counter_only_increments_when_requested() -> None:
    state = GameState()
    before = state.narrator_messages_since_last_media

    state.push("System", "no media", count_for_media=False)
    assert state.narrator_messages_since_last_media == before

    state.push("System", "with media", count_for_media=True)
    assert state.narrator_messages_since_last_media == before + 1


def test_advance_world_time_updates_world_state() -> None:
    state = GameState()
    state.world_time_minutes = 0
    state.sync_world_state()

    state.advance_world_time(6 * 60)

    assert int(state.world_state.get("day_counter") or 0) >= 1
    assert str(state.world_state.get("time_of_day") or "") in {"morning", "afternoon", "nightfall", "night"}
    assert 0 <= int(state.world_state.get("global_tension") or 0) <= 100
    assert 0 <= int(state.world_state.get("instability_level") or 0) <= 100


def test_default_travel_state_is_idle() -> None:
    state = GameState()
    state.sync_travel_state()

    assert state.travel_state.status == "idle"
    assert state.travel_state.total_distance == 0
