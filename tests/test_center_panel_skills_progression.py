from app.gamemaster.skill_manager import SkillManager
from app.ui.components.center_panel_skills_progression import (
    append_skill_usage_log,
    apply_skill_usage_progress_from_text,
    ensure_skill_state,
    find_player_skill_entry,
)
from app.ui.state.game_state import GameState


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def test_ensure_skill_state_drops_target_object_intents(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    state = GameState()
    state.skill_passive_practice = {
        "ennemi": {"count": 4, "threshold": 6, "unlocked_skill_ids": []},
        "cible": {"count": 2, "threshold": 6, "unlocked_skill_ids": []},
        "bouclier": {"count": 3, "threshold": 6, "unlocked_skill_ids": []},
    }

    ensure_skill_state(state, skill_manager=manager, safe_int=_safe_int)

    assert "ennemi" not in state.skill_passive_practice
    assert "cible" not in state.skill_passive_practice
    assert "bouclier" in state.skill_passive_practice


def test_apply_skill_usage_progress_counts_each_repeated_cast(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    catalog = manager.load_catalog()
    sample = next(iter(catalog.values()))
    state = GameState()
    state.player_skills = [
        {
            "skill_id": sample.skill_id,
            "name": sample.name,
            "category": sample.category,
            "description": sample.description,
            "difficulty": sample.difficulty,
            "rank": 1,
            "level": 1,
            "xp": 0,
            "xp_to_next": manager.xp_needed_for_next_level(1),
            "uses": 0,
        }
    ]

    def _ensure(s: GameState) -> None:
        ensure_skill_state(s, skill_manager=manager, safe_int=_safe_int)

    def _append_log(
        s: GameState,
        *,
        skill_entry: dict,
        xp_gain: int,
        levels_gained: int,
        level_after: int,
        source: str = "usage",
        reason: str = "",
    ) -> None:
        append_skill_usage_log(
            s,
            skill_entry=skill_entry,
            xp_gain=xp_gain,
            levels_gained=levels_gained,
            level_after=level_after,
            source=source,
            reason=reason,
            safe_int=_safe_int,
            utc_now_iso=lambda: "2026-02-24T00:00:00+00:00",
        )

    first_lines = apply_skill_usage_progress_from_text(
        state,
        f"J'utilise {sample.name}.",
        ensure_skill_state_fn=_ensure,
        skill_manager=manager,
        find_player_skill_entry_fn=find_player_skill_entry,
        append_skill_usage_log_fn=_append_log,
        safe_int=_safe_int,
        utc_now_iso=lambda: "2026-02-24T00:00:00+00:00",
    )
    second_lines = apply_skill_usage_progress_from_text(
        state,
        f"J'utilise {sample.name}.",
        ensure_skill_state_fn=_ensure,
        skill_manager=manager,
        find_player_skill_entry_fn=find_player_skill_entry,
        append_skill_usage_log_fn=_append_log,
        safe_int=_safe_int,
        utc_now_iso=lambda: "2026-02-24T00:00:00+00:00",
    )

    entry = state.player_skills[0]
    assert first_lines
    assert second_lines
    assert int(entry.get("uses") or 0) == 2
    assert int(entry.get("xp") or 0) > 0
