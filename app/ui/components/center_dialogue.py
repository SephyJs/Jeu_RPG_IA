import asyncio
from datetime import datetime, timezone

from nicegui import ui

from app.ui.state.game_state import GameState, Choice, Scene
from app.ui.state.inventory import ItemStack

from app.gamemaster.ollama_client import OllamaClient
from app.gamemaster.gamemaster import GameMaster
from app.gamemaster.npc_manager import (
    NPCProfileManager,
    npc_profile_key,
    profile_display_name,
    resolve_profile_role,
    profile_summary_line,
)
from app.gamemaster.location_manager import (
    LocationManager,
    is_building_scene_title,
    scene_open_status,
)
from app.gamemaster.dungeon_manager import DungeonManager
from app.gamemaster.quest_manager import QuestManager
from app.gamemaster.player_sheet_manager import PlayerSheetManager
from app.gamemaster.loot_manager import LootManager
from app.gamemaster.skill_manager import SkillManager, SkillDef
from app.gamemaster.economy_manager import EconomyManager
from app.gamemaster.conversation_memory import (
    build_global_memory_context,
    build_long_term_context,
    build_short_term_context,
    ensure_conversation_memory_state,
    remember_dialogue_turn,
    remember_system_event,
)
from app.gamemaster.world_time import format_fantasy_datetime

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


_llm = OllamaClient()
_gm = GameMaster(_llm, seed=123)
_npc_manager = NPCProfileManager(_llm)
_location_manager = LocationManager(_llm)
_dungeon_manager = DungeonManager(_llm)
_quest_manager = QuestManager(_llm)
_player_sheet_manager = PlayerSheetManager(_llm)
_loot_manager = LootManager(_llm, data_dir="data")
_skill_manager = SkillManager(_llm, data_path="data/skills_catalog.json")
_economy_manager = EconomyManager(data_dir="data")

QUEST_MIN_MESSAGES_BEFORE_OFFER = 3
QUEST_MESSAGES_PER_NEXT_OFFER = 3
PASSIVE_PRACTICE_BASE_THRESHOLD = 6
PASSIVE_PRACTICE_THRESHOLD_GROWTH = 1.7


@ui.refreshable
def _render_chat_messages(state: GameState) -> None:
    if not state.chat:
        ui.label("Aucun message pour l'instant.").classes("opacity-70")
    else:
        for msg in state.chat[-200:]:
            ui.markdown(f"**{msg.speaker}** : {msg.text}")


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_quest_state(state: GameState) -> None:
    if not isinstance(state.quests, list):
        state.quests = []
    if not isinstance(state.npc_dialogue_counts, dict):
        state.npc_dialogue_counts = {}
    if not isinstance(state.npc_quests_given, dict):
        state.npc_quests_given = {}
    if not isinstance(state.quest_generation_in_progress, set):
        state.quest_generation_in_progress = set()
    if not isinstance(state.quest_counters, dict):
        state.quest_counters = {}
    state.quest_counters.setdefault("player_messages_sent", 0)
    state.quest_counters.setdefault("dungeon_floors_cleared", 0)
    if not isinstance(state.quest_seq, int) or state.quest_seq < 0:
        state.quest_seq = len(state.quests)


def _ensure_skill_state(state: GameState) -> None:
    if not isinstance(state.skill_defs, dict) or not state.skill_defs:
        try:
            state.skill_defs = _skill_manager.load_catalog()
        except Exception:
            state.skill_defs = {}

    if not isinstance(state.player_skills, list):
        state.player_skills = []
    state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)

    state.skill_points = max(0, _safe_int(getattr(state, "skill_points", 1), 1))
    state.skill_training_in_progress = bool(getattr(state, "skill_training_in_progress", False))

    if not isinstance(state.skill_training_log, list):
        state.skill_training_log = []
    state.skill_training_log = [x for x in state.skill_training_log if isinstance(x, dict)][-300:]
    if not isinstance(state.skill_passive_practice, dict):
        state.skill_passive_practice = {}
    sanitized: dict[str, dict] = {}
    for key, value in state.skill_passive_practice.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        skill_ids_raw = value.get("unlocked_skill_ids")
        skill_ids = []
        if isinstance(skill_ids_raw, list):
            skill_ids = [str(x).strip().casefold() for x in skill_ids_raw if isinstance(x, str) and str(x).strip()]
        sanitized[key[:40]] = {
            "count": max(0, _safe_int(value.get("count"), 0)),
            "threshold": max(3, _safe_int(value.get("threshold"), 6)),
            "unlocked_skill_ids": sorted(set(skill_ids))[:12],
            "last_unlock_at": str(value.get("last_unlock_at") or "")[:40],
        }
    state.skill_passive_practice = sanitized


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
    if not isinstance(state.player_progress_log, list):
        state.player_progress_log = []

    stat_deltas_raw = progression.get("stat_deltas") if isinstance(progression.get("stat_deltas"), dict) else {}
    stat_deltas: dict[str, int] = {}
    for key, value in stat_deltas_raw.items():
        delta = _safe_int(value, 0)
        if delta > 0:
            stat_deltas[str(key)] = delta

    entry = {
        "at": _utc_now_iso(),
        "location_id": state.current_scene().id,
        "location_title": state.current_scene().title,
        "source_npc": str(getattr(state, "selected_npc", "") or ""),
        "xp_gain": max(0, _safe_int(progression.get("xp_gain"), 0)),
        "stat_deltas": stat_deltas,
        "summary": " | ".join(summary_lines[:6]),
        "reason": str(progression.get("reason") or "").strip(),
    }
    state.player_progress_log.append(entry)
    if len(state.player_progress_log) > 200:
        state.player_progress_log = state.player_progress_log[-200:]


def _player_stats_for_training(state: GameState) -> dict[str, int]:
    sheet = state.player_sheet if isinstance(state.player_sheet, dict) else {}
    stats = {}
    effective = sheet.get("effective_stats")
    if isinstance(effective, dict) and effective:
        stats = effective
    elif isinstance(sheet.get("stats"), dict):
        stats = sheet.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    out: dict[str, int] = {}
    for key in ("force", "intelligence", "magie", "defense", "sagesse", "agilite", "dexterite", "chance", "charisme"):
        out[key] = max(1, _safe_int(stats.get(key), 5))
    out["niveau"] = max(1, _safe_int(stats.get("niveau"), 1))
    return out


def _build_player_skill_context(state: GameState, recent_chat_lines: list[str] | None = None) -> str:
    sheet = state.player_sheet if isinstance(state.player_sheet, dict) else {}
    chunks: list[str] = []
    visual = sheet.get("description_visuelle", {}) if isinstance(sheet.get("description_visuelle"), dict) else {}
    short_desc = str(visual.get("courte") or "").strip()
    if short_desc:
        chunks.append(short_desc[:180])
    persona = str(sheet.get("char_persona") or "").strip()
    if persona:
        chunks.append(persona[:180])
    lore = sheet.get("lore_details", {}) if isinstance(sheet.get("lore_details"), dict) else {}
    passives = lore.get("passives")
    if isinstance(passives, list):
        for row in passives[:6]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("nom") or "").strip()
            effect = str(row.get("effet") or "").strip()
            if name or effect:
                chunks.append(f"{name}: {effect}".strip(": "))
    if isinstance(recent_chat_lines, list):
        for line in recent_chat_lines[-12:]:
            if isinstance(line, str) and line.strip():
                chunks.append(line[:180])
    return " | ".join(chunks)


def _upsert_learned_skill(state: GameState, skill: SkillDef, trainer_name: str) -> tuple[dict, bool]:
    now = _utc_now_iso()
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if str(row.get("skill_id") or "").strip().casefold() != skill.skill_id:
            continue
        row["rank"] = min(5, max(1, _safe_int(row.get("rank"), 1) + 1))
        row["trainer_npc"] = trainer_name[:80]
        row["learned_at"] = now
        row["name"] = skill.name
        row["category"] = skill.category
        row["description"] = skill.description
        row["difficulty"] = skill.difficulty
        row["primary_stats"] = list(skill.primary_stats)
        row["level"] = max(1, _safe_int(row.get("level"), 1))
        row["xp"] = max(0, _safe_int(row.get("xp"), 0))
        row["uses"] = max(0, _safe_int(row.get("uses"), 0))
        row["xp_to_next"] = _skill_manager.xp_needed_for_next_level(max(1, _safe_int(row.get("level"), 1)))
        return row, True

    entry = {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "category": skill.category,
        "description": skill.description,
        "difficulty": skill.difficulty,
        "primary_stats": list(skill.primary_stats),
        "rank": 1,
        "level": 1,
        "xp": 0,
        "xp_to_next": _skill_manager.xp_needed_for_next_level(1),
        "uses": 0,
        "trainer_npc": trainer_name[:80],
        "learned_at": now,
        "last_used_at": "",
    }
    state.player_skills.append(entry)
    state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    return entry, False


def _append_skill_training_log(
    state: GameState,
    *,
    npc_name: str,
    skill: SkillDef,
    chance: int,
    roll: int,
    success: bool,
    reason: str,
) -> None:
    entry = {
        "at": _utc_now_iso(),
        "npc_name": npc_name,
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "chance": max(0, min(100, _safe_int(chance, 0))),
        "roll": max(0, min(100, _safe_int(roll, 0))),
        "success": bool(success),
        "reason": str(reason or "").strip()[:220],
    }
    state.skill_training_log.append(entry)
    if len(state.skill_training_log) > 300:
        state.skill_training_log = state.skill_training_log[-300:]


def _find_player_skill_entry(state: GameState, skill_id: str) -> dict | None:
    key = str(skill_id or "").strip().casefold()
    if not key:
        return None
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if str(row.get("skill_id") or "").strip().casefold() == key:
            return row
    return None


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
    text_reason = str(reason or "").strip()
    if not text_reason:
        text_reason = f"Utilisation en jeu: +{max(0, _safe_int(xp_gain, 0))} XP, niveau {max(1, _safe_int(level_after, 1))}"
    entry = {
        "at": _utc_now_iso(),
        "npc_name": str(getattr(state, "selected_npc", "") or ""),
        "skill_id": str(skill_entry.get("skill_id") or ""),
        "skill_name": str(skill_entry.get("name") or ""),
        "chance": 0,
        "roll": 0,
        "success": True,
        "reason": text_reason[:220],
        "source": str(source or "usage")[:24],
        "levels_gained": max(0, _safe_int(levels_gained, 0)),
    }
    state.skill_training_log.append(entry)
    if len(state.skill_training_log) > 300:
        state.skill_training_log = state.skill_training_log[-300:]


def _apply_skill_usage_progress_from_text(state: GameState, text: str) -> list[str]:
    _ensure_skill_state(state)
    if not isinstance(text, str) or not text.strip():
        return []
    used_ids = _skill_manager.detect_used_skill_ids(text, state.player_skills)
    if not used_ids:
        return []

    lines: list[str] = []
    now = _utc_now_iso()
    for skill_id in used_ids[:3]:
        entry = _find_player_skill_entry(state, skill_id)
        if not isinstance(entry, dict):
            continue
        xp_gain = _skill_manager.estimate_usage_xp_gain(entry, text)
        progress = _skill_manager.apply_skill_xp(entry, xp_gain=xp_gain, used_at_iso=now)
        level_after = max(1, _safe_int(progress.get("level_after"), 1))
        xp_after = max(0, _safe_int(progress.get("xp_after"), 0))
        xp_to_next = max(0, _safe_int(progress.get("xp_to_next"), 0))
        levels_gained = max(0, _safe_int(progress.get("levels_gained"), 0))

        if xp_to_next > 0:
            lines.append(
                f"{entry.get('name', skill_id)} +{xp_gain} XP compet. ({xp_after}/{xp_to_next})"
            )
        else:
            lines.append(f"{entry.get('name', skill_id)} +{xp_gain} XP compet. (niveau max)")

        if levels_gained > 0:
            lines.append(f"⬆️ {entry.get('name', skill_id)} niveau +{levels_gained} (Niv. {level_after})")

        _append_skill_usage_log(
            state,
            skill_entry=entry,
            xp_gain=xp_gain,
            levels_gained=levels_gained,
            level_after=level_after,
        )

    if lines:
        state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    return lines[:6]


def _existing_skill_for_intent(state: GameState, intent: str) -> dict | None:
    key = str(intent or "").strip().casefold()
    if not key:
        return None
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if _skill_manager.skill_matches_intent(row, key):
            return row
    return None


def _next_passive_threshold(current_threshold: int) -> int:
    base = max(3, _safe_int(current_threshold, PASSIVE_PRACTICE_BASE_THRESHOLD))
    grown = int(round(base * PASSIVE_PRACTICE_THRESHOLD_GROWTH)) + 1
    return max(4, min(80, grown))


async def _apply_passive_skill_practice_from_text(state: GameState, text: str) -> list[str]:
    _ensure_skill_state(state)
    if not isinstance(text, str) or not text.strip():
        return []

    existing_intents = [str(k) for k in state.skill_passive_practice.keys() if isinstance(k, str)]
    known_categories = [
        str(row.get("category") or "")
        for row in state.player_skills
        if isinstance(row, dict) and str(row.get("category") or "").strip()
    ]
    known_skill_names = [
        str(row.get("name") or row.get("skill_id") or "")
        for row in state.player_skills
        if isinstance(row, dict) and str(row.get("name") or row.get("skill_id") or "").strip()
    ]
    intents = await _skill_manager.infer_intent_hints(
        text,
        existing_intents=existing_intents,
        known_categories=known_categories,
        known_skill_names=known_skill_names,
    )
    if not intents:
        return []

    lines: list[str] = []
    recent_chat = [f"{m.speaker}: {m.text}" for m in state.chat[-20:]]
    context = _build_player_skill_context(state, recent_chat_lines=recent_chat)
    stats = _player_stats_for_training(state)
    now = _utc_now_iso()
    unlocked_this_message = False

    for intent in intents[:3]:
        track = state.skill_passive_practice.get(intent, {}) if isinstance(state.skill_passive_practice.get(intent), dict) else {}
        count = max(0, _safe_int(track.get("count"), 0)) + 1
        threshold = max(3, _safe_int(track.get("threshold"), PASSIVE_PRACTICE_BASE_THRESHOLD))
        unlocked_ids_raw = track.get("unlocked_skill_ids")
        unlocked_ids_iter = unlocked_ids_raw if isinstance(unlocked_ids_raw, list) else []
        unlocked_ids = {
            str(x).strip().casefold()
            for x in unlocked_ids_iter
            if isinstance(x, str) and str(x).strip()
        }

        existing_match = _existing_skill_for_intent(state, intent)
        if existing_match:
            existing_id = str(existing_match.get("skill_id") or "").strip().casefold()
            if existing_id:
                unlocked_ids.add(existing_id)

        track["count"] = count
        track["threshold"] = threshold
        track["unlocked_skill_ids"] = sorted(unlocked_ids)
        state.skill_passive_practice[intent] = track

        # Une seule creation/deblocage passif par message pour eviter le spam.
        if unlocked_this_message or count < threshold:
            continue

        known_ids = {
            str(row.get("skill_id") or "").strip().casefold()
            for row in state.player_skills
            if isinstance(row, dict)
        }
        if existing_match and str(existing_match.get("skill_id") or "").strip().casefold() in known_ids:
            # Deja debloquee: on reset la pratique pour ne pas re-debloquer en boucle.
            track["count"] = 0
            track["threshold"] = _next_passive_threshold(threshold)
            track["last_unlock_at"] = now
            state.skill_passive_practice[intent] = track
            continue

        npc = str(getattr(state, "selected_npc", "") or "").strip() or "Pratique"
        profile = _selected_npc_profile(state)
        npc_role = resolve_profile_role(profile, npc) if isinstance(profile, dict) else state.current_scene().title
        suggestion = await _skill_manager.suggest_or_create_skill(
            catalog=state.skill_defs,
            known_skill_ids=known_ids,
            player_stats=stats,
            npc_name=npc,
            npc_role=npc_role,
            player_context=f"{context} | pratique repetee: {intent}",
            recent_chat_lines=recent_chat,
        )
        if not suggestion:
            track["threshold"] = _next_passive_threshold(threshold)
            state.skill_passive_practice[intent] = track
            continue

        skill = suggestion.get("skill")
        if not isinstance(skill, SkillDef):
            track["threshold"] = _next_passive_threshold(threshold)
            state.skill_passive_practice[intent] = track
            continue

        learned, already_known = _upsert_learned_skill(state, skill, trainer_name=npc)
        practice_xp = _skill_manager.estimate_usage_xp_gain(learned, f"{text} {intent}")
        progress = _skill_manager.apply_skill_xp(learned, xp_gain=practice_xp, used_at_iso=now)
        levels_gained = max(0, _safe_int(progress.get("levels_gained"), 0))
        level_after = max(1, _safe_int(progress.get("level_after"), 1))
        xp_after = max(0, _safe_int(progress.get("xp_after"), 0))
        xp_to_next = max(0, _safe_int(progress.get("xp_to_next"), 0))
        _append_skill_usage_log(
            state,
            skill_entry=learned,
            xp_gain=practice_xp,
            levels_gained=levels_gained,
            level_after=level_after,
            source="practice",
            reason=f"Pratique repetee ({intent})",
        )

        skill_id = str(learned.get("skill_id") or "").strip().casefold()
        if skill_id:
            unlocked_ids.add(skill_id)
        track["unlocked_skill_ids"] = sorted(unlocked_ids)
        track["count"] = 0
        track["threshold"] = _next_passive_threshold(threshold)
        track["last_unlock_at"] = now
        state.skill_passive_practice[intent] = track

        if bool(suggestion.get("created", False)):
            lines.append(f"Nouvelle competence creee par pratique: {skill.name} ({skill.skill_id})")
        if already_known:
            lines.append(f"A force de pratiquer {intent}, tu renforces {skill.name}.")
        else:
            lines.append(f"A force de pratiquer {intent}, tu debloques {skill.name}.")
        if xp_to_next > 0:
            lines.append(f"{skill.name}: +{practice_xp} XP compet. ({xp_after}/{xp_to_next})")
        if levels_gained > 0:
            lines.append(f"⬆️ {skill.name} niveau +{levels_gained} (Niv. {level_after})")

        unlocked_this_message = True

    if lines:
        state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    return lines[:6]


def _selected_npc_conversation_key(state: GameState) -> str | None:
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return None
    scene = state.current_scene()
    return resolve_scene_npc_key(state, npc, scene.id)


def _active_quest_for_npc(state: GameState, npc_key: str) -> dict | None:
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("source_npc_key") or "") != npc_key:
            continue
        if str(quest.get("status") or "in_progress") == "in_progress":
            return quest
    return None


def _quest_offer_threshold(state: GameState, npc_key: str) -> tuple[int, int]:
    talked = max(0, _safe_int(state.npc_dialogue_counts.get(npc_key), 0))
    given = max(0, _safe_int(state.npc_quests_given.get(npc_key), 0))
    threshold = QUEST_MIN_MESSAGES_BEFORE_OFFER + (given * QUEST_MESSAGES_PER_NEXT_OFFER)
    return talked, threshold


def _can_request_quest(state: GameState, npc_key: str) -> tuple[bool, str]:
    _ensure_quest_state(state)
    if npc_key in state.quest_generation_in_progress:
        return False, "Generation de quete en cours..."
    if _active_quest_for_npc(state, npc_key):
        return False, "Ce PNJ vous a deja confie une quete en cours."

    talked, threshold = _quest_offer_threshold(state, npc_key)
    if talked < threshold:
        return False, f"Parlez encore un peu au PNJ pour debloquer une quete ({talked}/{threshold})."
    return True, "Quete disponible."


def _objective_label(objective_type: str, target_count: int, target_npc: str, target_anchor: str) -> str:
    if objective_type == "talk_to_npc":
        who = target_npc or "ce PNJ"
        return f"Parler a {who} ({target_count}/{target_count})"
    if objective_type == "send_messages":
        return f"Echanger {target_count} messages"
    if objective_type == "explore_locations":
        return f"Explorer {target_count} nouveau(x) lieu(x)"
    if objective_type == "reach_anchor":
        return f"Atteindre {target_anchor or 'la zone cible'}"
    if objective_type == "collect_gold":
        return f"Gagner {target_count} or"
    if objective_type == "clear_dungeon_floors":
        return f"Nettoyer {target_count} etage(s) de donjon"
    return f"Objectif ({target_count})"


def _build_runtime_quest(
    state: GameState,
    *,
    quest_payload: dict,
    npc_name: str,
    npc_key: str,
    scene: Scene,
) -> dict:
    _ensure_quest_state(state)
    state.quest_seq += 1

    objective_type = str(quest_payload.get("objective_type") or "send_messages")
    target_count = max(1, _safe_int(quest_payload.get("target_count"), 1))
    target_npc = str(quest_payload.get("target_npc") or npc_name)
    target_anchor = str(quest_payload.get("target_anchor") or (scene.map_anchor or "Lumeria"))
    target_npc_key = npc_profile_key(target_npc, scene.id)
    if objective_type == "talk_to_npc":
        target_npc = npc_name
        target_npc_key = npc_key

    objective = {
        "type": objective_type,
        "target": target_count,
        "target_npc": target_npc,
        "target_npc_key": target_npc_key,
        "target_anchor": target_anchor,
        "label": _objective_label(objective_type, target_count, target_npc, target_anchor),
        "start_player_messages": max(0, _safe_int(state.quest_counters.get("player_messages_sent"), 0)),
        "start_npc_messages": max(0, _safe_int(state.npc_dialogue_counts.get(target_npc_key), 0)),
        "start_discovered_locations": len(state.discovered_scene_ids),
        "start_gold": max(0, _safe_int(state.player.gold, 0)),
        "start_dungeon_floors": max(0, _safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0)),
    }
    rewards = quest_payload.get("rewards") if isinstance(quest_payload.get("rewards"), dict) else {}

    quest = {
        "id": f"quest_{state.quest_seq:04d}",
        "title": str(quest_payload.get("title") or f"Mission de {npc_name}"),
        "description": str(quest_payload.get("description") or ""),
        "source_npc_name": npc_name,
        "source_npc_key": npc_key,
        "location_id": scene.id,
        "location_title": scene.title,
        "objective": objective,
        "progress": {"current": 0, "target": target_count, "percent": 0.0},
        "rewards": {
            "gold": max(0, _safe_int(rewards.get("gold"), 0)),
            "items": rewards.get("items") if isinstance(rewards.get("items"), list) else [],
            "shop_discount_pct": max(0, _safe_int(rewards.get("shop_discount_pct"), 0)),
            "temple_heal_bonus": max(0, _safe_int(rewards.get("temple_heal_bonus"), 0)),
        },
        "progress_hint": str(quest_payload.get("progress_hint") or ""),
        "status": "in_progress",
        "reward_claimed": False,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "completed_at": "",
    }
    return quest


def _has_secret_charity_quest(state: GameState, npc_key: str) -> bool:
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        meta = quest.get("meta", {}) if isinstance(quest.get("meta"), dict) else {}
        if str(meta.get("secret_kind") or "") != "charity_beggar":
            continue
        if str(quest.get("source_npc_key") or "") != str(npc_key or ""):
            continue
        return True
    return False


def _maybe_unlock_secret_charity_quest(
    state: GameState,
    *,
    npc_name: str,
    npc_key: str,
    scene: Scene,
    trade_context: dict,
) -> None:
    if not bool(trade_context.get("target_is_beggar")):
        return
    if _has_secret_charity_quest(state, npc_key):
        return

    flags = state.gm_state.setdefault("flags", {})
    if bool(flags.get("secret_quest_beggar_unlocked", False)):
        return

    qty_done = max(0, _safe_int(trade_context.get("qty_done"), 0))
    if qty_done <= 0:
        return
    beggar_total = max(0, _safe_int(trade_context.get("charity_to_beggar_total"), 0))
    roll = _loot_manager.rng.random()
    chance = min(0.6, 0.15 + (beggar_total * 0.08))
    if beggar_total < 2 and roll > chance:
        return

    item_name = str(trade_context.get("item_name") or "provision").strip()
    payload = {
        "title": "Les Mains dans l'Ombre",
        "description": f"Le mendiant semble en savoir plus qu'il n'y parait apres ton don ({item_name}).",
        "objective_type": "talk_to_npc",
        "target_count": 1,
        "target_npc": npc_name,
        "target_anchor": scene.map_anchor or "Lumeria",
        "rewards": {
            "gold": 12,
            "items": [{"item_id": "pain_01", "qty": 1}],
            "shop_discount_pct": 3,
            "temple_heal_bonus": 0,
        },
        "progress_hint": "Reparle au mendiant en confidence.",
        "quest_intro": "Le mendiant baisse la voix: 'On me paie pour oublier des choses... et j'ai vu quelque chose pour toi.'",
    }
    quest = _build_runtime_quest(
        state,
        quest_payload=payload,
        npc_name=npc_name,
        npc_key=npc_key,
        scene=scene,
    )
    quest["is_secret"] = True
    quest["meta"] = {"secret_kind": "charity_beggar", "trigger_item": str(trade_context.get("item_id") or "")}
    state.quests.append(quest)
    flags["secret_quest_beggar_unlocked"] = True
    state.push("Système", f"🕯️ Quete secrete debloquee: {quest.get('title', 'Mission cachee')}", count_for_media=False)


def _apply_trade_from_player_message(
    state: GameState,
    *,
    user_text: str,
    selected_npc: str | None,
    npc_key: str | None,
    selected_profile: dict | None,
) -> dict:
    _ensure_quest_state(state)
    if not selected_npc:
        return {"attempted": False}
    _ensure_item_state(state)
    outcome = _economy_manager.process_trade_message(
        state=state,
        user_text=user_text,
        selected_npc_name=str(selected_npc),
        selected_npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
        item_defs=state.item_defs,
    )
    if not bool(outcome.get("attempted")):
        return outcome

    for line in outcome.get("system_lines", []):
        if isinstance(line, str) and line.strip():
            state.push("Système", line.strip(), count_for_media=False)

    trade_context = outcome.get("trade_context", {}) if isinstance(outcome.get("trade_context"), dict) else {}
    if trade_context:
        trade_context = dict(trade_context)
        trade_context["npc_name"] = str(selected_npc)
        if npc_key:
            trade_context["npc_key"] = str(npc_key)
        trade_context["gold_after"] = max(0, _safe_int(state.player.gold, 0))
        trade_context["inventory_after"] = _economy_manager.inventory_summary(state, state.item_defs)
        state.gm_state["last_trade"] = trade_context

    if bool(outcome.get("secret_charity_candidate")) and npc_key:
        _maybe_unlock_secret_charity_quest(
            state,
            npc_name=str(selected_npc),
            npc_key=str(npc_key),
            scene=state.current_scene(),
            trade_context=trade_context,
        )
    return outcome


def _compute_quest_progress(state: GameState, quest: dict) -> tuple[int, int]:
    objective = quest.get("objective", {}) if isinstance(quest.get("objective"), dict) else {}
    objective_type = str(objective.get("type") or "send_messages")
    target = max(1, _safe_int(objective.get("target"), 1))

    if objective_type == "talk_to_npc":
        npc_key = str(objective.get("target_npc_key") or "")
        base = max(0, _safe_int(objective.get("start_npc_messages"), 0))
        current_total = max(0, _safe_int(state.npc_dialogue_counts.get(npc_key), 0))
        return max(0, current_total - base), target

    if objective_type == "send_messages":
        base = max(0, _safe_int(objective.get("start_player_messages"), 0))
        current_total = max(0, _safe_int(state.quest_counters.get("player_messages_sent"), 0))
        return max(0, current_total - base), target

    if objective_type == "explore_locations":
        base = max(0, _safe_int(objective.get("start_discovered_locations"), 0))
        return max(0, len(state.discovered_scene_ids) - base), target

    if objective_type == "reach_anchor":
        target_anchor = str(objective.get("target_anchor") or "").strip().casefold()
        current_anchor = str(state.current_scene().map_anchor or "").strip().casefold()
        return (1 if target_anchor and target_anchor == current_anchor else 0), 1

    if objective_type == "collect_gold":
        base = max(0, _safe_int(objective.get("start_gold"), 0))
        return max(0, _safe_int(state.player.gold, 0) - base), target

    if objective_type == "clear_dungeon_floors":
        base = max(0, _safe_int(objective.get("start_dungeon_floors"), 0))
        current_total = max(0, _safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0))
        return max(0, current_total - base), target

    return 0, target


def _find_existing_stack_slot(state: GameState, item_id: str) -> tuple[str, int] | None:
    for idx, stack in enumerate(state.carried.slots):
        if stack and stack.item_id == item_id:
            return ("carried", idx)
    for idx, stack in enumerate(state.storage.slots):
        if stack and stack.item_id == item_id:
            return ("storage", idx)
    return None


def _find_empty_slot(state: GameState) -> tuple[str, int] | None:
    for idx, stack in enumerate(state.carried.slots):
        if stack is None:
            return ("carried", idx)
    for idx, stack in enumerate(state.storage.slots):
        if stack is None:
            return ("storage", idx)
    return None


def _item_stack_max(state: GameState, item_id: str) -> int:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    try:
        value = int(getattr(item, "stack_max", 1))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 999))


def _grant_item_reward(state: GameState, item_id: str, qty: int) -> int:
    if qty <= 0:
        return 0

    remaining = qty
    granted = 0
    stack_max = _item_stack_max(state, item_id)

    # Remplit d'abord les piles existantes.
    for grid in (state.carried, state.storage):
        for idx, stack in enumerate(grid.slots):
            if remaining <= 0:
                break
            if stack is None or stack.item_id != item_id:
                continue
            capacity = max(0, stack_max - int(stack.qty))
            if capacity <= 0:
                continue
            take = min(capacity, remaining)
            stack.qty += take
            remaining -= take
            granted += take
        if remaining <= 0:
            break

    # Puis crée de nouvelles piles dans les slots vides.
    while remaining > 0:
        empty = _find_empty_slot(state)
        if not empty:
            break
        which, idx = empty
        grid = state.carried if which == "carried" else state.storage
        take = min(stack_max, remaining)
        grid.set(idx, ItemStack(item_id=item_id, qty=take))
        remaining -= take
        granted += take

    return granted


def _apply_quest_rewards(state: GameState, quest: dict) -> list[str]:
    rewards = quest.get("rewards", {}) if isinstance(quest.get("rewards"), dict) else {}
    lines: list[str] = []

    gold = max(0, _safe_int(rewards.get("gold"), 0))
    if gold > 0:
        state.player.gold += gold
        lines.append(f"+{gold} or")

    items_raw = rewards.get("items")
    if isinstance(items_raw, list):
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "").strip()
            qty = max(1, _safe_int(item.get("qty"), 1))
            if not item_id:
                continue
            granted = _grant_item_reward(state, item_id, qty)
            if granted > 0:
                lines.append(f"+{item_id} x{granted}")
            else:
                lines.append(f"Inventaire plein: recompense {item_id} perdue")

    flags = state.gm_state.setdefault("flags", {})
    shop_discount = max(0, _safe_int(rewards.get("shop_discount_pct"), 0))
    if shop_discount > 0:
        current = max(0, _safe_int(flags.get("shop_discount_pct"), 0))
        flags["shop_discount_pct"] = max(current, shop_discount)
        lines.append(f"Reduction boutique {flags['shop_discount_pct']}%")

    temple_bonus = max(0, _safe_int(rewards.get("temple_heal_bonus"), 0))
    if temple_bonus > 0:
        current = max(0, _safe_int(flags.get("temple_heal_bonus"), 0))
        flags["temple_heal_bonus"] = max(current, temple_bonus)
        lines.append(f"Bonus soins temple +{flags['temple_heal_bonus']}")

    quest["reward_claimed"] = True
    return lines


def _update_quests_and_notify(state: GameState) -> None:
    _ensure_quest_state(state)
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("status") or "in_progress") != "in_progress":
            continue

        current, target = _compute_quest_progress(state, quest)
        current = min(current, target)
        percent = current / float(target) if target > 0 else 0.0
        quest["progress"] = {
            "current": current,
            "target": target,
            "percent": percent,
        }
        quest["updated_at"] = _utc_now_iso()

        if current < target:
            continue

        quest["status"] = "completed"
        quest["completed_at"] = _utc_now_iso()
        state.push("Système", f"✅ Quete terminee: {quest.get('title', 'Quete')}", count_for_media=False)

        if not bool(quest.get("reward_claimed", False)):
            reward_lines = _apply_quest_rewards(state, quest)
            if reward_lines:
                state.push("Système", "Recompenses: " + " | ".join(reward_lines), count_for_media=False)


def _item_display_name(state: GameState, item_id: str) -> str:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    name = str(getattr(item, "name", "") or "").strip()
    return name or item_id


async def _maybe_award_loot_from_dungeon_event(state: GameState, event: dict) -> None:
    if not isinstance(event, dict):
        return
    _ensure_item_state(state)

    event_type = str(event.get("type") or "").strip().casefold()
    chances = {
        "monster": 0.35,
        "mimic": 0.75,
        "treasure": 1.0,
    }
    chance = chances.get(event_type, 0.0)
    if chance <= 0.0:
        return
    if _loot_manager.rng.random() > chance:
        return

    floor = max(1, _safe_int(event.get("floor"), 1))
    anchor = ""
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if run:
        anchor = str(run.get("anchor") or "").strip()
    if not anchor:
        anchor = str(state.current_scene().map_anchor or state.current_scene().title or "Lumeria")

    loot = await _loot_manager.generate_loot(
        source_type=event_type or "treasure",
        floor=floor,
        anchor=anchor,
        known_items=state.item_defs,
    )
    item_id, updated_defs, created_new = _loot_manager.ensure_item_exists(loot, state.item_defs)
    state.item_defs = updated_defs

    qty = max(1, _safe_int(loot.get("qty"), 1))
    granted = _grant_item_reward(state, item_id, qty)
    rarity = str(loot.get("rarity") or "").strip().casefold()
    label = _item_display_name(state, item_id)

    if created_new:
        state.push("Système", f"Nouvel objet cree: {label} ({item_id})", count_for_media=False)

    if granted <= 0:
        state.push("Système", f"Butin perdu (inventaire plein): {label} x{qty}", count_for_media=False)
        return

    line = f"Butin obtenu: {label} x{granted}"
    if rarity:
        line += f" [{rarity}]"
    if granted < qty:
        line += " (inventaire limite)"
    state.push("Système", line, count_for_media=False)


def center_dialogue(state: GameState, on_change) -> None:
    _ensure_quest_state(state)
    _ensure_player_sheet_state(state)
    ensure_conversation_memory_state(state)
    ensure_npc_world_state(state)
    sync_npc_registry_from_profiles(state)
    scene = state.current_scene()

    ui.label("Dialogues").classes("text-lg font-semibold")
    ui.separator()

    with ui.card().classes("w-full rounded-2xl shadow-sm dialogue-chat-card").style("height: 55vh; overflow-y: auto;"):
        _render_chat_messages(state)

    ui.separator()

    if not state.player_sheet_ready:
        state.selected_npc = None
        ui.label("Creation du personnage").classes("font-semibold")
        missing = state.player_sheet_missing if isinstance(state.player_sheet_missing, list) else []
        if missing:
            ui.label("Informations manquantes: " + _creation_missing_labels(missing)).classes("text-sm opacity-80")
        ui.label(_player_sheet_manager.next_creation_question(missing)).classes("text-sm")

        with ui.row().classes("w-full items-center gap-2 chat-input-row"):
            inp = ui.input(placeholder="Decris ton personnage...").classes("w-full").bind_value(state, "chat_draft")

            def _click_send_creation():
                if state.player_sheet_generation_in_progress:
                    return
                asyncio.create_task(_send_creation_message(state, inp, on_change))

            inp.on("keydown.enter", lambda e: _click_send_creation())
            btn = ui.button("Valider profil", on_click=_click_send_creation).props("dense size=sm no-caps").classes("send-btn")
            if state.player_sheet_generation_in_progress:
                btn.disable()
        return

    ui.label("Temps du monde: " + format_fantasy_datetime(state.world_time_minutes)).classes("text-xs opacity-75")
    ui.label("Actions de deplacement/exploration disponibles dans l'onglet Carte.").classes("text-xs opacity-70")

    ui.separator()

    if scene.npc_names:
        ui.label("PNJ présents :").classes("font-semibold")
        with ui.row().classes("flex-wrap gap-1 npc-row"):
            for npc in scene.npc_names:
                ui.button(npc, on_click=lambda npc=npc: _select_npc(state, npc, on_change)) \
                    .props("outline dense size=sm no-caps").classes("npc-btn").style("border-radius: 6px;")

    ui.separator()

    if state.selected_npc:
        selected_profile = _selected_npc_profile(state)
        if selected_profile:
            display_name = profile_display_name(selected_profile, state.selected_npc)
            role = resolve_profile_role(selected_profile, state.selected_npc)
            ui.label(f"Vous parlez à : {display_name} ({role})").classes("text-sm opacity-80")
            with ui.card().classes("w-full rounded-xl shadow-sm"):
                identity = selected_profile.get("identity", {})
                first = str(identity.get("first_name") or "").strip()
                last = str(identity.get("last_name") or "").strip()
                species = str(identity.get("species") or "").strip()
                gender = str(identity.get("gender") or "").strip()
                ui.label(f"Nom: {first} {last}".strip()).classes("text-sm")
                if species or gender:
                    bits = [x for x in (species, gender) if x]
                    ui.label("Identite: " + " | ".join(bits)).classes("text-xs opacity-80")
                ui.label(f"Profil: {selected_profile.get('char_persona', '')}").classes("text-xs opacity-80")
        else:
            pending = resolve_scene_npc_key(state, state.selected_npc, state.current_scene().id) in state.npc_generation_in_progress
            if pending:
                ui.label(f"Vous parlez à : {state.selected_npc} (génération de fiche...)").classes("text-sm opacity-80")
            else:
                ui.label(f"Vous parlez à : {state.selected_npc}").classes("text-sm opacity-80")

        with ui.row().classes("w-full items-center gap-2 chat-input-row"):
            inp = ui.input(placeholder="Écrire un message...").classes("w-full").bind_value(state, "chat_draft")

            def _click_send():
                client = ui.context.client
                asyncio.create_task(_send_to_npc(state, inp, client, on_change))

            inp.on("keydown.enter", lambda e: _click_send())
            ui.button("Envoyer", on_click=_click_send).props("dense size=sm no-caps").classes("send-btn")

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
            else:
                with ui.row().classes("w-full items-center gap-2"):
                    ask_btn = ui.button(
                        "Demander une quete",
                        on_click=lambda: asyncio.create_task(_request_quest_from_selected_npc(state, on_change)),
                    ).props("dense size=sm no-caps")
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
                ).props("dense size=sm no-caps")
                if state.skill_training_in_progress or points <= 0:
                    train_btn.disable()
            if points <= 0:
                ui.label("Aucun point de competence. Monte de niveau pour en gagner.").classes("text-xs opacity-70")
            else:
                ui.label(f"Points competence disponibles: {points}").classes("text-xs opacity-70")
    else:
        ui.label("Selectionne un PNJ ici ou dans l'onglet PNJ pour discuter.").classes("opacity-70")


def _render_dungeon_actions(state: GameState, on_change) -> None:
    scene = state.current_scene()
    anchor = scene.map_anchor or "Lumeria"
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None

    if run and not bool(run.get("completed", False)):
        floor = int(run.get("current_floor", 0))
        total = int(run.get("total_floors", 0))
        name = str(run.get("dungeon_name") or "Donjon")
        ui.button(
            f"Explorer étage suivant ({floor}/{total})",
            on_click=lambda: asyncio.create_task(_advance_dungeon(state, on_change)),
        ).props("outline dense size=sm no-caps").classes("choice-btn")
        ui.button(f"Quitter {name}", on_click=lambda: _leave_dungeon(state, on_change)).props("outline dense size=sm no-caps").classes("choice-btn")
        return

    if state.dungeon_generation_in_progress:
        ui.button("Préparation du donjon...").props("disable flat dense size=sm no-caps").classes("choice-btn")
        return

    ui.button(
        f"Entrer dans le donjon de {anchor}",
        on_click=lambda: _enter_dungeon(state, on_change),
    ).props("outline dense size=sm no-caps").classes("choice-btn")


def _apply_choice(state: GameState, choice: Choice, on_change) -> None:
    state.push("Joueur", choice.label)
    if choice.next_scene_id:
        target = state.scenes.get(choice.next_scene_id)
        if isinstance(target, Scene):
            is_open, status_hint = scene_open_status(target, state.world_time_minutes)
            if not is_open:
                state.push("Système", f"🚪 {status_hint}", count_for_media=False)
                on_change()
                _render_chat_messages.refresh()
                return
        state.set_scene(choice.next_scene_id)
        state.advance_world_time(8 if isinstance(target, Scene) and is_building_scene_title(target.title) else 14)
        spawn_roaming_known_npcs(state)
        state.push("Système", f"➡️ Vous arrivez : {state.current_scene().title}")

    _update_quests_and_notify(state)
    on_change()
    _render_chat_messages.refresh()


def _explore_new_location(state: GameState, on_change) -> None:
    if state.location_generation_in_progress:
        return
    state.location_generation_in_progress = True
    state.push("Système", "Vous quittez la route balisée et cherchez un nouveau passage...", count_for_media=False)
    on_change()
    _render_chat_messages.refresh()
    asyncio.create_task(_generate_and_travel_to_new_location(state, on_change))


async def _generate_and_travel_to_new_location(state: GameState, on_change) -> None:
    origin = state.current_scene()
    try:
        new_scene, travel_label = await _location_manager.generate_next_scene(origin, state.scenes)
        target_anchor = new_scene.map_anchor or ""
        first_arrival_in_anchor = bool(target_anchor) and not any(
            scene.map_anchor == target_anchor for scene in state.scenes.values()
        )
        state.scenes[new_scene.id] = new_scene

        _link_scenes(origin, new_scene, travel_label)
        _link_scenes(new_scene, origin, f"Retour vers {origin.title}")

        if first_arrival_in_anchor and _location_manager.is_city_anchor(target_anchor):
            extra_scenes = _location_manager.generate_city_map_for_new_anchor(
                anchor=target_anchor,
                center_scene=new_scene,
                existing_scenes=state.scenes,
            )
            for extra in extra_scenes:
                state.scenes[extra.id] = extra
            if extra_scenes:
                state.push(
                    "Système",
                    f"🗺️ Nouveau plan de ville généré pour {target_anchor}: {len(extra_scenes) + 1} zones reliées par des ruelles.",
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
        spawn_roaming_known_npcs(state)
        state.push("Système", f"➡️ Vous arrivez : {new_scene.title}")

        state.gm_state["location"] = new_scene.title
        state.gm_state["location_id"] = new_scene.id
        state.gm_state["map_anchor"] = new_scene.map_anchor
    except Exception as e:
        state.push("Système", f"⚠️ Impossible de générer un nouveau lieu: {e}", count_for_media=False)
    finally:
        state.location_generation_in_progress = False

    _update_quests_and_notify(state)
    on_change()
    _render_chat_messages.refresh()


async def _enter_dungeon(state: GameState, on_change) -> None:
    if state.dungeon_generation_in_progress:
        return

    scene = state.current_scene()
    anchor = scene.map_anchor or "Lumeria"

    state.dungeon_generation_in_progress = True
    state.push("Système", f"Vous cherchez l'entrée du donjon de {anchor}...", count_for_media=False)
    on_change()
    _render_chat_messages.refresh()

    try:
        profile = await _dungeon_manager.ensure_dungeon_profile(state.dungeon_profiles, anchor)
        run = _dungeon_manager.start_run(anchor, profile)
        state.active_dungeon_run = run
        state.advance_world_time(18)
        state.push("Ataryxia", str(run.get("entry_text") or "Le donjon s'ouvre devant vous."), count_for_media=False)
        state.push(
            "Système",
            f"{run.get('dungeon_name', 'Donjon')} : {run.get('total_floors', 0)} étages pour cette expédition.",
            count_for_media=False,
        )
    except Exception as e:
        state.push("Système", f"⚠️ Échec de l'ouverture du donjon: {e}", count_for_media=False)
    finally:
        state.dungeon_generation_in_progress = False

    on_change()
    _render_chat_messages.refresh()


async def _advance_dungeon(state: GameState, on_change) -> None:
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if not run:
        return

    event = _dungeon_manager.advance_floor(run)
    if not event:
        state.push("Système", "Le donjon est vidé pour cette expédition.", count_for_media=False)
        state.active_dungeon_run = None
        on_change()
        _render_chat_messages.refresh()
        return

    floor = int(event.get("floor", run.get("current_floor", 0)))
    total = int(run.get("total_floors", 0))
    state.advance_world_time(35)
    state.push("Système", f"[Donjon] Étage {floor}/{total}", count_for_media=False)
    state.push("Ataryxia", str(event.get("text") or "L'étage est silencieux."), count_for_media=False)
    state.quest_counters["dungeon_floors_cleared"] = max(
        0,
        _safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0) + 1,
    )

    try:
        await _maybe_award_loot_from_dungeon_event(state, event)
    except Exception as e:
        state.push("Système", f"Loot indisponible sur cet etage: {e}", count_for_media=False)

    if bool(run.get("completed", False)):
        state.push("Système", "Vous atteignez la fin du donjon et ressortez chargé d'histoires.", count_for_media=False)
        state.active_dungeon_run = None

    _update_quests_and_notify(state)
    on_change()
    _render_chat_messages.refresh()


def _leave_dungeon(state: GameState, on_change) -> None:
    run = state.active_dungeon_run if isinstance(state.active_dungeon_run, dict) else None
    if not run:
        return

    name = str(run.get("dungeon_name") or "le donjon")
    state.active_dungeon_run = None
    state.advance_world_time(10)
    state.push("Système", f"Vous quittez {name} avant d'atteindre les profondeurs.", count_for_media=False)
    on_change()
    _render_chat_messages.refresh()


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
    _render_chat_messages.refresh()


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
    _render_chat_messages.refresh()


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


async def _send_creation_message(state: GameState, inp: ui.input, on_change) -> None:
    text = (state.chat_draft or inp.value or "").strip()
    if not text:
        return
    if state.player_sheet_generation_in_progress:
        return

    state.player_sheet_generation_in_progress = True
    state.push("Joueur", text)
    inp.value = ""
    state.chat_draft = ""
    _render_chat_messages.refresh()

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
    _render_chat_messages.refresh()


async def _send_to_npc(state: GameState, inp: ui.input, client, on_change) -> None:
    _ensure_player_sheet_state(state)
    ensure_conversation_memory_state(state)
    if not state.player_sheet_ready:
        state.push("Système", "Termine d'abord la creation de personnage.", count_for_media=False)
        on_change()
        _render_chat_messages.refresh()
        return

    text = (state.chat_draft or inp.value or "").strip()
    if not text:
        return

    scene = state.current_scene()
    npc = getattr(state, "selected_npc", None)
    npc_key: str | None = None
    npc_profile: dict | None = None
    if npc:
        npc_key = resolve_scene_npc_key(state, npc, scene.id)
        npc_profile = state.npc_profiles.get(npc_key)

        if not isinstance(npc_profile, dict) and npc_key != npc_profile_key(npc, scene.id):
            npc_profile = _npc_manager.load_profile_by_key(
                npc_key,
                fallback_label=npc,
                location_id=scene.id,
                location_title=scene.title,
            )
            if isinstance(npc_profile, dict):
                state.npc_profiles[npc_key] = npc_profile

        if not isinstance(npc_profile, dict):
            try:
                npc_profile = await _npc_manager.ensure_profile(
                    state.npc_profiles,
                    npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
                npc_key = str(npc_profile.get("npc_key") or npc_profile_key(npc, scene.id)).strip() or npc_profile_key(npc, scene.id)
                state.npc_profiles[npc_key] = npc_profile
            except Exception:
                npc_profile = None

        if isinstance(npc_profile, dict):
            register_npc_profile(state, npc_name=npc, scene=scene, profile=npc_profile, npc_key=npc_key or "")

    try:
        state.gm_state["player_name"] = getattr(state.player, "name", "l'Éveillé")
        state.gm_state["location"] = state.current_scene().title
        state.gm_state["location_id"] = state.current_scene().id
        state.gm_state["map_anchor"] = state.current_scene().map_anchor
        state.gm_state["world_time_minutes"] = max(0, _safe_int(state.world_time_minutes, 0))
        state.gm_state["world_datetime"] = format_fantasy_datetime(state.world_time_minutes)
        state.gm_state.setdefault("flags", {})
        state.gm_state["npc_profiles"] = state.npc_profiles
        state.gm_state["player_sheet"] = state.player_sheet
        state.gm_state["player_sheet_ready"] = bool(state.player_sheet_ready)
        state.gm_state["skill_points"] = max(0, _safe_int(state.skill_points, 0))
        state.gm_state["player_gold"] = max(0, _safe_int(state.player.gold, 0))
        state.gm_state["inventory_summary"] = _economy_manager.inventory_summary(state, state.item_defs)
        state.gm_state["player_skills"] = [
            {
                "skill_id": str(s.get("skill_id") or ""),
                "name": str(s.get("name") or ""),
                "category": str(s.get("category") or ""),
                "rank": max(1, _safe_int(s.get("rank"), 1)),
                "level": max(1, _safe_int(s.get("level"), 1)),
                "uses": max(0, _safe_int(s.get("uses"), 0)),
            }
            for s in state.player_skills
            if isinstance(s, dict)
        ]
        if npc and npc_key:
            state.gm_state["selected_npc"] = npc
            state.gm_state["selected_npc_key"] = npc_key
            selected_profile = state.npc_profiles.get(npc_key)
            if isinstance(selected_profile, dict):
                state.gm_state["selected_npc_profile"] = selected_profile
            else:
                state.gm_state.pop("selected_npc_profile", None)
        else:
            state.gm_state.pop("selected_npc", None)
            state.gm_state.pop("selected_npc_key", None)
            state.gm_state.pop("selected_npc_profile", None)

        state.gm_state["conversation_short_term"] = build_short_term_context(state, npc_key, max_lines=12)
        state.gm_state["conversation_long_term"] = build_long_term_context(state, npc_key, max_items=10)
        state.gm_state["conversation_global_memory"] = build_global_memory_context(state, max_items=8)
    except Exception:
        pass

    state.push("Joueur", text)
    state.quest_counters["player_messages_sent"] = max(
        0,
        _safe_int(state.quest_counters.get("player_messages_sent"), 0) + 1,
    )
    if npc_key:
        state.npc_dialogue_counts[npc_key] = max(0, _safe_int(state.npc_dialogue_counts.get(npc_key), 0) + 1)

    trade_outcome = _apply_trade_from_player_message(
        state,
        user_text=text,
        selected_npc=npc,
        npc_key=npc_key,
        selected_profile=npc_profile,
    )

    if isinstance(trade_outcome, dict) and bool(trade_outcome.get("attempted")):
        trade_context = trade_outcome.get("trade_context") if isinstance(trade_outcome.get("trade_context"), dict) else {}
        action = str(trade_context.get("action") or "").strip()
        status = str(trade_context.get("status") or "").strip()
        item_id = str(trade_context.get("item_id") or trade_context.get("query") or "").strip()
        qty_done = max(0, _safe_int(trade_context.get("qty_done"), 0))
        detail = item_id
        if qty_done > 0:
            detail = f"{item_id} x{qty_done}" if item_id else f"x{qty_done}"
        memory_line = f"Economie ({action or 'trade'}): {status or 'inconnu'}"
        if detail:
            memory_line += f" | {detail}"
        remember_system_event(
            state,
            fact_text=memory_line,
            npc_key=npc_key,
            npc_name=str(npc or ""),
            scene_id=scene.id,
            scene_title=scene.title,
            world_time_minutes=state.world_time_minutes,
            kind="trade",
            importance=4,
        )

    state.gm_state["player_gold"] = max(0, _safe_int(state.player.gold, 0))
    state.gm_state["inventory_summary"] = _economy_manager.inventory_summary(state, state.item_defs)
    _update_quests_and_notify(state)
    inp.value = ""
    state.chat_draft = ""
    _render_chat_messages.refresh()

    user_msg = text
    if npc and not user_msg.lstrip().startswith(("/", "@")):
        user_msg = f"@{npc} {user_msg}"

    try:
        res = await _gm.play_turn(state.gm_state, user_msg)
    except Exception as e:
        state.push("Système", f"❌ Erreur IA: {e}", count_for_media=False)
        on_change()
        _render_chat_messages.refresh()
        return

    if res.system:
        state.push("Système", res.system, count_for_media=False)

    if res.dialogue and res.speaker:
        state.push(res.speaker, res.dialogue)

        v = pick_random_video_url()
        if v:
            play_action_video_js(client, v)

    if res.narration:
        try:
            state.current_scene().narrator_text = res.narration
        except Exception:
            pass
        set_narrator_text_js(client, res.narration)

    try:
        remember_dialogue_turn(
            state,
            npc_key=npc_key,
            npc_name=str(res.speaker or npc or "PNJ"),
            player_text=text,
            npc_reply=str(res.dialogue or ""),
            scene_id=scene.id,
            scene_title=scene.title,
            world_time_minutes=state.world_time_minutes,
        )
    except Exception:
        pass

    pre_stats = state.player_sheet.get("stats", {}) if isinstance(state.player_sheet, dict) and isinstance(state.player_sheet.get("stats"), dict) else {}
    pre_level = max(1, _safe_int(pre_stats.get("niveau"), 1))

    try:
        progression = await _player_sheet_manager.infer_progression_update(
            sheet=state.player_sheet,
            user_message=text,
            npc_reply=str(res.dialogue or ""),
            narration=str(res.narration or ""),
        )
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
    on_change()
    _render_chat_messages.refresh()


async def _train_skill_with_selected_npc(state: GameState, on_change) -> None:
    _ensure_skill_state(state)
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return
    if state.skill_training_in_progress:
        return
    if state.skill_points <= 0:
        state.push("Système", "Tu n'as pas de point de competence a depenser pour l'instant.", count_for_media=False)
        on_change()
        _render_chat_messages.refresh()
        return

    scene = state.current_scene()
    npc_key = npc_profile_key(npc, scene.id)
    state.skill_training_in_progress = True
    state.push("Système", f"Vous demandez un entrainement a {npc}...", count_for_media=False)
    on_change()
    _render_chat_messages.refresh()

    try:
        profile = state.npc_profiles.get(npc_key)
        if not isinstance(profile, dict):
            try:
                profile = await _npc_manager.ensure_profile(
                    state.npc_profiles,
                    npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
            except Exception:
                profile = None

        npc_name = profile_display_name(profile, npc) if isinstance(profile, dict) else npc
        npc_role = resolve_profile_role(profile, npc_name) if isinstance(profile, dict) else str(npc_name)
        stats = _player_stats_for_training(state)
        known_ids = {
            str(row.get("skill_id") or "").strip().casefold()
            for row in state.player_skills
            if isinstance(row, dict)
        }
        recent_chat = [
            f"{str(getattr(m, 'speaker', ''))}: {str(getattr(m, 'text', ''))}"
            for m in state.chat[-20:]
            if str(getattr(m, "text", "")).strip()
        ]
        context = _build_player_skill_context(state, recent_chat_lines=recent_chat)

        suggestion = await _skill_manager.suggest_or_create_skill(
            catalog=state.skill_defs,
            known_skill_ids=known_ids,
            player_stats=stats,
            npc_name=npc_name,
            npc_role=npc_role,
            player_context=context,
            recent_chat_lines=recent_chat,
        )
        if not suggestion:
            state.push("Système", "Tu connais deja toutes les competences disponibles actuellement.", count_for_media=False)
            return

        skill = suggestion.get("skill")
        if not isinstance(skill, SkillDef):
            state.push("Système", "Ce PNJ n'a pas d'entrainement utile a te proposer pour le moment.", count_for_media=False)
            return
        if bool(suggestion.get("created", False)):
            state.push("Système", f"Nouvelle competence ajoutee au catalogue: {skill.name} ({skill.skill_id})", count_for_media=False)

        suggestion_reason = str(suggestion.get("reason") or "").strip()
        if suggestion_reason:
            state.push(npc_name, f"Je peux t'enseigner: {skill.name}. {suggestion_reason}", count_for_media=False)
        else:
            state.push(npc_name, f"Je peux t'enseigner: {skill.name}.", count_for_media=False)

        attempt = _skill_manager.attempt_learning(
            skill=skill,
            player_stats=stats,
            npc_role=npc_role,
            skill_points=state.skill_points,
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
                reason=f"Entrainement reussi: +{training_xp} XP compet.",
            )
            if already_known:
                rank = max(1, _safe_int(learned.get("rank"), 1))
                state.push("Système", f"Competence renforcee: {skill.name} (rang {rank}) - jet {roll}/{chance}", count_for_media=False)
            else:
                state.push("Système", f"Nouvelle competence apprise: {skill.name} - jet {roll}/{chance}", count_for_media=False)
            if xp_to_next > 0:
                state.push("Système", f"{skill.name}: +{training_xp} XP compet. ({xp_after}/{xp_to_next})", count_for_media=False)
            if training_levels > 0:
                state.push("Système", f"⬆️ {skill.name} niveau +{training_levels}", count_for_media=False)
        else:
            state.push("Système", f"Entrainement rate pour {skill.name} (jet {roll}/{chance}).", count_for_media=False)

        if reason:
            state.push("Système", reason, count_for_media=False)
        state.player_skills = _skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    except Exception as e:
        state.push("Système", f"Echec entrainement competence: {e}", count_for_media=False)
    finally:
        state.skill_training_in_progress = False

    state.advance_world_time(45)
    on_change()
    _render_chat_messages.refresh()


async def _request_quest_from_selected_npc(state: GameState, on_change) -> None:
    _ensure_quest_state(state)
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return

    scene = state.current_scene()
    npc_key = npc_profile_key(npc, scene.id)
    can_request, reason = _can_request_quest(state, npc_key)
    if not can_request:
        if reason:
            state.push("Système", reason, count_for_media=False)
        on_change()
        _render_chat_messages.refresh()
        return

    state.quest_generation_in_progress.add(npc_key)
    state.push("Système", f"Vous demandez une mission a {npc}...", count_for_media=False)
    on_change()
    _render_chat_messages.refresh()

    try:
        profile = state.npc_profiles.get(npc_key)
        if not isinstance(profile, dict):
            try:
                profile = await _npc_manager.ensure_profile(
                    state.npc_profiles,
                    npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
            except Exception:
                profile = None

        npc_name = profile_display_name(profile, npc) if isinstance(profile, dict) else npc
        existing_titles = [
            str(q.get("title") or "")
            for q in state.quests
            if isinstance(q, dict)
        ]
        quest_payload = await _quest_manager.generate_quest(
            player_name=getattr(state.player, "name", "l'Eveille"),
            npc_name=npc_name,
            location_id=scene.id,
            location_title=scene.title,
            map_anchor=scene.map_anchor or scene.title,
            npc_profile=profile if isinstance(profile, dict) else None,
            existing_titles=existing_titles,
        )

        quest = _build_runtime_quest(
            state,
            quest_payload=quest_payload,
            npc_name=npc_name,
            npc_key=npc_key,
            scene=scene,
        )
        state.quests.append(quest)
        state.npc_quests_given[npc_key] = max(0, _safe_int(state.npc_quests_given.get(npc_key), 0) + 1)

        intro = str(quest_payload.get("quest_intro") or "").strip()
        if intro:
            state.push(npc_name, intro, count_for_media=False)
        state.push("Système", f"Nouvelle quete: {quest.get('title', 'Mission')}", count_for_media=False)

        _update_quests_and_notify(state)
    except Exception as e:
        state.push("Système", f"Echec generation quete: {e}", count_for_media=False)
    finally:
        state.quest_generation_in_progress.discard(npc_key)

    on_change()
    _render_chat_messages.refresh()
