from __future__ import annotations

from contextlib import contextmanager
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Iterator

from app.gamemaster.conversation_memory import (
    sanitize_global_memory_payload,
    sanitize_long_term_payload,
    sanitize_short_term_payload,
)
from app.core.engine import (
    normalize_trade_session,
    normalize_travel_state,
    trade_session_from_legacy_pending_trade,
    trade_session_to_dict,
    travel_state_to_dict,
)
from app.gamemaster.location_manager import MAP_ANCHORS
from app.gamemaster.npc_manager import normalize_profile_extensions_in_place, normalize_profile_role_in_place
from app.core.models import ChatMessage, Choice, Scene
from app.ui.state.game_state import CHAT_HISTORY_MAX_ITEMS, GameState
from app.ui.state.inventory import InventoryGrid, ItemStack


_SAVE_SCHEMA_VERSION = 2
_BACKUP_SUFFIX = ".bak"
_LOCKFILE_NAME = ".save.lock"


class SaveManager:
    def __init__(self, *, saves_dir: str = "saves", slot_count: int = 3) -> None:
        self.saves_dir = Path(saves_dir)
        self.slot_count = max(1, int(slot_count))
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir = self.saves_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.saves_dir / "meta.json"
        self._known_anchors = set(MAP_ANCHORS)
        self.last_warning: str = ""

    def normalize_profile_id(self, profile: str) -> str:
        text = str(profile or "").strip()
        if not text:
            return "joueur"
        normalized = unicodedata.normalize("NFKD", text)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        key = re.sub(r"[^a-zA-Z0-9_-]+", "_", ascii_text.casefold()).strip("_")
        return (key[:64] or "joueur")

    def _profile_dir(self, profile: str | None) -> Path:
        if profile is None:
            return self.saves_dir
        key = self.normalize_profile_id(profile)
        target = self.profiles_dir / key
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _meta_path_for(self, profile: str | None = None) -> Path:
        if profile is None:
            return self.meta_path
        return self._profile_dir(profile) / "meta.json"

    def slot_path(self, slot: int, profile: str | None = None) -> Path:
        return self._profile_dir(profile) / f"slot_{slot}.json"

    def _backup_path(self, path: Path) -> Path:
        return path.with_name(path.name + _BACKUP_SUFFIX)

    def _lock_path_for(self, profile: str | None = None) -> Path:
        base_dir = self._profile_dir(profile) if profile is not None else self.saves_dir
        return base_dir / _LOCKFILE_NAME

    @contextmanager
    def _file_lock(self, profile: str | None = None) -> Iterator[None]:
        lock_path = self._lock_path_for(profile)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_fp:
            try:
                lock_fp.seek(0)
                lock_fp.write("0")
                lock_fp.flush()
                lock_fp.seek(0)
            except Exception:
                pass
            try:
                if os.name == "nt":
                    import msvcrt  # type: ignore

                    msvcrt.locking(lock_fp.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl  # type: ignore

                    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            except Exception:
                # Best effort: continue even if lock backend is unavailable.
                pass
            try:
                yield
            finally:
                try:
                    if os.name == "nt":
                        import msvcrt  # type: ignore

                        msvcrt.locking(lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl  # type: ignore

                        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)

    def _read_json_file(self, path: Path) -> dict | list | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_json_file(self, path: Path, payload: dict, *, backup: bool) -> None:
        if backup and path.exists():
            backup_path = self._backup_path(path)
            try:
                self._atomic_write_text(backup_path, path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self._atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _load_payload_with_backup(self, path: Path) -> tuple[dict | None, bool]:
        payload = self._read_json_file(path)
        if isinstance(payload, dict):
            return payload, False

        backup_path = self._backup_path(path)
        backup_payload = self._read_json_file(backup_path)
        if not isinstance(backup_payload, dict):
            return None, False

        restored = False
        try:
            self._atomic_write_text(path, backup_path.read_text(encoding="utf-8"))
            restored = True
        except Exception:
            restored = False
        return backup_payload, restored

    def profile_has_data(self, profile: str) -> bool:
        key = self.normalize_profile_id(profile)
        profile_dir = self.profiles_dir / key
        if not profile_dir.exists():
            return False
        for i in range(1, self.slot_count + 1):
            if (profile_dir / f"slot_{i}.json").exists():
                return True
        return False

    def list_profiles(self) -> list[dict]:
        profiles: list[dict] = []
        if not self.profiles_dir.exists():
            return profiles
        for child in self.profiles_dir.iterdir():
            if not child.is_dir():
                continue
            key = child.name
            meta_path = child / "meta.json"
            display_name = key
            updated_at = ""
            if meta_path.exists():
                try:
                    raw = self._read_json_file(meta_path)
                    if isinstance(raw, dict):
                        display_name = str(raw.get("display_name") or raw.get("profile_name") or key).strip() or key
                        updated_at = str(raw.get("updated_at") or "")
                except Exception:
                    pass
            has_slot = any((child / f"slot_{i}.json").exists() for i in range(1, self.slot_count + 1))
            if not has_slot:
                continue
            profiles.append(
                {
                    "profile_key": key,
                    "display_name": display_name[:80],
                    "updated_at": updated_at,
                }
            )
        profiles.sort(key=lambda p: str(p.get("updated_at") or ""), reverse=True)
        return profiles

    def has_legacy_saves(self) -> bool:
        for i in range(1, self.slot_count + 1):
            if (self.saves_dir / f"slot_{i}.json").exists():
                return True
        return False

    def migrate_legacy_saves_to_profile(self, profile: str, *, display_name: str | None = None) -> int:
        profile_key = self.normalize_profile_id(profile)
        target_dir = self._profile_dir(profile_key)
        migrated = 0
        for i in range(1, self.slot_count + 1):
            src = self.saves_dir / f"slot_{i}.json"
            dst = target_dir / f"slot_{i}.json"
            if not src.exists() or dst.exists():
                continue
            try:
                self._atomic_write_text(dst, src.read_text(encoding="utf-8"))
                migrated += 1
            except Exception:
                continue
        if migrated > 0:
            legacy_last = self.get_last_slot(default=1, profile=None)
            self.set_last_slot(legacy_last, profile=profile_key, display_name=display_name)
        return migrated

    def get_last_slot(self, default: int = 1, *, profile: str | None = None) -> int:
        default_slot = self._clamp_slot(default)
        meta_path = self._meta_path_for(profile)
        if not meta_path.exists():
            return default_slot
        try:
            raw = self._read_json_file(meta_path)
            if not isinstance(raw, dict):
                return default_slot
            return self._clamp_slot(int(raw.get("last_slot", default_slot)))
        except Exception:
            return default_slot

    def set_last_slot(self, slot: int, *, profile: str | None = None, display_name: str | None = None) -> None:
        with self._file_lock(profile):
            self._set_last_slot_unlocked(slot, profile=profile, display_name=display_name)

    def _set_last_slot_unlocked(self, slot: int, *, profile: str | None = None, display_name: str | None = None) -> None:
        chosen = self._clamp_slot(slot)
        meta_path = self._meta_path_for(profile)
        profile_name = str(display_name or profile or "").strip()
        payload = {
            "last_slot": chosen,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if profile is not None:
            payload["profile_key"] = self.normalize_profile_id(profile)
            if profile_name:
                payload["display_name"] = profile_name[:80]
        self._write_json_file(meta_path, payload, backup=True)

    def save_slot(
        self,
        slot: int,
        state: GameState,
        *,
        profile: str | None = None,
        display_name: str | None = None,
    ) -> None:
        chosen = self._clamp_slot(slot)
        payload = {
            "version": _SAVE_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "state": self._state_to_dict(state),
        }
        path = self.slot_path(chosen, profile=profile)
        with self._file_lock(profile):
            self._write_json_file(path, payload, backup=True)
            self._set_last_slot_unlocked(chosen, profile=profile, display_name=display_name)

    def load_slot(self, slot: int, state: GameState, *, profile: str | None = None) -> bool:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen, profile=profile)
        if not path.exists():
            return False

        self.last_warning = ""
        with self._file_lock(profile):
            payload, restored = self._load_payload_with_backup(path)
            if not isinstance(payload, dict):
                return False
            raw_state = payload.get("state", {})
            if not isinstance(raw_state, dict):
                return False
            try:
                self._apply_state_dict(state, raw_state)
            except Exception:
                return False
            self._set_last_slot_unlocked(chosen, profile=profile)
            if restored:
                self.last_warning = "Sauvegarde restaurée depuis backup après corruption du fichier principal."
            return True

    def slot_summary(self, slot: int, *, profile: str | None = None) -> dict:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen, profile=profile)
        if not path.exists():
            return {
                "slot": chosen,
                "exists": False,
                "saved_at": "",
                "location": "(vide)",
                "messages": 0,
            }

        try:
            payload, _ = self._load_payload_with_backup(path)
            if not isinstance(payload, dict):
                raise ValueError("payload invalide")
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

    def delete_slot(self, slot: int, *, profile: str | None = None) -> None:
        chosen = self._clamp_slot(slot)
        path = self.slot_path(chosen, profile=profile)
        backup_path = self._backup_path(path)
        with self._file_lock(profile):
            if path.exists():
                path.unlink()
            if backup_path.exists():
                backup_path.unlink()

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
            "chat": [asdict(msg) for msg in state.chat[-CHAT_HISTORY_MAX_ITEMS:]],
            "chat_draft": state.chat_draft,
            "selected_npc": state.selected_npc,
            "pending_choice_options": state.pending_choice_options,
            "pending_choice_prompt": state.pending_choice_prompt,
            "pending_choice_source_npc_key": state.pending_choice_source_npc_key,
            "pending_choice_created_at": state.pending_choice_created_at,
            "left_panel_tab": state.left_panel_tab,
            "world_time_minutes": int(getattr(state, "world_time_minutes", 0)),
            "world_state": dict(state.world_state) if isinstance(getattr(state, "world_state", None), dict) else {},
            "player_sheet": state.player_sheet,
            "player_sheet_ready": bool(state.player_sheet_ready),
            "player_sheet_missing": list(state.player_sheet_missing),
            "player_sheet_generation_in_progress": False,
            "player_progress_log": state.player_progress_log,
            "player_skills": state.player_skills,
            "skill_points": int(state.skill_points),
            "player_corruption_level": int(getattr(state, "player_corruption_level", 0)),
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
            "conversation_short_term": state.conversation_short_term,
            "conversation_long_term": state.conversation_long_term,
            "conversation_global_long_term": state.conversation_global_long_term,
            "faction_reputation": state.faction_reputation,
            "faction_reputation_log": state.faction_reputation_log,
            "faction_states": dict(state.faction_states) if isinstance(getattr(state, "faction_states", None), dict) else {},
            "trade_session": trade_session_to_dict(normalize_trade_session(getattr(state, "trade_session", None))),
            "travel_state": travel_state_to_dict(normalize_travel_state(getattr(state, "travel_state", None))),
            "gm_flags": dict(state.gm_state.get("flags", {})) if isinstance(state.gm_state, dict) else {},
            "gm_last_trade": (
                state.gm_state.get("last_trade")
                if isinstance(state.gm_state, dict) and isinstance(state.gm_state.get("last_trade"), dict)
                else None
            ),
            "gm_pending_trade": (
                state.gm_state.get("pending_trade")
                if isinstance(state.gm_state, dict) and isinstance(state.gm_state.get("pending_trade"), dict)
                else None
            ),
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
            for item in chat_raw[-CHAT_HISTORY_MAX_ITEMS:]:
                if not isinstance(item, dict):
                    continue
                speaker = item.get("speaker")
                text = item.get("text")
                if isinstance(speaker, str) and isinstance(text, str):
                    state.chat.append(ChatMessage(speaker=speaker, text=text))

        state.chat_draft = str(raw.get("chat_draft") or "")

        selected_npc = raw.get("selected_npc")
        state.selected_npc = selected_npc if isinstance(selected_npc, str) else None

        pending_choice_options = raw.get("pending_choice_options")
        if isinstance(pending_choice_options, list):
            state.pending_choice_options = [row for row in pending_choice_options if isinstance(row, dict)][:3]
        else:
            state.pending_choice_options = []
        state.pending_choice_prompt = str(raw.get("pending_choice_prompt") or "")[:220]
        state.pending_choice_source_npc_key = str(raw.get("pending_choice_source_npc_key") or "")[:180]
        state.pending_choice_created_at = str(raw.get("pending_choice_created_at") or "")[:40]

        left_tab = raw.get("left_panel_tab")
        if isinstance(left_tab, str) and left_tab:
            state.left_panel_tab = left_tab

        world_time_minutes = raw.get("world_time_minutes")
        if isinstance(world_time_minutes, int):
            state.world_time_minutes = max(0, world_time_minutes)
        else:
            state.world_time_minutes = max(0, int(getattr(state, "world_time_minutes", 0)))
        world_state = raw.get("world_state")
        if isinstance(world_state, dict):
            state.world_state = dict(world_state)
        else:
            state.world_state = {}
        state.sync_world_state()

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
        player_corruption_level = raw.get("player_corruption_level")
        if isinstance(player_corruption_level, int):
            state.player_corruption_level = max(0, min(100, int(player_corruption_level)))
        else:
            state.player_corruption_level = 0
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
            normalize_profile_extensions_in_place(profile, fallback_label=fallback_label)

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

        state.conversation_short_term = sanitize_short_term_payload(raw.get("conversation_short_term"))
        state.conversation_long_term = sanitize_long_term_payload(raw.get("conversation_long_term"))
        state.conversation_global_long_term = sanitize_global_memory_payload(
            raw.get("conversation_global_long_term")
        )

        raw_rep = raw.get("faction_reputation")
        if isinstance(raw_rep, dict):
            rep_out: dict[str, int] = {}
            for key, value in raw_rep.items():
                if not isinstance(key, str):
                    continue
                try:
                    score = int(value)
                except (TypeError, ValueError):
                    continue
                rep_out[key[:80]] = max(-100, min(100, score))
            state.faction_reputation = rep_out
        else:
            state.faction_reputation = {}

        raw_rep_log = raw.get("faction_reputation_log")
        if isinstance(raw_rep_log, list):
            logs: list[dict] = []
            for entry in raw_rep_log[-200:]:
                if isinstance(entry, dict):
                    logs.append(dict(entry))
            state.faction_reputation_log = logs
        else:
            state.faction_reputation_log = []

        raw_faction_states = raw.get("faction_states")
        if isinstance(raw_faction_states, dict):
            cleaned_states: dict[str, dict] = {}
            def _safe_score(value: object, default: int) -> int:
                try:
                    return max(0, min(100, int(value)))
                except (TypeError, ValueError):
                    return max(0, min(100, int(default)))
            for raw_name, raw_payload in raw_faction_states.items():
                faction = str(raw_name or "").strip()[:64]
                if not faction or not isinstance(raw_payload, dict):
                    continue
                cleaned_states[faction] = {
                    "power_level": _safe_score(raw_payload.get("power_level"), 40),
                    "brutality_index": _safe_score(raw_payload.get("brutality_index"), 35),
                    "corruption_index": _safe_score(raw_payload.get("corruption_index"), 30),
                    "relations": dict(raw_payload.get("relations") or {}),
                }
            state.faction_states = cleaned_states
        else:
            state.faction_states = {}

        raw_trade_session = raw.get("trade_session")
        state.trade_session = normalize_trade_session(raw_trade_session)
        state.travel_state = normalize_travel_state(raw.get("travel_state"))

        legacy_gm_state = raw.get("gm_state")
        raw_flags = raw.get("gm_flags")
        if not isinstance(raw_flags, dict) and isinstance(legacy_gm_state, dict):
            maybe_flags = legacy_gm_state.get("flags")
            raw_flags = maybe_flags if isinstance(maybe_flags, dict) else {}
        flags = raw_flags if isinstance(raw_flags, dict) else {}

        scene = state.scenes.get(state.current_scene_id)
        state.gm_state = {
            "player_name": str(getattr(state.player, "name", "l'Éveillé") or "l'Éveillé"),
            "location": str(scene.title if scene else "inconnu"),
            "location_id": str(scene.id if scene else state.current_scene_id or "inconnu"),
            "map_anchor": str(scene.map_anchor if scene else ""),
            "world_time_minutes": max(0, int(getattr(state, "world_time_minutes", 0))),
            "flags": dict(flags),
        }
        raw_last_trade = raw.get("gm_last_trade")
        if not isinstance(raw_last_trade, dict) and isinstance(legacy_gm_state, dict):
            maybe_trade = legacy_gm_state.get("last_trade")
            raw_last_trade = maybe_trade if isinstance(maybe_trade, dict) else None
        if isinstance(raw_last_trade, dict):
            state.gm_state["last_trade"] = dict(raw_last_trade)
        raw_pending_trade = raw.get("gm_pending_trade")
        if not isinstance(raw_pending_trade, dict) and isinstance(legacy_gm_state, dict):
            maybe_pending = legacy_gm_state.get("pending_trade")
            raw_pending_trade = maybe_pending if isinstance(maybe_pending, dict) else None
        if isinstance(raw_pending_trade, dict):
            state.gm_state["pending_trade"] = dict(raw_pending_trade)
            if state.trade_session.status == "idle":
                state.trade_session = normalize_trade_session(trade_session_from_legacy_pending_trade(raw_pending_trade))
        state.gm_state["trade_session"] = trade_session_to_dict(state.trade_session)
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
