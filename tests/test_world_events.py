from __future__ import annotations

from app.gamemaster.world_events import apply_world_time_events, try_resolve_nearby_world_event
from app.gamemaster.world_time import day_index
from app.ui.state.game_state import GameState


def test_world_event_updates_trade_and_travel_bias() -> None:
    state = GameState()
    state.world_time_minutes = 3 * 24 * 60 + 8 * 60
    state.world_state["instability_level"] = 62
    state.world_state["global_tension"] = 58

    lines = apply_world_time_events(state, utc_now_iso=lambda: "2026-02-24T10:00:00Z")
    assert any("Evenement du jour" in line for line in lines)

    flags = state.gm_state.get("flags", {})
    assert isinstance(flags, dict)
    assert isinstance(flags.get("world_event_travel_bias"), dict)
    assert isinstance(state.world_state.get("travel_event_bias"), dict)
    assert isinstance(state.world_state.get("market_price_mod_pct"), int)


def test_world_event_does_not_repeat_same_id_on_consecutive_days() -> None:
    state = GameState()
    state.world_time_minutes = 4 * 24 * 60 + 8 * 60
    state.world_state["instability_level"] = 40
    state.world_state["global_tension"] = 42

    apply_world_time_events(state, utc_now_iso=lambda: "2026-02-24T10:00:00Z")
    first_id = str(state.gm_state.get("flags", {}).get("world_event_name") or "")
    assert first_id

    state.advance_world_time(24 * 60)
    apply_world_time_events(state, utc_now_iso=lambda: "2026-02-25T10:00:00Z")
    second_id = str(state.gm_state.get("flags", {}).get("world_event_name") or "")
    assert second_id
    assert second_id != first_id


def test_world_event_notice_hidden_when_far(monkeypatch) -> None:
    state = GameState()
    state.world_time_minutes = 6 * 24 * 60 + 8 * 60

    def _far_incident(*_args, **_kwargs) -> dict:
        return {
            "id": "test_far",
            "anchor": "Ile d'Astra'Nyx",
            "label": "Incident lointain.",
            "success_text": "OK",
            "failure_text": "KO",
            "resolved": False,
            "dismissed": False,
            "day": day_index(state.world_time_minutes),
        }

    monkeypatch.setattr("app.gamemaster.world_events._event_incident_for_day", _far_incident)
    lines = apply_world_time_events(
        state,
        utc_now_iso=lambda: "2026-02-24T10:00:00Z",
        current_anchor="Lumeria",
        in_dungeon=False,
    )
    assert not any("Evenement du jour" in line for line in lines)
    assert not any("Tu peux intervenir" in line for line in lines)


def test_world_event_notice_and_intervention_when_near(monkeypatch) -> None:
    state = GameState()
    state.world_time_minutes = 7 * 24 * 60 + 8 * 60

    def _near_incident(*_args, **_kwargs) -> dict:
        return {
            "id": "test_near",
            "anchor": "Lumeria",
            "label": "Incident proche.",
            "success_text": "Situation stabilisee.",
            "failure_text": "Situation degradee.",
            "resolved": False,
            "dismissed": False,
            "day": day_index(state.world_time_minutes),
        }

    monkeypatch.setattr("app.gamemaster.world_events._event_incident_for_day", _near_incident)
    lines = apply_world_time_events(
        state,
        utc_now_iso=lambda: "2026-02-24T10:00:00Z",
        current_anchor="Lumeria",
        in_dungeon=False,
    )
    assert any("Evenement du jour" in line for line in lines)
    assert any("Tu peux intervenir" in line for line in lines)

    before_tension = int(state.world_state.get("global_tension") or 0)
    before_instability = int(state.world_state.get("instability_level") or 0)
    out = try_resolve_nearby_world_event(state, "j'interviens", utc_now_iso=lambda: "2026-02-24T10:05:00Z")

    assert out
    flags = state.gm_state.get("flags", {})
    incident = flags.get("world_event_incident") if isinstance(flags, dict) else {}
    assert isinstance(incident, dict) and bool(incident.get("resolved", False))
    after_tension = int(state.world_state.get("global_tension") or 0)
    after_instability = int(state.world_state.get("instability_level") or 0)
    assert (after_tension != before_tension) or (after_instability != before_instability)
