import asyncio
import json

from app.gamemaster.player_sheet_manager import PlayerSheetManager


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def generate(self, **_kwargs) -> str:
        return json.dumps(self._payload, ensure_ascii=False)


def test_progression_drops_trade_hallucination_when_trade_not_applied() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 3,
                "reason": "vous avez achete une arme",
                "stat_deltas": {"force": 1, "pv_max": 2},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Je vous achete l'epee",
            npc_reply="D'accord.",
            narration="La discussion continue.",
            trade_context={"action": "buy", "status": "offer_pending"},
            trade_applied=False,
        )
    )

    assert progression["xp_gain"] == 0
    assert progression["reason"] == ""
    assert all(int(v) == 0 for v in progression.get("stat_deltas", {}).values())


def test_progression_keeps_trade_reason_when_trade_applied() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 2,
                "reason": "achat effectue proprement",
                "stat_deltas": {"charisme": 1},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Oui je prends",
            npc_reply="Parfait.",
            narration="Transaction finalisee.",
            trade_context={"action": "buy", "status": "ok", "qty_done": 1},
            trade_applied=True,
        )
    )

    assert progression["xp_gain"] == 2
    assert progression["reason"] == "achat effectue proprement"
    assert progression.get("stat_deltas", {}).get("charisme") == 1


def test_progression_drops_question_reason_if_user_message_is_not_question() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 2,
                "reason": "Tu as pose une question interessante a Sephy",
                "stat_deltas": {"force": 1},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Je m'avance vers la forge.",
            npc_reply="Le forgeron te regarde en silence.",
            narration="Le feu de la forge gronde.",
            trade_context={},
            trade_applied=False,
            player_name="Sephy",
            selected_npc_name="Forgeron",
        )
    )

    assert progression["xp_gain"] == 0
    assert progression["reason"] == ""
    assert all(int(v) == 0 for v in progression.get("stat_deltas", {}).values())


def test_progression_drops_reason_targeting_player_name() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 1,
                "reason": "Discussion utile avec Sephy",
                "stat_deltas": {"charisme": 1},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Je me prepare calmement.",
            npc_reply="Le forgeron hoche la tete.",
            narration="Le marteau frappe l'enclume.",
            trade_context={},
            trade_applied=False,
            player_name="Sephy",
            selected_npc_name="Forgeron",
        )
    )

    assert progression["xp_gain"] == 0
    assert progression["reason"] == ""
    assert all(int(v) == 0 for v in progression.get("stat_deltas", {}).values())


def test_progression_heal_request_blocks_max_stat_gain_and_sets_full_heal_flag() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 1,
                "reason": "La pretresse te soigne.",
                "restore_hp_to_full": False,
                "stat_deltas": {"sagesse": 1, "pv_max": 3, "mana_max": 2},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="J'ai besoin de soins au temple",
            npc_reply="Je peux te soigner ici, au sanctuaire.",
            narration="La lumiere du temple apaise tes blessures.",
            trade_context={},
            trade_applied=False,
            selected_npc_name="Pretresse",
        )
    )

    assert progression["restore_hp_to_full"] is True
    assert progression.get("stat_deltas", {}).get("pv_max") == 0
    assert progression.get("stat_deltas", {}).get("mana_max") == 0
    assert progression.get("stat_deltas", {}).get("sagesse") == 1


def test_apply_progression_update_refills_hp_when_heal_flag_is_set() -> None:
    manager = PlayerSheetManager(None)
    sheet = manager.create_initial_sheet()
    stats = sheet.get("stats", {})
    assert isinstance(stats, dict)
    stats["pv"] = 6
    stats["pv_max"] = 20

    updated_sheet, lines = manager.apply_progression_update(
        sheet,
        {
            "xp_gain": 0,
            "reason": "soin de temple",
            "restore_hp_to_full": True,
            "stat_deltas": {},
        },
    )

    updated_stats = updated_sheet.get("stats", {})
    assert isinstance(updated_stats, dict)
    assert updated_stats.get("pv") == 20
    assert updated_stats.get("pv_max") == 20
    assert any("Soin du temple" in line for line in lines)


def test_progression_keeps_pv_max_outside_heal_service_context() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 2,
                "reason": "Entrainement physique",
                "stat_deltas": {"force": 1, "pv_max": 2},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Je m'entraine au combat",
            npc_reply="Continue, tu gagnes en endurance.",
            narration="La seance est intense.",
            trade_context={},
            trade_applied=False,
            selected_npc_name="Maitre d'armes",
        )
    )

    assert progression["restore_hp_to_full"] is False
    assert progression.get("stat_deltas", {}).get("pv_max") == 2


def test_progression_heal_action_converts_pv_max_delta_to_restore_hp() -> None:
    manager = PlayerSheetManager(
        _FakeLLM(
            {
                "xp_gain": 2,
                "reason": "Votre soin a permis de guerir vos blessures.",
                "restore_hp_to_full": False,
                "stat_deltas": {"pv_max": 4, "mana_max": 1},
            }
        )
    )
    sheet = manager.create_initial_sheet()

    progression = asyncio.run(
        manager.infer_progression_update(
            sheet=sheet,
            user_message="Je lance un soin sur moi.",
            npc_reply="Ta blessure se referme.",
            narration="L'energie chaude apaise la douleur.",
            trade_context={},
            trade_applied=False,
            selected_npc_name="Compagnon",
        )
    )

    assert progression["restore_hp_to_full"] is False
    assert progression.get("restore_hp") == 4
    assert progression.get("stat_deltas", {}).get("pv_max") == 0
    assert progression.get("stat_deltas", {}).get("mana_max") == 0


def test_apply_progression_update_applies_partial_restore_hp_without_pv_max_gain() -> None:
    manager = PlayerSheetManager(None)
    sheet = manager.create_initial_sheet()
    stats = sheet.get("stats", {})
    assert isinstance(stats, dict)
    stats["pv"] = 6
    stats["pv_max"] = 20

    updated_sheet, lines = manager.apply_progression_update(
        sheet,
        {
            "xp_gain": 0,
            "reason": "sort de soin",
            "restore_hp_to_full": False,
            "restore_hp": 4,
            "stat_deltas": {"pv_max": 0},
        },
    )

    updated_stats = updated_sheet.get("stats", {})
    assert isinstance(updated_stats, dict)
    assert updated_stats.get("pv") == 10
    assert updated_stats.get("pv_max") == 20
    assert any("Soin +4 PV" in line for line in lines)
