from __future__ import annotations

import re
import unicodedata

from app.gamemaster.skill_manager import SkillDef
from app.ui.state.game_state import GameState


def _norm_text(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", folded).strip().casefold()


def _extract_recent_player_training_intent(chat_rows: list[object]) -> str:
    if not isinstance(chat_rows, list):
        return ""
    for msg in reversed(chat_rows[-24:]):
        speaker = _norm_text(getattr(msg, "speaker", ""))
        if speaker and speaker not in {"joueur", "player"}:
            continue
        text = _norm_text(getattr(msg, "text", ""))
        if not text:
            continue
        if any(token in text for token in ("defense", "defence", "resistance", "bouclier", "parade", "protection", "armure")):
            return "defense"
        if any(token in text for token in ("soin", "guerison", "heal", "regene", "regeneration")):
            return "soin"
        if any(token in text for token in ("magie", "sort", "arcane", "mana", "incant")):
            return "magie"
        if any(token in text for token in ("attaque", "frappe", "combat", "epee", "lame")):
            return "combat"
    return ""


def _skill_role_match(skill: SkillDef, npc_role: str) -> bool:
    role = _norm_text(npc_role)
    if not role:
        return False
    for token in skill.trainer_roles:
        role_token = _norm_text(token)
        if role_token and role_token in role:
            return True
    return False


def _pick_skill_for_intent(
    *,
    intent: str,
    catalog: dict[str, SkillDef],
    known_skill_ids: set[str],
    npc_role: str,
    player_stats: dict[str, int],
    skill_manager,
) -> SkillDef | None:
    intent_key = _norm_text(intent)
    if not intent_key or not isinstance(catalog, dict):
        return None

    ranked: list[tuple[int, int, int, int, SkillDef]] = []
    for skill in catalog.values():
        if not isinstance(skill, SkillDef):
            continue
        if not (
            skill_manager.skill_matches_intent(skill, intent_key)
            or intent_key in _norm_text(skill.category)
            or intent_key in _norm_text(skill.name)
        ):
            continue
        unknown = 1 if skill.skill_id not in known_skill_ids else 0
        role_hit = 1 if _skill_role_match(skill, npc_role) else 0
        stat_score = sum(max(1, int(player_stats.get(stat, 5))) for stat in skill.primary_stats)
        low_diff_bonus = max(0, 6 - max(1, int(skill.difficulty)))
        ranked.append((unknown, role_hit, stat_score, low_diff_bonus, skill))

    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3], -len(row[4].name)), reverse=True)
    return ranked[0][4]


async def train_skill_with_selected_npc(
    state: GameState,
    on_change,
    *,
    ensure_skill_state_fn,
    safe_int,
    npc_profile_key,
    npc_manager,
    profile_display_name,
    resolve_profile_role,
    player_stats_for_training_fn,
    build_player_skill_context_fn,
    skill_manager,
    append_skill_training_log_fn,
    upsert_learned_skill_fn,
    append_skill_usage_log_fn,
    utc_now_iso,
    refresh_chat_messages_view,
) -> None:
    ensure_skill_state_fn(state)
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return
    if state.skill_training_in_progress:
        return
    if state.skill_points <= 0:
        state.push("Système", "Tu n'as pas de point de competence a depenser pour l'instant.", count_for_media=False)
        on_change()
        refresh_chat_messages_view()
        return

    scene = state.current_scene()
    npc_key = npc_profile_key(npc, scene.id)
    state.skill_training_in_progress = True
    state.push("Système", f"Vous demandez un entrainement a {npc}...", count_for_media=False)
    on_change()
    refresh_chat_messages_view()

    try:
        profile = state.npc_profiles.get(npc_key)
        if not isinstance(profile, dict):
            try:
                profile = await npc_manager.ensure_profile(
                    state.npc_profiles,
                    npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
            except Exception:
                profile = None

        npc_name = profile_display_name(profile, npc) if isinstance(profile, dict) else npc
        npc_role = resolve_profile_role(profile, npc_name) if isinstance(profile, dict) else str(npc_name)
        stats = player_stats_for_training_fn(state)
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
        context = build_player_skill_context_fn(state, recent_chat_lines=recent_chat)

        suggestion: dict | None = None
        explicit_intent = _extract_recent_player_training_intent(state.chat if isinstance(state.chat, list) else [])
        if explicit_intent:
            intent_skill = _pick_skill_for_intent(
                intent=explicit_intent,
                catalog=state.skill_defs,
                known_skill_ids=known_ids,
                npc_role=npc_role,
                player_stats=stats,
                skill_manager=skill_manager,
            )
            if isinstance(intent_skill, SkillDef):
                suggestion = {
                    "skill": intent_skill,
                    "created": False,
                    "reason": f"Tu demandes un entrainement oriente {explicit_intent}.",
                }

        if suggestion is None:
            suggestion = await skill_manager.suggest_or_create_skill(
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

        attempt = skill_manager.attempt_learning(
            skill=skill,
            player_stats=stats,
            npc_role=npc_role,
            skill_points=state.skill_points,
        )
        state.skill_points = max(0, safe_int(attempt.get("skill_points_after"), state.skill_points))

        success = bool(attempt.get("success", False))
        chance = max(0, min(100, safe_int(attempt.get("chance"), 0)))
        roll = max(0, min(100, safe_int(attempt.get("roll"), 0)))
        reason = str(attempt.get("reason") or "").strip()

        append_skill_training_log_fn(
            state,
            npc_name=npc_name,
            skill=skill,
            chance=chance,
            roll=roll,
            success=success,
            reason=reason,
        )

        if success:
            learned, already_known = upsert_learned_skill_fn(state, skill, npc_name)
            training_xp = skill_manager.estimate_training_xp_gain(learned, success=True)
            training_progress = skill_manager.apply_skill_xp(learned, xp_gain=training_xp, used_at_iso=utc_now_iso())
            training_levels = max(0, safe_int(training_progress.get("levels_gained"), 0))
            xp_after = max(0, safe_int(training_progress.get("xp_after"), 0))
            xp_to_next = max(0, safe_int(training_progress.get("xp_to_next"), 0))
            append_skill_usage_log_fn(
                state,
                skill_entry=learned,
                xp_gain=training_xp,
                levels_gained=training_levels,
                level_after=max(1, safe_int(training_progress.get("level_after"), 1)),
                source="training",
                reason=f"Entrainement reussi: +{training_xp} XP compet.",
            )
            if already_known:
                rank = max(1, safe_int(learned.get("rank"), 1))
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
        state.player_skills = skill_manager.normalize_known_skills(state.player_skills, state.skill_defs)
    except Exception as e:
        state.push("Système", f"Echec entrainement competence: {e}", count_for_media=False)
    finally:
        state.skill_training_in_progress = False

    state.advance_world_time(45)
    on_change()
    refresh_chat_messages_view()
