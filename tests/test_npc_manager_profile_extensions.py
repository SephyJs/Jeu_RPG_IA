from app.gamemaster.npc_manager import apply_attraction_delta, normalize_profile_extensions_in_place


def test_normalize_profile_extensions_adds_adult_fields_defaults() -> None:
    profile = {
        "label": "Mirelle",
        "role": "Geoliere",
        "needs": ["Stabilite"],
        "fears": ["Trahison"],
    }

    normalize_profile_extensions_in_place(profile, fallback_label="Mirelle")

    assert isinstance(profile.get("agenda_secret"), str) and str(profile.get("agenda_secret")).strip()
    assert isinstance(profile.get("besoin"), str) and str(profile.get("besoin")).strip()
    assert isinstance(profile.get("peur"), str) and str(profile.get("peur")).strip()
    assert isinstance(profile.get("traits"), list) and 3 <= len(profile.get("traits")) <= 6
    assert 0 <= int(profile.get("tension_level") or 0) <= 100
    assert 0 <= int(profile.get("morale") or 0) <= 100
    assert 0 <= int(profile.get("aggressiveness") or 0) <= 100
    assert 0 <= int(profile.get("corruption_level") or 0) <= 100
    assert str(profile.get("dominance_style") or "") in {"soft", "manipulative", "aggressive", "cold"}
    assert isinstance(profile.get("attraction_map"), dict)


def test_apply_attraction_delta_clamps_range() -> None:
    profile = {"attraction_map": {"Sephy": 98}, "truth_state": {}}

    old, new = apply_attraction_delta(profile, player_id="Sephy", delta=10, reason="test")
    assert old == 98
    assert new == 100
    assert profile["attraction_map"]["Sephy"] == 100
