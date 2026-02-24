from __future__ import annotations

import json

from nicegui import ui

from app.core.memory import MemoryAdmin


_admin = MemoryAdmin.from_default()


@ui.page("/memory-admin")
def memory_admin_page() -> None:
    ui.label("Memory Admin").classes("text-xl font-semibold")
    ui.label("Inspection et maintenance de la memoire canonique.").classes("text-sm opacity-70")

    profile_input = ui.input("Profile key (optionnel)").props("outlined dense")
    npc_select = ui.select(options=[]).props("outlined dense").classes("w-full")
    output = ui.textarea("Output").props("filled autogrow").classes("w-full")

    def _profile() -> str | None:
        key = str(profile_input.value or "").strip()
        return key or None

    def _refresh_npcs() -> None:
        profile_key = _profile()
        ids = _admin.list_npcs(profile_key=profile_key)
        options = {row: row for row in ids}
        npc_select.options = options
        if ids:
            npc_select.value = ids[0]
        output.value = json.dumps({"profile": profile_key or "", "npc_count": len(ids), "ids": ids[:60]}, ensure_ascii=False, indent=2)

    def _show_npc() -> None:
        npc_id = str(npc_select.value or "").strip()
        if not npc_id:
            output.value = json.dumps({"error": "npc_id manquant"}, ensure_ascii=False, indent=2)
            return
        payload = _admin.read_npc(profile_key=_profile(), npc_id=npc_id)
        output.value = json.dumps(payload, ensure_ascii=False, indent=2)

    def _show_world() -> None:
        payload = _admin.read_world()
        output.value = json.dumps(payload, ensure_ascii=False, indent=2)

    def _compact_now() -> None:
        npc_id = str(npc_select.value or "").strip()
        if not npc_id:
            output.value = json.dumps({"error": "npc_id manquant"}, ensure_ascii=False, indent=2)
            return
        report = _admin.compact_npc_now(profile_key=_profile(), npc_id=npc_id)
        output.value = json.dumps(report, ensure_ascii=False, indent=2)

    def _rebuild_npc_index() -> None:
        npc_id = str(npc_select.value or "").strip()
        if not npc_id:
            output.value = json.dumps({"error": "npc_id manquant"}, ensure_ascii=False, indent=2)
            return
        count = _admin.rebuild_npc_index(profile_key=_profile(), npc_id=npc_id)
        output.value = json.dumps({"npc_id": npc_id, "indexed_records": int(count)}, ensure_ascii=False, indent=2)

    def _purge_short() -> None:
        npc_id = str(npc_select.value or "").strip()
        if not npc_id:
            output.value = json.dumps({"error": "npc_id manquant"}, ensure_ascii=False, indent=2)
            return
        done = _admin.purge_short(profile_key=_profile(), npc_id=npc_id)
        output.value = json.dumps({"npc_id": npc_id, "purged": bool(done)}, ensure_ascii=False, indent=2)

    def _rebuild_world_index() -> None:
        count = _admin.rebuild_world_index()
        output.value = json.dumps({"world_indexed_records": int(count)}, ensure_ascii=False, indent=2)

    with ui.row().classes("gap-2"):
        ui.button("Refresh NPC list", on_click=_refresh_npcs).props("outline")
        ui.button("Show NPC memory", on_click=_show_npc).props("outline")
        ui.button("Show world memory", on_click=_show_world).props("outline")
    with ui.row().classes("gap-2"):
        ui.button("Compacter maintenant", on_click=_compact_now)
        ui.button("Rebuild index PNJ", on_click=_rebuild_npc_index)
        ui.button("Rebuild index monde", on_click=_rebuild_world_index)
        ui.button("Purger short", on_click=_purge_short).props("outline color=red")

    _refresh_npcs()

