from __future__ import annotations

import hashlib
import os
import re

from app.ui.state.game_state import GameState, Scene


_NSFW_SCENE_TOKEN_RE = re.compile(
    r"\b(maison\s+de\s+plaisir|maison\s+close|bordel|lupanar|baiser\s+de\s+velours|succube|debauche)\b",
    flags=re.IGNORECASE,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def gm_flags(state: GameState) -> dict:
    gm_state = state.gm_state if isinstance(state.gm_state, dict) else None
    if not isinstance(gm_state, dict):
        state.gm_state = {}
        gm_state = state.gm_state
    flags = gm_state.get("flags")
    if isinstance(flags, dict):
        return flags
    gm_state["flags"] = {}
    return gm_state["flags"]


def contains_nsfw_marker(text: str) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return False
    return bool(_NSFW_SCENE_TOKEN_RE.search(raw))


def is_nsfw_scene(scene: Scene | None) -> bool:
    if not isinstance(scene, Scene):
        return False
    if contains_nsfw_marker(scene.id):
        return True
    if contains_nsfw_marker(scene.title):
        return True
    if contains_nsfw_marker(scene.narrator_text):
        return True
    return any(contains_nsfw_marker(name) for name in scene.npc_names)


def is_nsfw_mode_enabled(state: GameState) -> bool:
    return bool(gm_flags(state).get("nsfw_enabled"))


def set_nsfw_mode_enabled(state: GameState, enabled: bool) -> None:
    gm_flags(state)["nsfw_enabled"] = bool(enabled)


def hash_nsfw_password(raw: str) -> str:
    return hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest().lower()


def read_nsfw_password_config(state: GameState) -> tuple[str, str, str]:
    plain = str(os.getenv("ATARYXIA_NSFW_PASSWORD") or "").strip()
    digest = str(os.getenv("ATARYXIA_NSFW_PASSWORD_SHA256") or "").strip().lower()
    if plain:
        return plain, "", "env"
    if _SHA256_RE.fullmatch(digest):
        return "", digest, "env"

    local_digest = str(gm_flags(state).get("nsfw_password_sha256") or "").strip().lower()
    if _SHA256_RE.fullmatch(local_digest):
        return "", local_digest, "profile"
    return "", "", "missing"


def nsfw_password_is_valid(state: GameState, candidate: str) -> bool:
    plain, digest, _ = read_nsfw_password_config(state)
    value = str(candidate or "")
    if plain:
        return value == plain
    if digest:
        return hash_nsfw_password(value) == digest
    return False


def set_profile_nsfw_password(state: GameState, raw_password: str) -> str:
    digest = hash_nsfw_password(raw_password)
    gm_flags(state)["nsfw_password_sha256"] = digest
    return digest


def pick_safe_scene_id(state: GameState, *, from_scene_id: str | None = None) -> str:
    source_id = str(from_scene_id or state.current_scene_id or "").strip()
    source_scene = state.scenes.get(source_id)
    if isinstance(source_scene, Scene):
        for choice in source_scene.choices:
            target_id = str(choice.next_scene_id or "").strip()
            target = state.scenes.get(target_id)
            if target_id and isinstance(target, Scene) and not is_nsfw_scene(target):
                return target_id

    for scene_id, scene in state.scenes.items():
        if isinstance(scene, Scene) and not is_nsfw_scene(scene):
            return scene_id
    return ""
