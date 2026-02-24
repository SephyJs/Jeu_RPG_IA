from __future__ import annotations

from app.gamemaster.story_manager import progress_main_story, story_status_text
from app.ui.state.game_state import GameState


def test_story_progress_unlocks_multiple_chapters() -> None:
    state = GameState()
    state.player_sheet_ready = True
    state.npc_dialogue_counts = {"npc_1": 2}
    state.quests = [{"id": "quest_0001", "status": "completed"}, {"id": "quest_0002", "status": "completed"}, {"id": "quest_0003", "status": "completed"}]
    state.quest_counters["dungeon_floors_cleared"] = 5
    state.faction_reputation = {
        "Marchands": 22,
        "Aventuriers": 35,
    }

    lines = progress_main_story(state, utc_now_iso=lambda: "2026-02-12T18:00:00+00:00")

    assert lines
    assert "Chapitre I" in lines[0]
    assert "Chapitre VII" in lines[-1]
    assert state.player.gold > 10
    assert state.skill_points >= 1


def test_story_status_text_reports_completion() -> None:
    state = GameState()
    state.player_sheet_ready = True
    state.npc_dialogue_counts = {"npc_1": 2}
    state.quests = [{"id": "quest_0001", "status": "completed"}, {"id": "quest_0002", "status": "completed"}, {"id": "quest_0003", "status": "completed"}]
    state.quest_counters["dungeon_floors_cleared"] = 5
    state.faction_reputation = {"Marchands": 30, "Aventuriers": 30}
    _ = progress_main_story(state, utc_now_iso=lambda: "2026-02-12T18:00:00+00:00")

    status = story_status_text(state)
    assert status == "Arc principal termine."
