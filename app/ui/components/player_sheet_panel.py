from __future__ import annotations

import json
from datetime import datetime

from nicegui import ui

from app.ui.state.game_state import GameState


_MISSING_LABELS = {
    "char_name": "Pseudo / nom",
    "gender": "Genre",
    "appearance": "Apparence",
    "strengths": "Atouts",
    "persona": "Personnalite",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return text[:16]


def _deltas_summary(deltas: object) -> str:
    if not isinstance(deltas, dict):
        return ""
    parts: list[str] = []
    for key, value in deltas.items():
        delta = _safe_int(value, 0)
        if delta <= 0:
            continue
        parts.append(f"+{delta} {key}")
    return " | ".join(parts[:6])


def player_sheet_panel(state: GameState, on_change) -> None:
    sheet = state.player_sheet if isinstance(state.player_sheet, dict) else {}
    if not sheet:
        ui.label("Aucune fiche joueur disponible pour le moment.").classes("opacity-70")
        return

    ready = bool(state.player_sheet_ready)
    missing = state.player_sheet_missing if isinstance(state.player_sheet_missing, list) else []

    char_name = str(sheet.get("char_name") or "Inconnu")
    metadata = sheet.get("metadata", {}) if isinstance(sheet.get("metadata"), dict) else {}
    char_id = str(metadata.get("char_id") or "")
    identity = sheet.get("identity", {}) if isinstance(sheet.get("identity"), dict) else {}
    family = identity.get("family", {}) if isinstance(identity.get("family"), dict) else {}
    visual = sheet.get("description_visuelle", {}) if isinstance(sheet.get("description_visuelle"), dict) else {}
    lore = sheet.get("lore_details", {}) if isinstance(sheet.get("lore_details"), dict) else {}
    world_logic = sheet.get("world_logic", {}) if isinstance(sheet.get("world_logic"), dict) else {}
    stats = sheet.get("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    effective_stats = sheet.get("effective_stats", {}) if isinstance(sheet.get("effective_stats"), dict) else {}
    shown_stats = effective_stats if effective_stats else stats

    ui.label("Fiche personnage").classes("text-lg font-semibold")
    with ui.column().classes("w-full gap-3"):
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px 14px;"):
            ui.label(f"Nom: {char_name}").classes("text-base font-semibold")
            if char_id:
                ui.label(f"ID: {char_id}").classes("text-sm opacity-70")
            if not ready:
                labels = [_MISSING_LABELS.get(k, k) for k in missing]
                ui.label("Creation en cours.").classes("text-sm text-amber-8")
                if labels:
                    ui.label("Infos manquantes: " + ", ".join(labels)).classes("text-sm opacity-80")
            else:
                ui.label("Fiche complete.").classes("text-sm text-green-8")

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px 14px;"):
            ui.label("Identité").classes("text-sm font-semibold")
            with ui.column().classes("w-full gap-1").style("margin-top:6px;"):
                ui.label(f"Genre: {identity.get('gender', 'inconnu')}").classes("text-sm leading-6")
                ui.label(f"Origine: {family.get('origin', 'inconnue')}").classes("text-sm leading-6")
                if str(visual.get("courte") or "").strip():
                    ui.label("Apparence: " + str(visual.get("courte"))).classes("text-sm leading-6")
                if str(sheet.get("char_persona") or "").strip():
                    ui.label("Personnalité: " + str(sheet.get("char_persona"))).classes("text-sm leading-6")

            passives = lore.get("passives")
            if isinstance(passives, list) and passives:
                ui.label("Atouts").classes("text-sm font-semibold").style("margin-top:8px;")
                with ui.column().classes("w-full gap-1").style("margin-top:4px;"):
                    for p in passives[:6]:
                        if not isinstance(p, dict):
                            continue
                        nom = str(p.get("nom") or "").strip()
                        eff = str(p.get("effet") or "").strip()
                        if not nom:
                            continue
                        label = f"- {nom}"
                        if eff:
                            label += f": {eff}"
                        ui.label(label).classes("text-sm opacity-90")

            goals = world_logic.get("goals")
            if isinstance(goals, list) and goals:
                ui.label("Objectifs: " + ", ".join(str(g) for g in goals[:4] if isinstance(g, str))).classes("text-sm opacity-80").style(
                    "margin-top:8px;"
                )

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px 14px;"):
            ui.label("Statistiques").classes("text-sm font-semibold")
            with ui.column().classes("w-full gap-1").style("margin-top:6px;"):
                ui.label(
                    f"Niveau: {_safe_int(stats.get('niveau'), 1)} | XP: {_safe_int(stats.get('experience'), 0)} | Or: {_safe_int(state.player.gold, 0)}"
                ).classes("text-sm leading-6")
                ui.label(f"Points de competence: {_safe_int(getattr(state, 'skill_points', 0), 0)}").classes("text-sm leading-6")
                ui.label(
                    f"PV: {_safe_int(shown_stats.get('pv'), state.player.hp)}/{_safe_int(shown_stats.get('pv_max'), state.player.max_hp)} | "
                    f"Mana: {_safe_int(shown_stats.get('mana'), 0)}/{_safe_int(shown_stats.get('mana_max'), 0)}"
                ).classes("text-sm leading-6")
                ui.label(
                    "Force: {force} | Magie: {magie} | Intelligence: {intelligence} | Defense: {defense}".format(
                        force=_safe_int(shown_stats.get("force"), 0),
                        magie=_safe_int(shown_stats.get("magie"), 0),
                        intelligence=_safe_int(shown_stats.get("intelligence"), 0),
                        defense=_safe_int(shown_stats.get("defense"), 0),
                    )
                ).classes("text-sm leading-6")
                ui.label(
                    "Sagesse: {sagesse} | Agilite: {agilite} | Dexterite: {dexterite} | Chance: {chance} | Charisme: {charisme}".format(
                        sagesse=_safe_int(shown_stats.get("sagesse"), 0),
                        agilite=_safe_int(shown_stats.get("agilite"), 0),
                        dexterite=_safe_int(shown_stats.get("dexterite"), 0),
                        chance=_safe_int(shown_stats.get("chance"), 0),
                        charisme=_safe_int(shown_stats.get("charisme"), 0),
                    )
                ).classes("text-sm leading-6")
                if effective_stats:
                    bonuses = sheet.get("equipment_bonuses", {}) if isinstance(sheet.get("equipment_bonuses"), dict) else {}
                    bonus_parts = [f"+{_safe_int(v)} {k}" for k, v in bonuses.items() if _safe_int(v) > 0]
                    if bonus_parts:
                        ui.label("Bonus equipement: " + " | ".join(bonus_parts[:8])).classes("text-sm opacity-80")

            equipment = sheet.get("equipment_runtime", {}) if isinstance(sheet.get("equipment_runtime"), dict) else {}
            if equipment:
                ui.label("Équipé").classes("text-sm font-semibold").style("margin-top:8px;")
                with ui.column().classes("w-full gap-1").style("margin-top:4px;"):
                    for slot in ("weapon", "armor", "accessory_1", "accessory_2"):
                        data = equipment.get(slot) if isinstance(equipment.get(slot), dict) else {}
                        item_name = str(data.get("name") or "").strip() or "(vide)"
                        ui.label(f"- {slot}: {item_name}").classes("text-sm opacity-90")

    logs = state.player_progress_log if isinstance(state.player_progress_log, list) else []
    ui.separator()

    def _clear_progress_log() -> None:
        state.player_progress_log = []
        on_change()
        ui.notify("Journal de progression vide.")

    with ui.dialog() as journal_dialog:
        with ui.card().classes("w-full rounded-2xl").style("max-width: 760px; width: min(95vw, 760px);"):
            with ui.row().classes("w-full items-center"):
                ui.label("Journal complet").classes("text-lg font-semibold")
                ui.space()
                ui.button("Fermer", on_click=journal_dialog.close).props("flat dense no-caps")
            ui.separator()

            if not logs:
                ui.label("Aucun gain enregistre pour le moment.").classes("text-sm opacity-70")
            else:
                for entry in list(reversed(logs[-50:])):
                    if not isinstance(entry, dict):
                        continue
                    when = _format_timestamp(entry.get("at"))
                    place = str(entry.get("location_title") or "")
                    xp = max(0, _safe_int(entry.get("xp_gain"), 0))
                    delta_text = _deltas_summary(entry.get("stat_deltas"))
                    summary = str(entry.get("summary") or "").strip()
                    reason = str(entry.get("reason") or "").strip()

                    with ui.card().classes("w-full rounded-lg shadow-sm"):
                        title_line = "Gain"
                        if when:
                            title_line += f" - {when}"
                        if place:
                            title_line += f" - {place}"
                        ui.label(title_line).classes("text-xs opacity-80")

                        parts: list[str] = []
                        if xp > 0:
                            parts.append(f"+{xp} XP")
                        if delta_text:
                            parts.append(delta_text)
                        if summary:
                            parts.append(summary)
                        if parts:
                            ui.label(" | ".join(parts[:3])).classes("text-xs")
                        if reason:
                            ui.label(reason).classes("text-xs opacity-70")

    with ui.card().classes("w-full rounded-2xl shadow-sm").style("margin:0; padding:10px;"):
        with ui.element("div").classes("w-full").style("position:relative; margin-top:6px;"):
            with ui.card().classes("w-full rounded-xl").style("min-height:280px; max-height:340px; overflow-y:auto;"):
                if not logs:
                    ui.label("Aucun evenement\nconsigne pour le moment.").classes("text-sm opacity-65").style("white-space:pre-line;")
                else:
                    for entry in list(reversed(logs[-6:])):
                        if not isinstance(entry, dict):
                            continue
                        when = _format_timestamp(entry.get("at"))
                        place = str(entry.get("location_title") or "").strip()
                        summary = str(entry.get("summary") or "").strip()
                        line = summary or "Evenement"
                        if place:
                            line = f"{place}: {line}"
                        if when:
                            ui.label(when).classes("text-[10px] opacity-60")
                        ui.label(line[:140]).classes("text-xs")
                        ui.separator()

            ui.label("Journal").classes("text-xs font-semibold rounded-md").style(
                "position:absolute; top:0; right:12px; transform:translateY(-50%); "
                "padding:3px 10px; border:1px solid rgba(255,255,255,0.28); "
                "background:rgba(15,23,42,0.95); color:#f8fafc; pointer-events:none;"
            )

        with ui.row().classes("w-full items-center justify-center gap-3").style("margin-top:12px;"):
            ui.button("Ouvrir", on_click=journal_dialog.open).props("outline no-caps").style("min-width:130px; min-height:36px;")
            ui.button("Supprimer", on_click=_clear_progress_log).props("outline no-caps").style("min-width:130px; min-height:36px;")

    with ui.expansion("JSON fiche joueur", icon="data_object").classes("w-full"):
        ui.code(json.dumps(sheet, ensure_ascii=False, indent=2), language="json").classes("w-full")
