from __future__ import annotations

from dataclasses import dataclass

from app.core.engine import normalize_trade_session, trade_session_to_dict
from app.ui.state.game_state import GameState


@dataclass(slots=True)
class SelectedNpcContext:
    npc_name: str | None
    npc_key: str | None
    npc_profile: dict | None


async def resolve_selected_npc_context(
    state: GameState,
    *,
    scene,
    npc_manager,
    npc_profile_key_fn,
    resolve_scene_npc_key_fn,
    register_npc_profile_fn,
) -> SelectedNpcContext:
    npc_name = getattr(state, "selected_npc", None)
    if not npc_name:
        return SelectedNpcContext(npc_name=None, npc_key=None, npc_profile=None)

    npc = str(npc_name)
    fallback_key = npc_profile_key_fn(npc, scene.id)
    npc_key = resolve_scene_npc_key_fn(state, npc, scene.id)
    npc_profile = state.npc_profiles.get(npc_key)

    if not isinstance(npc_profile, dict) and npc_key != fallback_key:
        loaded = npc_manager.load_profile_by_key(
            npc_key,
            fallback_label=npc,
            location_id=scene.id,
            location_title=scene.title,
        )
        if isinstance(loaded, dict):
            state.npc_profiles[npc_key] = loaded
            npc_profile = loaded

    if not isinstance(npc_profile, dict):
        try:
            generated = await npc_manager.ensure_profile(
                state.npc_profiles,
                npc,
                location_id=scene.id,
                location_title=scene.title,
            )
        except Exception:
            generated = None
        if isinstance(generated, dict):
            npc_profile = generated
            npc_key = str(generated.get("npc_key") or fallback_key).strip() or fallback_key
            state.npc_profiles[npc_key] = npc_profile

    if isinstance(npc_profile, dict):
        register_npc_profile_fn(state, npc_name=npc, scene=scene, profile=npc_profile, npc_key=npc_key or "")

    return SelectedNpcContext(
        npc_name=npc,
        npc_key=npc_key,
        npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
    )


def sync_post_trade_gm_state(
    state: GameState,
    *,
    safe_int,
    economy_manager,
    reputation_summary_fn,
) -> None:
    state.gm_state["player_gold"] = max(0, safe_int(state.player.gold, 0))
    state.gm_state["player_corruption_level"] = max(0, min(100, safe_int(getattr(state, "player_corruption_level", 0), 0)))
    state.gm_state["inventory_summary"] = economy_manager.inventory_summary(state, state.item_defs)
    trade_session = normalize_trade_session(getattr(state, "trade_session", None))
    state.gm_state["trade_session"] = trade_session_to_dict(trade_session)
    state.gm_state["trade_status"] = str(trade_session.status or "idle")
    state.gm_state["trade_mode"] = str(trade_session.mode or "sell")
    state.gm_state["trade_turn_id"] = max(0, safe_int(trade_session.turn_id, 0))
    state.gm_state["faction_reputation"] = dict(state.faction_reputation)
    state.gm_state["faction_reputation_summary"] = reputation_summary_fn(state, limit=6)
