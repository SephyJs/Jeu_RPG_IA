from __future__ import annotations

import asyncio

import pytest

from app.ui.components.gameplay_hooks import (
    GameplayHookUnavailable,
    advance_dungeon,
    enter_dungeon,
    explore_new_location,
    refresh_quests_and_story,
    register_gameplay_hooks,
    reset_gameplay_hooks,
)
from app.ui.state.game_state import GameState


def teardown_function() -> None:
    reset_gameplay_hooks()


def test_refresh_requires_registered_callbacks() -> None:
    state = GameState()
    with pytest.raises(GameplayHookUnavailable):
        refresh_quests_and_story(state)


def test_registered_sync_callbacks_are_called() -> None:
    state = GameState()
    seen: list[str] = []

    def _update(_state: GameState) -> None:
        seen.append("quests")

    def _progress(_state: GameState) -> None:
        seen.append("story")

    def _explore(_state: GameState, _on_change) -> None:
        seen.append("explore")

    register_gameplay_hooks(
        update_quests_and_notify=_update,
        apply_world_and_story_progress=_progress,
        explore_new_location=_explore,
    )

    refresh_quests_and_story(state)
    explore_new_location(state, lambda: None)

    assert seen == ["quests", "story", "explore"]


def test_registered_async_callbacks_are_called() -> None:
    state = GameState()
    seen: list[str] = []

    async def _enter(_state: GameState, _on_change) -> None:
        seen.append("enter")

    async def _advance(_state: GameState, _on_change) -> None:
        seen.append("advance")

    register_gameplay_hooks(
        enter_dungeon=_enter,
        advance_dungeon=_advance,
    )

    asyncio.run(enter_dungeon(state, lambda: None))
    asyncio.run(advance_dungeon(state, lambda: None))

    assert seen == ["enter", "advance"]
