from __future__ import annotations

from app.gamemaster.npc_manager import npc_profile_key
from app.ui.components.center_panel_quests import (
    apply_quest_timeouts,
    build_runtime_quest,
    choose_quest_branch,
)
from app.ui.state.game_state import GameState, Scene


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _now() -> str:
    return "2026-02-12T18:00:00+00:00"


def test_branch_choice_updates_objective_and_rewards() -> None:
    state = GameState()
    state.scenes = {
        "village_center_01": Scene(
            id="village_center_01",
            title="Lumeria - Place",
            narrator_text="Ataryxia : Le coeur de la ville.",
            map_anchor="Lumeria",
            npc_names=["Marchand local"],
            choices=[],
        )
    }
    state.current_scene_id = "village_center_01"
    state.world_time_minutes = 200

    payload = {
        "title": "Mission test",
        "description": "Un choix moral est propose.",
        "objective_type": "send_messages",
        "target_count": 3,
        "target_npc": "Marchand local",
        "target_anchor": "Lumeria",
        "progress_hint": "Parle a des habitants.",
        "quest_intro": "Choisis ta voie.",
        "rewards": {"gold": 10, "items": [], "shop_discount_pct": 0, "temple_heal_bonus": 0},
        "deadline_hours": 24,
        "failure_consequence": "Le donneur se sent trahi.",
        "branching": {
            "prompt": "Approche choisie ?",
            "options": [
                {
                    "id": "diplomatie",
                    "label": "Voie diplomatique",
                    "objective_delta": 1,
                    "rewards_bonus": {"gold": 0, "items": [], "shop_discount_pct": 2, "temple_heal_bonus": 0},
                    "reputation": {"Habitants": 2},
                },
                {
                    "id": "coercition",
                    "label": "Voie coercitive",
                    "objective_delta": -1,
                    "rewards_bonus": {"gold": 8, "items": [], "shop_discount_pct": 0, "temple_heal_bonus": 0},
                    "reputation": {"Habitants": -2},
                },
            ],
        },
    }

    quest = build_runtime_quest(
        state,
        quest_payload=payload,
        npc_name="Marchand local",
        npc_key=npc_profile_key("Marchand local", "village_center_01"),
        scene=state.current_scene(),
        safe_int=_safe_int,
        utc_now_iso=_now,
        npc_profile_key=npc_profile_key,
    )
    state.quests.append(quest)

    ok, _ = choose_quest_branch(
        state,
        quest_id=str(quest.get("id") or ""),
        option_id="diplomatie",
        safe_int=_safe_int,
        utc_now_iso=_now,
    )

    assert ok is True
    assert quest["objective"]["target"] == 4
    assert quest["rewards"]["gold"] == 10
    assert quest["rewards"]["shop_discount_pct"] == 2
    assert quest["branching"]["selected_option_id"] == "diplomatie"


def test_quest_deadline_marks_quest_as_failed() -> None:
    state = GameState()
    state.scenes = {
        "village_center_01": Scene(
            id="village_center_01",
            title="Lumeria - Place",
            narrator_text="Ataryxia : Le coeur de la ville.",
            map_anchor="Lumeria",
            npc_names=["Marchand local"],
            choices=[],
        )
    }
    state.current_scene_id = "village_center_01"
    state.world_time_minutes = 10

    quest = {
        "id": "quest_9999",
        "title": "Mission en retard",
        "status": "in_progress",
        "deadline_world_time_minutes": 12,
        "failure_consequence": "Le contact disparait.",
        "updated_at": "",
        "failed_at": "",
    }
    state.quests = [quest]
    state.world_time_minutes = 20

    lines = apply_quest_timeouts(
        state,
        safe_int=_safe_int,
        utc_now_iso=_now,
    )

    assert quest["status"] == "failed"
    assert quest["failed_at"] == _now()
    assert lines
    assert "Mission en retard" in lines[0]


def test_branch_choice_accepts_loose_quest_and_option_tokens() -> None:
    state = GameState()
    state.scenes = {
        "village_center_01": Scene(
            id="village_center_01",
            title="Lumeria - Place",
            narrator_text="Ataryxia : Le coeur de la ville.",
            map_anchor="Lumeria",
            npc_names=["Marchand local"],
            choices=[],
        )
    }
    state.current_scene_id = "village_center_01"
    state.world_time_minutes = 200

    payload = {
        "title": "Mission test 2",
        "description": "Un choix moral est propose.",
        "objective_type": "send_messages",
        "target_count": 3,
        "target_npc": "Marchand local",
        "target_anchor": "Lumeria",
        "progress_hint": "Parle a des habitants.",
        "quest_intro": "Choisis ta voie.",
        "rewards": {"gold": 10, "items": [], "shop_discount_pct": 0, "temple_heal_bonus": 0},
        "branching": {
            "prompt": "Approche choisie ?",
            "options": [
                {
                    "id": "diplomatie",
                    "label": "Voie diplomatique",
                    "objective_delta": 1,
                    "rewards_bonus": {"gold": 0, "items": [], "shop_discount_pct": 2, "temple_heal_bonus": 0},
                    "reputation": {"Habitants": 2},
                },
                {
                    "id": "coercition",
                    "label": "Voie coercitive",
                    "objective_delta": -1,
                    "rewards_bonus": {"gold": 8, "items": [], "shop_discount_pct": 0, "temple_heal_bonus": 0},
                    "reputation": {"Habitants": -2},
                },
            ],
        },
    }

    quest = build_runtime_quest(
        state,
        quest_payload=payload,
        npc_name="Marchand local",
        npc_key=npc_profile_key("Marchand local", "village_center_01"),
        scene=state.current_scene(),
        safe_int=_safe_int,
        utc_now_iso=_now,
        npc_profile_key=npc_profile_key,
    )
    state.quests.append(quest)

    ok, message = choose_quest_branch(
        state,
        quest_id="1",
        option_id="Voie diplomatique.",
        safe_int=_safe_int,
        utc_now_iso=_now,
    )

    assert ok is True
    assert "Branche choisie" in message
    assert quest["branching"]["selected_option_id"] == "diplomatie"
