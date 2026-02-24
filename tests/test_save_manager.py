from __future__ import annotations

import json

from app.core.engine import normalize_travel_state
from app.core.save.save_manager import SaveManager
from app.ui.state.game_state import CHAT_HISTORY_MAX_ITEMS, GameState, Scene


def _build_state() -> GameState:
    state = GameState()
    state.scenes = {
        "city": Scene(
            id="city",
            title="City",
            narrator_text="A calm square.",
            map_anchor="Lumeria",
            choices=[],
        )
    }
    state.current_scene_id = "city"
    return state


def test_save_payload_persists_compact_gm_fields(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()

    state.gm_state["flags"] = {"met_ataryxia": True, "shop_discount_pct": 10}
    state.gm_state["last_trade"] = {"action": "buy", "status": "ok"}
    state.gm_state["pending_trade"] = {"action": "buy", "item_id": "epee_rouillee", "qty": 1}

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    slot_path = save_manager.slot_path(1, profile="Tester")
    payload = json.loads(slot_path.read_text(encoding="utf-8"))
    raw_state = payload.get("state", {})

    assert "gm_state" not in raw_state
    assert raw_state.get("gm_flags", {}).get("met_ataryxia") is True
    assert raw_state.get("gm_last_trade", {}).get("action") == "buy"
    assert raw_state.get("gm_pending_trade", {}).get("item_id") == "epee_rouillee"


def test_load_restores_gm_flags_and_last_trade(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    state.gm_state["flags"] = {"met_ataryxia": True}
    state.gm_state["last_trade"] = {"action": "sell", "status": "ok"}
    state.gm_state["pending_trade"] = {"action": "sell", "item_id": "pain_01", "qty": 2}

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    assert loaded.gm_state.get("flags", {}).get("met_ataryxia") is True
    assert loaded.gm_state.get("last_trade", {}).get("action") == "sell"
    assert loaded.gm_state.get("pending_trade", {}).get("item_id") == "pain_01"
    loaded.sync_trade_session()
    assert loaded.trade_session.status in {"confirming", "idle"}


def test_save_chat_list_is_capped(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()

    for i in range(CHAT_HISTORY_MAX_ITEMS + 21):
        state.push("System", f"line-{i}", count_for_media=False)

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    slot_path = save_manager.slot_path(1, profile="Tester")
    payload = json.loads(slot_path.read_text(encoding="utf-8"))
    raw_chat = payload.get("state", {}).get("chat", [])

    assert len(raw_chat) == CHAT_HISTORY_MAX_ITEMS
    assert raw_chat[0]["text"] == "line-21"


def test_save_and_load_reputation_fields(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    state.faction_reputation = {"Marchands": 7, "Peuple": -2}
    state.faction_reputation_log = [
        {
            "at": "2026-01-01T10:00:00+00:00",
            "faction": "Marchands",
            "delta": 2,
            "before": 5,
            "after": 7,
            "reason": "transaction:buy",
            "source": "trade",
        }
    ]

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    assert loaded.faction_reputation == {"Marchands": 7, "Peuple": -2}
    assert len(loaded.faction_reputation_log) == 1
    assert loaded.faction_reputation_log[0]["faction"] == "Marchands"


def test_load_legacy_npc_profile_adds_extension_defaults(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    state.npc_profiles = {
        "city__mirelle": {
            "npc_key": "city__mirelle",
            "label": "Mirelle",
            "role": "Geoliere",
            "world_anchor": {"location_id": "city", "location_title": "City"},
            "identity": {
                "first_name": "Mirelle",
                "last_name": "Korr",
                "gender": "femme",
                "species": "humain",
                "origin": "City",
            },
        }
    }
    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")

    profile = loaded.npc_profiles.get("city__mirelle")
    assert isinstance(profile, dict)
    assert str(profile.get("agenda_secret") or "").strip()
    assert str(profile.get("besoin") or "").strip()
    assert str(profile.get("peur") or "").strip()
    assert isinstance(profile.get("rival_id"), (str, type(None)))
    traits = profile.get("traits")
    assert isinstance(traits, list)
    assert len(traits) >= 3
    tension = int(profile.get("tension_level") or 0)
    assert 0 <= tension <= 100
    morale = int(profile.get("morale") or 0)
    assert 0 <= morale <= 100
    aggressiveness = int(profile.get("aggressiveness") or 0)
    assert 0 <= aggressiveness <= 100
    corruption = int(profile.get("corruption_level") or 0)
    assert 0 <= corruption <= 100
    dominance_style = str(profile.get("dominance_style") or "")
    assert dominance_style in {"soft", "manipulative", "aggressive", "cold"}
    attraction_map = profile.get("attraction_map")
    assert isinstance(attraction_map, dict)
    truth_state = profile.get("truth_state")
    assert isinstance(truth_state, dict)
    assert "active_lies" in truth_state


def test_save_and_load_world_state_and_faction_states(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    state.world_state = {
        "time_of_day": "night",
        "day_counter": 8,
        "global_tension": 72,
        "instability_level": 67,
    }
    state.faction_states = {
        "Milice Urbaine": {
            "power_level": 66,
            "brutality_index": 58,
            "corruption_index": 34,
            "relations": {"Marchands": 10},
        }
    }

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    assert isinstance(loaded.world_state, dict)
    assert int(loaded.world_state.get("global_tension") or 0) == 72
    assert int(loaded.world_state.get("instability_level") or 0) == 67
    assert isinstance(loaded.faction_states, dict)
    assert "Milice Urbaine" in loaded.faction_states


def test_load_legacy_save_without_travel_state_uses_defaults(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    slot_path = save_manager.slot_path(1, profile="Tester")
    payload = json.loads(slot_path.read_text(encoding="utf-8"))
    raw_state = payload.get("state", {}) if isinstance(payload, dict) else {}
    if isinstance(raw_state, dict):
        raw_state.pop("travel_state", None)
    slot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    loaded.sync_travel_state()
    assert loaded.travel_state.status == "idle"
    assert loaded.travel_state.total_distance == 0
    assert loaded.travel_state.pending_event is None


def test_load_legacy_save_without_trade_session_uses_defaults(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    slot_path = save_manager.slot_path(1, profile="Tester")
    payload = json.loads(slot_path.read_text(encoding="utf-8"))
    raw_state = payload.get("state", {}) if isinstance(payload, dict) else {}
    if isinstance(raw_state, dict):
        raw_state.pop("trade_session", None)
    slot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    loaded.sync_trade_session()
    assert loaded.trade_session.status == "idle"
    assert loaded.trade_session.cart == []
    assert loaded.trade_session.pending_question is None


def test_save_and_load_travel_state_roundtrip(tmp_path) -> None:
    save_manager = SaveManager(saves_dir=str(tmp_path), slot_count=3)
    state = _build_state()
    state.travel_state = normalize_travel_state(
        {
            "status": "traveling",
            "from_location_id": "city",
            "to_location_id": "temple_01",
            "route": ["Lumeria", "Dun'Khar"],
            "total_distance": 60,
            "progress": 22,
            "danger_level": 48,
            "fatigue": 31,
            "supplies_used": {"food": 2, "water": 2, "torches": 1},
            "pending_event": {
                "id": "evt_1",
                "type": "hazard",
                "short_text": "Un arbre bloque la route.",
                "choices": [],
            },
        }
    )

    save_manager.save_slot(1, state, profile="Tester", display_name="Tester")

    loaded = GameState()
    assert save_manager.load_slot(1, loaded, profile="Tester")
    loaded.sync_travel_state()
    assert loaded.travel_state.status == "traveling"
    assert loaded.travel_state.from_location_id == "city"
    assert loaded.travel_state.to_location_id == "temple_01"
    assert loaded.travel_state.progress == 22
    assert int(loaded.travel_state.supplies_used.get("food") or 0) == 2
