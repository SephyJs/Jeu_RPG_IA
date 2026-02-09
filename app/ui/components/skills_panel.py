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
    ui.separator()

    points = max(0, _safe_int(getattr(state, "skill_points", 0), 0))
    ui.label(f"Points de competence disponibles: {points}").classes("text-sm")
    ui.label("Les competences s'apprennent surtout en parlant aux PNJ metiers.").classes("text-xs opacity-70")
    ui.label("Courbe XP des competences: non lineaire (exponentielle).").classes("text-xs opacity-70")
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
            ui.label("Pratique passive: " + " | ".join(snippets)).classes("text-xs opacity-70")

    skills = state.player_skills if isinstance(state.player_skills, list) else []
    if not skills:
        ui.separator()
        ui.label("Aucune competence apprise pour le moment.").classes("opacity-70")
        return

    ui.separator()
    ui.label(f"Competences apprises: {len(skills)}").classes("text-sm opacity-80")
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

        with ui.card().classes("w-full rounded-xl shadow-sm"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(name).classes("font-semibold")
                ui.label(f"Rang {rank} | Niv. {level}").classes("text-xs opacity-80")
            ui.label(f"Categorie: {category} | Difficulte: {difficulty}/5").classes("text-xs opacity-80")
            if xp_to_next > 0:
                ui.label(f"XP competence: {xp}/{xp_to_next} | Utilisations: {uses}").classes("text-xs opacity-80")
                ui.linear_progress(value=progress_value).props("instant-feedback")
            else:
                ui.label(f"XP competence: niveau max | Utilisations: {uses}").classes("text-xs opacity-80")
            if stats_text:
                ui.label(f"Stats cle: {stats_text}").classes("text-xs opacity-80")
            if desc:
                ui.label(desc).classes("text-sm")
            if trainer:
                ui.label(f"Appris via: {trainer}").classes("text-xs opacity-70")
