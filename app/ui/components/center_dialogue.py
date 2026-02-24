import asyncio
import re
from types import SimpleNamespace
import unicodedata

from nicegui import ui

from app.ui.components.center_panel_dungeon import (
    advance_dungeon as _advance_dungeon_action,
    enter_dungeon as _enter_dungeon_action,
    leave_dungeon as _leave_dungeon_action,
    render_dungeon_actions as _render_dungeon_actions_block,
    sync_dungeon_gm_context as _sync_dungeon_gm_context,
)
from app.ui.components.center_panel_memory import (
    prepare_gm_state_for_turn as _memory_prepare_gm_state_for_turn,
    record_trade_event_in_memory as _memory_record_trade_event_in_memory,
    remember_dialogue_turn_safe as _memory_remember_dialogue_turn_safe,
)
from app.ui.components.center_panel_quests import (
    active_quest_for_npc as _quest_active_quest_for_npc,
    active_quests as _quest_active_quests,
    build_runtime_quest as _quest_build_runtime_quest,
    can_request_quest as _quest_can_request_quest,
    choose_quest_branch as _quest_choose_branch,
    compute_quest_progress as _quest_compute_progress,
    ensure_quest_state as _ensure_quest_state,
    maybe_unlock_secret_charity_quest as _quest_maybe_unlock_secret,
    quest_branch_options_summary as _quest_branch_options_summary,
    request_quest_from_selected_npc as _request_quest_from_selected_npc_action,
    update_quests_and_notify as _quest_update_and_notify,
)
from app.ui.components.center_panel_support import (
    TransientInput as _TransientInput,
    experience_tier as _experience_tier,
    refresh_chat_messages_view as _refresh_chat_messages_view,
    render_chat_messages as _render_chat_messages,
    run_chat_command_handler as _run_chat_command_handler,
    safe_int as _safe_int,
    sanitize_progression_for_trade as _sanitize_progression_for_trade,
    schedule_chat_autoscroll as _schedule_chat_autoscroll,
    schedule_chat_input_focus as _schedule_chat_input_focus,
    utc_now_iso as _utc_now_iso,
)
from app.ui.components.center_panel_skills import train_skill_with_selected_npc as _train_skill_with_selected_npc_action
from app.ui.components.center_panel_skills_progression import (
    append_progress_log as _skills_append_progress_log,
    append_skill_training_log as _skills_append_skill_training_log,
    append_skill_usage_log as _skills_append_skill_usage_log,
    apply_passive_skill_practice_from_text as _skills_apply_passive_skill_practice_from_text,
    apply_skill_usage_progress_from_text as _skills_apply_skill_usage_progress_from_text,
    build_player_skill_context as _skills_build_player_skill_context,
    ensure_skill_state as _skills_ensure_skill_state,
    existing_skill_for_intent as _skills_existing_skill_for_intent,
    find_player_skill_entry as _skills_find_player_skill_entry,
    next_passive_threshold as _skills_next_passive_threshold,
    player_stats_for_training as _skills_player_stats_for_training,
    upsert_learned_skill as _skills_upsert_learned_skill,
)
from app.ui.components.center_panel_trade import (
    apply_quest_rewards as _trade_apply_quest_rewards,
    apply_trade_from_player_message as _trade_apply_trade_from_player_message,
    find_empty_slot as _trade_find_empty_slot,
    grant_item_reward as _trade_grant_item_reward,
    item_display_name as _trade_item_display_name,
    item_stack_max as _trade_item_stack_max,
    render_trade_dialogue as _trade_render_trade_dialogue,
)
from app.ui.components.center_dialogue_turn import (
    resolve_selected_npc_context as _turn_resolve_selected_npc_context,
    sync_post_trade_gm_state as _turn_sync_post_trade_gm_state,
)
from app.ui.components.gameplay_hooks import register_gameplay_hooks
from app.ui.state.game_state import GameState, Choice, Scene
from app.ui.nsfw import contains_nsfw_marker, is_nsfw_mode_enabled, is_nsfw_scene
from app.ui.components.consumables import (
    get_consumable_stat_bonus_totals as _get_consumable_stat_bonus_totals,
    tick_consumable_buffs as _tick_consumable_buffs,
)

from app.gamemaster.runtime import get_runtime_services
from app.gamemaster.npc_manager import (
    apply_attraction_delta,
    apply_tension_delta,
    is_npc_blacklisted,
    npc_profile_key,
    profile_corruption_level,
    profile_display_name,
    profile_morale_level,
    profile_tension_level,
    resolve_profile_role,
    set_npc_blacklist,
    profile_summary_line,
    tension_tier_label,
    update_profile_emotional_state,
)
from app.gamemaster.location_manager import (
    MAP_ANCHORS,
    is_building_scene_title,
    scene_open_status,
)
from app.gamemaster.dungeon_combat import (
    build_combat_state as _build_dungeon_combat_state,
    is_combat_event as _is_dungeon_combat_event,
    resolve_combat_turn as _resolve_dungeon_combat_turn,
    wants_repeat_heal_until_full as _wants_repeat_heal_until_full,
)
from app.gamemaster.skill_manager import SkillDef
from app.gamemaster.reputation_manager import (
    apply_dungeon_reputation as _rep_apply_dungeon_reputation,
    apply_quest_branch_reputation as _rep_apply_quest_branch_reputation,
    apply_quest_completion_reputation as _rep_apply_quest_completion_reputation,
    apply_trade_reputation as _rep_apply_trade_reputation,
    can_access_scene_by_reputation as _rep_can_access_scene,
    reputation_summary as _rep_reputation_summary,
)
from app.gamemaster.conversation_memory import (
    build_retrieved_context,
    build_global_memory_context,
    build_long_term_context,
    build_short_term_context,
    ensure_conversation_memory_state,
    remember_dialogue_turn,
    remember_system_event,
)
from app.gamemaster.world_time import format_fantasy_datetime
from app.gamemaster.story_manager import progress_main_story as _story_progress_main_story
from app.gamemaster.story_manager import story_status_text as _story_status_text
from app.gamemaster.world_events import (
    apply_world_time_events as _world_apply_time_events,
    try_resolve_nearby_world_event as _world_try_resolve_nearby_world_event,
)
from app.gamemaster.state_patch import apply_patch as _apply_state_patch_to_gm
from app.core.engine import normalize_trade_session
from app.core.events import (
    OnLocationEntered,
    OnNpcTensionChanged,
    OnQuestUpdated,
    OnTradeCompleted,
)

from app.ui.components.right_narrator import pick_random_video_url
from app.ui.components.right_narrator import set_narrator_text_js
from app.ui.components.right_narrator import play_action_video_js
from app.ui.components.npc_world import (
    ensure_npc_world_state,
    register_npc_profile,
    resolve_scene_npc_key,
    spawn_roaming_known_npcs,
    sync_npc_registry_from_profiles,
)
from app.ui.components.world_map import (
    _abort_travel,
    _resolve_travel_event_choice,
    _start_travel_to_scene,
    _tick_travel,
    _travel_in_progress,
    _travel_state,
)


_runtime_services = get_runtime_services()
_llm = _runtime_services.llm
_gm = _runtime_services.gm
_event_bus = _runtime_services.event_bus
_npc_manager = _runtime_services.npc_manager
_location_manager = _runtime_services.location_manager
_dungeon_manager = _runtime_services.dungeon_manager
_quest_manager = _runtime_services.quest_manager
_player_sheet_manager = _runtime_services.player_sheet_manager
_loot_manager = _runtime_services.loot_manager
_skill_manager = _runtime_services.skill_manager
_monster_manager = _runtime_services.monster_manager
_economy_manager = _runtime_services.economy_manager
_craft_manager = _runtime_services.craft_manager

QUEST_MIN_MESSAGES_BEFORE_OFFER = 3
QUEST_MESSAGES_PER_NEXT_OFFER = 3
PASSIVE_PRACTICE_BASE_THRESHOLD = 6
PASSIVE_PRACTICE_THRESHOLD_GROWTH = 1.7
_LOCAL_ENTRY_TARGET_FLAG = "local_entry_target_scene_id"
_COMBAT_QUICK_SKILL_FLAG = "combat_quick_skill_id"
_GUIDED_TRAINING_SESSION_FLAG = "guided_training_session"
_MAX_AUTO_HEAL_CASTS = 8


def _chat_turn_busy(state: GameState) -> bool:
    return bool(getattr(state, "chat_turn_in_progress", False))


def _set_chat_turn_busy(state: GameState, busy: bool) -> None:
    state.chat_turn_in_progress = bool(busy)


def _ensure_skill_state(state: GameState) -> None:
    _skills_ensure_skill_state(
        state,
        skill_manager=_skill_manager,
        safe_int=_safe_int,
    )


def _ensure_player_sheet_state(state: GameState) -> None:
    _ensure_item_state(state)
    _ensure_skill_state(state)
    if not isinstance(state.player_sheet, dict) or not state.player_sheet:
        state.player_sheet = _player_sheet_manager.create_initial_sheet(fallback_name="")
    else:
        state.player_sheet = _player_sheet_manager.ensure_sheet(
            state.player_sheet,
            fallback_name="",
        )

    if not isinstance(state.player_sheet_missing, list):
        state.player_sheet_missing = []
    state.player_sheet_missing = _player_sheet_manager.missing_creation_fields(state.player_sheet)
    state.player_sheet_ready = not state.player_sheet_missing
    state.player_sheet_generation_in_progress = bool(getattr(state, "player_sheet_generation_in_progress", False))
    state.player_sheet = _loot_manager.apply_equipment_to_sheet(state.player_sheet, state.item_defs, state.equipped_items)
    _player_sheet_manager.sync_player_basics(state.player_sheet, state.player)


def _creation_missing_labels(missing_fields: list[str]) -> str:
    labels = _player_sheet_manager.creation_missing_labels()
    return ", ".join(labels.get(k, k) for k in missing_fields)


def _ensure_item_state(state: GameState) -> None:
    if not isinstance(state.item_defs, dict) or not state.item_defs:
        try:
            state.item_defs = _loot_manager.load_item_defs()
        except Exception:
            state.item_defs = {}

    state.equipped_items = _loot_manager.normalize_equip_state(getattr(state, "equipped_items", {}))
    selected_slot = str(getattr(state, "selected_equipped_slot", "") or "")
    if selected_slot not in {"weapon", "armor", "accessory_1", "accessory_2"}:
        state.selected_equipped_slot = ""


def _append_progress_log(state: GameState, *, progression: dict, summary_lines: list[str]) -> None:
    _skills_append_progress_log(
        state,
        progression=progression,
        summary_lines=summary_lines,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    )


def _player_stats_for_training(state: GameState) -> dict[str, int]:
    return _skills_player_stats_for_training(state, safe_int=_safe_int)


def _build_player_skill_context(state: GameState, recent_chat_lines: list[str] | None = None) -> str:
    return _skills_build_player_skill_context(state, recent_chat_lines=recent_chat_lines)


def _upsert_learned_skill(state: GameState, skill, trainer_name: str) -> tuple[dict, bool]:
    return _skills_upsert_learned_skill(
        state,
        skill,
        trainer_name,
        skill_manager=_skill_manager,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    )


def _append_skill_training_log(
    state: GameState,
    *,
    npc_name: str,
    skill,
    chance: int,
    roll: int,
    success: bool,
    reason: str,
) -> None:
    _skills_append_skill_training_log(
        state,
        npc_name=npc_name,
        skill=skill,
        chance=chance,
        roll=roll,
        success=success,
        reason=reason,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    )


def _find_player_skill_entry(state: GameState, skill_id: str) -> dict | None:
    return _skills_find_player_skill_entry(state, skill_id)


def _append_skill_usage_log(
    state: GameState,
    *,
    skill_entry: dict,
    xp_gain: int,
    levels_gained: int,
    level_after: int,
    source: str = "usage",
    reason: str = "",
) -> None:
    _skills_append_skill_usage_log(
        state,
        skill_entry=skill_entry,
        xp_gain=xp_gain,
        levels_gained=levels_gained,
        level_after=level_after,
        source=source,
        reason=reason,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    )


def _apply_skill_usage_progress_from_text(state: GameState, text: str) -> list[str]:
    return _skills_apply_skill_usage_progress_from_text(
        state,
        text,
        ensure_skill_state_fn=_ensure_skill_state,
        skill_manager=_skill_manager,
        find_player_skill_entry_fn=_find_player_skill_entry,
        append_skill_usage_log_fn=_append_skill_usage_log,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    )


def _existing_skill_for_intent(state: GameState, intent: str) -> dict | None:
    return _skills_existing_skill_for_intent(
        state,
        intent,
        skill_manager=_skill_manager,
    )


def _next_passive_threshold(current_threshold: int) -> int:
    return _skills_next_passive_threshold(
        current_threshold,
        safe_int=_safe_int,
        base_threshold=PASSIVE_PRACTICE_BASE_THRESHOLD,
        threshold_growth=PASSIVE_PRACTICE_THRESHOLD_GROWTH,
    )


async def _apply_passive_skill_practice_from_text(state: GameState, text: str) -> list[str]:
    return await _skills_apply_passive_skill_practice_from_text(
        state,
        text,
        ensure_skill_state_fn=_ensure_skill_state,
        skill_manager=_skill_manager,
        build_player_skill_context_fn=_build_player_skill_context,
        player_stats_for_training_fn=_player_stats_for_training,
        utc_now_iso=_utc_now_iso,
        safe_int=_safe_int,
        existing_skill_for_intent_fn=_existing_skill_for_intent,
        append_skill_usage_log_fn=_append_skill_usage_log,
        next_passive_threshold_fn=_next_passive_threshold,
        selected_npc_profile_fn=_selected_npc_profile,
        resolve_profile_role_fn=resolve_profile_role,
        upsert_learned_skill_fn=_upsert_learned_skill,
        base_threshold=PASSIVE_PRACTICE_BASE_THRESHOLD,
    )


def _selected_npc_conversation_key(state: GameState) -> str | None:
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return None
    scene = state.current_scene()
    return resolve_scene_npc_key(state, npc, scene.id)


def _active_quest_for_npc(state: GameState, npc_key: str) -> dict | None:
    return _quest_active_quest_for_npc(state, npc_key)


def _can_request_quest(state: GameState, npc_key: str) -> tuple[bool, str]:
    return _quest_can_request_quest(
        state,
        npc_key,
        min_messages_before_offer=QUEST_MIN_MESSAGES_BEFORE_OFFER,
        messages_per_next_offer=QUEST_MESSAGES_PER_NEXT_OFFER,
        safe_int=_safe_int,
    )


def _build_runtime_quest(
    state: GameState,
    *,
    quest_payload: dict,
    npc_name: str,
    npc_key: str,
    scene: Scene,
) -> dict:
    return _quest_build_runtime_quest(
        state,
        quest_payload=quest_payload,
        npc_name=npc_name,
        npc_key=npc_key,
        scene=scene,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
        npc_profile_key=npc_profile_key,
    )


def _maybe_unlock_secret_charity_quest(
    state: GameState,
    *,
    npc_name: str,
    npc_key: str,
    scene: Scene,
    trade_context: dict,
) -> None:
    _quest_maybe_unlock_secret(
        state,
        npc_name=npc_name,
        npc_key=npc_key,
        scene=scene,
        trade_context=trade_context,
        safe_int=_safe_int,
        random_fn=_loot_manager.rng.random,
        build_runtime_quest_fn=_build_runtime_quest,
    )


def _apply_trade_from_player_message(
    state: GameState,
    *,
    user_text: str,
    selected_npc: str | None,
    npc_key: str | None,
    selected_profile: dict | None,
) -> dict:
    outcome = _trade_apply_trade_from_player_message(
        state,
        user_text=user_text,
        selected_npc=selected_npc,
        npc_key=npc_key,
        selected_profile=selected_profile,
        ensure_quest_state_fn=_ensure_quest_state,
        ensure_item_state_fn=_ensure_item_state,
        economy_manager=_economy_manager,
        safe_int=_safe_int,
        maybe_unlock_secret_charity_quest_fn=_maybe_unlock_secret_charity_quest,
        apply_trade_reputation_fn=_rep_apply_trade_reputation,
    )
    trade_context = outcome.get("trade_context") if isinstance(outcome.get("trade_context"), dict) else {}
    if bool(outcome.get("applied")) and trade_context:
        action = str(trade_context.get("action") or "").strip().casefold()
        status = str(trade_context.get("status") or "").strip().casefold()
        if status == "ok":
            _event_bus.publish(
                OnTradeCompleted(
                    npc_key=str(npc_key or ""),
                    npc_name=str(selected_npc or ""),
                    item_id=str(trade_context.get("item_id") or ""),
                    qty_done=max(0, _safe_int(trade_context.get("qty_done"), 0)),
                    gold_delta=_safe_int(trade_context.get("gold_delta"), 0),
                    action=action or "trade",
                )
            )
    return outcome


def _find_empty_slot(state: GameState) -> tuple[str, int] | None:
    return _trade_find_empty_slot(state)


def _item_stack_max(state: GameState, item_id: str) -> int:
    return _trade_item_stack_max(state, item_id)


def _grant_item_reward(state: GameState, item_id: str, qty: int) -> int:
    return _trade_grant_item_reward(
        state,
        item_id,
        qty,
        find_empty_slot_fn=_find_empty_slot,
        item_stack_max_fn=_item_stack_max,
    )


def _apply_quest_rewards(state: GameState, quest: dict) -> list[str]:
    return _trade_apply_quest_rewards(
        state,
        quest,
        safe_int=_safe_int,
        grant_item_reward_fn=_grant_item_reward,
    )


def _update_quests_and_notify(state: GameState) -> None:
    before: dict[str, str] = {}
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        qid = str(quest.get("id") or "").strip()
        if not qid:
            continue
        before[qid] = str(quest.get("status") or "in_progress")

    _quest_update_and_notify(
        state,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
        compute_quest_progress_fn=_quest_compute_progress,
        apply_quest_rewards_fn=_apply_quest_rewards,
        apply_quest_reputation_fn=_rep_apply_quest_completion_reputation,
        apply_quest_branch_reputation_fn=_rep_apply_quest_branch_reputation,
    )
    for line in _story_progress_main_story(
        state,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    ):
        if isinstance(line, str) and line.strip():
            state.push("Système", line.strip(), count_for_media=False)

    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        qid = str(quest.get("id") or "").strip()
        if not qid:
            continue
        status = str(quest.get("status") or "in_progress")
        if before.get(qid) == status:
            continue
        _event_bus.publish(
            OnQuestUpdated(
                quest_id=qid,
                status=status,
                source_npc_key=str(quest.get("source_npc_key") or ""),
                source_npc_name=str(quest.get("source_npc_name") or ""),
            )
        )


def _item_display_name(state: GameState, item_id: str) -> str:
    return _trade_item_display_name(state, item_id)


def _gm_flags(state: GameState) -> dict:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    flags = state.gm_state.get("flags")
    if isinstance(flags, dict):
        return flags
    state.gm_state["flags"] = {}
    return state.gm_state["flags"]


def _ai_enabled(state: GameState) -> bool:
    flags = _gm_flags(state)
    if "ai_enabled" not in flags:
        flags["ai_enabled"] = True
    return bool(flags.get("ai_enabled"))


def _set_ai_enabled(state: GameState, enabled: bool) -> None:
    flags = _gm_flags(state)
    flags["ai_enabled"] = bool(enabled)
    if enabled:
        flags["ai_unavailable_notified"] = False


def _deterministic_turn_result(state: GameState, *, user_text: str, npc_name: str | None) -> SimpleNamespace:
    text = re.sub(r"\s+", " ", str(user_text or "").strip())
    plain = _norm_training_text(text)
    speaker = str(npc_name or "Ataryxia").strip() or "Ataryxia"
    scene_title = str(state.current_scene().title or "ce lieu")

    if any(token in plain for token in ("bonjour", "salut", "bonsoir", "hello")):
        dialogue = "Je vous entends. Restez précis et avancez."
    elif "?" in text:
        dialogue = "Question reçue. Je peux vous répondre clairement, étape par étape."
    elif any(token in plain for token in ("entrain", "exerce", "practice", "drill", "combo")):
        dialogue = "Continuez l'entraînement: un geste propre vaut mieux qu'une force brute."
    elif any(token in plain for token in ("merci", "parfait", "ok", "daccord")):
        dialogue = "Bien. On garde ce cap et on consolide le résultat."
    else:
        dialogue = "Action notée. Décrivez votre prochaine décision concrète."

    narration = f"Le calme revient sur {scene_title}, pendant que la scene se poursuit."
    return SimpleNamespace(
        speaker=speaker,
        dialogue=dialogue,
        narration=narration,
        system=None,
        plan=None,
        output_type="dialogue",
        options=[],
        event_text=None,
    )


def _clear_pending_choice(state: GameState) -> None:
    state.pending_choice_options = []
    state.pending_choice_prompt = ""
    state.pending_choice_source_npc_key = ""
    state.pending_choice_created_at = ""


def _normalize_choice_options_payload(raw_options: object) -> list[dict]:
    if not isinstance(raw_options, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for idx, row in enumerate(raw_options[:3]):
        if isinstance(row, dict):
            option_id = str(row.get("id") or "").strip().casefold()
            text = str(row.get("text") or row.get("label") or "").strip()
            risk = str(row.get("risk_tag") or "").strip()
            hint = str(row.get("effects_hint") or "").strip()
            patch = row.get("state_patch") if isinstance(row.get("state_patch"), dict) else {}
        else:
            option_id = ""
            text = ""
            risk = ""
            hint = ""
            patch = {}
        if not option_id:
            option_id = f"option_{idx + 1}"
        if option_id in seen:
            continue
        seen.add(option_id)
        if not text:
            text = f"Option {idx + 1}"
        if not patch:
            patch = {"flags": {f"choice_{option_id[:24]}": True}}
        out.append(
            {
                "id": option_id[:40],
                "text": text[:120],
                "risk_tag": risk[:32],
                "effects_hint": hint[:160],
                "state_patch": patch,
            }
        )
    return out


def _set_pending_choice(
    state: GameState,
    *,
    options: object,
    prompt: str,
    source_npc_key: str,
) -> None:
    rows = _normalize_choice_options_payload(options)
    if not rows:
        _clear_pending_choice(state)
        return
    state.pending_choice_options = rows
    state.pending_choice_prompt = str(prompt or "").strip()[:220]
    state.pending_choice_source_npc_key = str(source_npc_key or "").strip()[:180]
    state.pending_choice_created_at = _utc_now_iso()


def _find_pending_choice_option(state: GameState, option_id: str) -> dict | None:
    target = str(option_id or "").strip().casefold()
    if not target:
        return None
    for row in state.pending_choice_options:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip().casefold() == target:
            return row
    return None


def _publish_tension_change(state: GameState, *, npc_key: str, npc_name: str, old_value: int, new_value: int, reason: str) -> None:
    if old_value == new_value:
        return
    _event_bus.publish(
        OnNpcTensionChanged(
            npc_key=str(npc_key or ""),
            npc_name=str(npc_name or ""),
            old_value=max(0, min(100, int(old_value))),
            new_value=max(0, min(100, int(new_value))),
            reason=str(reason or "")[:160],
        )
    )


def _apply_extended_state_patch_for_choice(
    state: GameState,
    *,
    patch: dict,
    npc_key: str | None,
    npc_name: str | None,
) -> list[str]:
    if not isinstance(patch, dict):
        return []

    lines: list[str] = []
    _apply_state_patch_to_gm(state.gm_state, patch)

    rep_patch = patch.get("reputation") if isinstance(patch.get("reputation"), dict) else {}
    if rep_patch:
        if not isinstance(state.faction_reputation, dict):
            state.faction_reputation = {}
        for faction, raw_delta in rep_patch.items():
            name = str(faction or "").strip()[:80]
            if not name:
                continue
            delta = max(-30, min(30, _safe_int(raw_delta, 0)))
            if delta == 0:
                continue
            before = _safe_int(state.faction_reputation.get(name), 0)
            after = max(-100, min(100, before + delta))
            state.faction_reputation[name] = after
            lines.append(f"Reputation {name}: {before:+d} -> {after:+d}")

    player_patch = patch.get("player") if isinstance(patch.get("player"), dict) else {}
    if player_patch:
        if "corruption_delta" in player_patch:
            before = max(0, min(100, _safe_int(getattr(state, "player_corruption_level", 0), 0)))
            delta = max(-35, min(35, _safe_int(player_patch.get("corruption_delta"), 0)))
            after = max(0, min(100, before + delta))
            state.player_corruption_level = after
            state.gm_state["player_corruption_level"] = after
            lines.append(f"Corruption joueur: {before} -> {after}")

    if npc_key:
        profile = state.npc_profiles.get(str(npc_key))
    else:
        profile = _selected_npc_profile(state)
    npc_patch = patch.get("npc") if isinstance(patch.get("npc"), dict) else {}
    if isinstance(profile, dict) and npc_patch:
        tension_before = profile_tension_level(profile)
        morale_before = profile_morale_level(profile)
        corruption_before = profile_corruption_level(profile)
        player_name = str(getattr(state.player, "name", "") or "").strip()
        attraction_map = profile.get("attraction_map") if isinstance(profile.get("attraction_map"), dict) else {}
        attraction_before = _safe_int(attraction_map.get(player_name), 0) if player_name else 0
        if "tension_set" in npc_patch:
            profile["tension_level"] = max(0, min(100, _safe_int(npc_patch.get("tension_set"), tension_before)))
        if "tension_delta" in npc_patch:
            apply_tension_delta(profile, delta=max(-35, min(35, _safe_int(npc_patch.get("tension_delta"), 0))), reason="choice")
        if "morale_delta" in npc_patch:
            profile["morale"] = max(0, min(100, morale_before + _safe_int(npc_patch.get("morale_delta"), 0)))
        if "aggressiveness_delta" in npc_patch:
            current_aggr = max(0, min(100, _safe_int(profile.get("aggressiveness"), 35)))
            profile["aggressiveness"] = max(0, min(100, current_aggr + _safe_int(npc_patch.get("aggressiveness_delta"), 0)))
        if "corruption_delta" in npc_patch:
            profile["corruption_level"] = max(0, min(100, corruption_before + _safe_int(npc_patch.get("corruption_delta"), 0)))
        if "attraction_delta" in npc_patch:
            attraction_player_id = str(npc_patch.get("attraction_player_id") or player_name).strip()
            apply_attraction_delta(
                profile,
                player_id=attraction_player_id,
                delta=max(-35, min(35, _safe_int(npc_patch.get("attraction_delta"), 0))),
                reason="choice",
            )
        tension_after = profile_tension_level(profile)
        morale_after = profile_morale_level(profile)
        corruption_after = profile_corruption_level(profile)
        attraction_after = 0
        if player_name:
            attraction_map_after = profile.get("attraction_map") if isinstance(profile.get("attraction_map"), dict) else {}
            attraction_after = max(0, min(100, _safe_int(attraction_map_after.get(player_name), 0)))
        if tension_after != tension_before:
            _publish_tension_change(
                state,
                npc_key=str(npc_key or ""),
                npc_name=str(npc_name or ""),
                old_value=tension_before,
                new_value=tension_after,
                reason="choice_state_patch",
            )
            lines.append(f"Tension {tension_tier_label(tension_before)} -> {tension_tier_label(tension_after)}")
        if morale_after != morale_before:
            lines.append(f"Morale PNJ: {morale_before} -> {morale_after}")
        if corruption_after != corruption_before:
            lines.append(f"Corruption PNJ: {corruption_before} -> {corruption_after}")
        if player_name and attraction_after != attraction_before:
            lines.append(f"Attraction PNJ: {attraction_before} -> {attraction_after}")
        if tension_after >= 90:
            set_npc_blacklist(profile, until_world_time_minutes=max(0, int(state.world_time_minutes)) + 180)
            lines.append("Le PNJ coupe court a la discussion (rupture).")

    return lines


def _norm_training_text(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", folded).strip()


def _training_intent_from_text(text: str) -> str:
    plain = _norm_training_text(text)
    if any(token in plain for token in ("defense", "defence", "resistance", "bouclier", "parade", "protection", "armure")):
        return "defense"
    if any(token in plain for token in ("soin", "heal", "guerison", "regeneration", "regene")):
        return "soin"
    if any(token in plain for token in ("magie", "sort", "arcane", "mana", "incant")):
        return "magie"
    if any(token in plain for token in ("attaque", "frappe", "combat", "epee", "lame", "duel")):
        return "combat"
    return ""


def _is_training_request_message(text: str) -> bool:
    plain = _norm_training_text(text)
    if not plain:
        return False
    asks = (
        "apprendre",
        "m apprendre",
        "enseigner",
        "m enseigner",
        "entrainer",
        "entrainement",
        "s entrainer",
        "me former",
        "formation",
        "competence",
        "sort",
        "technique",
    )
    return any(token in plain for token in asks)


def _is_training_ready_confirmation(text: str) -> bool:
    plain = _norm_training_text(text)
    if not plain:
        return False
    if re.search(r"\b(oui|ok|okay|daccord|d accord|pret|prets|prete|pretes|vas y|go)\b", plain):
        return True
    if "je suis pret" in plain or "je suis prete" in plain:
        return True
    return False


def _is_training_cancel_message(text: str) -> bool:
    plain = _norm_training_text(text)
    if not plain:
        return False
    return bool(re.search(r"\b(stop|annule|annuler|plus tard|laisse tomber|pas maintenant)\b", plain))


def _looks_like_training_action(text: str) -> bool:
    plain = _norm_training_text(text)
    if not plain:
        return False
    action_tokens = (
        "je lance",
        "je canalise",
        "je vise",
        "je frappe",
        "j attaque",
        "j utilise",
        "j invoque",
        "j active",
        "j essaie",
        "je tente",
        "j execute",
        "je pratique",
        "je me mets",
    )
    if any(token in plain for token in action_tokens):
        return True
    return len(plain) >= 20


def _role_matches_training_skill(skill: SkillDef, npc_role: str) -> bool:
    role = _norm_training_text(npc_role)
    if not role:
        return False
    for token in skill.trainer_roles:
        if _norm_training_text(token) and _norm_training_text(token) in role:
            return True
    return False


def _pick_guided_training_skill(state: GameState, *, intent: str, npc_role: str) -> SkillDef | None:
    _ensure_skill_state(state)
    catalog = state.skill_defs if isinstance(state.skill_defs, dict) else {}
    all_skills = [row for row in catalog.values() if isinstance(row, SkillDef)]
    if not all_skills:
        return None

    known_ids = {
        str(row.get("skill_id") or "").strip().casefold()
        for row in state.player_skills
        if isinstance(row, dict)
    }
    stats = _player_stats_for_training(state)

    filtered = all_skills
    intent_key = str(intent or "").strip().casefold()
    if intent_key:
        intent_matches: list[SkillDef] = []
        for skill in all_skills:
            if _skill_manager.skill_matches_intent(skill, intent_key):
                intent_matches.append(skill)
                continue
            if intent_key in _norm_training_text(skill.name) or intent_key in _norm_training_text(skill.category):
                intent_matches.append(skill)
        if intent_matches:
            filtered = intent_matches

    ranked: list[tuple[int, int, int, int, SkillDef]] = []
    for skill in filtered:
        unknown = 1 if skill.skill_id not in known_ids else 0
        role_hit = 1 if _role_matches_training_skill(skill, npc_role) else 0
        stat_score = sum(max(1, _safe_int(stats.get(key), 5)) for key in skill.primary_stats)
        low_diff_bonus = max(0, 6 - max(1, _safe_int(skill.difficulty, 2)))
        ranked.append((unknown, role_hit, stat_score, low_diff_bonus, skill))

    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3], -len(row[4].name)), reverse=True)
    return ranked[0][4]


def _guided_training_session(state: GameState, *, npc_key: str) -> dict | None:
    flags = _gm_flags(state)
    raw = flags.get(_GUIDED_TRAINING_SESSION_FLAG)
    if not isinstance(raw, dict):
        return None
    if str(raw.get("npc_key") or "").strip() != str(npc_key or "").strip():
        return None
    return raw


def _set_guided_training_session(state: GameState, session: dict) -> None:
    flags = _gm_flags(state)
    flags[_GUIDED_TRAINING_SESSION_FLAG] = dict(session)


def _clear_guided_training_session(state: GameState) -> None:
    flags = _gm_flags(state)
    flags.pop(_GUIDED_TRAINING_SESSION_FLAG, None)


def _guided_training_instruction(*, intent: str, skill_name: str, attempt_index: int) -> str:
    step = max(1, _safe_int(attempt_index, 1))
    if intent == "defense":
        if step == 1:
            return (
                f"Bien. Exercice 1 sur {skill_name}: ancre tes appuis, inspire lentement, "
                "puis dresse une garde devant toi comme si une lame arrivait. Decris ton geste."
            )
        return (
            f"On recommence {skill_name}: garde les coudes stables, canalise l'energie vers l'avant-bras "
            "et maintiens la protection 3 secondes. Decris ton execution."
        )
    if intent == "soin":
        return (
            f"Exercice {step} sur {skill_name}: concentre ton souffle sur la paume, puis applique l'energie "
            "sur une blessure imaginaire sans briser le flux. Decris ton mouvement."
        )
    if intent == "magie":
        return (
            f"Exercice {step} sur {skill_name}: canalise l'energie, vise un point fixe au loin, "
            "et relache l'incantation en une seule pulsation. Decris ton tir."
        )
    return (
        f"Exercice {step} sur {skill_name}: execute une posture nette, puis enchaine une action precise "
        "sans perdre l'equilibre. Decris ta sequence."
    )


def _guided_training_feedback(*, intent: str, skill_name: str, attempt_index: int) -> str:
    if intent == "defense":
        return (
            f"Pas encore. Ta garde sur {skill_name} est trop ouverte. "
            + _guided_training_instruction(intent=intent, skill_name=skill_name, attempt_index=attempt_index + 1)
        )
    if intent == "soin":
        return (
            f"Le flux de {skill_name} est instable. "
            + _guided_training_instruction(intent=intent, skill_name=skill_name, attempt_index=attempt_index + 1)
        )
    if intent == "magie":
        return (
            f"Ton canal sur {skill_name} se disperse trop vite. "
            + _guided_training_instruction(intent=intent, skill_name=skill_name, attempt_index=attempt_index + 1)
        )
    return (
        f"Le mouvement de {skill_name} manque encore de precision. "
        + _guided_training_instruction(intent=intent, skill_name=skill_name, attempt_index=attempt_index + 1)
    )


def _handle_guided_training_turn(
    state: GameState,
    *,
    user_text: str,
    npc_name: str,
    npc_key: str,
    npc_profile: dict | None,
) -> tuple[bool, str, list[str]]:
    _ensure_skill_state(state)
    if not str(npc_key or "").strip():
        _clear_guided_training_session(state)
        return False, "", []

    npc_role = resolve_profile_role(npc_profile, npc_name) if isinstance(npc_profile, dict) else str(npc_name)
    session = _guided_training_session(state, npc_key=npc_key)

    if session and _is_training_cancel_message(user_text):
        _clear_guided_training_session(state)
        return True, "D'accord. On suspend l'entrainement pour maintenant.", []

    if session:
        intent = str(session.get("intent") or "")
        skill_id = str(session.get("skill_id") or "").strip().casefold()
        skill = state.skill_defs.get(skill_id) if isinstance(state.skill_defs, dict) else None
        if not isinstance(skill, SkillDef):
            skill = _pick_guided_training_skill(state, intent=intent, npc_role=npc_role)
            if not isinstance(skill, SkillDef):
                _clear_guided_training_session(state)
                return False, "", []
            session["skill_id"] = skill.skill_id
            session["skill_name"] = skill.name

        stage = str(session.get("stage") or "awaiting_ready")
        attempts = max(0, _safe_int(session.get("attempts"), 0))

        if stage == "awaiting_ready":
            if _is_training_ready_confirmation(user_text):
                session["stage"] = "awaiting_attempt"
                session["updated_at"] = _utc_now_iso()
                _set_guided_training_session(state, session)
                return True, _guided_training_instruction(intent=intent, skill_name=skill.name, attempt_index=attempts + 1), []
            return True, f"Quand tu es pret pour {skill.name}, dis simplement: je suis pret.", []

        if stage != "awaiting_attempt":
            session["stage"] = "awaiting_attempt"
            _set_guided_training_session(state, session)

        if _is_training_ready_confirmation(user_text):
            return True, _guided_training_instruction(intent=intent, skill_name=skill.name, attempt_index=attempts + 1), []

        if not _looks_like_training_action(user_text):
            return (
                True,
                f"Decris ton execution de {skill.name} (posture, canalisation, cible, resultat).",
                [],
            )

        stats = _player_stats_for_training(state)
        attempt = _skill_manager.attempt_learning(
            skill=skill,
            player_stats=stats,
            npc_role=npc_role,
            skill_points=max(0, _safe_int(state.skill_points, 0)),
        )
        state.skill_points = max(0, _safe_int(attempt.get("skill_points_after"), state.skill_points))

        success = bool(attempt.get("success", False))
        chance = max(0, min(100, _safe_int(attempt.get("chance"), 0)))
        roll = max(0, min(100, _safe_int(attempt.get("roll"), 0)))
        reason = str(attempt.get("reason") or "").strip()
        _append_skill_training_log(
            state,
            npc_name=npc_name,
            skill=skill,
            chance=chance,
            roll=roll,
            success=success,
            reason=reason,
        )

        system_lines: list[str] = []
        if success:
            learned, already_known = _upsert_learned_skill(state, skill, npc_name)
            training_xp = _skill_manager.estimate_training_xp_gain(learned, success=True)
            training_progress = _skill_manager.apply_skill_xp(learned, xp_gain=training_xp, used_at_iso=_utc_now_iso())
            training_levels = max(0, _safe_int(training_progress.get("levels_gained"), 0))
            xp_after = max(0, _safe_int(training_progress.get("xp_after"), 0))
            xp_to_next = max(0, _safe_int(training_progress.get("xp_to_next"), 0))

            _append_skill_usage_log(
                state,
                skill_entry=learned,
                xp_gain=training_xp,
                levels_gained=training_levels,
                level_after=max(1, _safe_int(training_progress.get("level_after"), 1)),
                source="training",
                reason=f"Entrainement guide reussi: +{training_xp} XP compet.",
            )
            state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
            _clear_guided_training_session(state)

            if already_known:
                rank = max(1, _safe_int(learned.get("rank"), 1))
                system_lines.append(f"Competence renforcee: {skill.name} (rang {rank}) - jet {roll}/{chance}")
            else:
                system_lines.append(f"Nouvelle competence apprise: {skill.name} - jet {roll}/{chance}")
            if xp_to_next > 0:
                system_lines.append(f"{skill.name}: +{training_xp} XP compet. ({xp_after}/{xp_to_next})")
            if training_levels > 0:
                system_lines.append(f"⬆️ {skill.name} niveau +{training_levels}")
            if reason:
                system_lines.append(reason)
            return (
                True,
                f"Bien execute. Tu stabilises {skill.name}. Continue de le pratiquer en situation reelle.",
                system_lines,
            )

        attempts += 1
        session["attempts"] = attempts
        session["updated_at"] = _utc_now_iso()
        _set_guided_training_session(state, session)
        system_lines.append(f"Entrainement rate pour {skill.name} (jet {roll}/{chance}).")
        if reason:
            system_lines.append(reason)

        if state.skill_points <= 0:
            _clear_guided_training_session(state)
            return True, "On s'arrete ici: tu n'as plus de points de competence pour aujourd'hui.", system_lines
        if attempts >= 3:
            _clear_guided_training_session(state)
            return True, f"On stoppe pour maintenant. Reviens plus tard pour reprendre {skill.name}.", system_lines

        return True, _guided_training_feedback(intent=intent, skill_name=skill.name, attempt_index=attempts), system_lines

    if not _is_training_request_message(user_text):
        return False, "", []

    intent = _training_intent_from_text(user_text)
    skill = _pick_guided_training_skill(state, intent=intent, npc_role=npc_role)
    if not isinstance(skill, SkillDef):
        return False, "", []

    _set_guided_training_session(
        state,
        {
            "npc_key": npc_key,
            "npc_name": npc_name,
            "intent": intent,
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "stage": "awaiting_ready",
            "attempts": 0,
            "started_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        },
    )
    intent_hint = f" axe {intent}" if intent else ""
    return (
        True,
        f"Je peux te former sur {skill.name} ({skill.category}){intent_hint}. Dis \"je suis pret\" et on commence l'exercice.",
        [],
    )


def _apply_world_and_story_progress(state: GameState) -> None:
    in_dungeon = _active_dungeon_run(state) is not None
    _ensure_world_pressure_state(state)
    _apply_world_pressure_effects(state, in_dungeon=in_dungeon)

    for line in _world_apply_time_events(
        state,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
        current_anchor=str(state.current_scene().map_anchor or state.current_scene().title or "Lumeria"),
        in_dungeon=in_dungeon,
    ):
        if isinstance(line, str) and line.strip():
            state.push("Système", line.strip(), count_for_media=False)

    for line in _story_progress_main_story(
        state,
        safe_int=_safe_int,
        utc_now_iso=_utc_now_iso,
    ):
        if isinstance(line, str) and line.strip():
            state.push("Système", line.strip(), count_for_media=False)


def _ensure_world_pressure_state(state: GameState) -> None:
    state.sync_world_state()
    if not isinstance(state.faction_states, dict):
        state.faction_states = {}
    if not state.faction_states:
        state.faction_states = {
            "Marchands": {
                "power_level": 58,
                "brutality_index": 22,
                "corruption_index": 44,
                "relations": {"Habitants": 12, "Aventuriers": 8},
            },
            "Milice Urbaine": {
                "power_level": 63,
                "brutality_index": 54,
                "corruption_index": 36,
                "relations": {"Marchands": 5, "Habitants": -6},
            },
            "Bas-Fonds": {
                "power_level": 49,
                "brutality_index": 71,
                "corruption_index": 73,
                "relations": {"Milice Urbaine": -22, "Marchands": -12},
            },
        }


def _apply_world_pressure_effects(state: GameState, *, in_dungeon: bool = False) -> None:
    ws = state.world_state if isinstance(state.world_state, dict) else {}
    instability = max(0, min(100, _safe_int(ws.get("instability_level"), 0)))
    global_tension = max(0, min(100, _safe_int(ws.get("global_tension"), 0)))

    low_rep_pressure = 0
    if isinstance(state.faction_reputation, dict):
        for value in state.faction_reputation.values():
            score = _safe_int(value, 0)
            if score <= -30:
                low_rep_pressure += 1
    if low_rep_pressure > 0:
        global_tension = max(0, min(100, global_tension + min(4, low_rep_pressure)))

    faction_pressure = 0
    for faction_name, payload in state.faction_states.items():
        if not isinstance(payload, dict):
            continue
        brutality = max(0, min(100, _safe_int(payload.get("brutality_index"), 0)))
        corruption = max(0, min(100, _safe_int(payload.get("corruption_index"), 0)))
        rep = _safe_int(state.faction_reputation.get(faction_name), 0)
        if rep <= -40 and brutality >= 55:
            faction_pressure += 1
        if corruption >= 70:
            faction_pressure += 1
    if faction_pressure > 0:
        instability = max(0, min(100, instability + min(3, faction_pressure)))
        global_tension = max(0, min(100, global_tension + min(3, faction_pressure)))

    ws["instability_level"] = instability
    ws["global_tension"] = global_tension
    state.world_state = ws

    if in_dungeon:
        return

    trigger_crime = instability >= 70 and _loot_manager.rng.random() < 0.12
    trigger_attack = global_tension >= 75 and _loot_manager.rng.random() < 0.08
    if trigger_crime:
        state.push("Système", "⚠️ Un crime éclate non loin; la ville se raidit.", count_for_media=False)
    if trigger_attack:
        state.push("Système", "⚠️ Une rixe brutale force les gardes a se redeployer.", count_for_media=False)

    if (trigger_crime or trigger_attack) and state.selected_npc:
        profile = _selected_npc_profile(state)
        if isinstance(profile, dict):
            before = profile_tension_level(profile)
            apply_tension_delta(profile, delta=2 if trigger_crime else 3, reason="world_pressure")
            after = profile_tension_level(profile)
            if after != before:
                _publish_tension_change(
                    state,
                    npc_key=_selected_npc_conversation_key(state) or "",
                    npc_name=str(state.selected_npc or ""),
                    old_value=before,
                    new_value=after,
                    reason="world_pressure",
                )


def _norm_destination_hint(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", raw).strip()


def _resolve_travel_scene_id_from_hint(state: GameState, hint: str) -> str:
    raw_hint = str(hint or "").strip()
    if not raw_hint:
        return ""
    if raw_hint in state.scenes:
        return raw_hint

    folded_hint = _norm_destination_hint(raw_hint)
    if not folded_hint:
        return ""

    for anchor in MAP_ANCHORS:
        if folded_hint == _norm_destination_hint(anchor):
            if anchor in state.anchor_last_scene and state.anchor_last_scene[anchor] in state.scenes:
                return str(state.anchor_last_scene[anchor])
            for scene_id, scene in state.scenes.items():
                if str(scene.map_anchor or "") == anchor:
                    return scene_id

    best_scene_id = ""
    best_score = -1
    for scene_id, scene in state.scenes.items():
        title_folded = _norm_destination_hint(scene.title)
        anchor_folded = _norm_destination_hint(scene.map_anchor)
        score = 0
        if folded_hint == title_folded or folded_hint == anchor_folded:
            score = 100
        elif folded_hint in title_folded or title_folded in folded_hint:
            score = 60
        elif folded_hint in anchor_folded or anchor_folded in folded_hint:
            score = 45
        if score > best_score:
            best_score = score
            best_scene_id = scene_id

    if best_score >= 45:
        return best_scene_id
    return ""


def _travel_status_text(state: GameState) -> str:
    travel = _travel_state(state)
    status = str(travel.status or "idle")
    if status == "idle":
        return "Aucun trajet en cours."
    total = max(1, _safe_int(travel.total_distance, 1))
    progress = max(0, _safe_int(travel.progress, 0))
    danger = max(0, min(100, _safe_int(travel.danger_level, 0)))
    fatigue = max(0, min(100, _safe_int(travel.fatigue, 0)))
    from_title = state.scenes.get(str(travel.from_location_id or ""))
    to_title = state.scenes.get(str(travel.to_location_id or ""))
    from_label = from_title.title if isinstance(from_title, Scene) else str(travel.from_location_id or "")
    to_label = to_title.title if isinstance(to_title, Scene) else str(travel.to_location_id or "")
    return (
        f"Trajet {status}: {from_label} -> {to_label} | "
        f"{progress}/{total} | danger {danger} | fatigue {fatigue}"
    )


def _start_travel_from_hint(state: GameState, hint: str) -> tuple[bool, str]:
    if _travel_in_progress(state):
        return False, "Un trajet est déjà en cours."

    scene_id = _resolve_travel_scene_id_from_hint(state, hint)
    if not scene_id:
        return False, "Destination introuvable."

    destination = state.scenes.get(scene_id)
    if not isinstance(destination, Scene):
        return False, "Destination invalide."

    if is_nsfw_scene(destination) and not is_nsfw_mode_enabled(state):
        return False, "Zone restreinte: active le Mode Adulte pour y aller."

    can_access, rep_hint = _rep_can_access_scene(
        state,
        scene_id=destination.id,
        scene_title=destination.title,
    )
    if not can_access:
        return False, str(rep_hint or "Acces refuse.")

    is_open, status_hint = scene_open_status(destination, state.world_time_minutes)
    if not is_open:
        return False, str(status_hint or "Lieu ferme.")

    ok, message = _start_travel_to_scene(state, scene_id)
    if ok:
        state.selected_npc = None
        _set_local_entry_target(state, "")
    return ok, message


def _run_travel_action_command(state: GameState, action: str) -> tuple[bool, str]:
    ok, lines = _tick_travel(state, action=action)
    if ok:
        _update_quests_and_notify(state)
        _apply_world_and_story_progress(state)
    text = "\n".join(str(line).strip() for line in lines if str(line).strip())
    return ok, text or ("Action voyage appliquée." if ok else "Action voyage refusée.")


def _run_travel_choice_command(state: GameState, option_id: str) -> tuple[bool, str]:
    ok, lines = _resolve_travel_event_choice(state, option_id)
    if ok:
        _update_quests_and_notify(state)
        _apply_world_and_story_progress(state)
    text = "\n".join(str(line).strip() for line in lines if str(line).strip())
    return ok, text or ("Choix voyage appliqué." if ok else "Choix voyage invalide.")


def _run_travel_abort_command(state: GameState, *, return_back: bool = False) -> tuple[bool, str]:
    ok, text = _abort_travel(state, return_back=return_back)
    return ok, str(text or "")


def _looks_like_travel_request(text: str) -> bool:
    folded = _norm_destination_hint(text)
    if not folded:
        return False
    tokens = (
        "aller ",
        "aller a",
        "aller vers",
        "je vais",
        "je veux aller",
        "partir vers",
        "voyager",
        "route vers",
        "me rendre",
    )
    return any(token in folded for token in tokens)


def _extract_travel_hint_from_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lower = _norm_destination_hint(raw)
    patterns = [
        r"(?:aller|vais|partir|voyager|rendre)\s+(?:a|au|aux|vers)?\s*(.+)$",
        r"(?:direction|route)\s+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower)
        if m:
            return m.group(1).strip()
    return raw


def _handle_local_chat_command(state: GameState, text: str) -> tuple[bool, str, str]:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return False, "", raw

    parts = raw.split()
    if not parts:
        return False, "", raw

    cmd = parts[0].casefold()
    if cmd in {"/story", "/chapitre"}:
        return True, _story_status_text(state), raw

    if cmd in {"/verbose", "/v"}:
        if len(parts) < 2:
            enabled = bool(_gm_flags(state).get("verbose_dialogue"))
            return True, f"/verbose {'on' if enabled else 'off'}", raw
        choice = str(parts[1] or "").strip().casefold()
        if choice not in {"on", "off"}:
            return True, "Usage: /verbose on|off", raw
        enabled = choice == "on"
        _gm_flags(state)["verbose_dialogue"] = enabled
        return True, f"Mode verbose {'active' if enabled else 'desactive'}.", raw

    if cmd in {"/decision_mode", "/decisionmode"}:
        if len(parts) < 2:
            enabled = bool(_gm_flags(state).get("decision_mode_v2", True))
            return True, f"/decision_mode {'on' if enabled else 'off'}", raw
        choice = str(parts[1] or "").strip().casefold()
        if choice not in {"on", "off"}:
            return True, "Usage: /decision_mode on|off", raw
        enabled = choice == "on"
        _gm_flags(state)["decision_mode_v2"] = enabled
        return True, f"Decision mode v2 {'active' if enabled else 'desactive'}.", raw

    if cmd in {"/ia", "/ai"}:
        if len(parts) < 2 or str(parts[1] or "").strip().casefold() in {"status", "etat"}:
            enabled = _ai_enabled(state)
            mode = "active" if enabled else "desactivee"
            return True, f"IA locale {mode}.", raw
        choice = str(parts[1] or "").strip().casefold()
        if choice not in {"on", "off"}:
            return True, "Usage: /ia on|off|status", raw
        enabled = choice == "on"
        _set_ai_enabled(state, enabled)
        return True, f"IA locale {'active' if enabled else 'desactivee'}.", raw

    if cmd in {"/travel", "/voyage"}:
        if len(parts) == 1 or str(parts[1] or "").strip().casefold() in {"help", "aide", "?"}:
            return (
                True,
                "Commandes voyage:\n"
                "- /travel status\n"
                "- /travel continue|accelerate|detour|camp\n"
                "- /travel choice <option_id>\n"
                "- /travel abort|return\n"
                "- /travel <destination>",
                raw,
            )

        sub = str(parts[1] or "").strip().casefold()
        if sub in {"status", "etat"}:
            return True, _travel_status_text(state), raw
        if sub in {"continue", "accelerate", "detour", "camp"}:
            _, text_out = _run_travel_action_command(state, sub)
            return True, text_out, raw
        if sub in {"choice", "choix"}:
            if len(parts) < 3:
                return True, "Usage: /travel choice <option_id>", raw
            _, text_out = _run_travel_choice_command(state, str(parts[2] or ""))
            return True, text_out, raw
        if sub in {"abort", "abandon", "annuler"}:
            _, text_out = _run_travel_abort_command(state, return_back=False)
            return True, text_out, raw
        if sub in {"return", "back", "retour"}:
            _, text_out = _run_travel_abort_command(state, return_back=True)
            return True, text_out, raw

        hint = " ".join(parts[1:]).strip()
        ok, message = _start_travel_from_hint(state, hint)
        return True, message if ok else f"Voyage refusé: {message}", raw

    if cmd == "/quest":
        if len(parts) == 1 or parts[1].casefold() in {"help", "aide", "?"}:
            return (
                True,
                "Commandes quete:\n"
                "- /quest list\n"
                "- /quest choose <quest_id> <option_id>",
                raw,
            )
        sub = parts[1].casefold()
        if sub in {"list", "ls"}:
            quests = _quest_active_quests(state)
            if not quests:
                return True, "Aucune quete active.", raw
            lines: list[str] = []
            for quest in quests[:20]:
                quest_id = str(quest.get("id") or "?")
                title = str(quest.get("title") or "Quete")
                progress = quest.get("progress") if isinstance(quest.get("progress"), dict) else {}
                current = max(0, _safe_int(progress.get("current"), 0))
                target = max(1, _safe_int(progress.get("target"), 1))
                line = f"{quest_id} - {title} ({current}/{target})"
                branch_summary = _quest_branch_options_summary(quest)
                if branch_summary:
                    branching = quest.get("branching") if isinstance(quest.get("branching"), dict) else {}
                    chosen = str(branching.get("selected_option_id") or "").strip()
                    if chosen:
                        line += f" | branche: {chosen}"
                    else:
                        line += f" | options: {branch_summary}"
                lines.append(line)
            return True, "\n".join(lines), raw
        if sub in {"choose", "choix"}:
            active_rows = _quest_active_quests(state)
            if len(parts) >= 4:
                quest_id_arg = str(parts[2] or "").strip()
                option_id_arg = str(parts[3] or "").strip()
            elif len(parts) == 3 and len(active_rows) == 1:
                quest_id_arg = str(active_rows[0].get("id") or "").strip()
                option_id_arg = str(parts[2] or "").strip()
            elif len(parts) == 3:
                quest_ids = [str(row.get("id") or "").strip() for row in active_rows if isinstance(row, dict)]
                if not quest_ids:
                    return True, "Aucune quete active.", raw
                return (
                    True,
                    "Plusieurs quetes actives. Usage: /quest choose <quest_id> <option_id>\n"
                    "Actives: " + ", ".join(quest_ids[:20]),
                    raw,
                )
            else:
                return True, "Usage: /quest choose <quest_id> <option_id>", raw
            ok, message = _quest_choose_branch(
                state,
                quest_id=quest_id_arg,
                option_id=option_id_arg,
                safe_int=_safe_int,
                utc_now_iso=_utc_now_iso,
            )
            if ok:
                _update_quests_and_notify(state)
            return True, message, raw
        return True, "Sous-commande quete inconnue. Utilise /quest help.", raw

    if cmd == "/craft":
        _ensure_item_state(state)
        if len(parts) == 1 or parts[1].casefold() in {"help", "aide", "?"}:
            return (
                True,
                "Commandes craft:\n"
                "- /craft list\n"
                "- /craft <recipe_id> [qty]",
                raw,
            )

        if parts[1].casefold() in {"list", "ls"}:
            listing = _craft_manager.list_recipes_text(item_defs=state.item_defs)
            return True, listing, raw

        recipe_id = str(parts[1] or "").strip().casefold()
        qty = 1
        if len(parts) >= 3:
            qty = max(1, _safe_int(parts[2], 1))

        outcome = _craft_manager.craft(
            state=state,
            recipe_id=recipe_id,
            qty=qty,
            item_defs=state.item_defs if isinstance(state.item_defs, dict) else {},
            scene_title=str(state.current_scene().title or ""),
        )
        lines = outcome.get("lines") if isinstance(outcome.get("lines"), list) else []
        text_out = "\n".join(str(line).strip() for line in lines if str(line).strip())
        if outcome.get("ok"):
            _update_quests_and_notify(state)
        return True, text_out or "Aucun resultat.", raw

    return False, "", raw


def _prepare_choice_message_for_gm(state: GameState, text: str) -> tuple[bool, str, str]:
    raw = str(text or "").strip()
    parts = raw.split()
    if not parts or parts[0].casefold() != "/choice":
        return False, "", raw
    if len(parts) < 2:
        return True, "Usage: /choice <option_id>", raw

    option_id = str(parts[1] or "").strip().casefold()
    option = _find_pending_choice_option(state, option_id)
    if not isinstance(option, dict):
        available = ", ".join(
            str(row.get("id") or "").strip()
            for row in state.pending_choice_options
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        )
        if available:
            return True, f"Option inconnue. Disponibles: {available}", raw
        return True, "Aucun choix actif.", raw

    label = str(option.get("text") or option_id).strip()
    patch = option.get("state_patch") if isinstance(option.get("state_patch"), dict) else {}
    lines = _apply_extended_state_patch_for_choice(
        state,
        patch=patch,
        npc_key=state.pending_choice_source_npc_key or _selected_npc_conversation_key(state),
        npc_name=str(state.selected_npc or ""),
    )
    flags = _gm_flags(state)
    flags["last_choice_option_id"] = option_id
    flags["last_choice_option_text"] = label[:120]
    _clear_pending_choice(state)

    if lines:
        state.push("Système", "Choix: " + " | ".join(lines), count_for_media=False)

    gm_text = f"Je choisis {option_id}: {label}"
    return True, "", gm_text


def _set_local_entry_target(state: GameState, scene_id: str) -> None:
    flags = _gm_flags(state)
    clean = str(scene_id or "").strip()
    if clean:
        flags[_LOCAL_ENTRY_TARGET_FLAG] = clean
    else:
        flags.pop(_LOCAL_ENTRY_TARGET_FLAG, None)


def _get_local_entry_target_scene(state: GameState) -> Scene | None:
    flags = _gm_flags(state)
    scene_id = str(flags.get(_LOCAL_ENTRY_TARGET_FLAG) or "").strip()
    if not scene_id:
        return None
    scene = state.scenes.get(scene_id)
    if not isinstance(scene, Scene):
        _set_local_entry_target(state, "")
        return None
    if str(scene.map_anchor or "").strip() != str(state.current_scene().map_anchor or "").strip():
        _set_local_entry_target(state, "")
        return None
    if not is_building_scene_title(scene.title):
        _set_local_entry_target(state, "")
        return None
    if is_nsfw_scene(scene) and not is_nsfw_mode_enabled(state):
        return None
    return scene


def _find_choice_to_scene(scene: Scene, target_scene_id: str) -> Choice | None:
    target_id = str(target_scene_id or "").strip()
    for choice in scene.choices:
        if str(choice.next_scene_id or "").strip() == target_id:
            return choice
    return None


def _combat_quick_skill_rows(state: GameState, *, limit: int = 18) -> list[dict]:
    rows: list[dict] = []
    known = state.player_skills if isinstance(state.player_skills, list) else []
    for row in known:
        if not isinstance(row, dict):
            continue
        skill_id = str(row.get("skill_id") or "").strip().casefold()
        name = str(row.get("name") or skill_id).strip()
        if not skill_id or not name:
            continue
        rows.append(
            {
                "skill_id": skill_id,
                "name": name[:80],
                "category": str(row.get("category") or "").strip().casefold(),
                "level": max(1, _safe_int(row.get("level"), 1)),
            }
        )
    rows.sort(key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("skill_id") or "")))
    return rows[: max(1, int(limit))]


def _combat_quick_action_text(action_key: str, *, skill_row: dict | None = None) -> str:
    key = str(action_key or "").strip().casefold()
    if key == "attack":
        return "j'attaque l'ennemi"
    if key == "defend":
        return "je me mets en defense"
    if key == "spell":
        return "je lance un sort offensif"
    if key == "heal":
        return "je lance un sort de soin"

    if key == "skill" and isinstance(skill_row, dict):
        name = str(skill_row.get("name") or skill_row.get("skill_id") or "competence").strip() or "competence"
        category = str(skill_row.get("category") or "").strip().casefold()
        if any(token in category for token in ("soin", "sacre", "heal")):
            return f"j'utilise {name} pour me soigner"
        if any(token in category for token in ("defense", "bouclier", "parade")):
            return f"j'utilise {name} en defense"
        if any(token in category for token in ("magie", "arcane", "sort")):
            return f"j'utilise {name} comme sort"
        return f"j'utilise {name} pour attaquer"

    return "j'attaque"


async def _send_combat_quick_action(
    state: GameState,
    *,
    action_text: str,
    client,
    on_change,
    chat_command_handler=None,
) -> None:
    if _chat_turn_busy(state):
        return

    run = _active_dungeon_run(state)
    if not _active_dungeon_combat(run):
        state.push("Système", "Aucun combat actif.", count_for_media=False)
        on_change()
        _refresh_chat_messages_view()
        return

    synthetic_input = _TransientInput(action_text)
    state.chat_draft = action_text
    await _send_to_npc(
        state,
        synthetic_input,
        client,
        on_change,
        chat_command_handler=chat_command_handler,
    )


def _enter_local_entry_target_from_dialogue(state: GameState, on_change) -> None:
    target = _get_local_entry_target_scene(state)
    if not isinstance(target, Scene):
        state.push("Système", "Aucun bâtiment sélectionné sur la map.", count_for_media=False)
        on_change()
        _refresh_chat_messages_view()
        return

    if is_nsfw_scene(target) and not is_nsfw_mode_enabled(state):
        state.push("Système", "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.", count_for_media=False)
        on_change()
        _refresh_chat_messages_view()
        return

    entry_choice = _find_choice_to_scene(state.current_scene(), target.id)
    if entry_choice is None:
        state.push("Système", "Aucune entrée accessible depuis cette position.", count_for_media=False)
        on_change()
        _refresh_chat_messages_view()
        return

    _set_local_entry_target(state, "")
    _apply_choice(state, entry_choice, on_change)


def _dungeon_gold_gain(event_type: str, floor: int) -> int:
    kind = str(event_type or "").strip().casefold()
    f = max(1, _safe_int(floor, 1))
    if kind == "monster":
        base = _loot_manager.rng.randint(4, 10)
    elif kind == "mimic":
        base = _loot_manager.rng.randint(10, 22)
    elif kind == "treasure":
        base = _loot_manager.rng.randint(8, 18)
    elif kind == "boss":
        base = _loot_manager.rng.randint(24, 42)
    else:
        return 0
    return max(0, base + max(0, f))


def _potion_hint_for_drop(floor: int) -> str:
    hints = [
        "potion de soin",
        "potion de mana",
        "potion de force",
        "potion de dexterite",
        "potion d'agilite",
        "potion de defense",
    ]
    if floor >= 10:
        hints.append("potion de sagesse")
    if floor >= 16:
        hints.append("potion de magie")
    return str(_loot_manager.rng.choice(hints))


def _potion_bonus_drop_chance(event_type: str) -> float:
    return {
        "monster": 0.25,
        "mimic": 0.42,
        "treasure": 0.28,
        "boss": 0.65,
    }.get(str(event_type or "").strip().casefold(), 0.22)


def _grant_generated_loot(
    state: GameState,
    *,
    loot: dict,
    prefix: str,
    create_prefix: str = "Nouvel objet cree",
) -> bool:
    item_id, updated_defs, created_new = _loot_manager.ensure_item_exists(loot, state.item_defs)
    state.item_defs = updated_defs

    qty = max(1, _safe_int(loot.get("qty"), 1))
    granted = _grant_item_reward(state, item_id, qty)
    rarity = str(loot.get("rarity") or "").strip().casefold()
    label = _item_display_name(state, item_id)

    if created_new:
        state.push("Système", f"{create_prefix}: {label} ({item_id})", count_for_media=False)

    if granted <= 0:
        state.push("Système", f"{prefix} perdu (inventaire plein): {label} x{qty}", count_for_media=False)
        return False

    line = f"{prefix}: {label} x{granted}"
    if rarity:
        line += f" [{rarity}]"
    if granted < qty:
        line += " (inventaire limite)"
    state.push("Système", line, count_for_media=False)
    return True


def _active_dungeon_run(state: GameState) -> dict | None:
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if not run:
        return None
    if bool(run.get("completed", False)):
        return None
    return run


def _active_dungeon_combat(run: dict | None) -> dict | None:
    if not isinstance(run, dict):
        return None
    combat = run.get("combat") if isinstance(run.get("combat"), dict) else None
    if not combat:
        return None
    if not bool(combat.get("active", True)):
        return None
    return combat


def _set_player_hp_from_combat(state: GameState, hp_value: int) -> None:
    max_hp = max(1, _safe_int(getattr(state.player, "max_hp", 1), 1))
    hp = min(max(0, _safe_int(hp_value, max_hp)), max_hp)
    state.player.hp = hp

    if isinstance(state.player_sheet, dict):
        stats = state.player_sheet.get("stats")
        if isinstance(stats, dict):
            stats["pv"] = hp
        effective = state.player_sheet.get("effective_stats")
        if isinstance(effective, dict):
            effective["pv"] = hp


def _start_dungeon_combat(state: GameState, run: dict, event: dict) -> bool:
    if not _is_dungeon_combat_event(event):
        return False
    if _active_dungeon_combat(run):
        return True

    run["completion_pending"] = bool(run.get("completed", False))
    run["completed"] = False
    event_with_context = dict(event)
    run_relic = run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None
    if isinstance(run_relic, dict):
        event_with_context["run_relic"] = run_relic
    run["current_event"] = event_with_context
    run["combat"] = _build_dungeon_combat_state(
        event_with_context,
        rng=_loot_manager.rng,
        monster_manager=_monster_manager,
    )
    combat = run.get("combat") if isinstance(run.get("combat"), dict) else {}
    enemy = str(combat.get("enemy_name") or event.get("name") or "Adversaire")
    enemy_hp = max(1, _safe_int(combat.get("enemy_hp"), 1))
    state.push("Système", f"⚔️ Combat engage contre {enemy} ({enemy_hp} PV).", count_for_media=False)
    state.push(
        "Système",
        "Actions disponibles via chat: attaque, defense, sort, soin. Le d20 decide l'issue.",
        count_for_media=False,
    )
    return True


async def _finalize_dungeon_floor_clear(state: GameState, run: dict, event: dict) -> None:
    event_type = str(event.get("type") or "").strip().casefold()
    floor = max(1, _safe_int(event.get("floor"), _safe_int(run.get("current_floor"), 1)))
    if event_type == "boss":
        state.push("Système", "⚔️ Boss de fin vaincu!", count_for_media=False)

    state.quest_counters["dungeon_floors_cleared"] = max(
        0,
        _safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0) + 1,
    )

    try:
        await _maybe_award_loot_from_dungeon_event(state, event)
    except Exception as e:
        state.push("Système", f"Loot indisponible sur cet etage: {e}", count_for_media=False)

    rep_lines = _rep_apply_dungeon_reputation(
        state,
        floor=floor,
        event_type=event_type,
    )
    if rep_lines:
        state.push("Système", "Réputation: " + " | ".join(rep_lines), count_for_media=False)

    run.pop("combat", None)
    run.pop("current_event", None)
    if bool(run.pop("completion_pending", False)):
        run["completed"] = True

    _sync_dungeon_gm_context(state, run)
    if bool(run.get("completed", False)):
        state.push("Système", "Vous atteignez la fin du donjon et ressortez charge d'histoires.", count_for_media=False)
        state.active_dungeon_run = None
        _sync_dungeon_gm_context(state, None)

    _update_quests_and_notify(state)


async def _handle_active_dungeon_combat_action(state: GameState, text: str) -> list[str]:
    run = _active_dungeon_run(state)
    combat = _active_dungeon_combat(run)
    if not run or not combat:
        return []

    skill_lines: list[str] = []
    repeat_heal_requested = _wants_repeat_heal_until_full(text)
    max_turns = _MAX_AUTO_HEAL_CASTS if repeat_heal_requested else 1
    if repeat_heal_requested and _safe_int(getattr(state.player, "hp", 0), 0) >= _safe_int(getattr(state.player, "max_hp", 1), 1):
        repeat_heal_requested = False
        max_turns = 1
        state.push("Système", "Vous etes deja soigne au maximum.", count_for_media=False)

    reached_turn_cap = False

    for turn_index in range(max_turns):
        run = _active_dungeon_run(state)
        combat = _active_dungeon_combat(run)
        if not run or not combat:
            break

        hp_before = _safe_int(getattr(state.player, "hp", 0), 0)
        runtime_stat_bonuses = _get_consumable_stat_bonus_totals(state)
        result = _resolve_dungeon_combat_turn(
            combat_state=combat,
            action_text=text,
            player_hp=hp_before,
            player_max_hp=_safe_int(getattr(state.player, "max_hp", 1), 1),
            player_sheet=state.player_sheet if isinstance(state.player_sheet, dict) else {},
            known_skills=state.player_skills if isinstance(state.player_skills, list) else [],
            runtime_stat_bonuses=runtime_stat_bonuses,
            skill_manager=_skill_manager,
            rng=_loot_manager.rng,
            run_relic=run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None,
        )

        run["combat"] = result.get("combat") if isinstance(result.get("combat"), dict) else combat
        _set_player_hp_from_combat(state, _safe_int(result.get("player_hp"), state.player.hp))
        hp_after = _safe_int(getattr(state.player, "hp", 0), 0)

        for line in result.get("lines") if isinstance(result.get("lines"), list) else []:
            if not isinstance(line, str) or not line.strip():
                continue
            state.push("Système", line, count_for_media=False)

        if repeat_heal_requested and str(result.get("action_kind") or "").strip().casefold() == "heal":
            heal_gain = max(0, hp_after - hp_before)
            state.push(
                "Système",
                f"Sort de soin {turn_index + 1}: +{heal_gain} PV ({hp_after}/{state.player.max_hp}).",
                count_for_media=False,
            )

        try:
            turn_skill_lines = _apply_skill_usage_progress_from_text(state, text)
            if turn_skill_lines:
                skill_lines.extend(turn_skill_lines)
        except Exception:
            pass

        expired_buffs = _tick_consumable_buffs(state)
        for buff in expired_buffs:
            if not isinstance(buff, dict):
                continue
            label = str(buff.get("item_name") or "").strip() or str(buff.get("stat") or "bonus").strip()
            state.push("Système", f"Effet termine: {label}.", count_for_media=False)

        state.advance_world_time(6)
        _apply_world_and_story_progress(state)

        if bool(result.get("defeat", False)):
            recovery = max(1, _safe_int(state.player.max_hp, 1) // 2)
            _set_player_hp_from_combat(state, recovery)
            state.push("Système", "On vous evacue du carnage en urgence, couvert de sang.", count_for_media=False)
            state.push("Système", f"Vous revenez recousu a la hate: {recovery}/{state.player.max_hp} PV.", count_for_media=False)
            state.active_dungeon_run = None
            _sync_dungeon_gm_context(state, None)
            _update_quests_and_notify(state)
            return skill_lines[:12]

        if bool(result.get("victory", False)):
            event = run.get("current_event") if isinstance(run.get("current_event"), dict) else {}
            if not event:
                combat_ref = run.get("combat") if isinstance(run.get("combat"), dict) else {}
                event = {
                    "type": str(combat_ref.get("event_type") or "monster"),
                    "floor": _safe_int(combat_ref.get("floor"), _safe_int(run.get("current_floor"), 1)),
                    "name": str(combat_ref.get("enemy_name") or "Adversaire"),
                }
            await _finalize_dungeon_floor_clear(state, run, event)
            return skill_lines[:12]

        if repeat_heal_requested and hp_after >= _safe_int(getattr(state.player, "max_hp", 1), 1):
            state.push("Système", "Vous etes completement soigne.", count_for_media=False)
            break

        if repeat_heal_requested and turn_index + 1 >= max_turns:
            reached_turn_cap = True

    run = _active_dungeon_run(state)
    combat_now = _active_dungeon_combat(run)
    if combat_now:
        enemy = str(combat_now.get("enemy_name") or "Adversaire")
        enemy_hp = max(0, _safe_int(combat_now.get("enemy_hp"), 0))
        enemy_max_hp = max(1, _safe_int(combat_now.get("enemy_max_hp"), enemy_hp))
        state.push(
            "Système",
            f"Etat combat -> {enemy}: {enemy_hp}/{enemy_max_hp} PV | Vous: {state.player.hp}/{state.player.max_hp} PV.",
            count_for_media=False,
        )
    if run:
        _sync_dungeon_gm_context(state, run)
    if repeat_heal_requested and reached_turn_cap and _safe_int(getattr(state.player, "hp", 0), 0) < _safe_int(getattr(state.player, "max_hp", 1), 1):
        state.push(
            "Système",
            f"Sequence de soins stoppee apres {_MAX_AUTO_HEAL_CASTS} lancers pour eviter le spam.",
            count_for_media=False,
        )
    return skill_lines[:12]


async def _maybe_award_loot_from_dungeon_event(state: GameState, event: dict) -> None:
    if not isinstance(event, dict):
        return
    _ensure_item_state(state)

    event_type = str(event.get("type") or "").strip().casefold()
    chances = {
        "monster": 0.35,
        "mimic": 0.75,
        "treasure": 1.0,
        "boss": 1.0,
    }
    chance = chances.get(event_type, 0.0)
    if chance <= 0.0:
        return
    if _loot_manager.rng.random() > chance:
        return

    floor = max(1, _safe_int(event.get("floor"), 1))
    gold_gain = _dungeon_gold_gain(event_type, floor)
    if gold_gain > 0:
        state.player.gold += gold_gain
        state.push("Système", f"Butin: +{gold_gain} or", count_for_media=False)

    anchor = ""
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if run:
        anchor = str(run.get("anchor") or "").strip()
    if not anchor:
        anchor = str(state.current_scene().map_anchor or state.current_scene().title or "Lumeria")

    hint_text = ""
    if event_type == "monster":
        hint_text = str(
            event.get("monster_base_name")
            or event.get("name")
            or event.get("monster_id")
            or ""
        ).strip()
    elif event_type == "treasure":
        hint_text = str(event.get("loot") or "").strip()
    elif event_type == "mimic":
        hint_text = str(event.get("loot_lure") or event.get("loot") or "").strip()

    loot = await _loot_manager.generate_loot(
        source_type=event_type or "treasure",
        floor=floor,
        anchor=anchor,
        known_items=state.item_defs,
        hint_text=hint_text,
    )
    _grant_generated_loot(
        state,
        loot=loot,
        prefix="Butin obtenu",
    )

    if event_type == "boss":
        bonus_loot = await _loot_manager.generate_loot(
            source_type="boss",
            floor=floor + 2,
            anchor=anchor,
            known_items=state.item_defs,
            hint_text=str(event.get("loot") or "").strip(),
        )
        _grant_generated_loot(
            state,
            loot=bonus_loot,
            prefix="Butin du boss",
        )

    if _loot_manager.rng.random() <= _potion_bonus_drop_chance(event_type):
        potion_loot = await _loot_manager.generate_loot(
            source_type=event_type or "treasure",
            floor=max(1, floor),
            anchor=anchor,
            known_items=state.item_defs,
            hint_text=_potion_hint_for_drop(floor),
        )
        _grant_generated_loot(
            state,
            loot=potion_loot,
            prefix="Potion trouvee",
        )


def center_dialogue(state: GameState, on_change, chat_command_handler=None) -> None:
    _ensure_quest_state(state)
    _ensure_player_sheet_state(state)
    ensure_conversation_memory_state(state)
    ensure_npc_world_state(state)
    sync_npc_registry_from_profiles(state)
    _apply_world_and_story_progress(state)

    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Dialogues").classes("text-lg font-semibold")
        ui.label("Temps du monde: " + format_fantasy_datetime(state.world_time_minutes)).classes("text-xs").style(
            "padding:4px 10px; border-radius:999px; "
            "background:rgba(20,20,20,0.85); color:#f2f2f2; border:1px solid rgba(255,255,255,0.16); "
            "backdrop-filter: blur(2px);"
        )
    ui.separator()

    with ui.card().classes("w-full rounded-2xl shadow-sm dialogue-chat-card h-[45vh] md:h-[55vh]").style(
        "overflow-y: auto;"
    ):
        _render_chat_messages(state)
    _schedule_chat_autoscroll()

    ui.separator()

    if not state.player_sheet_ready:
        state.selected_npc = None
        ui.label("Creation du personnage").classes("font-semibold")
        missing = state.player_sheet_missing if isinstance(state.player_sheet_missing, list) else []
        if missing:
            ui.label("Informations manquantes: " + _creation_missing_labels(missing)).classes("text-sm opacity-80")
        ui.label(_player_sheet_manager.next_creation_question(missing)).classes("text-sm")

        with ui.row().classes("w-full items-center gap-2 chat-input-row"):
            inp = (
                ui.input(placeholder="Decris ton personnage...")
                .classes("w-full")
                .props("id=main_chat_input")
                .bind_value(state, "chat_draft")
            )

            def _click_send_creation():
                if state.player_sheet_generation_in_progress:
                    return
                asyncio.create_task(
                    _send_creation_message(
                        state,
                        inp,
                        on_change,
                        chat_command_handler=chat_command_handler,
                    )
                )

            inp.on("keydown.enter", lambda e: _click_send_creation())
            btn = ui.button("Valider profil", on_click=_click_send_creation).props("dense no-caps").classes("send-btn")
            if state.player_sheet_generation_in_progress:
                btn.disable()
            else:
                _schedule_chat_input_focus()
        return

    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    in_dungeon = bool(run and not bool(run.get("completed", False)))
    combat = _active_dungeon_combat(run) if in_dungeon else None
    if in_dungeon:
        with ui.card().classes("w-full rounded-xl").style(
            "border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.03);"
        ):
            floor = int(run.get("current_floor", 0))
            total = int(run.get("total_floors", 0))
            ui.label(f"Donjon actif: {run.get('dungeon_name', 'Donjon')} ({floor}/{total})").classes("text-sm font-semibold")
            relic = run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None
            if isinstance(relic, dict):
                relic_name = str(relic.get("name") or "Relique").strip()
                relic_desc = str(relic.get("description") or "").strip()
                if relic_desc:
                    ui.label(f"Relique de run: {relic_name} - {relic_desc}").classes("text-xs opacity-80")
                else:
                    ui.label(f"Relique de run: {relic_name}").classes("text-xs opacity-80")
            if combat:
                enemy = str(combat.get("enemy_name") or "Adversaire")
                enemy_hp = max(0, _safe_int(combat.get("enemy_hp"), 0))
                enemy_max_hp = max(1, _safe_int(combat.get("enemy_max_hp"), enemy_hp))
                ui.label(f"Combat actif: {enemy} ({enemy_hp}/{enemy_max_hp} PV)").classes("text-xs opacity-90")
                ui.label("Decris ton action dans le chat: attaque, defense, sort, soin.").classes("text-xs opacity-80")
            else:
                ui.label("Mode action libre: decris tes sorts, tactiques et actions directement dans le chat.").classes(
                    "text-xs opacity-80"
                )

    trade_session = normalize_trade_session(getattr(state, "trade_session", None))
    trade_active = trade_session.status in {"selecting", "confirming", "executing", "done", "aborted"}
    trade_selected_npc = str(state.selected_npc or "").strip()
    if not trade_selected_npc and trade_active:
        trade_selected_npc = str(trade_session.npc_id or "").strip()
    if trade_selected_npc:
        session_npc_norm = _norm_destination_hint(trade_session.npc_id)
        selected_npc_norm = _norm_destination_hint(state.selected_npc or trade_selected_npc)
        trade_same_npc = not session_npc_norm or not selected_npc_norm or session_npc_norm == selected_npc_norm
        if trade_active and trade_same_npc:
            with ui.card().classes("w-full rounded-xl").style(
                "border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.03);"
            ):
                ui.label("Commerce").classes("text-sm font-semibold")
                mode_label = {"sell": "Vente", "buy": "Achat", "barter": "Echange"}.get(
                    str(trade_session.mode or "").casefold(),
                    "Commerce",
                )
                ui.label(
                    f"{mode_label} | Etat: {str(trade_session.status or 'idle')} | Tour: {max(0, int(trade_session.turn_id))}"
                ).classes("text-xs opacity-80")
                recap = []
                total = 0
                for row in trade_session.cart[:6]:
                    subtotal = max(0, int(getattr(row, "subtotal", 0) or 0))
                    total += subtotal
                    recap.append(
                        f"{str(getattr(row, 'item_name', '') or getattr(row, 'item_id', 'objet'))} x{max(1, int(getattr(row, 'qty', 1) or 1))} ({max(0, int(getattr(row, 'unit_price', 0) or 0))}/u)"
                    )
                if recap:
                    ui.label("Panier: " + " | ".join(recap)).classes("text-xs opacity-80")
                    ui.label(f"Total: {total} or").classes("text-xs font-semibold")
                pending_q = trade_session.pending_question if isinstance(trade_session.pending_question, dict) else None
                if pending_q:
                    ui.label(str(pending_q.get("text") or "Choisissez une option.")).classes("text-xs opacity-80")

                qty_max = max(1, int((pending_q or {}).get("max", 1) or 1))
                qty_state = {"value": 1}
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    if pending_q:
                        for opt in (pending_q.get("options") if isinstance(pending_q.get("options"), list) else [])[:4]:
                            if not isinstance(opt, dict):
                                continue
                            opt_id = str(opt.get("id") or "").strip()
                            opt_text = str(opt.get("text") or opt_id).strip()[:48]
                            if not opt_id:
                                continue
                            cmd = "/trade qty {n}" if opt_id == "set_qty" else (
                                "/trade all" if opt_id == "sell_all" else (
                                    "/trade one" if opt_id == "sell_one" else "/trade cancel"
                                )
                            )
                            ui.button(
                                opt_text,
                                on_click=lambda c=cmd: asyncio.create_task(
                                    _send_to_npc(
                                        state,
                                        _TransientInput(c.format(n=max(1, qty_state["value"]))),
                                        ui.context.client,
                                        on_change,
                                        chat_command_handler=chat_command_handler,
                                    )
                                ),
                            ).props("outline dense no-caps")
                        if any(str((opt or {}).get("id") or "") == "set_qty" for opt in (pending_q.get("options") or [])):
                            ui.number("Quantité", min=1, max=qty_max, value=min(1, qty_max), step=1).classes("w-24").on(
                                "change",
                                lambda e: qty_state.__setitem__("value", max(1, min(qty_max, _safe_int(getattr(e, "value", 1), 1)))),
                            )
                    else:
                        ui.button(
                            "Confirmer",
                            on_click=lambda: asyncio.create_task(
                                _send_to_npc(
                                    state,
                                    _TransientInput("/trade confirm"),
                                    ui.context.client,
                                    on_change,
                                    chat_command_handler=chat_command_handler,
                                )
                            ),
                        ).props("dense no-caps color=positive")
                        ui.button(
                            "Annuler",
                            on_click=lambda: asyncio.create_task(
                                _send_to_npc(
                                    state,
                                    _TransientInput("/trade cancel"),
                                    ui.context.client,
                                    on_change,
                                    chat_command_handler=chat_command_handler,
                                )
                            ),
                        ).props("outline dense no-caps color=negative")
                        if trade_session.status in {"done", "aborted"}:
                            ui.button(
                                "Fermer",
                                on_click=lambda: asyncio.create_task(
                                    _send_to_npc(
                                        state,
                                        _TransientInput("/trade close"),
                                        ui.context.client,
                                        on_change,
                                        chat_command_handler=chat_command_handler,
                                    )
                                ),
                            ).props("outline dense no-caps")
        else:
            with ui.card().classes("w-full rounded-xl").style(
                "border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.02);"
            ):
                ui.label("Commerce rapide").classes("text-sm font-semibold")
                ui.label("Demarre le commerce ici, puis continue en chat (ex: je vends epee x2).").classes(
                    "text-xs opacity-75"
                )
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.button(
                        "Vendre",
                        on_click=lambda: asyncio.create_task(
                            _send_to_npc(
                                state,
                                _TransientInput("/trade mode sell"),
                                ui.context.client,
                                on_change,
                                chat_command_handler=chat_command_handler,
                            )
                        ),
                    ).props("outline dense no-caps")
                    ui.button(
                        "Acheter",
                        on_click=lambda: asyncio.create_task(
                            _send_to_npc(
                                state,
                                _TransientInput("/trade mode buy"),
                                ui.context.client,
                                on_change,
                                chat_command_handler=chat_command_handler,
                            )
                        ),
                    ).props("outline dense no-caps")
                    ui.button(
                        "LLM vendeur ON",
                        on_click=lambda: asyncio.create_task(
                            _send_to_npc(
                                state,
                                _TransientInput("/trade llm on"),
                                ui.context.client,
                                on_change,
                                chat_command_handler=chat_command_handler,
                            )
                        ),
                    ).props("outline dense no-caps")
                    ui.button(
                        "LLM vendeur OFF",
                        on_click=lambda: asyncio.create_task(
                            _send_to_npc(
                                state,
                                _TransientInput("/trade llm off"),
                                ui.context.client,
                                on_change,
                                chat_command_handler=chat_command_handler,
                            )
                        ),
                    ).props("outline dense no-caps")

        if state.pending_choice_options:
            with ui.card().classes("w-full rounded-xl").style(
                "border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.03);"
            ):
                ui.label("Choix en attente").classes("text-sm font-semibold")
                prompt = str(state.pending_choice_prompt or "").strip()
                if prompt:
                    ui.label(prompt).classes("text-xs opacity-80")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    for row in state.pending_choice_options[:3]:
                        if not isinstance(row, dict):
                            continue
                        option_id = str(row.get("id") or "").strip()
                        label = str(row.get("text") or option_id).strip()[:80]
                        risk = str(row.get("risk_tag") or "").strip()
                        hint = str(row.get("effects_hint") or "").strip()
                        if not option_id:
                            continue
                        btn_label = label if not risk else f"{label} [{risk}]"
                        ui.button(
                            btn_label,
                            on_click=lambda oid=option_id: asyncio.create_task(
                                _send_to_npc(
                                    state,
                                    _TransientInput(f"/choice {oid}"),
                                    ui.context.client,
                                    on_change,
                                    chat_command_handler=chat_command_handler,
                                )
                            ),
                        ).props("outline dense no-caps")
                        if hint:
                            ui.label(hint).classes("text-[11px] opacity-70")

    with ui.row().classes("w-full items-center gap-2 chat-input-row"):
        turn_busy = _chat_turn_busy(state)
        placeholder = "Écrire un message..."
        if not state.selected_npc:
            placeholder = "Décris ton action libre (ex: je lance un sort de soins)."
        inp = ui.input(placeholder=placeholder).classes("w-full").props("id=main_chat_input").bind_value(state, "chat_draft")
        if turn_busy:
            inp.disable()

        def _click_send():
            if _chat_turn_busy(state):
                return
            client = ui.context.client
            asyncio.create_task(
                _send_to_npc(
                    state,
                    inp,
                    client,
                    on_change,
                    chat_command_handler=chat_command_handler,
                )
            )

        inp.on("keydown.enter", lambda e: _click_send())
        send_btn = ui.button("Envoyer", on_click=_click_send).props("dense no-caps").classes("send-btn")
        if turn_busy:
            send_btn.disable()
            ui.label("Action en cours...").classes("text-xs opacity-70")
        else:
            _schedule_chat_input_focus()

    if combat:
        _render_combat_quick_actions(
            state,
            on_change,
            chat_command_handler=chat_command_handler,
        )

    with ui.column().classes("w-full gap-2"):
        ui.label("Actions donjon rapides").classes("text-xs opacity-75")
        _render_dungeon_actions(state, on_change)

    entry_target = _get_local_entry_target_scene(state)
    if isinstance(entry_target, Scene):
        entry_choice = _find_choice_to_scene(state.current_scene(), entry_target.id)
        can_enter = entry_choice is not None
        with ui.row().classes("w-full items-center gap-2"):
            entry_btn = ui.button(
                f"Entrée : {entry_target.title}",
                on_click=(lambda: _enter_local_entry_target_from_dialogue(state, on_change)) if can_enter else None,
            ).props("outline dense no-caps").classes("w-full")
            if not can_enter:
                entry_btn.disable()
                with entry_btn:
                    ui.tooltip("Approche-toi d'une rue connectée à ce bâtiment pour entrer.")

    if state.selected_npc:
        npc_key = _selected_npc_conversation_key(state)
        if npc_key:
            active_quest = _active_quest_for_npc(state, npc_key)
            can_request, reason = _can_request_quest(state, npc_key)

            if active_quest:
                progress = active_quest.get("progress", {}) if isinstance(active_quest.get("progress"), dict) else {}
                current = max(0, _safe_int(progress.get("current"), 0))
                target = max(1, _safe_int(progress.get("target"), 1))
                title = str(active_quest.get("title") or "Quete en cours")
                ui.label(f"Quete active avec ce PNJ: {title} ({current}/{target})").classes("text-xs opacity-80")

                branching = active_quest.get("branching") if isinstance(active_quest.get("branching"), dict) else {}
                selected_branch = str(branching.get("selected_option_id") or "").strip() if isinstance(branching, dict) else ""
                options = branching.get("options") if isinstance(branching.get("options"), list) else []
                if options and not selected_branch:
                    ui.label("Choisis la branche de cette quete:").classes("text-xs opacity-75")

                    def _choose_branch_from_ui(quest_id: str, option_id: str) -> None:
                        ok, message = _quest_choose_branch(
                            state,
                            quest_id=quest_id,
                            option_id=option_id,
                            safe_int=_safe_int,
                            utc_now_iso=_utc_now_iso,
                        )
                        state.push("Système", message, count_for_media=False)
                        if ok:
                            _update_quests_and_notify(state)
                        on_change()
                        _refresh_chat_messages_view()

                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        for row in options[:4]:
                            if not isinstance(row, dict):
                                continue
                            option_id = str(row.get("id") or "").strip()
                            label = str(row.get("label") or option_id).strip()
                            if not option_id:
                                continue
                            ui.button(
                                label,
                                on_click=lambda qid=str(active_quest.get("id") or ""), oid=option_id: _choose_branch_from_ui(qid, oid),
                            ).props("dense outline no-caps")
            else:
                with ui.row().classes("w-full items-center gap-2"):
                    ask_btn = ui.button(
                        "Demander une quete",
                        on_click=lambda: asyncio.create_task(_request_quest_from_selected_npc(state, on_change)),
                    ).props("dense no-caps")
                    if not can_request:
                        ask_btn.disable()
                if reason:
                    ui.label(reason).classes("text-xs opacity-70")

            ui.separator()
            points = max(0, _safe_int(getattr(state, "skill_points", 0), 0))
            with ui.row().classes("w-full items-center gap-2"):
                train_btn = ui.button(
                    "S'entrainer (competence)",
                    on_click=lambda: asyncio.create_task(_train_skill_with_selected_npc(state, on_change)),
                ).props("dense no-caps")
                if state.skill_training_in_progress or points <= 0:
                    train_btn.disable()
            if points <= 0:
                ui.label("Aucun point de competence. Monte de niveau pour en gagner.").classes("text-xs opacity-70")
            else:
                ui.label(f"Points competence disponibles: {points}").classes("text-xs opacity-70")
    else:
        if in_dungeon:
            ui.label("Aucun PNJ sélectionné: actions libres en donjon actives.").classes("opacity-70")
        else:
            ui.label("Aucun PNJ sélectionné: tu peux quand même écrire des actions libres.").classes("opacity-70")


def _render_dungeon_actions(state: GameState, on_change) -> None:
    _render_dungeon_actions_block(
        state,
        on_change,
        advance_dungeon_fn=_advance_dungeon,
        leave_dungeon_fn=_leave_dungeon,
        enter_dungeon_fn=_enter_dungeon,
    )


def _render_combat_quick_actions(state: GameState, on_change, chat_command_handler=None) -> None:
    run = _active_dungeon_run(state)
    if not _active_dungeon_combat(run):
        return

    def _trigger_action(action_key: str, *, skill_row: dict | None = None) -> None:
        if _chat_turn_busy(state):
            return
        action_text = _combat_quick_action_text(action_key, skill_row=skill_row)
        client = ui.context.client
        asyncio.create_task(
            _send_combat_quick_action(
                state,
                action_text=action_text,
                client=client,
                on_change=on_change,
                chat_command_handler=chat_command_handler,
            )
        )

    with ui.card().classes("w-full rounded-xl").style(
        "border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.03);"
    ):
        turn_busy = _chat_turn_busy(state)
        ui.label("Actions de combat").classes("text-sm font-semibold")
        with ui.row().classes("w-full items-center gap-2"):
            attack_btn = ui.button("Attaque", on_click=lambda: _trigger_action("attack")).props("dense no-caps").classes("flex-1")
            defend_btn = ui.button("Defense", on_click=lambda: _trigger_action("defend")).props("dense no-caps").classes("flex-1")
            spell_btn = ui.button("Sort", on_click=lambda: _trigger_action("spell")).props("dense no-caps").classes("flex-1")
            heal_btn = ui.button("Soin", on_click=lambda: _trigger_action("heal")).props("dense no-caps").classes("flex-1")
            if turn_busy:
                attack_btn.disable()
                defend_btn.disable()
                spell_btn.disable()
                heal_btn.disable()

        skill_rows = _combat_quick_skill_rows(state)
        if not skill_rows:
            ui.label("Aucune competence apprise pour le moment.").classes("text-xs opacity-75")
            return

        skill_by_id = {str(row.get("skill_id") or ""): row for row in skill_rows}
        options = {
            str(row.get("skill_id") or ""): f"{row.get('name', 'Competence')} (niv {row.get('level', 1)})"
            for row in skill_rows
        }
        flags = _gm_flags(state)
        default_skill_id = str(flags.get(_COMBAT_QUICK_SKILL_FLAG) or "").strip().casefold()
        if default_skill_id not in skill_by_id:
            default_skill_id = str(skill_rows[0].get("skill_id") or "")

        with ui.row().classes("w-full items-center gap-2"):
            skill_select = ui.select(options=options, value=default_skill_id, label="Competence").props(
                "dense options-dense"
            ).classes("w-full")

            def _trigger_selected_skill() -> None:
                selected_skill_id = str(skill_select.value or "").strip().casefold()
                if not selected_skill_id:
                    selected_skill_id = default_skill_id
                flags[_COMBAT_QUICK_SKILL_FLAG] = selected_skill_id
                selected = skill_by_id.get(selected_skill_id)
                if not isinstance(selected, dict):
                    state.push("Système", "Competence de combat invalide.", count_for_media=False)
                    on_change()
                    _refresh_chat_messages_view()
                    return
                _trigger_action("skill", skill_row=selected)

            use_skill_btn = ui.button("Utiliser competence", on_click=_trigger_selected_skill).props(
                "dense no-caps color=primary"
            )
            if turn_busy:
                use_skill_btn.disable()


def _apply_choice(state: GameState, choice: Choice, on_change) -> None:
    origin_scene = state.current_scene()
    state.push("Joueur", choice.label)
    if choice.next_scene_id:
        target = state.scenes.get(choice.next_scene_id)
        if isinstance(target, Scene):
            if is_nsfw_scene(target) and not is_nsfw_mode_enabled(state):
                state.push(
                    "Système",
                    "🔒 Zone restreinte: active le Mode Adulte pour entrer ici.",
                    count_for_media=False,
                )
                on_change()
                _refresh_chat_messages_view()
                return
            can_access, access_hint = _rep_can_access_scene(
                state,
                scene_id=target.id,
                scene_title=target.title,
            )
            if not can_access:
                state.push(
                    "Système",
                    f"🚫 {access_hint or 'Acces refuse pour des raisons de reputation.'}",
                    count_for_media=False,
                )
                on_change()
                _refresh_chat_messages_view()
                return
            is_open, status_hint = scene_open_status(target, state.world_time_minutes)
            if not is_open:
                state.push("Système", f"🚪 {status_hint}", count_for_media=False)
                on_change()
                _refresh_chat_messages_view()
                return
        state.set_scene(choice.next_scene_id)
        _set_local_entry_target(state, "")
        state.advance_world_time(8 if isinstance(target, Scene) and is_building_scene_title(target.title) else 14)
        _apply_world_and_story_progress(state)
        spawn_roaming_known_npcs(state)
        state.push("Système", f"➡️ Vous arrivez : {state.current_scene().title}")
        current = state.current_scene()
        _event_bus.publish(
            OnLocationEntered(
                scene_id=current.id,
                scene_title=current.title,
                map_anchor=str(current.map_anchor or ""),
                context={
                    "from_scene_id": origin_scene.id,
                    "from_scene_title": origin_scene.title,
                    "selected_npc": str(state.selected_npc or ""),
                },
            )
        )

    _update_quests_and_notify(state)
    on_change()
    _refresh_chat_messages_view()


def _explore_new_location(state: GameState, on_change) -> None:
    if state.location_generation_in_progress:
        return
    state.location_generation_in_progress = True
    state.push("Système", "Vous quittez la route balisée et cherchez un nouveau passage...", count_for_media=False)
    on_change()
    _refresh_chat_messages_view()
    asyncio.create_task(_generate_and_travel_to_new_location(state, on_change))


async def _generate_and_travel_to_new_location(state: GameState, on_change) -> None:
    origin = state.current_scene()
    try:
        new_scene, travel_label = await _location_manager.generate_next_scene(origin, state.scenes)
        if is_nsfw_scene(new_scene) and not is_nsfw_mode_enabled(state):
            state.push(
                "Système",
                "Ataryxia detourne votre route: une zone reservee aux adultes reste inaccessible.",
                count_for_media=False,
            )
        else:
            target_anchor = new_scene.map_anchor or ""
            first_arrival_in_anchor = bool(target_anchor) and not any(
                scene.map_anchor == target_anchor for scene in state.scenes.values()
            )
            state.scenes[new_scene.id] = new_scene

            _link_scenes(origin, new_scene, travel_label)
            _link_scenes(new_scene, origin, f"Retour vers {origin.title}")

            if first_arrival_in_anchor:
                settlement_kind, extra_scenes = _location_manager.generate_settlement_map_for_new_anchor(
                    anchor=target_anchor,
                    center_scene=new_scene,
                    existing_scenes=state.scenes,
                )
                for extra in extra_scenes:
                    state.scenes[extra.id] = extra
                if extra_scenes:
                    place_label = "ville" if settlement_kind == "city" else "village"
                    state.push(
                        "Système",
                        f"🗺️ Nouveau plan de {place_label} généré pour {target_anchor}: {len(extra_scenes) + 1} zones reliées.",
                        count_for_media=False,
                    )

            if target_anchor:
                _location_manager.apply_city_street_layout(
                    state.scenes,
                    target_anchor,
                    prefer_center_scene_id=new_scene.id,
                )

            state.set_scene(new_scene.id)
            travel_minutes = 50 if (origin.map_anchor and new_scene.map_anchor and origin.map_anchor != new_scene.map_anchor) else 30
            state.advance_world_time(travel_minutes)
            _apply_world_and_story_progress(state)
            spawn_roaming_known_npcs(state)
            state.push("Système", f"➡️ Vous arrivez : {new_scene.title}")
            _event_bus.publish(
                OnLocationEntered(
                    scene_id=new_scene.id,
                    scene_title=new_scene.title,
                    map_anchor=str(new_scene.map_anchor or ""),
                    context={
                        "from_scene_id": origin.id,
                        "from_scene_title": origin.title,
                        "selected_npc": str(state.selected_npc or ""),
                    },
                )
            )

            state.gm_state["location"] = new_scene.title
            state.gm_state["location_id"] = new_scene.id
            state.gm_state["map_anchor"] = new_scene.map_anchor
    except Exception as e:
        state.push("Système", f"⚠️ Impossible de générer un nouveau lieu: {e}", count_for_media=False)
    finally:
        state.location_generation_in_progress = False

    _update_quests_and_notify(state)
    on_change()
    _refresh_chat_messages_view()


async def _enter_dungeon(state: GameState, on_change) -> None:
    await _enter_dungeon_action(
        state,
        on_change,
        dungeon_manager=_dungeon_manager,
        refresh_chat_messages_view=_refresh_chat_messages_view,
    )
    _apply_world_and_story_progress(state)
    on_change()
    _refresh_chat_messages_view()


async def _advance_dungeon(state: GameState, on_change) -> None:
    await _advance_dungeon_action(
        state,
        on_change,
        dungeon_manager=_dungeon_manager,
        start_dungeon_combat_fn=_start_dungeon_combat,
        maybe_award_loot_from_dungeon_event_fn=_maybe_award_loot_from_dungeon_event,
        update_quests_and_notify_fn=_update_quests_and_notify,
        safe_int=_safe_int,
        refresh_chat_messages_view=_refresh_chat_messages_view,
    )
    _apply_world_and_story_progress(state)
    on_change()
    _refresh_chat_messages_view()


def _leave_dungeon(state: GameState, on_change) -> None:
    _leave_dungeon_action(
        state,
        on_change,
        refresh_chat_messages_view=_refresh_chat_messages_view,
    )
    _apply_world_and_story_progress(state)
    on_change()
    _refresh_chat_messages_view()


register_gameplay_hooks(
    update_quests_and_notify=_update_quests_and_notify,
    apply_world_and_story_progress=_apply_world_and_story_progress,
    explore_new_location=_explore_new_location,
    enter_dungeon=_enter_dungeon,
    advance_dungeon=_advance_dungeon,
    leave_dungeon=_leave_dungeon,
)


def _link_scenes(source, target, label: str) -> None:
    if any(c.next_scene_id == target.id for c in source.choices):
        return
    source.choices.append(
        Choice(
            id=f"go_{target.id}",
            label=label.strip() or f"Aller vers {target.title}",
            next_scene_id=target.id,
        )
    )


def _maybe_discover_location_from_npc_reply(state: GameState, *, npc_name: str, reply_text: str) -> tuple[str, str] | None:
    if not isinstance(reply_text, str) or not reply_text.strip():
        return None
    scene = state.current_scene()
    existing_titles = [s.title for s in state.scenes.values()]
    hint_title = _location_manager.suggest_hint_location_title(
        text=reply_text,
        existing_titles=existing_titles,
    )
    if not hint_title:
        return None
    if contains_nsfw_marker(hint_title) and not is_nsfw_mode_enabled(state):
        return None

    current_anchor = str(scene.map_anchor or "Lumeria").strip() or "Lumeria"
    anchor = _location_manager.choose_hint_anchor(
        current_anchor=current_anchor,
        text=reply_text,
        hint_title=hint_title,
        rng=_loot_manager.rng,
    )
    full_title = _location_manager._unique_title(
        f"{anchor} - {hint_title}",
        set(existing_titles),
    )
    scene_id = _location_manager._unique_scene_id(anchor, full_title, set(state.scenes.keys()))
    narrator_line = f"Ataryxia : Une nouvelle rumeur prend forme autour de {hint_title}."

    new_scene = Scene(
        id=scene_id,
        title=full_title,
        narrator_text=narrator_line,
        map_anchor=anchor,
        generated=True,
        npc_names=[],
        choices=[],
    )
    state.scenes[new_scene.id] = new_scene
    if anchor != current_anchor:
        _link_scenes(scene, new_scene, f"Voyager vers {anchor}: {hint_title}")
    else:
        _link_scenes(scene, new_scene, f"Se rendre a {hint_title}")
    _link_scenes(new_scene, scene, f"Retour vers {scene.title}")
    return hint_title, anchor


def _select_npc(state: GameState, npc: str, on_change) -> None:
    ensure_npc_world_state(state)
    sync_npc_registry_from_profiles(state)
    scene = state.current_scene()
    state.selected_npc = npc
    key = resolve_scene_npc_key(state, npc, scene.id)
    profile = state.npc_profiles.get(key)
    if not isinstance(profile, dict) and key != npc_profile_key(npc, scene.id):
        loaded = _npc_manager.load_profile_by_key(
            key,
            fallback_label=npc,
            location_id=scene.id,
            location_title=scene.title,
        )
        if isinstance(loaded, dict):
            state.npc_profiles[key] = loaded
            profile = loaded
    if profile:
        register_npc_profile(state, npc_name=npc, scene=scene, profile=profile, npc_key=key)
        state.push("Système", f"Vous vous tournez vers {profile_summary_line(profile, npc)}.", count_for_media=False)
        _emit_first_contact_line_if_needed(state, profile, npc)
    else:
        state.push("Système", f"Vous vous tournez vers {npc}. Je rassemble ses informations...", count_for_media=False)
        if key not in state.npc_generation_in_progress:
            state.npc_generation_in_progress.add(key)
            asyncio.create_task(_generate_npc_profile(state, npc, on_change, forced_key=key))
    on_change()
    _refresh_chat_messages_view()


def select_npc_for_dialogue(state: GameState, npc: str, on_change) -> None:
    _select_npc(state, npc, on_change)


def _selected_npc_profile(state: GameState) -> dict | None:
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return None
    scene = state.current_scene()
    npc_key = resolve_scene_npc_key(state, npc, scene.id)
    return state.npc_profiles.get(npc_key)


async def _generate_npc_profile(
    state: GameState,
    npc: str,
    on_change,
    *,
    forced_key: str | None = None,
) -> None:
    scene = state.current_scene()
    key = str(forced_key or resolve_scene_npc_key(state, npc, scene.id)).strip() or npc_profile_key(npc, scene.id)
    try:
        profile: dict | None = None
        if key and key != npc_profile_key(npc, scene.id):
            profile = state.npc_profiles.get(key)
            if not isinstance(profile, dict):
                profile = _npc_manager.load_profile_by_key(
                    key,
                    fallback_label=npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
                if isinstance(profile, dict):
                    state.npc_profiles[key] = profile

        if not isinstance(profile, dict):
            profile = await _npc_manager.ensure_profile(
                state.npc_profiles,
                npc,
                location_id=scene.id,
                location_title=scene.title,
            )
            key = str(profile.get("npc_key") or npc_profile_key(npc, scene.id)).strip() or npc_profile_key(npc, scene.id)
            state.npc_profiles[key] = profile

        register_npc_profile(state, npc_name=npc, scene=scene, profile=profile, npc_key=key)
        state.gm_state["npc_profiles"] = state.npc_profiles
        state.push("Système", f"Fiche PNJ prête: {profile_summary_line(profile, npc)}.", count_for_media=False)
        _emit_first_contact_line_if_needed(state, profile, npc)
    except Exception as e:
        state.push("Système", f"⚠️ Impossible de générer la fiche de {npc}: {e}", count_for_media=False)
    finally:
        state.npc_generation_in_progress.discard(key)
        if forced_key:
            state.npc_generation_in_progress.discard(str(forced_key))
    on_change()
    _refresh_chat_messages_view()


def _emit_first_contact_line_if_needed(state: GameState, profile: dict, fallback_npc: str) -> None:
    flags = profile.setdefault("dynamic_flags", {})
    if bool(flags.get("is_met", False)):
        return

    first_message = str(profile.get("first_message") or "").strip()
    if first_message:
        speaker = profile_display_name(profile, fallback_npc)
        state.push(speaker, first_message, count_for_media=False)

    flags["is_met"] = True
    try:
        _npc_manager.save_profile(fallback_npc, profile, location_id=state.current_scene().id)
    except Exception:
        pass


async def _send_creation_message(state: GameState, inp: ui.input, on_change, chat_command_handler=None) -> None:
    text = (state.chat_draft or inp.value or "").strip()
    if not text:
        return
    if text.startswith("/"):
        handled, response, user_echo = await _run_chat_command_handler(text, chat_command_handler)
        if handled:
            state.push("Joueur", user_echo, count_for_media=False)
            if response:
                state.push("Système", response, count_for_media=False)
            inp.value = ""
            state.chat_draft = ""
            on_change()
            _refresh_chat_messages_view()
            return
    if state.player_sheet_generation_in_progress:
        return

    state.player_sheet_generation_in_progress = True
    state.push("Joueur", text)
    inp.value = ""
    state.chat_draft = ""
    _refresh_chat_messages_view()

    try:
        recent = [f"{msg.speaker}: {msg.text}" for msg in state.chat[-20:]]
        result = await _player_sheet_manager.ingest_creation_message(
            sheet=state.player_sheet,
            user_message=text,
            recent_chat_lines=recent,
        )
        state.player_sheet = result.get("sheet") if isinstance(result.get("sheet"), dict) else state.player_sheet
        state.player_sheet = _loot_manager.apply_equipment_to_sheet(state.player_sheet, state.item_defs, state.equipped_items)
        state.player_sheet_missing = result.get("missing_fields") if isinstance(result.get("missing_fields"), list) else []
        was_ready = bool(state.player_sheet_ready)
        state.player_sheet_ready = bool(result.get("ready", False))
        _player_sheet_manager.sync_player_basics(state.player_sheet, state.player)

        ack = str(result.get("ack_text") or "").strip()
        if ack:
            state.push("Système", ack, count_for_media=False)

        if state.player_sheet_ready and not was_ready:
            state.push("Système", "Fiche joueur creee. Les choix et PNJ sont maintenant accessibles.", count_for_media=False)
            state.push("Système", "Tu debutes sans sort avance: entraine-toi avec des PNJ pour apprendre des competences.", count_for_media=False)
            state.push("Ataryxia", "Parfait. Maintenant, avance.", count_for_media=False)
        else:
            q = str(result.get("next_question") or "").strip()
            if q:
                state.push("Système", q, count_for_media=False)
    except Exception as e:
        state.push("Système", f"Impossible de mettre a jour la fiche joueur: {e}", count_for_media=False)
    finally:
        state.player_sheet_generation_in_progress = False

    on_change()
    _refresh_chat_messages_view()


async def _send_to_npc(state: GameState, inp: ui.input, client, on_change, chat_command_handler=None) -> None:
    text = (state.chat_draft or inp.value or "").strip()
    if not text:
        return
    if _chat_turn_busy(state):
        return

    _set_chat_turn_busy(state, True)

    try:
        choice_forward_handled, choice_message, choice_forward_text = _prepare_choice_message_for_gm(state, text)
        if choice_forward_handled:
            if choice_message:
                state.push("Système", choice_message, count_for_media=False)
                on_change()
                _refresh_chat_messages_view()
                return
            text = str(choice_forward_text or "").strip()

        if text.startswith("/"):
            handled, response, user_echo = await _run_chat_command_handler(text, chat_command_handler)
            if not handled:
                handled, response, user_echo = _handle_local_chat_command(state, text)
            if handled:
                state.push("Joueur", user_echo, count_for_media=False)
                if response:
                    state.push("Système", response, count_for_media=False)
                inp.value = ""
                state.chat_draft = ""
                on_change()
                _refresh_chat_messages_view()
                return

        _ensure_player_sheet_state(state)
        ensure_conversation_memory_state(state)
        if not state.player_sheet_ready:
            state.push("Système", "Termine d'abord la creation de personnage.", count_for_media=False)
            on_change()
            _refresh_chat_messages_view()
            return

        run = _active_dungeon_run(state)
        if _active_dungeon_combat(run):
            state.push("Joueur", text)
            state.quest_counters["player_messages_sent"] = max(
                0,
                _safe_int(state.quest_counters.get("player_messages_sent"), 0) + 1,
            )
            inp.value = ""
            state.chat_draft = ""
            _refresh_chat_messages_view()

            skill_lines = await _handle_active_dungeon_combat_action(state, text)
            if skill_lines:
                state.push("Système", "Progression competences: " + " | ".join(skill_lines), count_for_media=False)

            try:
                passive_lines = await _apply_passive_skill_practice_from_text(state, text)
                if passive_lines:
                    state.push("Système", "Apprentissage passif: " + " | ".join(passive_lines), count_for_media=False)
            except Exception:
                pass

            on_change()
            _refresh_chat_messages_view()
            return

        scene = state.current_scene()
        npc_context = await _turn_resolve_selected_npc_context(
            state,
            scene=scene,
            npc_manager=_npc_manager,
            npc_profile_key_fn=npc_profile_key,
            resolve_scene_npc_key_fn=resolve_scene_npc_key,
            register_npc_profile_fn=register_npc_profile,
        )
        npc = npc_context.npc_name
        npc_key = npc_context.npc_key
        npc_profile = npc_context.npc_profile

        _memory_prepare_gm_state_for_turn(
            state,
            scene=scene,
            npc=npc,
            npc_key=npc_key,
            safe_int=_safe_int,
            economy_manager=_economy_manager,
            format_fantasy_datetime=format_fantasy_datetime,
            experience_tier=_experience_tier,
            build_short_term_context=build_short_term_context,
            build_long_term_context=build_long_term_context,
            build_global_memory_context=build_global_memory_context,
            build_retrieved_context=build_retrieved_context,
        )

        state.push("Joueur", text)
        state.quest_counters["player_messages_sent"] = max(
            0,
            _safe_int(state.quest_counters.get("player_messages_sent"), 0) + 1,
        )
        if npc_key:
            state.npc_dialogue_counts[npc_key] = max(0, _safe_int(state.npc_dialogue_counts.get(npc_key), 0) + 1)

        world_intervention_lines = _world_try_resolve_nearby_world_event(
            state,
            text,
            safe_int=_safe_int,
            utc_now_iso=_utc_now_iso,
        )
        if world_intervention_lines:
            for line in world_intervention_lines:
                clean = str(line or "").strip()
                if clean:
                    state.push("Système", clean, count_for_media=False)
            inp.value = ""
            state.chat_draft = ""
            state.advance_world_time(4)
            _apply_world_and_story_progress(state)
            on_change()
            _refresh_chat_messages_view()
            return

        guided_handled, guided_reply, guided_system_lines = _handle_guided_training_turn(
            state,
            user_text=text,
            npc_name=str(npc or "PNJ"),
            npc_key=npc_key,
            npc_profile=npc_profile,
        )
        if guided_handled:
            npc_reply_text = str(guided_reply or "").strip()
            if npc_reply_text:
                state.push(str(npc or "PNJ"), npc_reply_text)
            for line in guided_system_lines:
                cleaned = str(line or "").strip()
                if cleaned:
                    state.push("Système", cleaned, count_for_media=False)

            if npc_key:
                profile_ref = state.npc_profiles.get(npc_key)
                if isinstance(profile_ref, dict):
                    update_profile_emotional_state(
                        profile_ref,
                        user_text=text,
                        npc_reply=npc_reply_text,
                        event_hint="training:guided",
                    )

            _memory_remember_dialogue_turn_safe(
                state,
                npc_key=npc_key,
                npc_name=str(npc or "PNJ"),
                player_text=text,
                npc_reply=npc_reply_text,
                scene_id=scene.id,
                scene_title=scene.title,
                world_time_minutes=state.world_time_minutes,
                remember_dialogue_turn_fn=remember_dialogue_turn,
            )

            _update_quests_and_notify(state)
            inp.value = ""
            state.chat_draft = ""
            state.advance_world_time(6)
            _apply_world_and_story_progress(state)
            on_change()
            _refresh_chat_messages_view()
            return

        trade_outcome = _apply_trade_from_player_message(
            state,
            user_text=text,
            selected_npc=npc,
            npc_key=npc_key,
            selected_profile=npc_profile,
        )

        trade_event_hint = _memory_record_trade_event_in_memory(
            state,
            trade_outcome=trade_outcome if isinstance(trade_outcome, dict) else {},
            npc_key=npc_key,
            npc_name=str(npc or ""),
            scene=scene,
            safe_int=_safe_int,
            remember_system_event_fn=remember_system_event,
        )

        trade_context_for_turn = (
            trade_outcome.get("trade_context")
            if isinstance(trade_outcome, dict) and isinstance(trade_outcome.get("trade_context"), dict)
            else {}
        )

        _turn_sync_post_trade_gm_state(
            state,
            safe_int=_safe_int,
            economy_manager=_economy_manager,
            reputation_summary_fn=_rep_reputation_summary,
        )
        _update_quests_and_notify(state)
        inp.value = ""
        state.chat_draft = ""
        _refresh_chat_messages_view()

        if bool(isinstance(trade_outcome, dict) and trade_outcome.get("attempted")):
            vendor_line = await _trade_render_trade_dialogue(
                state=state,
                selected_npc=str(npc or "PNJ"),
                selected_profile=npc_profile if isinstance(npc_profile, dict) else None,
                llm_client=_llm,
            )
            vendor_line = str(vendor_line or "").strip()
            if vendor_line:
                state.push(str(npc or "PNJ"), vendor_line)

            if npc_key:
                profile_ref = state.npc_profiles.get(npc_key)
                if isinstance(profile_ref, dict):
                    update_profile_emotional_state(
                        profile_ref,
                        user_text=text,
                        npc_reply=vendor_line,
                        event_hint=trade_event_hint,
                    )

            _memory_remember_dialogue_turn_safe(
                state,
                npc_key=npc_key,
                npc_name=str(npc or "PNJ"),
                player_text=text,
                npc_reply=vendor_line,
                scene_id=scene.id,
                scene_title=scene.title,
                world_time_minutes=state.world_time_minutes,
                remember_dialogue_turn_fn=remember_dialogue_turn,
            )

            state.advance_world_time(2)
            _apply_world_and_story_progress(state)
            on_change()
            _refresh_chat_messages_view()
            return

        user_msg = text
        tension_before = None
        if npc_key:
            profile_before = state.npc_profiles.get(npc_key)
            if isinstance(profile_before, dict):
                tension_before = profile_tension_level(profile_before)

        ai_mode_enabled = _ai_enabled(state)
        llm_available = False
        if ai_mode_enabled:
            try:
                llm_available = await _llm.is_available(cache_ttl_seconds=2.0, probe_timeout_seconds=1.4)
            except Exception:
                llm_available = False

        if ai_mode_enabled and llm_available:
            _gm_flags(state)["ai_unavailable_notified"] = False
            try:
                res = await _gm.play_turn(state.gm_state, user_msg)
            except Exception as e:
                state.push("Système", f"⚠️ IA indisponible ({e}). Basculage en mode deterministe.", count_for_media=False)
                _gm_flags(state)["ai_unavailable_notified"] = True
                res = _deterministic_turn_result(state, user_text=user_msg, npc_name=npc)
        else:
            flags = _gm_flags(state)
            if ai_mode_enabled and not bool(flags.get("ai_unavailable_notified", False)):
                state.push("Système", "⚠️ IA locale indisponible: fallback deterministe actif.", count_for_media=False)
                flags["ai_unavailable_notified"] = True
            res = _deterministic_turn_result(state, user_text=user_msg, npc_name=npc)

        trade_turn_locked = bool(isinstance(trade_outcome, dict) and trade_outcome.get("attempted"))
        effective_speaker = str(res.speaker or "")
        effective_dialogue = "" if trade_turn_locked else str(res.dialogue or "")
        effective_narration = "" if trade_turn_locked else str(res.narration or "")

        travel_started = False
        if getattr(res, "plan", None):
            plan = res.plan
            plan_type = str(getattr(plan, "type", "") or "").strip().casefold()
            plan_intent = str(getattr(plan, "intent", "") or "").strip()
            wants_travel = plan_type == "travel"
            if not wants_travel and _looks_like_travel_request(text):
                intent_hint = _norm_destination_hint(plan_intent)
                wants_travel = any(token in intent_hint for token in ("aller", "voyag", "route", "rendre"))
            if wants_travel:
                state_patch = plan.state_patch if isinstance(plan.state_patch, dict) else {}
                hint_candidates = [
                    str(state_patch.get("location_id") or ""),
                    str(state_patch.get("location") or ""),
                    str(state_patch.get("map_anchor") or ""),
                    plan_intent,
                    _extract_travel_hint_from_text(text),
                ]
                travel_error = ""
                for hint in hint_candidates:
                    cleaned_hint = str(hint or "").strip()
                    if not cleaned_hint:
                        continue
                    ok, message = _start_travel_from_hint(state, cleaned_hint)
                    if ok:
                        state.push("Système", f"🧭 {message}", count_for_media=False)
                        travel_started = True
                        break
                    travel_error = str(message or "")
                if not travel_started and travel_error:
                    state.push("Système", f"Voyage non lancé: {travel_error}", count_for_media=False)
        if not travel_started and _looks_like_travel_request(text):
            fallback_hint = _extract_travel_hint_from_text(text)
            ok, message = _start_travel_from_hint(state, fallback_hint)
            if ok:
                state.push("Système", f"🧭 {message}", count_for_media=False)
                travel_started = True

        if res.system:
            state.push("Système", res.system, count_for_media=False)
            lowered = str(res.system or "").casefold()
            if "mensonge" in lowered or "secret" in lowered or "revelation" in lowered:
                try:
                    remember_system_event(
                        state,
                        fact_text=str(res.system),
                        npc_key=npc_key,
                        npc_name=str(npc or ""),
                        scene_id=scene.id,
                        scene_title=scene.title,
                        world_time_minutes=state.world_time_minutes,
                        kind="secret" if "secret" in lowered else "mensonge",
                        importance=4,
                    )
                except Exception:
                    pass

        gm_corruption = _safe_int(state.gm_state.get("player_corruption_level"), getattr(state, "player_corruption_level", 0))
        state.player_corruption_level = max(0, min(100, gm_corruption))

        if (
            not trade_turn_locked
            and str(getattr(res, "output_type", "dialogue") or "dialogue") == "choice_required"
            and getattr(res, "options", None)
        ):
            _set_pending_choice(
                state,
                options=[opt.model_dump() if hasattr(opt, "model_dump") else opt for opt in (res.options or [])],
                prompt=str(res.dialogue or (res.plan.narration_hooks[0] if res.plan and res.plan.narration_hooks else "")),
                source_npc_key=str(npc_key or ""),
            )
        else:
            _clear_pending_choice(state)

        if not trade_turn_locked and getattr(res, "event_text", None):
            event_line = str(res.event_text or "").strip()
            if event_line:
                state.push("Narration système", event_line, count_for_media=False)
                try:
                    remember_system_event(
                        state,
                        fact_text=event_line,
                        npc_key=npc_key,
                        npc_name=str(npc or ""),
                        scene_id=scene.id,
                        scene_title=scene.title,
                        world_time_minutes=state.world_time_minutes,
                        kind="system",
                        importance=3,
                    )
                except Exception:
                    pass

        if effective_dialogue and effective_speaker:
            state.push(effective_speaker, effective_dialogue)
            discovered = _maybe_discover_location_from_npc_reply(
                state,
                npc_name=effective_speaker,
                reply_text=effective_dialogue,
            )
            if discovered:
                discovered_title, discovered_anchor = discovered
                current_anchor = str(scene.map_anchor or "Lumeria").strip() or "Lumeria"
                if discovered_anchor and discovered_anchor != current_anchor:
                    state.push(
                        "Système",
                        f"🗺️ Nouveau lieu repere: {discovered_title} ({discovered_anchor}).",
                        count_for_media=False,
                    )
                else:
                    state.push(
                        "Système",
                        f"🗺️ Nouveau lieu repere: {discovered_title}.",
                        count_for_media=False,
                    )

            v = pick_random_video_url()
            if v:
                play_action_video_js(client, v)

        if effective_narration:
            try:
                state.current_scene().narrator_text = effective_narration
            except Exception:
                pass
            set_narrator_text_js(client, effective_narration)

        if npc_key:
            profile_ref = state.npc_profiles.get(npc_key)
            if isinstance(profile_ref, dict):
                update_profile_emotional_state(
                    profile_ref,
                    user_text=text,
                    npc_reply=effective_dialogue,
                    event_hint=trade_event_hint,
                )
                tension_after = profile_tension_level(profile_ref)
                if tension_before is not None and tension_after != tension_before:
                    _publish_tension_change(
                        state,
                        npc_key=npc_key,
                        npc_name=str(npc or ""),
                        old_value=int(tension_before),
                        new_value=int(tension_after),
                        reason="gm_turn",
                    )
                    state.push(
                        "Système",
                        f"Tension PNJ: {tension_tier_label(int(tension_before))} -> {tension_tier_label(int(tension_after))}.",
                        count_for_media=False,
                    )
                if is_npc_blacklisted(profile_ref, world_time_minutes=max(0, int(state.world_time_minutes))):
                    state.push(
                        "Système",
                        "Le PNJ vous ignore pour un temps apres la rupture.",
                        count_for_media=False,
                    )

        _memory_remember_dialogue_turn_safe(
            state,
            npc_key=npc_key,
            npc_name=str(effective_speaker or npc or "PNJ"),
            player_text=text,
            npc_reply=effective_dialogue,
            scene_id=scene.id,
            scene_title=scene.title,
            world_time_minutes=state.world_time_minutes,
            remember_dialogue_turn_fn=remember_dialogue_turn,
        )

        pre_stats = state.player_sheet.get("stats", {}) if isinstance(state.player_sheet, dict) and isinstance(state.player_sheet.get("stats"), dict) else {}
        pre_level = max(1, _safe_int(pre_stats.get("niveau"), 1))

        try:
            progression = await _player_sheet_manager.infer_progression_update(
                sheet=state.player_sheet,
                user_message=text,
                npc_reply=effective_dialogue,
                narration=effective_narration,
                trade_context=trade_context_for_turn,
                trade_applied=bool(isinstance(trade_outcome, dict) and trade_outcome.get("applied")),
                player_name=str(getattr(state.player, "name", "") or ""),
                selected_npc_name=str(npc or ""),
            )
            progression = _sanitize_progression_for_trade(progression, trade_outcome if isinstance(trade_outcome, dict) else {})
            updated_sheet, lines = _player_sheet_manager.apply_progression_update(state.player_sheet, progression)
            state.player_sheet = _loot_manager.apply_equipment_to_sheet(updated_sheet, state.item_defs, state.equipped_items)
            _player_sheet_manager.sync_player_basics(state.player_sheet, state.player)
            post_stats = state.player_sheet.get("stats", {}) if isinstance(state.player_sheet.get("stats"), dict) else {}
            post_level = max(1, _safe_int(post_stats.get("niveau"), pre_level))
            level_gain = max(0, post_level - pre_level)
            if level_gain > 0:
                state.skill_points = max(0, _safe_int(state.skill_points, 0) + level_gain)
                state.push("Système", f"+{level_gain} point(s) de competence (gain de niveau).", count_for_media=False)
            if lines:
                _append_progress_log(state, progression=progression, summary_lines=lines)
                state.push("Système", "Progression: " + " | ".join(lines[:6]), count_for_media=False)
        except Exception:
            pass

        try:
            skill_lines = _apply_skill_usage_progress_from_text(state, text)
            if skill_lines:
                state.push("Système", "Progression competences: " + " | ".join(skill_lines), count_for_media=False)
        except Exception:
            pass

        try:
            passive_lines = await _apply_passive_skill_practice_from_text(state, text)
            if passive_lines:
                state.push("Système", "Apprentissage passif: " + " | ".join(passive_lines), count_for_media=False)
        except Exception:
            pass

        state.advance_world_time(6)
        _apply_world_and_story_progress(state)
        on_change()
        _refresh_chat_messages_view()
    finally:
        _set_chat_turn_busy(state, False)
        on_change()


async def _train_skill_with_selected_npc(state: GameState, on_change) -> None:
    await _train_skill_with_selected_npc_action(
        state,
        on_change,
        ensure_skill_state_fn=_ensure_skill_state,
        safe_int=_safe_int,
        npc_profile_key=npc_profile_key,
        npc_manager=_npc_manager,
        profile_display_name=profile_display_name,
        resolve_profile_role=resolve_profile_role,
        player_stats_for_training_fn=_player_stats_for_training,
        build_player_skill_context_fn=_build_player_skill_context,
        skill_manager=_skill_manager,
        append_skill_training_log_fn=_append_skill_training_log,
        upsert_learned_skill_fn=_upsert_learned_skill,
        append_skill_usage_log_fn=_append_skill_usage_log,
        utc_now_iso=_utc_now_iso,
        refresh_chat_messages_view=_refresh_chat_messages_view,
    )


async def _request_quest_from_selected_npc(state: GameState, on_change) -> None:
    await _request_quest_from_selected_npc_action(
        state,
        on_change,
        safe_int=_safe_int,
        npc_profile_key=npc_profile_key,
        npc_manager=_npc_manager,
        profile_display_name=profile_display_name,
        quest_manager=_quest_manager,
        can_request_quest_fn=_can_request_quest,
        build_runtime_quest_fn=_build_runtime_quest,
        update_quests_and_notify_fn=_update_quests_and_notify,
        refresh_chat_messages_view=_refresh_chat_messages_view,
    )
