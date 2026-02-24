from __future__ import annotations

import re
import unicodedata

from app.gamemaster.skill_manager import SkillDef
from app.ui.state.game_state import GameState


def ensure_skill_state(state: GameState, *, skill_manager, safe_int) -> None:
    if not isinstance(state.skill_defs, dict) or not state.skill_defs:
        try:
            state.skill_defs = skill_manager.load_catalog()
        except Exception:
            state.skill_defs = {}

    if not isinstance(state.player_skills, list):
        state.player_skills = []
    state.player_skills = skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)

    state.skill_points = max(0, safe_int(getattr(state, "skill_points", 1), 1))
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
        key_norm = skill_manager.canonicalize_intent_label(key)
        if not key_norm:
            continue
        skill_ids_raw = value.get("unlocked_skill_ids")
        skill_ids = []
        if isinstance(skill_ids_raw, list):
            skill_ids = [str(x).strip().casefold() for x in skill_ids_raw if isinstance(x, str) and str(x).strip()]
        merged = sanitized.get(key_norm, {})
        merged_count = max(0, safe_int(merged.get("count"), 0)) + max(0, safe_int(value.get("count"), 0))
        merged_threshold = max(3, safe_int(value.get("threshold"), 6))
        if isinstance(merged, dict) and merged:
            merged_threshold = min(merged_threshold, max(3, safe_int(merged.get("threshold"), merged_threshold)))
        merged_ids_raw = merged.get("unlocked_skill_ids") if isinstance(merged, dict) else []
        merged_ids = set(skill_ids)
        if isinstance(merged_ids_raw, list):
            merged_ids.update(str(x).strip().casefold() for x in merged_ids_raw if isinstance(x, str) and str(x).strip())
        merged_last = str(value.get("last_unlock_at") or "")[:40]
        prev_last = str(merged.get("last_unlock_at") or "")[:40] if isinstance(merged, dict) else ""
        if prev_last and prev_last > merged_last:
            merged_last = prev_last
        sanitized[key_norm[:40]] = {
            "count": min(999, merged_count),
            "threshold": merged_threshold,
            "unlocked_skill_ids": sorted(merged_ids)[:12],
            "last_unlock_at": merged_last,
        }
    state.skill_passive_practice = sanitized


def append_progress_log(
    state: GameState,
    *,
    progression: dict,
    summary_lines: list[str],
    safe_int,
    utc_now_iso,
) -> None:
    if not isinstance(state.player_progress_log, list):
        state.player_progress_log = []

    stat_deltas_raw = progression.get("stat_deltas") if isinstance(progression.get("stat_deltas"), dict) else {}
    stat_deltas: dict[str, int] = {}
    for key, value in stat_deltas_raw.items():
        delta = safe_int(value, 0)
        if delta > 0:
            stat_deltas[str(key)] = delta

    entry = {
        "at": utc_now_iso(),
        "location_id": state.current_scene().id,
        "location_title": state.current_scene().title,
        "source_npc": str(getattr(state, "selected_npc", "") or ""),
        "xp_gain": max(0, safe_int(progression.get("xp_gain"), 0)),
        "stat_deltas": stat_deltas,
        "summary": " | ".join(summary_lines[:6]),
        "reason": str(progression.get("reason") or "").strip(),
    }
    state.player_progress_log.append(entry)
    if len(state.player_progress_log) > 200:
        state.player_progress_log = state.player_progress_log[-200:]


def player_stats_for_training(state: GameState, *, safe_int) -> dict[str, int]:
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
        out[key] = max(1, safe_int(stats.get(key), 5))
    out["niveau"] = max(1, safe_int(stats.get("niveau"), 1))
    return out


def build_player_skill_context(state: GameState, recent_chat_lines: list[str] | None = None) -> str:
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


def upsert_learned_skill(
    state: GameState,
    skill: SkillDef,
    trainer_name: str,
    *,
    skill_manager,
    safe_int,
    utc_now_iso,
) -> tuple[dict, bool]:
    now = utc_now_iso()
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if str(row.get("skill_id") or "").strip().casefold() != skill.skill_id:
            continue
        row["rank"] = min(5, max(1, safe_int(row.get("rank"), 1) + 1))
        row["trainer_npc"] = trainer_name[:80]
        row["learned_at"] = now
        row["name"] = skill.name
        row["category"] = skill.category
        row["description"] = skill.description
        row["difficulty"] = skill.difficulty
        row["primary_stats"] = list(skill.primary_stats)
        row["level"] = max(1, safe_int(row.get("level"), 1))
        row["xp"] = max(0, safe_int(row.get("xp"), 0))
        row["uses"] = max(0, safe_int(row.get("uses"), 0))
        row["xp_to_next"] = skill_manager.xp_needed_for_next_level(max(1, safe_int(row.get("level"), 1)))
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
        "xp_to_next": skill_manager.xp_needed_for_next_level(1),
        "uses": 0,
        "trainer_npc": trainer_name[:80],
        "learned_at": now,
        "last_used_at": "",
    }
    state.player_skills.append(entry)
    state.player_skills = skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    return entry, False


def append_skill_training_log(
    state: GameState,
    *,
    npc_name: str,
    skill: SkillDef,
    chance: int,
    roll: int,
    success: bool,
    reason: str,
    safe_int,
    utc_now_iso,
) -> None:
    entry = {
        "at": utc_now_iso(),
        "npc_name": npc_name,
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "chance": max(0, min(100, safe_int(chance, 0))),
        "roll": max(0, min(100, safe_int(roll, 0))),
        "success": bool(success),
        "reason": str(reason or "").strip()[:220],
    }
    state.skill_training_log.append(entry)
    if len(state.skill_training_log) > 300:
        state.skill_training_log = state.skill_training_log[-300:]


def find_player_skill_entry(state: GameState, skill_id: str) -> dict | None:
    key = str(skill_id or "").strip().casefold()
    if not key:
        return None
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if str(row.get("skill_id") or "").strip().casefold() == key:
            return row
    return None


def append_skill_usage_log(
    state: GameState,
    *,
    skill_entry: dict,
    xp_gain: int,
    levels_gained: int,
    level_after: int,
    source: str = "usage",
    reason: str = "",
    safe_int,
    utc_now_iso,
) -> None:
    text_reason = str(reason or "").strip()
    if not text_reason:
        text_reason = f"Utilisation en jeu: +{max(0, safe_int(xp_gain, 0))} XP, niveau {max(1, safe_int(level_after, 1))}"
    entry = {
        "at": utc_now_iso(),
        "npc_name": str(getattr(state, "selected_npc", "") or ""),
        "skill_id": str(skill_entry.get("skill_id") or ""),
        "skill_name": str(skill_entry.get("name") or ""),
        "chance": 0,
        "roll": 0,
        "success": True,
        "reason": text_reason[:220],
        "source": str(source or "usage")[:24],
        "levels_gained": max(0, safe_int(levels_gained, 0)),
    }
    state.skill_training_log.append(entry)
    if len(state.skill_training_log) > 300:
        state.skill_training_log = state.skill_training_log[-300:]


def apply_skill_usage_progress_from_text(
    state: GameState,
    text: str,
    *,
    ensure_skill_state_fn,
    skill_manager,
    find_player_skill_entry_fn,
    append_skill_usage_log_fn,
    safe_int,
    utc_now_iso,
) -> list[str]:
    ensure_skill_state_fn(state)
    if not isinstance(text, str) or not text.strip():
        return []
    used_ids = skill_manager.detect_used_skill_ids(text, state.player_skills)
    if not used_ids:
        return []

    lines: list[str] = []
    now = utc_now_iso()
    for skill_id in used_ids[:3]:
        entry = find_player_skill_entry_fn(state, skill_id)
        if not isinstance(entry, dict):
            continue
        xp_gain = skill_manager.estimate_usage_xp_gain(entry, text)
        progress = skill_manager.apply_skill_xp(entry, xp_gain=xp_gain, used_at_iso=now)
        level_after = max(1, safe_int(progress.get("level_after"), 1))
        xp_after = max(0, safe_int(progress.get("xp_after"), 0))
        xp_to_next = max(0, safe_int(progress.get("xp_to_next"), 0))
        levels_gained = max(0, safe_int(progress.get("levels_gained"), 0))

        if xp_to_next > 0:
            lines.append(f"{entry.get('name', skill_id)} +{xp_gain} XP compet. ({xp_after}/{xp_to_next})")
        else:
            lines.append(f"{entry.get('name', skill_id)} +{xp_gain} XP compet. (niveau max)")

        if levels_gained > 0:
            lines.append(f"⬆️ {entry.get('name', skill_id)} niveau +{levels_gained} (Niv. {level_after})")

        append_skill_usage_log_fn(
            state,
            skill_entry=entry,
            xp_gain=xp_gain,
            levels_gained=levels_gained,
            level_after=level_after,
        )

    if lines:
        state.player_skills = skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    return lines[:6]


def existing_skill_for_intent(state: GameState, intent: str, *, skill_manager) -> dict | None:
    key = str(intent or "").strip().casefold()
    if not key:
        return None
    for row in state.player_skills:
        if not isinstance(row, dict):
            continue
        if skill_manager.skill_matches_intent(row, key):
            return row
    return None


def next_passive_threshold(current_threshold: int, *, safe_int, base_threshold: int, threshold_growth: float) -> int:
    base = max(3, safe_int(current_threshold, base_threshold))
    grown = int(round(base * float(threshold_growth))) + 1
    return max(4, min(80, grown))


def is_explicit_training_message(text: str) -> bool:
    raw = unicodedata.normalize("NFKD", str(text or "").strip()).encode("ascii", "ignore").decode("ascii").lower()
    plain = re.sub(r"\s+", " ", raw)
    if not plain:
        return False
    return bool(
        re.search(
            r"\b(entraine|entrainer|entrainement|pratique|pratiquer|exerce|exercer|repete|repetition|drill|sparring|combo)\b",
            plain,
        )
    )


def set_skill_debug_snapshot(
    state: GameState,
    *,
    message: str,
    training_focus: bool,
    intents: list[str],
    used_skill_ids: set[str],
    passive_lines: list[str],
    track_preview: list[str],
    utc_now_iso,
) -> None:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    state.gm_state["skill_debug"] = {
        "at": utc_now_iso(),
        "message": str(message or "")[:180],
        "training_focus": bool(training_focus),
        "intents": [str(x)[:40] for x in intents[:4]],
        "used_skill_ids": sorted(str(x)[:64] for x in used_skill_ids if isinstance(x, str))[:6],
        "passive_lines": [str(x)[:180] for x in passive_lines[:4]],
        "track_preview": [str(x)[:80] for x in track_preview[:8]],
    }


async def apply_passive_skill_practice_from_text(
    state: GameState,
    text: str,
    *,
    ensure_skill_state_fn,
    skill_manager,
    build_player_skill_context_fn,
    player_stats_for_training_fn,
    utc_now_iso,
    safe_int,
    existing_skill_for_intent_fn,
    append_skill_usage_log_fn,
    next_passive_threshold_fn,
    selected_npc_profile_fn,
    resolve_profile_role_fn,
    upsert_learned_skill_fn,
    base_threshold: int,
) -> list[str]:
    ensure_skill_state_fn(state)
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
    intents = await skill_manager.infer_intent_hints(
        text,
        existing_intents=existing_intents,
        known_categories=known_categories,
        known_skill_names=known_skill_names,
    )
    lines: list[str] = []
    recent_chat = [f"{m.speaker}: {m.text}" for m in state.chat[-20:]]
    context = build_player_skill_context_fn(state, recent_chat_lines=recent_chat)
    stats = player_stats_for_training_fn(state)
    now = utc_now_iso()
    training_focus = is_explicit_training_message(text)
    used_ids = set(skill_manager.detect_used_skill_ids(text, state.player_skills))
    known_ids = {
        str(row.get("skill_id") or "").strip().casefold()
        for row in state.player_skills
        if isinstance(row, dict)
    }
    unlocked_this_message = False
    reinforced_skill_ids: set[str] = set()
    track_preview: list[str] = []

    if not intents:
        set_skill_debug_snapshot(
            state,
            message=text,
            training_focus=training_focus,
            intents=[],
            used_skill_ids=used_ids,
            passive_lines=[],
            track_preview=[],
            utc_now_iso=utc_now_iso,
        )
        return []

    for intent in intents[:3]:
        track = state.skill_passive_practice.get(intent, {}) if isinstance(state.skill_passive_practice.get(intent), dict) else {}
        count = max(0, safe_int(track.get("count"), 0)) + 1
        threshold = max(3, safe_int(track.get("threshold"), base_threshold))
        unlocked_ids_raw = track.get("unlocked_skill_ids")
        unlocked_ids_iter = unlocked_ids_raw if isinstance(unlocked_ids_raw, list) else []
        unlocked_ids = {
            str(x).strip().casefold()
            for x in unlocked_ids_iter
            if isinstance(x, str) and str(x).strip()
        }

        existing_match = existing_skill_for_intent_fn(state, intent)
        existing_id = ""
        if existing_match:
            existing_id = str(existing_match.get("skill_id") or "").strip().casefold()
            if existing_id:
                unlocked_ids.add(existing_id)

        track["count"] = count
        track["threshold"] = threshold
        track["unlocked_skill_ids"] = sorted(unlocked_ids)
        state.skill_passive_practice[intent] = track

        effective_threshold = threshold
        if training_focus:
            effective_threshold = max(2, threshold - 3)
        track_preview.append(f"{intent}: {count}/{effective_threshold} (base {threshold})")

        if (
            training_focus
            and existing_match
            and existing_id
            and existing_id in known_ids
            and existing_id not in reinforced_skill_ids
            and existing_id not in used_ids
        ):
            reinforce_xp = max(1, skill_manager.estimate_usage_xp_gain(existing_match, f"{text} pratique {intent}") // 2)
            reinforce_progress = skill_manager.apply_skill_xp(existing_match, xp_gain=reinforce_xp, used_at_iso=now)
            reinforce_levels = max(0, safe_int(reinforce_progress.get("levels_gained"), 0))
            reinforce_level_after = max(1, safe_int(reinforce_progress.get("level_after"), 1))
            reinforce_xp_after = max(0, safe_int(reinforce_progress.get("xp_after"), 0))
            reinforce_xp_to_next = max(0, safe_int(reinforce_progress.get("xp_to_next"), 0))
            append_skill_usage_log_fn(
                state,
                skill_entry=existing_match,
                xp_gain=reinforce_xp,
                levels_gained=reinforce_levels,
                level_after=reinforce_level_after,
                source="practice",
                reason=f"Entrainement explicite ({intent})",
            )
            skill_name = str(existing_match.get("name") or existing_id or "competence").strip()
            if reinforce_xp_to_next > 0:
                lines.append(f"Pratique ({intent}): {skill_name} +{reinforce_xp} XP compet. ({reinforce_xp_after}/{reinforce_xp_to_next})")
            else:
                lines.append(f"Pratique ({intent}): {skill_name} +{reinforce_xp} XP compet. (niveau max)")
            if reinforce_levels > 0:
                lines.append(f"⬆️ {skill_name} niveau +{reinforce_levels} (Niv. {reinforce_level_after})")
            reinforced_skill_ids.add(existing_id)

        if unlocked_this_message or count < effective_threshold:
            continue

        if existing_match and existing_id in known_ids:
            track["count"] = 0
            track["threshold"] = next_passive_threshold_fn(threshold)
            track["last_unlock_at"] = now
            state.skill_passive_practice[intent] = track
            continue

        npc = str(getattr(state, "selected_npc", "") or "").strip() or "Pratique"
        profile = selected_npc_profile_fn(state)
        npc_role = resolve_profile_role_fn(profile, npc) if isinstance(profile, dict) else state.current_scene().title
        suggestion = await skill_manager.suggest_or_create_skill(
            catalog=state.skill_defs,
            known_skill_ids=known_ids,
            player_stats=stats,
            npc_name=npc,
            npc_role=npc_role,
            player_context=f"{context} | pratique repetee: {intent}",
            recent_chat_lines=recent_chat,
        )
        if not suggestion:
            track["threshold"] = next_passive_threshold_fn(threshold)
            state.skill_passive_practice[intent] = track
            continue

        skill = suggestion.get("skill")
        if not isinstance(skill, SkillDef):
            track["threshold"] = next_passive_threshold_fn(threshold)
            state.skill_passive_practice[intent] = track
            continue

        learned, already_known = upsert_learned_skill_fn(state, skill, trainer_name=npc)
        practice_xp = skill_manager.estimate_usage_xp_gain(learned, f"{text} {intent}")
        progress = skill_manager.apply_skill_xp(learned, xp_gain=practice_xp, used_at_iso=now)
        levels_gained = max(0, safe_int(progress.get("levels_gained"), 0))
        level_after = max(1, safe_int(progress.get("level_after"), 1))
        xp_after = max(0, safe_int(progress.get("xp_after"), 0))
        xp_to_next = max(0, safe_int(progress.get("xp_to_next"), 0))
        append_skill_usage_log_fn(
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
            known_ids.add(skill_id)
        track["unlocked_skill_ids"] = sorted(unlocked_ids)
        track["count"] = 0
        track["threshold"] = next_passive_threshold_fn(threshold)
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
        state.player_skills = skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    set_skill_debug_snapshot(
        state,
        message=text,
        training_focus=training_focus,
        intents=intents,
        used_skill_ids=used_ids,
        passive_lines=lines,
        track_preview=track_preview,
        utc_now_iso=utc_now_iso,
    )
    return lines[:6]
