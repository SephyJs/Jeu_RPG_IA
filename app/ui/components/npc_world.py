from __future__ import annotations

import random
import re
import unicodedata

from app.gamemaster.location_manager import is_street_scene, refresh_roaming_street_npcs
from app.gamemaster.npc_manager import npc_profile_key, profile_display_name, resolve_profile_role
from app.ui.state.game_state import GameState, Scene


def _norm_token(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def _binding_key(scene_id: str, npc_name: str) -> str:
    return f"{str(scene_id).strip()}::{_norm_token(npc_name)}"


def _dedupe_names(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in names:
        name = str(row or "").strip()
        if not name:
            continue
        key = _norm_token(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def ensure_npc_world_state(state: GameState) -> None:
    registry = getattr(state, "npc_registry", None)
    if not isinstance(registry, dict):
        state.npc_registry = {}
    else:
        sanitized: dict[str, dict] = {}
        for npc_key, entry in registry.items():
            if not isinstance(npc_key, str) or not isinstance(entry, dict):
                continue
            name = str(entry.get("display_name") or entry.get("label") or "").strip()
            if not name:
                continue
            out = dict(entry)
            out["npc_key"] = str(out.get("npc_key") or npc_key).strip() or npc_key
            out["display_name"] = name[:80]
            out["label"] = str(out.get("label") or name).strip()[:80]
            out["role"] = str(out.get("role") or out["label"] or "PNJ").strip()[:80]
            out["home_location_id"] = str(out.get("home_location_id") or "").strip()[:120]
            out["home_location_title"] = str(out.get("home_location_title") or "").strip()[:120]
            out["home_anchor"] = str(out.get("home_anchor") or "").strip()[:120]
            out["last_seen_scene_id"] = str(out.get("last_seen_scene_id") or "").strip()[:120]
            out["last_seen_scene_title"] = str(out.get("last_seen_scene_title") or "").strip()[:120]
            aliases_raw = out.get("aliases")
            aliases = []
            if isinstance(aliases_raw, list):
                aliases = [str(x).strip()[:80] for x in aliases_raw if str(x).strip()]
            if out["display_name"] not in aliases:
                aliases.append(out["display_name"])
            if out["label"] not in aliases:
                aliases.append(out["label"])
            out["aliases"] = _dedupe_names(aliases)[:16]
            out["can_roam"] = bool(out.get("can_roam", True))
            sanitized[out["npc_key"]] = out
        state.npc_registry = sanitized

    bindings = getattr(state, "npc_scene_bindings", None)
    if not isinstance(bindings, dict):
        state.npc_scene_bindings = {}
    else:
        state.npc_scene_bindings = {
            str(scene_key)[:220]: str(npc_key).strip()
            for scene_key, npc_key in bindings.items()
            if isinstance(scene_key, str) and isinstance(npc_key, str) and str(npc_key).strip()
        }


def _bind_scene_npc(state: GameState, *, scene_id: str, npc_name: str, npc_key: str) -> None:
    name = str(npc_name or "").strip()
    key = str(npc_key or "").strip()
    if not name or not key:
        return
    state.npc_scene_bindings[_binding_key(scene_id, name)] = key


def sync_npc_registry_from_profiles(state: GameState) -> None:
    ensure_npc_world_state(state)
    if not isinstance(state.npc_profiles, dict):
        return

    for profile_key, profile in state.npc_profiles.items():
        if not isinstance(profile, dict):
            continue
        npc_key = str(profile.get("npc_key") or profile_key).strip()
        if not npc_key:
            continue

        world_anchor = profile.get("world_anchor", {}) if isinstance(profile.get("world_anchor"), dict) else {}
        home_location_id = str(world_anchor.get("location_id") or "").strip()
        home_location_title = str(world_anchor.get("location_title") or "").strip()
        home_anchor = ""
        if home_location_id and home_location_id in state.scenes:
            home_anchor = str(state.scenes[home_location_id].map_anchor or "").strip()

        fallback = str(profile.get("label") or npc_key.split("__")[-1]).replace("_", " ").strip() or "PNJ"
        display_name = profile_display_name(profile, fallback)
        role = resolve_profile_role(profile, fallback)

        entry = state.npc_registry.get(npc_key, {})
        aliases_raw = entry.get("aliases") if isinstance(entry, dict) else []
        aliases = []
        if isinstance(aliases_raw, list):
            aliases = [str(x).strip()[:80] for x in aliases_raw if isinstance(x, str) and str(x).strip()]
        aliases.extend([fallback, display_name, str(profile.get("label") or "").strip()])
        aliases = _dedupe_names(aliases)

        state.npc_registry[npc_key] = {
            "npc_key": npc_key,
            "display_name": display_name[:80],
            "label": str(profile.get("label") or fallback).strip()[:80],
            "role": role[:80],
            "home_location_id": home_location_id[:120],
            "home_location_title": home_location_title[:120],
            "home_anchor": (str(entry.get("home_anchor") or home_anchor).strip() if isinstance(entry, dict) else home_anchor)[:120],
            "last_seen_scene_id": str(entry.get("last_seen_scene_id") or "").strip()[:120] if isinstance(entry, dict) else "",
            "last_seen_scene_title": str(entry.get("last_seen_scene_title") or "").strip()[:120] if isinstance(entry, dict) else "",
            "aliases": aliases[:16],
            "can_roam": bool(entry.get("can_roam", True)) if isinstance(entry, dict) else True,
        }


def resolve_scene_npc_key(state: GameState, npc_name: str, scene_id: str) -> str:
    ensure_npc_world_state(state)
    name = str(npc_name or "").strip()
    scene = str(scene_id or "").strip()
    if not name or not scene:
        return npc_profile_key(name, scene)

    bound = state.npc_scene_bindings.get(_binding_key(scene, name))
    if isinstance(bound, str) and bound.strip():
        return bound.strip()

    norm = _norm_token(name)
    if norm:
        matches: list[str] = []
        for npc_key, entry in state.npc_registry.items():
            if not isinstance(entry, dict):
                continue
            aliases = entry.get("aliases")
            alias_values = aliases if isinstance(aliases, list) else []
            haystack = [entry.get("display_name"), entry.get("label"), *alias_values]
            if any(_norm_token(str(alias or "")) == norm for alias in haystack):
                matches.append(str(npc_key))
        if len(matches) == 1:
            chosen = matches[0]
            _bind_scene_npc(state, scene_id=scene, npc_name=name, npc_key=chosen)
            return chosen

    return npc_profile_key(name, scene)


def register_npc_profile(
    state: GameState,
    *,
    npc_name: str,
    scene: Scene,
    profile: dict | None,
    npc_key: str,
) -> str:
    ensure_npc_world_state(state)
    key = str(npc_key or "").strip() or npc_profile_key(npc_name, scene.id)
    profile_dict = profile if isinstance(profile, dict) else {}

    fallback = str(profile_dict.get("label") or npc_name).strip() or npc_name
    display_name = profile_display_name(profile_dict, fallback)
    role = resolve_profile_role(profile_dict, fallback)

    world_anchor = profile_dict.get("world_anchor", {}) if isinstance(profile_dict.get("world_anchor"), dict) else {}
    home_location_id = str(world_anchor.get("location_id") or scene.id).strip()
    home_location_title = str(world_anchor.get("location_title") or scene.title).strip()
    home_anchor = ""
    if home_location_id in state.scenes:
        home_anchor = str(state.scenes[home_location_id].map_anchor or "").strip()
    if not home_anchor:
        home_anchor = str(scene.map_anchor or "").strip()

    current = state.npc_registry.get(key, {})
    aliases_raw = current.get("aliases") if isinstance(current, dict) else []
    aliases = []
    if isinstance(aliases_raw, list):
        aliases = [str(x).strip()[:80] for x in aliases_raw if isinstance(x, str) and str(x).strip()]
    aliases.extend(
        [
            npc_name,
            display_name,
            fallback,
            str(profile_dict.get("label") or "").strip(),
        ]
    )
    aliases = _dedupe_names(aliases)

    state.npc_registry[key] = {
        "npc_key": key,
        "display_name": display_name[:80],
        "label": str(profile_dict.get("label") or fallback).strip()[:80],
        "role": role[:80],
        "home_location_id": home_location_id[:120],
        "home_location_title": home_location_title[:120],
        "home_anchor": home_anchor[:120],
        "last_seen_scene_id": scene.id[:120],
        "last_seen_scene_title": scene.title[:120],
        "aliases": aliases[:16],
        "can_roam": bool(current.get("can_roam", True)) if isinstance(current, dict) else True,
    }

    _bind_scene_npc(state, scene_id=scene.id, npc_name=npc_name, npc_key=key)
    _bind_scene_npc(state, scene_id=scene.id, npc_name=display_name, npc_key=key)
    for alias in aliases:
        _bind_scene_npc(state, scene_id=scene.id, npc_name=alias, npc_key=key)
    return key


def spawn_roaming_known_npcs(state: GameState, *, max_total: int = 5) -> bool:
    ensure_npc_world_state(state)
    sync_npc_registry_from_profiles(state)

    scene = state.current_scene()
    if not is_street_scene(scene):
        return False

    same_anchor: list[str] = []
    other_places: list[str] = []
    candidate_key_by_name: dict[str, str] = {}
    scene_anchor = str(scene.map_anchor or "").strip()
    fixed_norm = {_norm_token(name) for name in scene.npc_names}

    for npc_key, entry in state.npc_registry.items():
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("can_roam", True)):
            continue
        name = str(entry.get("display_name") or entry.get("label") or "").strip()
        if not name:
            continue
        if _norm_token(name) in fixed_norm:
            continue
        if str(entry.get("home_location_id") or "").strip() == scene.id:
            continue

        home_anchor = str(entry.get("home_anchor") or "").strip()
        if home_anchor and scene_anchor and home_anchor == scene_anchor:
            same_anchor.append(name)
        else:
            other_places.append(name)
        candidate_key_by_name[name] = str(npc_key)

    random.shuffle(same_anchor)
    random.shuffle(other_places)
    weighted = [*same_anchor, *same_anchor, *other_places]
    candidates = _dedupe_names(weighted)[:20]

    changed = refresh_roaming_street_npcs(
        scene,
        max_total=max_total,
        roaming_candidates=candidates if candidates else None,
    )

    for npc_name in scene.npc_names:
        npc_key = candidate_key_by_name.get(npc_name)
        if not npc_key:
            continue
        _bind_scene_npc(state, scene_id=scene.id, npc_name=npc_name, npc_key=npc_key)
        entry = state.npc_registry.get(npc_key)
        if isinstance(entry, dict):
            entry["last_seen_scene_id"] = scene.id[:120]
            entry["last_seen_scene_title"] = scene.title[:120]
    return changed
