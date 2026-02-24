from app.gamemaster.state_patch import apply_patch, sanitize_state_patch


def test_apply_patch_accepts_whitelisted_fields() -> None:
    state = {"flags": {"old": True}, "vars": {"coins": 10}, "location": "Ancien lieu"}
    patch = {
        "flags": {"met_npc": True},
        "vars": {"coins": 22, "delta": -3},
        "location": "Nouveau lieu",
        "location_id": "new_place_01",
        "map_anchor": "Lumeria",
    }

    apply_patch(state, patch)

    assert state["flags"]["old"] is True
    assert state["flags"]["met_npc"] is True
    assert state["vars"]["coins"] == 22
    assert state["vars"]["delta"] == -3
    assert state["location"] == "Nouveau lieu"
    assert state["location_id"] == "new_place_01"
    assert state["map_anchor"] == "Lumeria"


def test_sanitize_state_patch_rejects_invalid_shapes() -> None:
    patch = {
        "flags": {"ok_key": True, "bad key !": True, "nested": {"x": 1}},
        "vars": {"xp": 12.5, "none": None},
        "location": "   ",
        "location_id": {"bad": "type"},
        "map_anchor": 123,
        "unknown": "ignored",
    }

    normalized = sanitize_state_patch(patch)

    assert normalized == {
        "flags": {"ok_key": True},
        "vars": {"xp": 12.5},
    }
