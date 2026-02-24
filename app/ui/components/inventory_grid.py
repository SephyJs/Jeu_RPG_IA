from nicegui import ui
from app.ui.state.game_state import GameState
from app.ui.state.inventory import InventoryGrid, ItemStack
from app.ui.components.consumables import add_consumable_stat_buff


SLOT_SIZE_PX = 46
SLOT_GAP_PX = 6
_RARITY_LABELS = {
    "common": "Commun",
    "uncommon": "Inhabituel",
    "rare": "Rare",
    "epic": "Epique",
    "legendary": "Legendaire",
}
_TYPE_LABELS = {
    "weapon": "Arme",
    "armor": "Armure",
    "accessory": "Accessoire",
    "consumable": "Consommable",
    "material": "Materiau",
    "misc": "Divers",
}
_STAT_LABELS = {
    "pv_max": "PV max",
    "mana_max": "Mana max",
    "force": "Force",
    "intelligence": "Intelligence",
    "magie": "Magie",
    "defense": "Defense",
    "sagesse": "Sagesse",
    "agilite": "Agilite",
    "dexterite": "Dexterite",
    "chance": "Chance",
    "charisme": "Charisme",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def inventory_panel(state: GameState, on_change) -> None:
    # Conteneur global : empêche les débordements dans la colonne gauche
    with ui.element("div").classes("w-full").style("max-width: 100%; min-width: 0; overflow: hidden;"):
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px;"):
            ui.label("Inventaire porte").classes("text-base font-semibold")
            ui.label("Objets sur toi").classes("text-xs opacity-70")
            _grid(state, state.carried, "carried", on_change)

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px; margin-top:10px;"):
            ui.label("Stockage").classes("text-base font-semibold")
            ui.label("Reserve locale").classes("text-xs opacity-70")
            _grid(state, state.storage, "storage", on_change)

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px; margin-top:10px;"):
            ui.label("Equipement").classes("text-base font-semibold")
            _equipment_panel(state, on_change)

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px; margin-top:10px;"):
            _actions(state, on_change)

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px; margin-top:10px;"):
            _selected_item_details(state)


def _grid(state: GameState, grid: InventoryGrid, which: str, on_change) -> None:
    cols = grid.cols

    # Wrapper scroll horizontal si un jour tu mets plus de colonnes que la largeur dispo
    with ui.element("div").classes("w-full").style("max-width:100%; overflow-x:auto; overflow-y:hidden; margin-top:8px;"):
        # Grille
        with ui.element("div").style(
            "display: grid;"
            f"grid-template-columns: repeat({cols}, {SLOT_SIZE_PX}px);"
            f"gap: {SLOT_GAP_PX}px;"
            "justify-content: start;"
            "width: max-content;"  # important: la grid prend la largeur de son contenu
        ):
            for idx in range(grid.cols * grid.rows):
                stack = grid.get(idx)
                selected = (state.selected_slot == (which, idx))

                label = ""
                if stack:
                    # court pour tenir dans 46px
                    short_name = _item_name(state, stack.item_id)[:6]
                    label = f"{short_name}\n×{stack.qty}"

                btn_style = (
                    f"width:{SLOT_SIZE_PX}px; height:{SLOT_SIZE_PX}px;"
                    "white-space:pre; font-size:10px; line-height:1.05;"
                    "padding: 0; box-sizing: border-box;"
                )
                if selected:
                    btn_style += "outline: 2px solid #999;"

                btn = ui.button(
                    label,
                    on_click=lambda idx=idx, which=which: _select(state, which, idx, on_change),
                ).props("flat").style(btn_style)
                if stack:
                    with btn:
                        ui.tooltip(_item_tooltip_text(state, stack.item_id, stack.qty)).style(
                            "white-space: pre-line; max-width: 360px;"
                        )


def _select(state: GameState, which: str, idx: int, on_change) -> None:
    state.selected_slot = (which, idx)
    on_change()


def _actions(state: GameState, on_change) -> None:
    ui.label("Actions").classes("text-sm font-semibold")

    # Container full width, min-width:0 = évite les débordements en flex
    with ui.element("div").classes("w-full").style("max-width:100%; min-width:0;"):
        with ui.column().classes("w-full").style("gap: 8px;"):
            _full_button("Vers stockage", lambda: _move(state, "carried", "storage", on_change))
            _full_button("Vers porté", lambda: _move(state, "storage", "carried", on_change))
            _full_button("Utiliser selection", lambda: _use_selected(state, on_change))
            _full_button("Équiper sélection", lambda: _equip_selected(state, on_change))
            _full_button("Retirer équipement", lambda: _unequip_selected(state, on_change))


def _full_button(text: str, on_click) -> None:
    # style: width 100% + box-sizing pour ne jamais dépasser la card
    ui.button(text, on_click=on_click) \
        .props("outline no-caps") \
        .classes("w-full") \
        .style(
            "width: 100%; max-width: 100%; min-width: 0;"
            "box-sizing: border-box;"
            "border-radius: 8px;"
            "min-height: 36px;"
            "padding: 8px 10px;"
            "font-size: 13px;"
        )


def _move(state: GameState, src: str, dst: str, on_change) -> None:
    if not state.selected_slot:
        return

    which, idx = state.selected_slot
    if which != src:
        return

    src_grid = state.carried if src == "carried" else state.storage
    dst_grid = state.carried if dst == "carried" else state.storage

    stack = src_grid.get(idx)
    if not stack:
        return

    # trouver un slot vide destination
    try:
        empty_idx = dst_grid.slots.index(None)
    except ValueError:
        state.push("Système", "Aucun slot vide.", count_for_media=False)
        on_change()
        return

    dst_grid.set(empty_idx, stack)
    src_grid.set(idx, None)
    state.selected_slot = (dst, empty_idx)
    on_change()


def _equipment_panel(state: GameState, on_change) -> None:
    slot_titles = {
        "weapon": "Arme",
        "armor": "Armure",
        "accessory_1": "Accessoire 1",
        "accessory_2": "Accessoire 2",
    }
    equipped = state.equipped_items if isinstance(state.equipped_items, dict) else {}
    with ui.column().classes("w-full").style("gap: 6px;"):
        for slot in ("weapon", "armor", "accessory_1", "accessory_2"):
            item_id = str(equipped.get(slot) or "").strip().casefold()
            name = _item_name(state, item_id) if item_id else "(vide)"
            label = f"{slot_titles.get(slot, slot)} : {name}"
            selected = (state.selected_equipped_slot == slot)
            btn = ui.button(label, on_click=lambda slot=slot: _select_equipped_slot(state, slot, on_change)) \
                .props("outline no-caps")
            if selected:
                btn.style("border: 2px solid #999;")
            btn.classes("w-full").style("justify-content: flex-start; text-align: left; min-height:34px;")
            if item_id:
                with btn:
                    ui.tooltip(_item_tooltip_text(state, item_id, 1)).style(
                        "white-space: pre-line; max-width: 360px;"
                    )


def _select_equipped_slot(state: GameState, slot: str, on_change) -> None:
    state.selected_equipped_slot = slot if slot in {"weapon", "armor", "accessory_1", "accessory_2"} else ""
    on_change()


def _item_name(state: GameState, item_id: str) -> str:
    if not item_id:
        return ""
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    name = str(getattr(item, "name", "") or "").strip()
    return name or item_id


def _item_def(state: GameState, item_id: str):
    if not item_id:
        return None
    if not isinstance(state.item_defs, dict):
        return None
    return state.item_defs.get(item_id)


def _item_rarity(state: GameState, item_id: str) -> str:
    item = _item_def(state, item_id)
    return str(getattr(item, "rarity", "") or "common").strip().casefold() or "common"


def _item_description(state: GameState, item_id: str) -> str:
    item = _item_def(state, item_id)
    return str(getattr(item, "description", "") or "").strip()


def _item_value_gold(state: GameState, item_id: str) -> int:
    item = _item_def(state, item_id)
    try:
        value = int(getattr(item, "value_gold", 0))
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def _item_stat_bonuses(state: GameState, item_id: str) -> dict[str, int]:
    item = _item_def(state, item_id)
    bonuses_raw = getattr(item, "stat_bonuses", None)
    if not isinstance(bonuses_raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in bonuses_raw.items():
        if not isinstance(key, str):
            continue
        try:
            delta = int(value)
        except (TypeError, ValueError):
            continue
        if delta == 0:
            continue
        out[key] = delta
    return out


def _item_effects(state: GameState, item_id: str) -> list[dict]:
    item = _item_def(state, item_id)
    effects_raw = getattr(item, "effects", None)
    if not isinstance(effects_raw, list):
        return []
    return [effect for effect in effects_raw if isinstance(effect, dict)]


def _sheet_stats_refs(state: GameState) -> tuple[dict | None, dict | None]:
    if not isinstance(state.player_sheet, dict):
        return None, None
    stats = state.player_sheet.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        state.player_sheet["stats"] = stats
    effective = state.player_sheet.get("effective_stats")
    if isinstance(effective, dict):
        return stats, effective
    return stats, None


def _set_sheet_hp(state: GameState, hp: int) -> None:
    stats, effective = _sheet_stats_refs(state)
    if not isinstance(stats, dict):
        return
    stats["pv"] = hp
    if isinstance(effective, dict):
        effective["pv"] = hp


def _heal_player_from_consumable(state: GameState, value: object) -> str:
    amount = max(1, _safe_int(value, 1))
    max_hp = max(1, _safe_int(getattr(state.player, "max_hp", 1), 1))
    current = max(0, _safe_int(getattr(state.player, "hp", 0), 0))
    target = min(max_hp, current + amount)
    gained = max(0, target - current)
    state.player.hp = target
    _set_sheet_hp(state, target)
    if gained <= 0:
        return f"PV deja au maximum ({target}/{max_hp})."
    return f"Soin: +{gained} PV ({target}/{max_hp})."


def _restore_mana_from_consumable(state: GameState, value: object) -> str:
    amount = max(1, _safe_int(value, 1))
    stats, effective = _sheet_stats_refs(state)
    if not isinstance(stats, dict):
        return "Fiche joueur absente: mana non modifie."

    shown = effective if isinstance(effective, dict) else stats
    mana_max = max(0, _safe_int(shown.get("mana_max"), _safe_int(stats.get("mana_max"), 0)))
    if mana_max <= 0:
        return "Pas de reserve de mana sur cette fiche."

    current = max(0, _safe_int(stats.get("mana"), _safe_int(shown.get("mana"), 0)))
    target = min(mana_max, current + amount)
    gained = max(0, target - current)

    stats["mana"] = target
    if isinstance(effective, dict):
        effective["mana"] = target

    if gained <= 0:
        return f"Mana deja au maximum ({target}/{mana_max})."
    return f"Mana: +{gained} ({target}/{mana_max})."


def _apply_consumable_effect(state: GameState, item_id: str, item_name: str, effect: dict) -> tuple[str | None, bool]:
    kind = str(effect.get("kind") or "").strip().casefold()
    if kind == "heal":
        return _heal_player_from_consumable(state, effect.get("value")), True

    if kind == "mana":
        return _restore_mana_from_consumable(state, effect.get("value")), True

    if kind == "stat_buff":
        stat = str(effect.get("stat") or "").strip().casefold()
        value = _safe_int(effect.get("value"), 0)
        duration = max(1, _safe_int(effect.get("duration_turns"), 3))
        buff = add_consumable_stat_buff(
            state,
            stat=stat,
            value=value,
            duration_turns=duration,
            item_id=item_id,
            item_name=item_name,
        )
        if not isinstance(buff, dict):
            return "Bonus ignore: effet invalide.", False
        turns = max(1, _safe_int(buff.get("turns_remaining"), duration))
        label = _STAT_LABELS.get(stat, stat or "stat")
        sign = "+" if value > 0 else ""
        return f"Bonus temporaire: {label} {sign}{value} ({turns} tours).", True

    return None, False


def _format_stat_line(key: str, value: int) -> str:
    label = _STAT_LABELS.get(key, key)
    sign = "+" if value > 0 else ""
    return f"{label} {sign}{value}"


def _format_effect_line(effect: dict) -> str:
    kind = str(effect.get("kind") or "").strip().casefold()
    try:
        value = int(effect.get("value", 0))
    except (TypeError, ValueError):
        value = 0

    if kind == "heal":
        return f"Restaure {max(1, value)} PV"
    if kind == "mana":
        return f"Restaure {max(1, value)} mana"
    if kind == "stat_buff":
        stat = str(effect.get("stat") or "").strip().casefold()
        try:
            duration = int(effect.get("duration_turns", 3) or 3)
        except (TypeError, ValueError):
            duration = 3
        duration = max(1, duration)
        stat_label = _STAT_LABELS.get(stat, stat or "stat")
        sign = "+" if value > 0 else ""
        return f"Bonus {stat_label} {sign}{value} ({duration} tours)"
    if kind == "passive":
        label = str(effect.get("name") or effect.get("label") or "Effet passif").strip()
        if value:
            sign = "+" if value > 0 else ""
            return f"{label} ({sign}{value})"
        return label
    label = str(effect.get("label") or kind or "Effet").strip()
    if value:
        sign = "+" if value > 0 else ""
        return f"{label} ({sign}{value})"
    return label


def _item_detail_lines(state: GameState, item_id: str, qty: int | None = None) -> list[str]:
    clean_id = str(item_id or "").strip().casefold()
    if not clean_id:
        return ["Aucun objet selectionne."]

    name = _item_name(state, clean_id)
    item_type, slot = _item_type_and_slot(state, clean_id)
    rarity = _item_rarity(state, clean_id)
    rarity_label = _RARITY_LABELS.get(rarity, rarity.title() if rarity else "Commun")
    type_label = _TYPE_LABELS.get(item_type, item_type or "Objet")
    slot_label = f" ({slot})" if slot else ""
    value_gold = _item_value_gold(state, clean_id)
    stack_max = _item_stack_max(state, clean_id)
    description = _item_description(state, clean_id)
    bonuses = _item_stat_bonuses(state, clean_id)
    effects = _item_effects(state, clean_id)

    lines: list[str] = [f"{name} [{rarity_label}]"]
    if qty is not None:
        try:
            qty_value = int(qty)
        except (TypeError, ValueError):
            qty_value = 1
        lines.append(f"Quantite: x{max(1, qty_value)}")
    lines.append(f"Type: {type_label}{slot_label}")
    lines.append(f"Pile max: {stack_max}")
    if value_gold > 0:
        lines.append(f"Valeur: {value_gold} or")
    if description:
        lines.append(description)

    if bonuses:
        lines.append("Stats:")
        for key in sorted(bonuses.keys()):
            lines.append(f"- {_format_stat_line(key, bonuses[key])}")
    if effects:
        lines.append("Effets:")
        for effect in effects[:6]:
            lines.append(f"- {_format_effect_line(effect)}")
    if not bonuses and not effects:
        lines.append("Aucun bonus special.")

    return lines


def _item_tooltip_text(state: GameState, item_id: str, qty: int) -> str:
    return "\n".join(_item_detail_lines(state, item_id, qty))


def _selected_item_details(state: GameState) -> None:
    ui.label("Infos objet").classes("text-sm font-semibold")
    selected_slot = state.selected_slot if isinstance(state.selected_slot, tuple) else None
    if selected_slot and len(selected_slot) == 2:
        which, idx = selected_slot
        grid = state.carried if which == "carried" else state.storage
        stack = grid.get(idx)
        if stack:
            for line in _item_detail_lines(state, stack.item_id, stack.qty):
                cls = "text-xs"
                if line.endswith(":"):
                    cls = "text-xs font-semibold"
                ui.label(line).classes(cls).style("white-space: pre-wrap;")
            return

    equipped_slot = str(getattr(state, "selected_equipped_slot", "") or "").strip()
    if equipped_slot in {"weapon", "armor", "accessory_1", "accessory_2"}:
        equipped = state.equipped_items if isinstance(state.equipped_items, dict) else {}
        item_id = str(equipped.get(equipped_slot) or "").strip().casefold()
        if item_id:
            ui.label(f"Slot equipe: {equipped_slot}").classes("text-xs opacity-70")
            for line in _item_detail_lines(state, item_id, 1):
                cls = "text-xs"
                if line.endswith(":"):
                    cls = "text-xs font-semibold"
                ui.label(line).classes(cls).style("white-space: pre-wrap;")
            return

    ui.label("Clique sur un objet (inventaire ou equipement) pour afficher ses details.").classes("text-xs opacity-70")


def _item_stack_max(state: GameState, item_id: str) -> int:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    try:
        raw = int(getattr(item, "stack_max", 1))
    except (TypeError, ValueError):
        raw = 1
    return max(1, min(raw, 999))


def _item_type_and_slot(state: GameState, item_id: str) -> tuple[str, str]:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    t = str(getattr(item, "type", "") or "").strip().casefold()
    slot = str(getattr(item, "slot", "") or "").strip().casefold()
    return t, slot


def _target_equip_slot(state: GameState, item_id: str) -> str | None:
    item_type, slot = _item_type_and_slot(state, item_id)
    equipped = state.equipped_items if isinstance(state.equipped_items, dict) else {}
    if slot in {"weapon", "armor", "accessory_1", "accessory_2"}:
        return slot
    if slot in {"accessory", "ring", "amulet", "trinket"} or item_type in {"accessory", "trinket"}:
        if not str(equipped.get("accessory_1") or "").strip():
            return "accessory_1"
        return "accessory_2"
    if item_type == "weapon":
        return "weapon"
    if item_type in {"armor", "shield", "helm"}:
        return "armor"
    return None


def _add_item_to_inventory(state: GameState, item_id: str, qty: int) -> int:
    if qty <= 0:
        return 0
    remaining = qty
    added = 0
    stack_max = _item_stack_max(state, item_id)

    for grid in (state.carried, state.storage):
        for stack in grid.slots:
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
            added += take
        if remaining <= 0:
            break

    for grid in (state.carried, state.storage):
        while remaining > 0:
            try:
                idx = grid.slots.index(None)
            except ValueError:
                break
            take = min(stack_max, remaining)
            grid.slots[idx] = ItemStack(item_id=item_id, qty=take)
            remaining -= take
            added += take
        if remaining <= 0:
            break

    return added


def _use_selected(state: GameState, on_change) -> None:
    if not state.selected_slot:
        state.push("Système", "Selectionne d'abord un consommable a utiliser.", count_for_media=False)
        on_change()
        return

    which, idx = state.selected_slot
    src_grid = state.carried if which == "carried" else state.storage
    stack = src_grid.get(idx)
    if not stack:
        return

    item_id = str(stack.item_id or "").strip().casefold()
    if not item_id:
        return
    item_name = _item_name(state, item_id)
    item_type, _slot = _item_type_and_slot(state, item_id)
    if item_type != "consumable":
        state.push("Système", "Cet objet n'est pas un consommable.", count_for_media=False)
        on_change()
        return

    effects = _item_effects(state, item_id)
    if not effects:
        state.push("Système", "Ce consommable n'a pas d'effet exploitable.", count_for_media=False)
        on_change()
        return

    applied_lines: list[str] = []
    applied_any = False
    for effect in effects[:6]:
        line, applied = _apply_consumable_effect(state, item_id, item_name, effect)
        if isinstance(line, str) and line.strip():
            applied_lines.append(line.strip())
        if applied:
            applied_any = True

    if not applied_any:
        state.push("Système", "Aucun effet n'a pu etre applique.", count_for_media=False)
        on_change()
        return

    stack.qty -= 1
    if stack.qty <= 0:
        src_grid.set(idx, None)
        state.selected_slot = None

    state.push("Système", f"Utilise: {item_name}.", count_for_media=False)
    for line in applied_lines[:6]:
        state.push("Système", line, count_for_media=False)
    on_change()


def _equip_selected(state: GameState, on_change) -> None:
    if not state.selected_slot:
        state.push("Système", "Sélectionne d'abord un item à équiper.", count_for_media=False)
        on_change()
        return

    which, idx = state.selected_slot
    src_grid = state.carried if which == "carried" else state.storage
    stack = src_grid.get(idx)
    if not stack:
        return

    item_id = str(stack.item_id or "").strip().casefold()
    if not item_id:
        return

    target_slot = _target_equip_slot(state, item_id)
    if not target_slot:
        state.push("Système", "Cet objet ne peut pas être équipé.", count_for_media=False)
        on_change()
        return

    equipped = state.equipped_items if isinstance(state.equipped_items, dict) else {}
    old_item = str(equipped.get(target_slot) or "").strip().casefold()
    if old_item:
        returned = _add_item_to_inventory(state, old_item, 1)
        if returned < 1:
            state.push("Système", "Inventaire plein: impossible de remplacer cet équipement.", count_for_media=False)
            on_change()
            return

    stack.qty -= 1
    if stack.qty <= 0:
        src_grid.set(idx, None)
        state.selected_slot = None
    equipped[target_slot] = item_id
    state.equipped_items = equipped
    state.selected_equipped_slot = target_slot
    state.push("Système", f"Équipé: {_item_name(state, item_id)} ({target_slot})", count_for_media=False)
    on_change()


def _unequip_selected(state: GameState, on_change) -> None:
    slot = str(state.selected_equipped_slot or "").strip()
    if slot not in {"weapon", "armor", "accessory_1", "accessory_2"}:
        state.push("Système", "Sélectionne un slot d'équipement à retirer.", count_for_media=False)
        on_change()
        return

    equipped = state.equipped_items if isinstance(state.equipped_items, dict) else {}
    item_id = str(equipped.get(slot) or "").strip().casefold()
    if not item_id:
        state.push("Système", "Ce slot est déjà vide.", count_for_media=False)
        on_change()
        return

    returned = _add_item_to_inventory(state, item_id, 1)
    if returned < 1:
        state.push("Système", "Inventaire plein: impossible de retirer l'équipement.", count_for_media=False)
        on_change()
        return

    equipped[slot] = ""
    state.equipped_items = equipped
    state.push("Système", f"Retiré: {_item_name(state, item_id)}", count_for_media=False)
    on_change()
