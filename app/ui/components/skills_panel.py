from __future__ import annotations

from nicegui import ui

from app.ui.state.game_state import GameState


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def skills_panel(state: GameState, on_change) -> None:
    _ = on_change
    ui.label("Competences").classes("text-lg font-semibold")

    points = max(0, _safe_int(getattr(state, "skill_points", 0), 0))
    with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px 14px; margin-top:6px;"):
        ui.label(f"Points de competence disponibles: {points}").classes("text-sm font-semibold")
        ui.label("Les competences s'apprennent surtout en parlant aux PNJ metiers.").classes("text-sm opacity-75")

    gm_state = state.gm_state if isinstance(getattr(state, "gm_state", None), dict) else {}
    skill_debug = gm_state.get("skill_debug") if isinstance(gm_state.get("skill_debug"), dict) else {}
    if skill_debug:
        intents = skill_debug.get("intents") if isinstance(skill_debug.get("intents"), list) else []
        used_ids = skill_debug.get("used_skill_ids") if isinstance(skill_debug.get("used_skill_ids"), list) else []
        track_preview = skill_debug.get("track_preview") if isinstance(skill_debug.get("track_preview"), list) else []
        passive_lines = skill_debug.get("passive_lines") if isinstance(skill_debug.get("passive_lines"), list) else []
        training_focus = bool(skill_debug.get("training_focus"))
        message = str(skill_debug.get("message") or "").strip()

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px; margin-top:8px; border:1px dashed #6b7280;"):
            ui.label("Debug apprentissage (dernier message)").classes("text-sm font-semibold")
            ui.label(f"Focus entrainement: {'oui' if training_focus else 'non'}").classes("text-xs opacity-80")
            if message:
                ui.label(f"Message: {message}").classes("text-xs opacity-75")
            if intents:
                ui.label("Intentions detectees: " + ", ".join(str(x) for x in intents[:4])).classes("text-xs opacity-80")
            else:
                ui.label("Intentions detectees: aucune").classes("text-xs opacity-70")
            if used_ids:
                ui.label("Competences reconnues dans le texte: " + ", ".join(str(x) for x in used_ids[:4])).classes("text-xs opacity-80")
            if track_preview:
                ui.label("Compteurs passifs: " + " | ".join(str(x) for x in track_preview[:4])).classes("text-xs opacity-80")
            if passive_lines:
                ui.label("Resultat passif: " + " | ".join(str(x) for x in passive_lines[:2])).classes("text-xs opacity-80")

    passive = state.skill_passive_practice if isinstance(getattr(state, "skill_passive_practice", {}), dict) else {}
    if passive:
        snippets: list[str] = []
        for intent, row in list(passive.items())[:6]:
            if not isinstance(intent, str) or not isinstance(row, dict):
                continue
            count = max(0, _safe_int(row.get("count"), 0))
            threshold = max(1, _safe_int(row.get("threshold"), 1))
            label = intent.replace("_", " ")
            snippets.append(f"{label}: {count}/{threshold}")
        if snippets:
            ui.label("Pratique passive: " + " | ".join(snippets)).classes("text-sm opacity-75").style("margin-top:6px;")

    skills = state.player_skills if isinstance(state.player_skills, list) else []
    if not skills:
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px; margin-top:10px;"):
            ui.label("Aucune competence apprise pour le moment.").classes("opacity-70")
        return

    ui.label(f"Competences apprises: {len(skills)}").classes("text-sm opacity-80").style("margin-top:10px;")
    for row in skills[:80]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("skill_id") or "Competence").strip()
        category = str(row.get("category") or "general").strip()
        desc = str(row.get("description") or "").strip()
        rank = max(1, _safe_int(row.get("rank"), 1))
        level = max(1, _safe_int(row.get("level"), 1))
        xp = max(0, _safe_int(row.get("xp"), 0))
        xp_to_next = max(0, _safe_int(row.get("xp_to_next"), 0))
        uses = max(0, _safe_int(row.get("uses"), 0))
        difficulty = max(1, _safe_int(row.get("difficulty"), 1))
        trainer = str(row.get("trainer_npc") or "").strip()
        primary_stats = row.get("primary_stats") if isinstance(row.get("primary_stats"), list) else []
        stats_text = ", ".join(str(x) for x in primary_stats[:3] if isinstance(x, str))
        progress_value = min(max((xp / float(xp_to_next)) if xp_to_next > 0 else 1.0, 0.0), 1.0)
        summary = f"{name} · Rang {rank} · Niv. {level}"
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:6px 10px;"):
            with ui.expansion(summary).classes("w-full").props("dense switch-toggle-side expand-separator"):
                with ui.column().classes("w-full gap-1").style("padding:4px 2px 8px 2px;"):
                    ui.label(f"Categorie: {category} | Difficulte: {difficulty}/5").classes("text-sm opacity-75")
                    if xp_to_next > 0:
                        ui.label(f"XP competence: {xp}/{xp_to_next} | Utilisations: {uses}").classes("text-sm opacity-80")
                        ui.linear_progress(value=progress_value).props("instant-feedback")
                    else:
                        ui.label(f"XP competence: niveau max | Utilisations: {uses}").classes("text-sm opacity-80")
                    if stats_text:
                        ui.label(f"Stats cle: {stats_text}").classes("text-sm opacity-80")
                    if desc:
                        ui.label(desc).classes("text-sm leading-6")
                    if trainer:
                        ui.label(f"Appris via: {trainer}").classes("text-sm opacity-70")
