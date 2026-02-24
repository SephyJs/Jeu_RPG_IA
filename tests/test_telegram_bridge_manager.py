import json

from app.telegram.bridge_manager import TelegramBridgeManager


def test_validate_token() -> None:
    manager = TelegramBridgeManager(project_dir=".", saves_dir="saves", slot_count=3)
    assert manager.validate_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ12345") is True
    assert manager.validate_token("abc") is False
    assert manager.validate_token("123:short") is False


def test_config_roundtrip(tmp_path) -> None:
    manager = TelegramBridgeManager(project_dir=str(tmp_path), saves_dir="saves", slot_count=3)
    manager.configure(
        profile_key="Sephy Player",
        token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ12345",
        profile_name="Sephy",
        slot=2,
    )

    cfg = manager.load_config("sephy_player")
    assert cfg["profile_key"] == "sephy_player"
    assert cfg["profile_name"] == "Sephy"
    assert cfg["slot"] == 2
    assert cfg["token"].startswith("123456789:")

    status = manager.status("sephy_player")
    assert status["has_token"] is True
    assert status["slot"] == 2
    assert status["running"] is False


def test_set_slot_is_clamped(tmp_path) -> None:
    manager = TelegramBridgeManager(project_dir=str(tmp_path), saves_dir="saves", slot_count=3)
    manager.set_slot(profile_key="abc", slot=99)
    cfg = manager.load_config("abc")
    assert cfg["slot"] == 3


def test_token_is_encrypted_at_rest(tmp_path) -> None:
    manager = TelegramBridgeManager(project_dir=str(tmp_path), saves_dir="saves", slot_count=3)
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"
    manager.configure(profile_key="Alice", token=token, profile_name="Alice", slot=1)

    key = manager.normalize_profile_key("Alice")
    config_path = tmp_path / "saves" / "profiles" / key / "telegram_bridge.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    assert str(raw.get("token") or "") != token
    assert isinstance(raw.get("token_enc"), str) and raw["token_enc"].startswith("v1:")
    assert manager.load_config(key)["token"] == token
