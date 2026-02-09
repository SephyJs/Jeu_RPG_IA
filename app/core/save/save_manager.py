from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.gamemaster.location_manager import MAP_ANCHORS
from app.gamemaster.npc_manager import normalize_profile_role_in_place
from app.ui.state.game_state import ChatMessage, Choice, GameState, Scene
from app.ui.state.inventory import InventoryGrid, ItemStack


class SaveManager:
    def __init__(self, *, saves_dir: str = "saves", slot_count: int = 3) -> None:
        self.saves_dir = Path(saves_dir)
        self.slot_count = max(1, int(slot_count))
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.saves_dir / "meta.json"
        self._known_anchors = set(MAP_ANCHORS)

    def slot_path(self, slot: int) -> Path:
        return self.saves_dir / f"slot_{slot}.json"

    def get_last_slot(self, default: int = 1) -> int:
        default_slot = self._clamp_slot(default)
        if not self.meta_path.exists():
            return default_slot
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return self._clamp_slot(int(raw.get("last_slot", default_slot)))
        except Exception:
            return default_slot

    def set_last_slot(self, slot: int) -> None:
        chosen = self._clamp_slot(slot)
        payload = {
            "last_slot": chosen,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_slot(self, slot: int, state: GameState) -> None:
        chosen = self._clamp_slot(slot)
        payload = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "state": self._state_to_dict(state),
        }
        self.slot_path(chosen).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.set_last_slot(chosen)

    def load_slot(self, slot: int, state: GameState) -> bool:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen)
        if not path.exists():
            return False

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_state = payload.get("state", {}) if isinstance(payload, dict) else {}
            if not isinstance(raw_state, dict):
                return False
            self._apply_state_dict(state, raw_state)
            self.set_last_slot(chosen)
            return True
        except Exception:
            return False

    def slot_summary(self, slot: int) -> dict:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen)
        if not path.exists():
            return {
                "slot": chosen,
                "exists": False,
                "saved_at": "",
                "location": "(vide)",
                "messages": 0,
            }

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_state = payload.get("state", {}) if isinstance(payload, dict) else {}
            scene_id = str(raw_state.get("current_scene_id") or "inconnu")
            chat = raw_state.get("chat", []) if isinstance(raw_state, dict) else []
            return {
                "slot": chosen,
                "exists": True,
                "saved_at": str(payload.get("saved_at") or ""),
                "location": scene_id,
                "messages": len(chat) if isinstance(chat, list) else 0,
            }
        except Exception:
            return {
                "slot": chosen,
                "exists": False,
                "saved_at": "",
                "location": "(erreur)",
                "messages": 0,
            }

    def delete_slot(self, slot: int) -> None:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen)
        if path.exists():
            path.unlink()

    def _clamp_slot(self, slot: int) -> int:
        value = int(slot)
        if value < 1:
            return 1
        if value > self.slot_count:
            return self.slot_count
        return value

    def _state_to_dict(self, state: GameState) -> dict:
        return {
            "player": asdict(state.player),
            "scenes": self._scenes_to_dict(state),
            "current_scene_id": state.current_scene_id,
            "scene_narrator_texts": {sid: scene.narrator_text for sid, scene in state.scenes.items()},
            "chat": [asdict(msg) for msg in state.chat],
            "chat_draft": state.chat_draft,
            "selected_npc": state.selected_npc,
            "left_panel_tab": state.left_panel_tab,
            "world_time_minutes": int(getattr(state, "world_time_minutes", 0)),
            "player_sheet": state.player_sheet,
            "player_sheet_ready": bool(state.player_sheet_ready),
            "player_sheet_missing": list(state.player_sheet_missing),
            "player_sheet_generation_in_progress": False,
            "player_progress_log": state.player_progress_log,
            "player_skills": state.player_skills,
            "skill_points": int(state.skill_points),
            "skill_training_in_progress": False,
            "skill_training_log": state.skill_training_log,
            "skill_passive_practice": state.skill_passive_practice,
            "equipped_items": dict(state.equipped_items),
            "selected_equipped_slot": str(state.selected_equipped_slot or ""),
            "carried": self._inventory_to_dict(state.carried),
            "storage": self._inventory_to_dict(state.storage),
            "selected_slot": list(state.selected_slot) if state.selected_slot else None,
            "narrator_media_url": state.narrator_media_url,
            "narrator_media_expires_at": float(state.narrator_media_expires_at),
            "narrator_messages_since_last_media": int(state.narrator_messages_since_last_media),
            "discovered_scene_ids": sorted(state.discovered_scene_ids),
            "discovered_anchors": sorted(state.discovered_anchors),
            "anchor_last_scene": dict(state.anchor_last_scene),
            "npc_profiles": state.npc_profiles,
            "npc_registry": state.npc_registry,
            "npc_scene_bindings": state.npc_scene_bindings,
            "npc_generation_in_progress": [],
            "location_generation_in_progress": False,
            "dungeon_profiles": state.dungeon_profiles,
            "active_dungeon_run": state.active_dungeon_run,
            "dungeon_generation_in_progress": False,
            "quests": state.quests,
            "quest_seq": int(state.quest_seq),
            "npc_dialogue_counts": dict(state.npc_dialogue_counts),
            "npc_quests_given": dict(state.npc_quests_given),
            "quest_generation_in_progress": [],
            "quest_counters": dict(state.quest_counters),
            "gm_state": state.gm_state,
        }

    def _apply_state_dict(self, state: GameState, raw: dict) -> None:
        saved_scenes = raw.get("scenes")
        if isinstance(saved_scenes, dict):
            parsed_scenes = self._scenes_from_dict(saved_scenes)
            if parsed_scenes:
                state.scenes = parsed_scenes

        player = raw.get("player", {})
        if isinstance(player, dict):
            if isinstance(player.get("name"), str):
                state.player.name = player["name"]
            if isinstance(player.get("hp"), int):
                state.player.hp = player["hp"]
            if isinstance(player.get("max_hp"), int):
                state.player.max_hp = player["max_hp"]
            if isinstance(player.get("gold"), int):
                state.player.gold = player["gold"]

        scene_id = raw.get("current_scene_id")
        if isinstance(scene_id, str) and scene_id in state.scenes:
            state.current_scene_id = scene_id

        scene_texts = raw.get("scene_narrator_texts", {})
        if isinstance(scene_texts, dict):
            for sid, text in scene_texts.items():
                if sid in state.scenes and isinstance(text, str) and text.strip():
                    state.scenes[sid].narrator_text = text

        chat_raw = raw.get("chat", [])
        state.chat = []
        if isinstance(chat_raw, list):
            for item in chat_raw:
                if not isinstance(item, dict):
                    continue
                speaker = item.get("speaker")
                text = item.get("text")
                if isinstance(speaker, str) and isinstance(text, str):
                    state.chat.append(ChatMessage(speaker=speaker, text=text))

        state.chat_draft = str(raw.get("chat_draft") or "")

        selected_npc = raw.get("selected_npc")
        state.selected_npc = selected_npc if isinstance(selected_npc, str) else None

        left_tab = raw.get("left_panel_tab")
        if isinstance(left_tab, str) and left_tab:
            state.left_panel_tab = left_tab

        world_time_minutes = raw.get("world_time_minutes")
        if isinstance(world_time_minutes, int):
            state.world_time_minutes = max(0, world_time_minutes)
        else:
            state.world_time_minutes = max(0, int(getattr(state, "world_time_minutes", 0)))

        player_sheet = raw.get("player_sheet")
        state.player_sheet = player_sheet if isinstance(player_sheet, dict) else {}

        player_sheet_ready = raw.get("player_sheet_ready")
        state.player_sheet_ready = bool(player_sheet_ready) if isinstance(player_sheet_ready, bool) else False

        player_sheet_missing = raw.get("player_sheet_missing")
        if isinstance(player_sheet_missing, list):
            state.player_sheet_missing = [x for x in player_sheet_missing if isinstance(x, str)]
        else:
            state.player_sheet_missing = []
        state.player_sheet_generation_in_progress = False

        player_progress_log = raw.get("player_progress_log")
        if isinstance(player_progress_log, list):
            state.player_progress_log = [x for x in player_progress_log if isinstance(x, dict)][:300]
        else:
            state.player_progress_log = []

        player_skills = raw.get("player_skills")
        if isinstance(player_skills, list):
            state.player_skills = [x for x in player_skills if isinstance(x, dict)][:120]
        else:
            state.player_skills = []

        skill_points = raw.get("skill_points")
        state.skill_points = max(0, int(skill_points)) if isinstance(skill_points, int) else 1
        state.skill_training_in_progress = False

        skill_training_log = raw.get("skill_training_log")
        if isinstance(skill_training_log, list):
            state.skill_training_log = [x for x in skill_training_log if isinstance(x, dict)][:400]
        else:
            state.skill_training_log = []

        skill_passive_practice = raw.get("skill_passive_practice")
        if isinstance(skill_passive_practice, dict):
            out: dict[str, dict] = {}
            for key, value in skill_passive_practice.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                out[key[:40]] = dict(value)
            state.skill_passive_practice = out
        else:
            state.skill_passive_practice = {}

        equipped_items = raw.get("equipped_items")
        if isinstance(equipped_items, dict):
            state.equipped_items = {
                "weapon": str(equipped_items.get("weapon") or "").strip().casefold(),
                "armor": str(equipped_items.get("armor") or "").strip().casefold(),
                "accessory_1": str(equipped_items.get("accessory_1") or "").strip().casefold(),
                "accessory_2": str(equipped_items.get("accessory_2") or "").strip().casefold(),
            }
        else:
            state.equipped_items = {
                "weapon": "",
                "armor": "",
                "accessory_1": "",
                "accessory_2": "",
            }

        selected_equipped_slot = raw.get("selected_equipped_slot")
        if isinstance(selected_equipped_slot, str) and selected_equipped_slot in {"weapon", "armor", "accessory_1", "accessory_2"}:
            state.selected_equipped_slot = selected_equipped_slot
        else:
            state.selected_equipped_slot = ""

        state.carried = self._inventory_from_dict(raw.get("carried"), default_cols=6, default_rows=4)
        state.storage = self._inventory_from_dict(raw.get("storage"), default_cols=10, default_rows=6)

        selected_slot = raw.get("selected_slot")
        if isinstance(selected_slot, list) and len(selected_slot) == 2:
            which = selected_slot[0]
            idx = selected_slot[1]
            if isinstance(which, str) and isinstance(idx, int):
                state.selected_slot = (which, idx)
            else:
                state.selected_slot = None
        else:
            state.selected_slot = None

        narrator_media_url = raw.get("narrator_media_url")
        if isinstance(narrator_media_url, str) and narrator_media_url:
            state.narrator_media_url = narrator_media_url

        expires_at = raw.get("narrator_media_expires_at")
        if isinstance(expires_at, (int, float)):
            state.narrator_media_expires_at = float(expires_at)

        media_count = raw.get("narrator_messages_since_last_media")
        if isinstance(media_count, int):
            state.narrator_messages_since_last_media = media_count

        discovered_scene_ids = raw.get("discovered_scene_ids")
        if isinstance(discovered_scene_ids, list):
            state.discovered_scene_ids = {x for x in discovered_scene_ids if isinstance(x, str) and x in state.scenes}
        else:
            state.discovered_scene_ids = {state.current_scene_id} if state.current_scene_id in state.scenes else set()

        discovered_anchors = raw.get("discovered_anchors")
        if isinstance(discovered_anchors, list):
            state.discovered_anchors = {
                x for x in discovered_anchors if isinstance(x, str) and x in self._known_anchors
            }
        else:
            scene = state.scenes.get(state.current_scene_id)
            state.discovered_anchors = {scene.map_anchor} if scene and scene.map_anchor else set()

        anchor_last_scene = raw.get("anchor_last_scene")
        if isinstance(anchor_last_scene, dict):
            state.anchor_last_scene = {
                str(anchor): str(scene_id)
                for anchor, scene_id in anchor_last_scene.items()
                if (
                    isinstance(anchor, str)
                    and anchor in self._known_anchors
                    and isinstance(scene_id, str)
                    and scene_id in state.scenes
                )
            }
        else:
            state.anchor_last_scene = {}
            current = state.scenes.get(state.current_scene_id)
            if current and current.map_anchor:
                state.anchor_last_scene[current.map_anchor] = current.id

        profiles = raw.get("npc_profiles", {})
        state.npc_profiles = profiles if isinstance(profiles, dict) else {}
        for key, profile in state.npc_profiles.items():
            if not isinstance(profile, dict):
                continue
            fallback_label = str(profile.get("label") or "").strip()
            if not fallback_label:
                fallback_label = str(key).split("__")[-1].replace("_", " ").strip() or "PNJ"
            normalize_profile_role_in_place(profile, fallback_label)

        registry_raw = raw.get("npc_registry")
        if isinstance(registry_raw, dict):
            registry_out: dict[str, dict] = {}
            for key, value in registry_raw.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                entry = dict(value)
                display_name = str(entry.get("display_name") or entry.get("label") or "").strip()
                if not display_name:
                    continue
                entry["npc_key"] = str(entry.get("npc_key") or key).strip() or key
                entry["display_name"] = display_name[:80]
                entry["label"] = str(entry.get("label") or display_name).strip()[:80]
                entry["role"] = str(entry.get("role") or entry.get("label") or "PNJ").strip()[:80]
                entry["home_location_id"] = str(entry.get("home_location_id") or "").strip()[:120]
                entry["home_location_title"] = str(entry.get("home_location_title") or "").strip()[:120]
                entry["home_anchor"] = str(entry.get("home_anchor") or "").strip()[:120]
                entry["last_seen_scene_id"] = str(entry.get("last_seen_scene_id") or "").strip()[:120]
                entry["last_seen_scene_title"] = str(entry.get("last_seen_scene_title") or "").strip()[:120]
                aliases_raw = entry.get("aliases")
                if isinstance(aliases_raw, list):
                    aliases = [str(x).strip()[:80] for x in aliases_raw if str(x).strip()]
                else:
                    aliases = []
                if entry["display_name"] not in aliases:
                    aliases.append(entry["display_name"])
                if entry["label"] not in aliases:
                    aliases.append(entry["label"])
                entry["aliases"] = aliases[:16]
                entry["can_roam"] = bool(entry.get("can_roam", True))
                registry_out[entry["npc_key"]] = entry
            state.npc_registry = registry_out
        else:
            state.npc_registry = {}

        bindings_raw = raw.get("npc_scene_bindings")
        if isinstance(bindings_raw, dict):
            state.npc_scene_bindings = {
                str(scene_key)[:220]: str(npc_key).strip()
                for scene_key, npc_key in bindings_raw.items()
                if isinstance(scene_key, str) and isinstance(npc_key, str) and str(npc_key).strip()
            }
        else:
            state.npc_scene_bindings = {}

        dungeon_profiles = raw.get("dungeon_profiles", {})
        state.dungeon_profiles = dungeon_profiles if isinstance(dungeon_profiles, dict) else {}

        active_dungeon_run = raw.get("active_dungeon_run")
        state.active_dungeon_run = active_dungeon_run if isinstance(active_dungeon_run, dict) else None
        state.dungeon_generation_in_progress = False

        quests_raw = raw.get("quests")
        if isinstance(quests_raw, list):
            state.quests = [q for q in quests_raw if isinstance(q, dict)]
        else:
            state.quests = []

        quest_seq = raw.get("quest_seq")
        state.quest_seq = int(quest_seq) if isinstance(quest_seq, int) and quest_seq >= 0 else len(state.quests)

        npc_dialogue_counts = raw.get("npc_dialogue_counts")
        if isinstance(npc_dialogue_counts, dict):
            state.npc_dialogue_counts = {
                str(k): int(v)
                for k, v in npc_dialogue_counts.items()
                if isinstance(k, str) and isinstance(v, int) and v >= 0
            }
        else:
            state.npc_dialogue_counts = {}

        npc_quests_given = raw.get("npc_quests_given")
        if isinstance(npc_quests_given, dict):
            state.npc_quests_given = {
                str(k): int(v)
                for k, v in npc_quests_given.items()
                if isinstance(k, str) and isinstance(v, int) and v >= 0
            }
        else:
            state.npc_quests_given = {}

        quest_counters = raw.get("quest_counters")
        if isinstance(quest_counters, dict):
            player_messages_sent = quest_counters.get("player_messages_sent")
            dungeon_floors_cleared = quest_counters.get("dungeon_floors_cleared")
            state.quest_counters = {
                "player_messages_sent": int(player_messages_sent) if isinstance(player_messages_sent, int) and player_messages_sent >= 0 else 0,
                "dungeon_floors_cleared": int(dungeon_floors_cleared) if isinstance(dungeon_floors_cleared, int) and dungeon_floors_cleared >= 0 else 0,
            }
        else:
            state.quest_counters = {
                "player_messages_sent": 0,
                "dungeon_floors_cleared": 0,
            }

        gm_state = raw.get("gm_state", {})
        state.gm_state = gm_state if isinstance(gm_state, dict) else {"player_name": "l'Éveillé", "location": "inconnu", "flags": {}}

        state.gm_state.setdefault("flags", {})
        state.gm_state["npc_profiles"] = state.npc_profiles

        state.npc_generation_in_progress.clear()
        state.quest_generation_in_progress.clear()
        state.location_generation_in_progress = False

    def _inventory_to_dict(self, inv: InventoryGrid) -> dict:
        return {
            "cols": inv.cols,
            "rows": inv.rows,
            "slots": [
                None if stack is None else {"item_id": stack.item_id, "qty": stack.qty}
                for stack in inv.slots
            ],
        }

    def _inventory_from_dict(self, raw: object, *, default_cols: int, default_rows: int) -> InventoryGrid:
        if not isinstance(raw, dict):
            return InventoryGrid.empty(default_cols, default_rows)

        cols = raw.get("cols")
        rows = raw.get("rows")
        slots = raw.get("slots")

        if not isinstance(cols, int) or cols <= 0:
            cols = default_cols
        if not isinstance(rows, int) or rows <= 0:
            rows = default_rows

        inv = InventoryGrid.empty(cols, rows)
        expected = cols * rows
        if not isinstance(slots, list):
            return inv

        for idx in range(min(expected, len(slots))):
            cell = slots[idx]
            if cell is None:
                continue
            if not isinstance(cell, dict):
                continue
            item_id = cell.get("item_id")
            qty = cell.get("qty")
            if isinstance(item_id, str) and isinstance(qty, int) and qty > 0:
                inv.slots[idx] = ItemStack(item_id=item_id, qty=qty)

        return inv

    def _scenes_to_dict(self, state: GameState) -> dict:
        out: dict[str, dict] = {}
        for sid, scene in state.scenes.items():
            out[sid] = {
                "id": scene.id,
                "title": scene.title,
                "narrator_text": scene.narrator_text,
                "map_anchor": scene.map_anchor,
                "generated": bool(scene.generated),
                "npc_names": list(scene.npc_names),
                "choices": [
                    {"id": c.id, "label": c.label, "next_scene_id": c.next_scene_id}
                    for c in scene.choices
                ],
            }
        return out

    def _scenes_from_dict(self, raw: dict) -> dict[str, Scene]:
        scenes: dict[str, Scene] = {}
        for sid, item in raw.items():
            if not isinstance(item, dict):
                continue
            scene_id = item.get("id")
            title = item.get("title")
            narrator_text = item.get("narrator_text")
            if not isinstance(scene_id, str) or not scene_id:
                continue
            if not isinstance(title, str) or not title:
                continue
            if not isinstance(narrator_text, str) or not narrator_text:
                continue

            npcs_raw = item.get("npc_names", [])
            npc_names = [n for n in npcs_raw if isinstance(n, str)] if isinstance(npcs_raw, list) else []

            choices: list[Choice] = []
            choices_raw = item.get("choices", [])
            if isinstance(choices_raw, list):
                for c in choices_raw:
                    if not isinstance(c, dict):
                        continue
                    cid = c.get("id")
                    label = c.get("label")
                    next_scene_id = c.get("next_scene_id")
                    if isinstance(cid, str) and cid and isinstance(label, str) and label:
                        if next_scene_id is None or isinstance(next_scene_id, str):
                            choices.append(Choice(id=cid, label=label, next_scene_id=next_scene_id))

            scene = Scene(
                id=scene_id,
                title=title,
                narrator_text=narrator_text,
                map_anchor=(
                    str(item.get("map_anchor") or "")
                    if str(item.get("map_anchor") or "") in self._known_anchors else ""
                ),
                generated=bool(item.get("generated", False)),
                npc_names=npc_names,
                choices=choices,
            )
            scenes[scene_id] = scene

        return scenes
