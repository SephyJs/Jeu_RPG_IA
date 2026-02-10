import asyncio

from app.core.save import SaveManager
from app.telegram.runtime import TelegramGameSession
from app.ui.state.game_state import Choice, GameState, Scene


def _build_session(state: GameState) -> TelegramGameSession:
    session = TelegramGameSession(
        chat_id=1,
        profile_key="pytest_telegram",
        profile_name="PyTest",
        slot=1,
        save_manager=SaveManager(slot_count=1),
    )
    session.state = state
    session.save = lambda: None  # type: ignore[method-assign]
    return session


def test_travel_by_index_moves_player_when_destination_is_open() -> None:
    state = GameState()
    state.world_time_minutes = 10 * 60  # 10:00
    state.scenes = {
        "start": Scene(
            id="start",
            title="Lumeria - Ruelle des Lanternes",
            narrator_text="Ataryxia : Une ruelle animee.",
            map_anchor="Lumeria",
            npc_names=["Guide"],
            choices=[Choice(id="go_shop", label="Entrer dans la boutique", next_scene_id="boutique_01")],
        ),
        "boutique_01": Scene(
            id="boutique_01",
            title="Lumeria - Boutique des Brumes",
            narrator_text="Ataryxia : Des etageres chargees de fioles.",
            map_anchor="Lumeria",
            npc_names=["Marchand"],
            choices=[],
        ),
    }
    state.current_scene_id = "start"
    state.selected_npc = "Guide"

    session = _build_session(state)
    options = session.travel_options()

    assert len(options) == 1
    assert options[0].is_open is True
    assert options[0].is_building is True

    output = asyncio.run(session.travel_by_index(0))

    assert state.current_scene_id == "boutique_01"
    assert state.world_time_minutes == (10 * 60) + 8
    assert "Vous arrivez : Lumeria - Boutique des Brumes" in output.text
    assert "PNJ actif: Marchand" in output.text


def test_travel_by_index_refuses_closed_destination() -> None:
    state = GameState()
    state.world_time_minutes = 2 * 60  # 02:00
    state.scenes = {
        "start": Scene(
            id="start",
            title="Lumeria - Ruelle des Lanternes",
            narrator_text="Ataryxia : Une ruelle silencieuse.",
            map_anchor="Lumeria",
            npc_names=["Guide"],
            choices=[Choice(id="go_shop", label="Entrer dans la boutique", next_scene_id="boutique_01")],
        ),
        "boutique_01": Scene(
            id="boutique_01",
            title="Lumeria - Boutique des Brumes",
            narrator_text="Ataryxia : Les volets sont clos.",
            map_anchor="Lumeria",
            npc_names=["Marchand"],
            choices=[],
        ),
    }
    state.current_scene_id = "start"
    state.selected_npc = "Guide"

    session = _build_session(state)
    options = session.travel_options()

    assert len(options) == 1
    assert options[0].is_open is False

    output = asyncio.run(session.travel_by_index(0))

    assert state.current_scene_id == "start"
    assert "ferme" in output.text.lower()
