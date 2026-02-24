from app.ui.nsfw import (
    contains_nsfw_marker,
    is_nsfw_mode_enabled,
    is_nsfw_scene,
    nsfw_password_is_valid,
    pick_safe_scene_id,
    read_nsfw_password_config,
    set_nsfw_mode_enabled,
    set_profile_nsfw_password,
)
from app.ui.state.game_state import Choice, GameState, Scene


def test_detects_nsfw_markers() -> None:
    assert contains_nsfw_marker("Maison de plaisir")
    assert contains_nsfw_marker("Le Baiser de Velours")
    assert not contains_nsfw_marker("Place du village")


def test_detects_nsfw_scene_by_title() -> None:
    scene = Scene(
        id="safe_scene",
        title="Maison de plaisir",
        narrator_text="Texte",
    )
    assert is_nsfw_scene(scene)


def test_nsfw_mode_flag_roundtrip() -> None:
    state = GameState()
    assert not is_nsfw_mode_enabled(state)

    set_nsfw_mode_enabled(state, True)
    assert is_nsfw_mode_enabled(state)

    set_nsfw_mode_enabled(state, False)
    assert not is_nsfw_mode_enabled(state)


def test_profile_password_config_and_validation() -> None:
    state = GameState()
    set_profile_nsfw_password(state, "abcd")

    plain, digest, source = read_nsfw_password_config(state)
    assert plain == ""
    assert len(digest) == 64
    assert source == "profile"
    assert nsfw_password_is_valid(state, "abcd")
    assert not nsfw_password_is_valid(state, "wrong")


def test_env_password_overrides_profile(monkeypatch) -> None:
    state = GameState()
    set_profile_nsfw_password(state, "local-pass")
    monkeypatch.setenv("ATARYXIA_NSFW_PASSWORD", "env-pass")
    monkeypatch.delenv("ATARYXIA_NSFW_PASSWORD_SHA256", raising=False)

    plain, digest, source = read_nsfw_password_config(state)
    assert plain == "env-pass"
    assert digest == ""
    assert source == "env"
    assert nsfw_password_is_valid(state, "env-pass")
    assert not nsfw_password_is_valid(state, "local-pass")


def test_pick_safe_scene_prefers_neighbor() -> None:
    state = GameState()
    state.scenes = {
        "safe": Scene(
            id="safe",
            title="Place du village",
            narrator_text="Rien a signaler",
        ),
        "maison_de_plaisir_01": Scene(
            id="maison_de_plaisir_01",
            title="Le Baiser de Velours",
            narrator_text="Zone adulte",
            choices=[Choice(id="sortie", label="Sortir", next_scene_id="safe")],
        ),
    }
    state.current_scene_id = "maison_de_plaisir_01"

    assert pick_safe_scene_id(state) == "safe"
