from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.ui.state.game_state import GameState


OnChange = Callable[[], None]
StateCallback = Callable[[GameState], None]
SyncCallback = Callable[[GameState, OnChange], None]
AsyncCallback = Callable[[GameState, OnChange], Awaitable[None]]


class GameplayHookUnavailable(RuntimeError):
    pass


@dataclass
class GameplayHooks:
    update_quests_and_notify: StateCallback | None = None
    apply_world_and_story_progress: StateCallback | None = None
    explore_new_location: SyncCallback | None = None
    enter_dungeon: AsyncCallback | None = None
    advance_dungeon: AsyncCallback | None = None
    leave_dungeon: SyncCallback | None = None


_hooks = GameplayHooks()


def register_gameplay_hooks(
    *,
    update_quests_and_notify: StateCallback | None = None,
    apply_world_and_story_progress: StateCallback | None = None,
    explore_new_location: SyncCallback | None = None,
    enter_dungeon: AsyncCallback | None = None,
    advance_dungeon: AsyncCallback | None = None,
    leave_dungeon: SyncCallback | None = None,
) -> None:
    if callable(update_quests_and_notify):
        _hooks.update_quests_and_notify = update_quests_and_notify
    if callable(apply_world_and_story_progress):
        _hooks.apply_world_and_story_progress = apply_world_and_story_progress
    if callable(explore_new_location):
        _hooks.explore_new_location = explore_new_location
    if callable(enter_dungeon):
        _hooks.enter_dungeon = enter_dungeon
    if callable(advance_dungeon):
        _hooks.advance_dungeon = advance_dungeon
    if callable(leave_dungeon):
        _hooks.leave_dungeon = leave_dungeon


def reset_gameplay_hooks() -> None:
    _hooks.update_quests_and_notify = None
    _hooks.apply_world_and_story_progress = None
    _hooks.explore_new_location = None
    _hooks.enter_dungeon = None
    _hooks.advance_dungeon = None
    _hooks.leave_dungeon = None


def _require_callback(callback: object, *, label: str) -> Callable:
    if callable(callback):
        return callback
    raise GameplayHookUnavailable(f"Hook indisponible: {label}")


def refresh_quests_and_story(state: GameState) -> None:
    refresh_quests = _require_callback(_hooks.update_quests_and_notify, label="update_quests_and_notify")
    refresh_story = _require_callback(_hooks.apply_world_and_story_progress, label="apply_world_and_story_progress")
    refresh_quests(state)
    refresh_story(state)


def explore_new_location(state: GameState, on_change: OnChange) -> None:
    callback = _require_callback(_hooks.explore_new_location, label="explore_new_location")
    callback(state, on_change)


async def enter_dungeon(state: GameState, on_change: OnChange) -> None:
    callback = _require_callback(_hooks.enter_dungeon, label="enter_dungeon")
    await callback(state, on_change)


async def advance_dungeon(state: GameState, on_change: OnChange) -> None:
    callback = _require_callback(_hooks.advance_dungeon, label="advance_dungeon")
    await callback(state, on_change)


def leave_dungeon(state: GameState, on_change: OnChange) -> None:
    callback = _require_callback(_hooks.leave_dungeon, label="leave_dungeon")
    callback(state, on_change)
