from __future__ import annotations

from app.core.engine import TravelEngine, normalize_travel_state


def test_start_travel_initializes_state() -> None:
    engine = TravelEngine(seed=7)

    state = engine.start_travel(
        "city",
        "temple",
        {
            "route": ["Lumeria", "Dun'Khar", "Temple Ensablé"],
            "total_distance": 90,
            "danger_level": 44,
        },
    )

    assert state.status == "traveling"
    assert state.from_location_id == "city"
    assert state.to_location_id == "temple"
    assert state.total_distance == 90
    assert state.progress == 0
    assert state.danger_level == 44
    assert isinstance(state.route, list)


def test_tick_travel_increases_progress_and_consumes_supplies() -> None:
    engine = TravelEngine(seed=11)
    initial = engine.start_travel(
        "city",
        "temple",
        {
            "route": ["Lumeria", "Dun'Khar"],
            "total_distance": 60,
            "danger_level": 18,
        },
    )
    initial_progress = int(initial.progress)

    updated, _ = engine.tick_travel(
        {"time_of_day": "afternoon", "global_tension": 10, "instability_level": 10},
        {"world_time_minutes": 420},
        action="continue",
    )

    assert updated.progress > initial_progress
    assert int(updated.supplies_used.get("food") or 0) >= 1
    assert int(updated.supplies_used.get("water") or 0) >= 1


def test_pending_event_blocks_progression_until_choice() -> None:
    engine = TravelEngine(seed=13)
    state = engine.start_travel("city", "temple", {"total_distance": 50})
    state.pending_event = {
        "id": "evt_test",
        "type": "hazard",
        "short_text": "Un obstacle bloque la route.",
        "choices": [
            {
                "id": "detour",
                "text": "Contourner",
                "risk_tag": "moyen",
                "effects_hint": "Plus lent",
                "state_patch": {},
                "travel_patch": {},
            }
        ],
    }
    state.progress = 12
    engine.load_state(state)

    updated, event = engine.tick_travel(
        {"time_of_day": "night", "global_tension": 50, "instability_level": 40},
        {"world_time_minutes": 500},
        action="continue",
    )

    assert updated.progress == 12
    assert isinstance(event, dict)
    assert str(event.get("id") or "") == "evt_test"


def test_arrive_returns_location_patch_and_resets_to_idle() -> None:
    engine = TravelEngine(seed=17)
    state = engine.start_travel("city", "temple", {"total_distance": 30})
    state.progress = 30
    state.status = "arrived"
    engine.load_state(state)

    patch = engine.arrive()
    after = engine.export_state()

    assert str(patch.get("location_id") or "") == "temple"
    assert after.status == "idle"
    assert after.total_distance == 0


def test_abort_travel_resets_status_to_idle() -> None:
    engine = TravelEngine(seed=19)
    engine.start_travel("city", "temple", {"total_distance": 40})

    aborted = engine.abort_travel()

    assert aborted.status == "idle"


def test_resolve_choice_applies_patch_and_clears_pending_event() -> None:
    engine = TravelEngine(seed=23)
    state = engine.start_travel("city", "temple", {"total_distance": 80})
    state.pending_event = {
        "id": "evt_choice",
        "type": "encounter",
        "short_text": "Une caravane bloque la route.",
        "choices": [
            {
                "id": "trade",
                "text": "Payer",
                "risk_tag": "faible",
                "effects_hint": "Moins de danger",
                "state_patch": {"player": {"gold_delta": -8}},
                "travel_patch": {"danger_delta": -5, "progress_delta": 2},
            }
        ],
    }
    engine.load_state(normalize_travel_state(state))

    patch = engine.resolve_travel_choice("trade")
    updated = engine.export_state()

    assert patch.get("player", {}).get("gold_delta") == -8
    assert updated.pending_event is None
    assert updated.progress >= 2


def test_event_cooldown_blocks_consecutive_route_events() -> None:
    engine = TravelEngine(seed=31)
    engine.start_travel("city", "temple", {"total_distance": 120})

    calls = {"count": 0}

    def _fake_event(**kwargs):
        calls["count"] += 1
        return {
            "id": "evt_test",
            "type": "encounter",
            "short_text": "Une rencontre bloque la route.",
            "choices": [],
            "state_patch": {},
        }

    engine._maybe_route_event = _fake_event  # noqa: SLF001 - targeted behavior test

    first_state, first_event = engine.tick_travel(
        {"time_of_day": "day", "global_tension": 20, "instability_level": 20},
        {"world_time_minutes": 120},
        action="continue",
    )
    assert isinstance(first_event, dict)
    assert first_state.event_cooldown_ticks >= 1
    _ = engine.resolve_travel_choice("opt_1")  # no-op, puis on force la reprise de route
    engine.state.pending_event = None

    second_state, second_event = engine.tick_travel(
        {"time_of_day": "day", "global_tension": 20, "instability_level": 20},
        {"world_time_minutes": 126},
        action="continue",
    )
    assert second_event is None
    assert calls["count"] == 1
    assert second_state.event_cooldown_ticks >= 0


def test_event_weights_use_bias_and_reduce_recent_repeats() -> None:
    engine = TravelEngine(seed=37)
    engine.start_travel("city", "temple", {"total_distance": 100})
    engine.state.recent_event_types = ["ambush", "hazard"]

    baseline = engine._event_weights(  # noqa: SLF001 - targeted behavior test
        world_tension=75,
        world_instability=72,
        time_of_day="night",
        world_bias={},
    )
    weights = engine._event_weights(  # noqa: SLF001 - targeted behavior test
        world_tension=75,
        world_instability=72,
        time_of_day="night",
        world_bias={"discovery": 40, "ambush": -35},
    )
    assert weights["discovery"] > baseline["discovery"]
    assert weights["ambush"] < weights["encounter"]
    assert weights["hazard"] >= 1
