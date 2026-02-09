from nicegui import ui
from app.ui.state.game_state import GameState
from app.ui.state.inventory import InventoryGrid, ItemStack


SLOT_SIZE_PX = 46
SLOT_GAP_PX = 6


def inventory_panel(state: GameState, on_change) -> None:
    # Conteneur global : empêche les débordements dans la colonne gauche
    with ui.element("div").classes("w-full").style("max-width: 100%; min-width: 0; overflow: hidden;"):
        ui.label("Inventaire porté").classes("text-lg font-semibold")
        _grid(state, state.carried, "carried", on_change)

        ui.separator()

        ui.label("Stockage").classes("text-lg font-semibold")
        _grid(state, state.storage, "storage", on_change)

        ui.separator()
        ui.label("Équipement").classes("text-lg font-semibold")
        _equipment_panel(state, on_change)

        ui.separator()
        _actions(state, on_change)


def _grid(state: GameState, grid: InventoryGrid, which: str, on_change) -> None:
    cols = grid.cols

    # Wrapper scroll horizontal si un jour tu mets plus de colonnes que la largeur dispo
    with ui.element("div").classes("w-full").style("max-width:100%; overflow-x:auto; overflow-y:hidden;"):
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

                ui.button(
                    label,
                    on_click=lambda idx=idx, which=which: _select(state, which, idx, on_change),
                ).props("flat").style(btn_style)


def _select(state: GameState, which: str, idx: int, on_change) -> None:
    state.selected_slot = (which, idx)
    on_change()


def _actions(state: GameState, on_change) -> None:
    ui.label("Actions").classes("font-semibold")

    # Container full width, min-width:0 = évite les débordements en flex
    with ui.element("div").classes("w-full").style("max-width:100%; min-width:0;"):
        with ui.column().classes("w-full").style("gap: 8px;"):
            _full_button("Vers stockage", lambda: _move(state, "carried", "storage", on_change))
            _full_button("Vers porté", lambda: _move(state, "storage", "carried", on_change))
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
            "padding: 6px 8px;"
            "font-size: 12px;"
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
                .props("outline dense no-caps")
            if selected:
                btn.style("border: 2px solid #999;")
            btn.classes("w-full").style("justify-content: flex-start; text-align: left;")


def _select_equipped_slot(state: GameState, slot: str, on_change) -> None:
    state.selected_equipped_slot = slot if slot in {"weapon", "armor", "accessory_1", "accessory_2"} else ""
    on_change()


def _item_name(state: GameState, item_id: str) -> str:
    if not item_id:
        return ""
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    name = str(getattr(item, "name", "") or "").strip()
    return name or item_id


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
