from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from nicegui import ui
from pydantic import ValidationError

from app.core.data.item_manager import DataError as ItemDataError
from app.core.data.item_manager import ItemsManager
from app.gamemaster.models import model_for
from app.gamemaster.npc_manager import NPCProfile
from app.gamemaster.npc_manager import NPCProfileManager
from app.gamemaster.npc_manager import npc_profile_key
from app.gamemaster.ollama_client import OllamaClient


DATA_DIR = Path("data")
ITEMS_DIR = DATA_DIR / "items"
MONSTERS_DIR = DATA_DIR / "monsters"
NPCS_DIR = DATA_DIR / "npcs" / "generated"
MERCHANTS_DIR = DATA_DIR / "merchants"
WORLD_DIR = DATA_DIR / "world"
CITIES_XY_PATH = WORLD_DIR / "cities_xy.json"

_llm = OllamaClient()
_items_manager = ItemsManager(data_dir=str(DATA_DIR))
_npc_manager = NPCProfileManager(_llm, storage_dir=str(NPCS_DIR))


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _slug(value: str, fallback: str = "entry") -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", folded.casefold()).strip("_")
    return slug or fallback


def _json_pretty(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json_fragment(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "{}"

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


def _read_json_dict(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.as_posix()}: JSON object attendu")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_pretty(payload), encoding="utf-8")


def _list_json_stems(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.is_file())


def _set_select_options(widget: Any, options: list[str], value: str | None = None) -> None:
    setter = getattr(widget, "set_options", None)
    if callable(setter):
        setter(options)
    else:
        widget.options = options
    if value is not None:
        widget.value = value


def _deep_merge_dict(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


async def _ai_json(prompt: str, *, temperature: float = 0.35, num_predict: int = 900) -> dict:
    raw = await _llm.generate(
        model=model_for("rules"),
        prompt=prompt,
        temperature=temperature,
        num_ctx=4096,
        num_predict=num_predict,
        stop=None,
    )
    fragment = _extract_json_fragment(raw)
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("Le modele n'a pas retourne un JSON objet")
    return payload


def _npc_template(
    *,
    label: str = "Nouveau PNJ",
    role: str = "Habitant",
    location_id: str = "village_center_01",
    location_title: str = "La Place du Village",
) -> dict:
    key = npc_profile_key(label, location_id)
    return {
        "template_version": "1.0",
        "npc_key": key,
        "label": label,
        "role": role,
        "world_anchor": {
            "location_id": location_id,
            "location_title": location_title,
        },
        "identity": {
            "first_name": "Prenom",
            "last_name": "Nom",
            "alias": "",
            "social_class": "commun",
            "age_apparent": "adulte",
            "gender": "homme",
            "species": "humain",
            "origin": location_title,
            "reputation": "locale",
        },
        "speech_style": {
            "register": "neutre",
            "ton": "prudent",
            "verbosity": "equilibre",
            "max_sentences_per_reply": 3,
            "vocabulary": "simple",
            "pronouns": "vouvoiement",
        },
        "char_persona": "Personnalite courte du PNJ.",
        "trait_sombre": "",
        "first_message": "Le PNJ vous observe avant de parler.",
        "backstory": "Passe du PNJ en 2-3 phrases.",
        "knowledge_base": [],
        "goals": [],
        "desires": [],
        "needs": [],
        "fears": [],
        "secrets": [],
        "quest_hooks": [],
        "relations": {"allies": [], "enemies": []},
        "dynamic_flags": {
            "is_met": False,
            "relation_score": 0,
            "is_angry": False,
            "current_mood": "neutre",
            "is_hostile": False,
            "is_bribeable": False,
            "is_quest_giver": False,
        },
        "emotional_state": {
            "dominant_emotion": "neutre",
            "toward_player": "neutre",
            "trust_player": 0,
            "stress": 30,
            "affection": 20,
            "curiosity": 40,
            "last_trigger": "",
        },
    }


def _monster_template(*, monster_id: str = "nouveau_monstre") -> dict:
    clean_id = _slug(monster_id, fallback="nouveau_monstre")
    return {
        "id": clean_id,
        "name": "Nouveau monstre",
        "aliases": ["monstre"],
        "archetype": "brute",
        "tier": 1,
        "description": "Description courte du monstre.",
        "combat": {
            "base_hp": 18,
            "base_dc": 12,
            "base_attack_bonus": 3,
            "base_damage_min": 3,
            "base_damage_max": 6,
            "hp_per_floor": 1.1,
            "dc_per_5_floors": 1,
            "attack_per_6_floors": 1,
            "damage_per_8_floors": 1,
        },
        "boss_modifiers": {
            "hp_mult": 1.5,
            "damage_mult": 1.35,
            "dc_bonus": 2,
            "attack_bonus": 1,
        },
        "media": {
            "image": "",
            "clip": "",
        },
    }


def _normalize_monster_payload(payload: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    monster_id = _slug(str(source.get("id") or source.get("name") or "nouveau_monstre"), fallback="nouveau_monstre")
    name = str(source.get("name") or "Nouveau monstre").strip()[:80] or "Nouveau monstre"

    aliases_raw = source.get("aliases")
    aliases: list[str] = []
    if isinstance(aliases_raw, list):
        for value in aliases_raw[:12]:
            alias = str(value or "").strip()
            if alias:
                aliases.append(alias[:80])
    if not aliases:
        aliases = [name]

    combat_src = source.get("combat") if isinstance(source.get("combat"), dict) else {}
    boss_src = source.get("boss_modifiers") if isinstance(source.get("boss_modifiers"), dict) else {}
    media_src = source.get("media") if isinstance(source.get("media"), dict) else {}

    return {
        "id": monster_id,
        "name": name,
        "aliases": list(dict.fromkeys(aliases)),
        "archetype": str(source.get("archetype") or "brute").strip().casefold()[:32] or "brute",
        "tier": max(1, min(_safe_int(source.get("tier"), 1), 5)),
        "description": str(source.get("description") or "").strip()[:220],
        "combat": {
            "base_hp": max(6, _safe_int(combat_src.get("base_hp"), 18)),
            "base_dc": max(8, _safe_int(combat_src.get("base_dc"), 12)),
            "base_attack_bonus": max(1, _safe_int(combat_src.get("base_attack_bonus"), 3)),
            "base_damage_min": max(1, _safe_int(combat_src.get("base_damage_min"), 3)),
            "base_damage_max": max(1, _safe_int(combat_src.get("base_damage_max"), 6)),
            "hp_per_floor": max(0.2, _safe_float(combat_src.get("hp_per_floor"), 1.1)),
            "dc_per_5_floors": max(0, _safe_int(combat_src.get("dc_per_5_floors"), 1)),
            "attack_per_6_floors": max(0, _safe_int(combat_src.get("attack_per_6_floors"), 1)),
            "damage_per_8_floors": max(0, _safe_int(combat_src.get("damage_per_8_floors"), 1)),
        },
        "boss_modifiers": {
            "hp_mult": max(1.0, _safe_float(boss_src.get("hp_mult"), 1.5)),
            "damage_mult": max(1.0, _safe_float(boss_src.get("damage_mult"), 1.35)),
            "dc_bonus": max(0, _safe_int(boss_src.get("dc_bonus"), 2)),
            "attack_bonus": max(0, _safe_int(boss_src.get("attack_bonus"), 1)),
        },
        "media": {
            "image": str(media_src.get("image") or "").strip()[:240],
            "clip": str(media_src.get("clip") or "").strip()[:240],
        },
    }


def _item_template(*, item_id: str = "nouvel_objet") -> dict:
    clean_id = _slug(item_id, fallback="nouvel_objet")
    return {
        "id": clean_id,
        "name": "Nouvel objet",
        "stack_max": 1,
        "type": "misc",
        "slot": "",
        "rarity": "common",
        "description": "Description courte de l'objet.",
        "stat_bonuses": {},
        "effects": [],
        "value_gold": 10,
    }


def _normalize_item_payload(payload: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    item_id = _slug(str(source.get("id") or source.get("name") or "nouvel_objet"), fallback="nouvel_objet")
    name = str(source.get("name") or "Nouvel objet").strip() or "Nouvel objet"

    bonuses_src = source.get("stat_bonuses") if isinstance(source.get("stat_bonuses"), dict) else {}
    bonuses: dict[str, int] = {}
    for key, value in bonuses_src.items():
        stat = str(key or "").strip().casefold()
        if not stat:
            continue
        delta = _safe_int(value, 0)
        if delta == 0:
            continue
        bonuses[stat] = max(-999, min(delta, 999))

    effects_src = source.get("effects") if isinstance(source.get("effects"), list) else []
    effects = [row for row in effects_src[:8] if isinstance(row, dict)]

    return {
        "id": item_id,
        "name": name,
        "stack_max": max(1, min(_safe_int(source.get("stack_max"), 1), 999)),
        "type": str(source.get("type") or "misc").strip().casefold() or "misc",
        "slot": str(source.get("slot") or "").strip().casefold(),
        "rarity": str(source.get("rarity") or "common").strip().casefold() or "common",
        "description": str(source.get("description") or "").strip(),
        "stat_bonuses": bonuses,
        "effects": effects,
        "value_gold": max(0, min(_safe_int(source.get("value_gold"), 0), 99999)),
    }


def _merchant_template(*, merchant_id: str = "marchand_nouveau") -> dict:
    clean_id = _slug(merchant_id, fallback="marchand_nouveau")
    location_id = "boutique_01"
    location_title = "La Boutique"
    merchant_name = "Marchand local"
    return {
        "id": clean_id,
        "name": merchant_name,
        "location": {
            "location_id": location_id,
            "location_title": location_title,
        },
        "npc_profile": _npc_template(
            label=merchant_name,
            role="Marchand",
            location_id=location_id,
            location_title=location_title,
        ),
        "inventory": [
            {
                "item_id": "pain_01",
                "stock": 20,
                "price_multiplier": 1.0,
            }
        ],
        "notes": "",
    }


def _normalize_merchant_payload(payload: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    merchant_id = _slug(str(source.get("id") or source.get("name") or "marchand_nouveau"), fallback="marchand_nouveau")
    name = str(source.get("name") or "Marchand local").strip() or "Marchand local"

    location_src = source.get("location") if isinstance(source.get("location"), dict) else {}
    location_id = _slug(str(location_src.get("location_id") or "boutique_01"), fallback="boutique_01")
    location_title = str(location_src.get("location_title") or "La Boutique").strip() or "La Boutique"

    inventory_src = source.get("inventory") if isinstance(source.get("inventory"), list) else []
    inventory: list[dict] = []
    for row in inventory_src[:64]:
        if not isinstance(row, dict):
            continue
        item_id = _slug(str(row.get("item_id") or ""), fallback="")
        if not item_id:
            continue
        inventory.append(
            {
                "item_id": item_id,
                "stock": max(0, _safe_int(row.get("stock"), 0)),
                "price_multiplier": max(0.2, min(_safe_float(row.get("price_multiplier"), 1.0), 5.0)),
            }
        )

    npc_source = source.get("npc_profile") if isinstance(source.get("npc_profile"), dict) else {}
    npc_base = _npc_template(
        label=name,
        role="Marchand",
        location_id=location_id,
        location_title=location_title,
    )
    npc_payload = _deep_merge_dict(npc_base, npc_source)
    npc_payload["label"] = str(npc_payload.get("label") or name).strip() or name
    npc_payload["role"] = str(npc_payload.get("role") or "Marchand").strip() or "Marchand"
    npc_payload["world_anchor"] = {
        "location_id": location_id,
        "location_title": location_title,
    }
    npc_payload["npc_key"] = npc_profile_key(npc_payload["label"], location_id)

    return {
        "id": merchant_id,
        "name": name,
        "location": {
            "location_id": location_id,
            "location_title": location_title,
        },
        "npc_profile": npc_payload,
        "inventory": inventory,
        "notes": str(source.get("notes") or "").strip(),
    }


def _default_cities_xy_payload() -> dict:
    return {
        "version": 1,
        "continents": [
            {
                "id": "ataryxia",
                "name": "Ataryxia",
                "description": "Continent principal.",
                "cities": [
                    {
                        "id": "lumeria",
                        "name": "Lumeria",
                        "x": 0,
                        "y": 0,
                        "kind": "capitale",
                    }
                ],
            }
        ],
    }


def _sanitize_cities_xy_payload(payload: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    continents_src = source.get("continents") if isinstance(source.get("continents"), list) else []

    continents: list[dict] = []
    for row in continents_src[:40]:
        if not isinstance(row, dict):
            continue
        cid = _slug(str(row.get("id") or row.get("name") or "continent"), fallback="continent")
        name = str(row.get("name") or cid).strip() or cid
        description = str(row.get("description") or "").strip()
        cities_src = row.get("cities") if isinstance(row.get("cities"), list) else []

        cities: list[dict] = []
        for city in cities_src[:800]:
            if not isinstance(city, dict):
                continue
            city_id = _slug(str(city.get("id") or city.get("name") or "ville"), fallback="ville")
            city_name = str(city.get("name") or city_id).strip() or city_id
            kind = str(city.get("kind") or "ville").strip() or "ville"
            cities.append(
                {
                    "id": city_id,
                    "name": city_name,
                    "x": _safe_int(city.get("x"), 0),
                    "y": _safe_int(city.get("y"), 0),
                    "kind": kind,
                }
            )

        seen: set[str] = set()
        unique_cities: list[dict] = []
        for city in cities:
            city_id = str(city.get("id") or "")
            if not city_id or city_id in seen:
                continue
            seen.add(city_id)
            unique_cities.append(city)

        continents.append(
            {
                "id": cid,
                "name": name,
                "description": description,
                "cities": unique_cities,
            }
        )

    if not continents:
        return _default_cities_xy_payload()

    return {
        "version": 1,
        "continents": continents,
    }


def _load_cities_xy_payload() -> dict:
    if not CITIES_XY_PATH.exists():
        payload = _default_cities_xy_payload()
        _write_json(CITIES_XY_PATH, payload)
        return payload
    try:
        return _sanitize_cities_xy_payload(_read_json_dict(CITIES_XY_PATH))
    except Exception:
        payload = _default_cities_xy_payload()
        _write_json(CITIES_XY_PATH, payload)
        return payload


def _format_json_text(raw_text: str) -> str:
    payload = json.loads(str(raw_text or "{}"))
    return _json_pretty(payload)


def _validate_json_dict_text(raw_text: str) -> dict:
    payload = json.loads(str(raw_text or "{}"))
    if not isinstance(payload, dict):
        raise ValueError("JSON objet attendu")
    return payload


def _hint_box(text: str) -> None:
    with ui.element("div").classes("studio-hint"):
        ui.label(str(text or "")).classes("text-sm").style("white-space: pre-wrap;")


def _build_studio_page() -> None:
    ui.dark_mode().enable()

    ui.add_head_html(
        """
        <style>
          .studio-page {
            min-height: calc(100vh - 24px);
            background:
              radial-gradient(circle at 0% 0%, rgba(56, 189, 248, 0.13), transparent 36%),
              radial-gradient(circle at 100% 0%, rgba(74, 222, 128, 0.10), transparent 34%),
              linear-gradient(180deg, rgba(10, 14, 22, 0.92), rgba(7, 10, 16, 0.96));
            border-radius: 14px;
            padding: 12px;
          }
          .studio-card {
            border: 1px solid rgba(255, 255, 255, 0.10);
            background: linear-gradient(180deg, rgba(16, 20, 30, 0.84), rgba(12, 16, 24, 0.86));
            border-radius: 12px;
          }
          .studio-hero {
            border: 1px solid rgba(125, 211, 252, 0.30);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(30, 41, 59, 0.82));
            border-radius: 14px;
            padding: 12px 14px;
          }
          .studio-summary-chip {
            border: 1px solid rgba(255, 255, 255, 0.15);
            background: rgba(12, 18, 28, 0.78);
            border-radius: 10px;
            min-width: 130px;
            padding: 8px 10px;
          }
          .studio-toolbar {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
            justify-content: flex-start;
          }
          .studio-hint {
            border-left: 3px solid rgba(125, 211, 252, 0.7);
            background: rgba(15, 23, 42, 0.55);
            border-radius: 8px;
            padding: 8px 10px;
            color: rgba(226, 232, 240, 0.95);
          }
          .studio-editor textarea {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            font-size: 12px;
            line-height: 1.35;
          }
          .studio-scroll {
            max-height: calc(100vh - 180px);
            overflow-y: auto;
          }
          .studio-tabs .q-tab {
            border-radius: 8px;
            margin-right: 6px;
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: rgba(15, 23, 42, 0.55);
          }
          .studio-tabs .q-tab--active {
            background: rgba(56, 189, 248, 0.22) !important;
            border-color: rgba(125, 211, 252, 0.55) !important;
          }
          @media (max-width: 1024px) {
            .studio-page {
              padding: 10px;
            }
            .studio-summary-chip {
              min-width: 110px;
            }
          }
        </style>
        """,
        shared=False,
    )

    world_payload = _load_cities_xy_payload()
    world_continents = world_payload.get("continents") if isinstance(world_payload.get("continents"), list) else []

    counts = {
        "PNJ": len(_list_json_stems(NPCS_DIR)),
        "Monstres": len(_list_json_stems(MONSTERS_DIR)),
        "Items": len(_list_json_stems(ITEMS_DIR)),
        "Marchands": len(_list_json_stems(MERCHANTS_DIR)),
        "Continents": len(world_continents),
    }

    with ui.column().classes("w-full gap-3 studio-page"):
        with ui.card().classes("w-full studio-hero"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("Studio de Contenu").classes("text-2xl font-bold")
                    ui.label("Creation, edition et pre-remplissage IA des donnees du jeu").classes("text-sm opacity-80")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Aller au jeu", on_click=lambda: ui.navigate.to("/game")).props("outline no-caps")
                    ui.button("Prototype 2D", on_click=lambda: ui.navigate.to("/prototype-2d")).props("outline no-caps")

            with ui.row().classes("w-full gap-2 items-stretch").style("margin-top: 10px; flex-wrap: wrap;"):
                for label in ("PNJ", "Monstres", "Items", "Marchands", "Continents"):
                    with ui.element("div").classes("studio-summary-chip"):
                        ui.label(label).classes("text-xs opacity-75")
                        ui.label(str(counts.get(label, 0))).classes("text-xl font-semibold")

            with ui.expansion("Guide de prise en main", value=False).classes("w-full").style("margin-top: 8px;"):
                with ui.column().classes("w-full gap-2"):
                    ui.label("1. Choisis un onglet (PNJ, Monstres, Items, Marchands, Villes XY).").classes("text-sm")
                    ui.label("2. Charge un fichier existant, ou pars d'un template vierge.").classes("text-sm")
                    ui.label("3. Optionnel: utilise Pre-remplir IA avec un brief court.").classes("text-sm")
                    ui.label("4. Sauvegarde; les JSON sont ecrits directement dans data/.").classes("text-sm")
                    _hint_box("Astuce: valide/formate le JSON avant de sauvegarder pour eviter les erreurs.")

        with ui.tabs(value="npc").props("dense mobile-arrows outside-arrows align=left").classes("w-full studio-tabs") as tabs:
            tab_npc = ui.tab("npc", "PNJ")
            tab_monster = ui.tab("monster", "Monstres")
            tab_item = ui.tab("item", "Items")
            tab_merchant = ui.tab("merchant", "Marchands")
            tab_map = ui.tab("map", "Villes XY")

        with ui.tab_panels(tabs, value=tab_npc).props("animated").classes("w-full"):
            with ui.tab_panel(tab_npc).classes("w-full"):
                _build_npc_tab()
            with ui.tab_panel(tab_monster).classes("w-full"):
                _build_monster_tab()
            with ui.tab_panel(tab_item).classes("w-full"):
                _build_item_tab()
            with ui.tab_panel(tab_merchant).classes("w-full"):
                _build_merchant_tab()
            with ui.tab_panel(tab_map).classes("w-full"):
                _build_cities_xy_tab()


def _build_npc_tab() -> None:
    state = {
        "brief": "",
        "json": _json_pretty(_npc_template()),
        "filter": "",
        "quick_label": "",
        "quick_role": "",
        "quick_location_id": "",
        "quick_location_title": "",
        "quick_first_name": "",
        "quick_last_name": "",
        "quick_gender": "homme",
        "quick_species": "humain",
        "quick_first_message": "",
    }

    with ui.card().classes("w-full studio-card"):
        ui.label("PNJ").classes("text-lg font-semibold")
        ui.label("Template vierge + pre-remplissage IA + sauvegarde dans data/npcs/generated").classes("text-xs opacity-70")
        ui.label(f"Dossier cible: {NPCS_DIR.as_posix()}").classes("text-xs opacity-60")

        counter_label = ui.label("").classes("text-xs opacity-75")
        status_label = ui.label("JSON pret.").classes("text-xs opacity-80")

        def _set_status(message: str, *, tone: str = "") -> None:
            style = "opacity:0.86;"
            if tone == "ok":
                style = "color:#86efac; opacity:0.95;"
            elif tone == "warn":
                style = "color:#fcd34d; opacity:0.95;"
            elif tone == "error":
                style = "color:#fda4af; opacity:0.95;"
            status_label.set_text(message)
            status_label.style(style)

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            npc_select = ui.select(options=[], label="PNJ existant").classes("w-72")
            filter_input = ui.input("Filtre fichiers").classes("w-56")

            def _refresh_npc_options(selected: str | None = None) -> None:
                options = _list_json_stems(NPCS_DIR)
                query = str(state.get("filter") or "").strip().casefold()
                if query:
                    options = [name for name in options if query in name.casefold()]
                keep = selected if selected in options else (options[0] if options else "")
                _set_select_options(npc_select, options, value=keep)
                counter_label.set_text(f"{len(options)} fichier(s) visible(s)")

            def _load_selected_npc() -> None:
                selected = str(npc_select.value or "").strip()
                if not selected:
                    ui.notify("Selectionne un PNJ a charger.", color="warning")
                    _set_status("Aucun PNJ selectionne.", tone="warn")
                    return
                path = NPCS_DIR / f"{selected}.json"
                if not path.exists():
                    ui.notify("Fichier introuvable.", color="negative")
                    _refresh_npc_options()
                    _set_status("Le fichier selectionne est introuvable.", tone="error")
                    return
                try:
                    payload = _read_json_dict(path)
                except Exception as exc:
                    ui.notify(f"Lecture impossible: {exc}", color="negative")
                    _set_status(f"Lecture impossible: {exc}", tone="error")
                    return
                state["json"] = _json_pretty(payload)
                npc_editor.value = state["json"]
                ui.notify(f"PNJ charge: {selected}", color="positive")
                _set_status(f"Charge: {path.as_posix()}", tone="ok")
                _sync_quick_from_npc_json()

            filter_input.bind_value(state, "filter")
            filter_input.on_value_change(lambda e: _refresh_npc_options(npc_select.value))

            ui.button("Charger", on_click=_load_selected_npc).props("outline no-caps")
            ui.button("Rafraichir", on_click=lambda: _refresh_npc_options(npc_select.value)).props("outline no-caps")

        ui.textarea(label="Brief IA (optionnel)").bind_value(state, "brief").classes("w-full")
        _hint_box("Exemple brief: 'Aubergiste cynique de Lumeria, cache une dette envers une guilde'.")

        npc_editor = ui.textarea(label="JSON PNJ").bind_value(state, "json").classes("w-full studio-editor").style(
            "min-height: 460px;"
        )

        with ui.expansion("Assistant rapide PNJ (formulaire guide)", value=False).classes("w-full"):
            with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
                quick_label_input = ui.input("Label").classes("w-64")
                quick_role_input = ui.input("Role").classes("w-64")
                quick_location_id_input = ui.input("location_id").classes("w-64")
                quick_location_title_input = ui.input("location_title").classes("w-80")
            with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
                quick_first_name_input = ui.input("Prenom").classes("w-56")
                quick_last_name_input = ui.input("Nom").classes("w-56")
                quick_gender_input = ui.select(
                    options=["homme", "femme", "non-binaire"],
                    label="Genre",
                    value="homme",
                ).classes("w-40")
                quick_species_input = ui.input("Espece").classes("w-56")
            quick_first_message_input = ui.input("Premiere replique").classes("w-full")

            def _sync_quick_from_npc_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception as exc:
                    _set_status(f"Sync formulaire impossible: {exc}", tone="error")
                    return
                identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
                world_anchor = payload.get("world_anchor") if isinstance(payload.get("world_anchor"), dict) else {}
                state["quick_label"] = str(payload.get("label") or "")
                state["quick_role"] = str(payload.get("role") or "")
                state["quick_location_id"] = str(world_anchor.get("location_id") or "")
                state["quick_location_title"] = str(world_anchor.get("location_title") or "")
                state["quick_first_name"] = str(identity.get("first_name") or "")
                state["quick_last_name"] = str(identity.get("last_name") or "")
                state["quick_gender"] = str(identity.get("gender") or "homme")
                state["quick_species"] = str(identity.get("species") or "humain")
                state["quick_first_message"] = str(payload.get("first_message") or "")

                quick_label_input.value = state["quick_label"]
                quick_role_input.value = state["quick_role"]
                quick_location_id_input.value = state["quick_location_id"]
                quick_location_title_input.value = state["quick_location_title"]
                quick_first_name_input.value = state["quick_first_name"]
                quick_last_name_input.value = state["quick_last_name"]
                quick_gender_input.value = state["quick_gender"] if state["quick_gender"] else "homme"
                quick_species_input.value = state["quick_species"]
                quick_first_message_input.value = state["quick_first_message"]
                _set_status("Formulaire PNJ synchronise depuis le JSON.", tone="ok")

            def _apply_quick_to_npc_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception:
                    payload = _npc_template()

                identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
                world_anchor = payload.get("world_anchor") if isinstance(payload.get("world_anchor"), dict) else {}

                label = str(quick_label_input.value or "").strip() or str(payload.get("label") or "Nouveau PNJ")
                role = str(quick_role_input.value or "").strip() or str(payload.get("role") or "Habitant")
                location_id = _slug(
                    str(quick_location_id_input.value or world_anchor.get("location_id") or "village_center_01"),
                    fallback="village_center_01",
                )
                location_title = str(quick_location_title_input.value or world_anchor.get("location_title") or "Lieu").strip() or "Lieu"

                payload["label"] = label
                payload["role"] = role
                payload["world_anchor"] = {
                    "location_id": location_id,
                    "location_title": location_title,
                }
                identity["first_name"] = str(quick_first_name_input.value or identity.get("first_name") or "Prenom").strip() or "Prenom"
                identity["last_name"] = str(quick_last_name_input.value or identity.get("last_name") or "").strip()
                identity["gender"] = str(quick_gender_input.value or identity.get("gender") or "homme").strip() or "homme"
                identity["species"] = str(quick_species_input.value or identity.get("species") or "humain").strip() or "humain"
                identity.setdefault("social_class", "commun")
                identity.setdefault("age_apparent", "adulte")
                identity.setdefault("origin", location_title)
                identity.setdefault("reputation", "locale")
                payload["identity"] = identity
                payload["first_message"] = str(
                    quick_first_message_input.value or payload.get("first_message") or "Le PNJ vous observe."
                ).strip() or "Le PNJ vous observe."
                payload["npc_key"] = npc_profile_key(label, location_id)

                try:
                    validated = NPCProfile.model_validate(payload).model_dump(by_alias=True)
                except Exception as exc:
                    ui.notify(f"Formulaire invalide: {exc}", color="negative")
                    _set_status(f"Formulaire invalide: {exc}", tone="error")
                    return

                state["json"] = _json_pretty(validated)
                npc_editor.value = state["json"]
                _set_status("JSON PNJ mis a jour depuis le formulaire.", tone="ok")

            with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
                ui.button("JSON -> formulaire", on_click=_sync_quick_from_npc_json).props("outline no-caps")
                ui.button("Formulaire -> JSON", on_click=_apply_quick_to_npc_json).props("outline no-caps")

        with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
            def _format_npc_json() -> None:
                try:
                    state["json"] = _format_json_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"Formatage impossible: {exc}", tone="error")
                    return
                npc_editor.value = state["json"]
                _set_status("JSON formate.", tone="ok")
                _sync_quick_from_npc_json()

            def _validate_npc_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                    NPCProfile.model_validate(payload)
                except Exception as exc:
                    ui.notify(f"Validation echouee: {exc}", color="negative")
                    _set_status(f"Validation echouee: {exc}", tone="error")
                    return
                ui.notify("Validation PNJ OK.", color="positive")
                _set_status("Validation PNJ OK.", tone="ok")

            def _reset_npc_template() -> None:
                state["json"] = _json_pretty(_npc_template())
                npc_editor.value = state["json"]
                ui.notify("Template PNJ reinitialise.")
                _set_status("Template PNJ vierge charge.", tone="ok")
                _sync_quick_from_npc_json()

            async def _prefill_npc_ai() -> None:
                prefill_btn.disable()
                _set_status("Generation IA PNJ en cours...", tone="warn")
                try:
                    current = json.loads(state["json"]) if state["json"].strip() else _npc_template()
                    if not isinstance(current, dict):
                        current = _npc_template()
                except Exception:
                    current = _npc_template()

                fallback_label = str(current.get("label") or "Nouveau PNJ").strip() or "Nouveau PNJ"
                world_anchor = current.get("world_anchor") if isinstance(current.get("world_anchor"), dict) else {}
                fallback_location_id = _slug(str(world_anchor.get("location_id") or "village_center_01"), fallback="village_center_01")
                fallback_location_title = str(world_anchor.get("location_title") or "La Place du Village").strip() or "La Place du Village"
                schema = _npc_template(
                    label=fallback_label,
                    role=str(current.get("role") or "Habitant").strip() or "Habitant",
                    location_id=fallback_location_id,
                    location_title=fallback_location_title,
                )

                prompt = (
                    "Tu es un generateur de fiches PNJ dark fantasy. "
                    "Reponds en JSON valide uniquement. "
                    "Pas de markdown, pas de commentaire. "
                    f"Brief utilisateur: {state.get('brief') or 'Aucun brief.'}\n"
                    "Respecte exactement le schema et remplis les champs de maniere jouable.\n"
                    "Le role doit etre concret (ex: Aubergiste, Forgeron, Marchand).\n"
                    "Le champ identity.gender doit etre explicite.\n"
                    "Schema:\n"
                    f"{_json_pretty(schema)}"
                )

                try:
                    ai_payload = await _ai_json(prompt)
                    merged = _deep_merge_dict(schema, ai_payload)
                    validated = NPCProfile.model_validate(merged).model_dump(by_alias=True)
                    state["json"] = _json_pretty(validated)
                    npc_editor.value = state["json"]
                    ui.notify("Pre-remplissage IA PNJ termine.", color="positive")
                    _set_status("Pre-remplissage IA PNJ termine.", tone="ok")
                    _sync_quick_from_npc_json()
                except Exception as exc:
                    ui.notify(f"Pre-remplissage IA PNJ echoue: {exc}", color="negative")
                    _set_status(f"Pre-remplissage IA echoue: {exc}", tone="error")
                finally:
                    prefill_btn.enable()

            def _save_npc() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"JSON invalide: {exc}", tone="error")
                    return

                label = str(payload.get("label") or "Nouveau PNJ").strip() or "Nouveau PNJ"
                role = str(payload.get("role") or "Habitant").strip() or "Habitant"
                world_anchor = payload.get("world_anchor") if isinstance(payload.get("world_anchor"), dict) else {}
                location_id = _slug(str(world_anchor.get("location_id") or "village_center_01"), fallback="village_center_01")
                location_title = str(world_anchor.get("location_title") or "Lieu inconnu").strip() or "Lieu inconnu"

                merged = _deep_merge_dict(
                    _npc_template(label=label, role=role, location_id=location_id, location_title=location_title),
                    payload,
                )
                merged["npc_key"] = npc_profile_key(str(merged.get("label") or label), location_id)
                merged["world_anchor"] = {
                    "location_id": location_id,
                    "location_title": location_title,
                }

                try:
                    validated = NPCProfile.model_validate(merged).model_dump(by_alias=True)
                    _npc_manager.save_profile(str(validated.get("label") or label), validated, location_id=location_id)
                except ValidationError as exc:
                    ui.notify(f"Validation PNJ echouee: {exc}", color="negative")
                    _set_status(f"Validation PNJ echouee: {exc}", tone="error")
                    return
                except Exception as exc:
                    ui.notify(f"Sauvegarde PNJ echouee: {exc}", color="negative")
                    _set_status(f"Sauvegarde echouee: {exc}", tone="error")
                    return

                state["json"] = _json_pretty(validated)
                npc_editor.value = state["json"]
                _refresh_npc_options(str(validated.get("npc_key") or ""))
                ui.notify(f"PNJ sauvegarde: {validated.get('npc_key')}", color="positive")
                _set_status(f"PNJ sauvegarde: {validated.get('npc_key')}", tone="ok")
                _sync_quick_from_npc_json()

            ui.button("Valider JSON", on_click=_validate_npc_json).props("outline no-caps")
            ui.button("Formater JSON", on_click=_format_npc_json).props("outline no-caps")
            ui.button("Template vierge", on_click=_reset_npc_template).props("outline no-caps")
            prefill_btn = ui.button("Pre-remplir IA", on_click=lambda: asyncio.create_task(_prefill_npc_ai())).props(
                "outline no-caps"
            )
            ui.button("Sauvegarder PNJ", on_click=_save_npc).props("color=primary no-caps")

        with ui.expansion("Aide schema PNJ", value=False).classes("w-full"):
            ui.label("Champs minimaux utiles: label, role, world_anchor, identity, first_message.").classes("text-sm")
            ui.label("Le role doit etre explicite (pas 'inconnu').").classes("text-sm")
            ui.label("Astuce: valide le JSON avant sauvegarde pour voir les erreurs de schema.").classes("text-sm")

        _refresh_npc_options()
        _sync_quick_from_npc_json()


def _build_monster_tab() -> None:
    state = {
        "brief": "",
        "json": _json_pretty(_monster_template()),
        "filter": "",
        "quick_id": "",
        "quick_name": "",
        "quick_archetype": "",
        "quick_tier": "1",
        "quick_hp": "18",
        "quick_dc": "12",
        "quick_attack": "3",
        "quick_damage_min": "3",
        "quick_damage_max": "6",
        "quick_description": "",
    }

    with ui.card().classes("w-full studio-card"):
        ui.label("Monstres").classes("text-lg font-semibold")
        ui.label("Template vide base sur data/monsters + pre-remplissage IA").classes("text-xs opacity-70")
        ui.label(f"Dossier cible: {MONSTERS_DIR.as_posix()}").classes("text-xs opacity-60")

        counter_label = ui.label("").classes("text-xs opacity-75")
        status_label = ui.label("JSON pret.").classes("text-xs opacity-80")

        def _set_status(message: str, *, tone: str = "") -> None:
            style = "opacity:0.86;"
            if tone == "ok":
                style = "color:#86efac; opacity:0.95;"
            elif tone == "warn":
                style = "color:#fcd34d; opacity:0.95;"
            elif tone == "error":
                style = "color:#fda4af; opacity:0.95;"
            status_label.set_text(message)
            status_label.style(style)

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            monster_select = ui.select(options=[], label="Monstre existant").classes("w-72")
            filter_input = ui.input("Filtre fichiers").classes("w-56")

            def _refresh_monster_options(selected: str | None = None) -> None:
                options = _list_json_stems(MONSTERS_DIR)
                query = str(state.get("filter") or "").strip().casefold()
                if query:
                    options = [name for name in options if query in name.casefold()]
                keep = selected if selected in options else (options[0] if options else "")
                _set_select_options(monster_select, options, value=keep)
                counter_label.set_text(f"{len(options)} fichier(s) visible(s)")

            def _load_selected_monster() -> None:
                selected = str(monster_select.value or "").strip()
                if not selected:
                    ui.notify("Selectionne un monstre a charger.", color="warning")
                    _set_status("Aucun monstre selectionne.", tone="warn")
                    return
                path = MONSTERS_DIR / f"{selected}.json"
                if not path.exists():
                    ui.notify("Fichier introuvable.", color="negative")
                    _refresh_monster_options()
                    _set_status("Le fichier selectionne est introuvable.", tone="error")
                    return
                try:
                    payload = _read_json_dict(path)
                except Exception as exc:
                    ui.notify(f"Lecture impossible: {exc}", color="negative")
                    _set_status(f"Lecture impossible: {exc}", tone="error")
                    return
                state["json"] = _json_pretty(payload)
                monster_editor.value = state["json"]
                ui.notify(f"Monstre charge: {selected}", color="positive")
                _set_status(f"Charge: {path.as_posix()}", tone="ok")
                _sync_quick_from_monster_json()

            filter_input.bind_value(state, "filter")
            filter_input.on_value_change(lambda e: _refresh_monster_options(monster_select.value))

            ui.button("Charger", on_click=_load_selected_monster).props("outline no-caps")
            ui.button("Rafraichir", on_click=lambda: _refresh_monster_options(monster_select.value)).props("outline no-caps")

        ui.textarea(label="Brief IA (optionnel)").bind_value(state, "brief").classes("w-full")
        _hint_box("Exemple brief: 'Predateur furtif des ruines, faible PV mais degats eleves'.")

        monster_editor = ui.textarea(label="JSON Monstre").bind_value(state, "json").classes("w-full studio-editor").style(
            "min-height: 420px;"
        )

        with ui.expansion("Assistant rapide Monstre (formulaire guide)", value=False).classes("w-full"):
            with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
                quick_id_input = ui.input("id").classes("w-56")
                quick_name_input = ui.input("Nom").classes("w-72")
                quick_archetype_input = ui.input("Archetype").classes("w-56")
                quick_tier_input = ui.input("Tier").classes("w-24")
            with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
                quick_hp_input = ui.input("base_hp").classes("w-32")
                quick_dc_input = ui.input("base_dc").classes("w-32")
                quick_attack_input = ui.input("base_attack_bonus").classes("w-40")
                quick_damage_min_input = ui.input("damage_min").classes("w-32")
                quick_damage_max_input = ui.input("damage_max").classes("w-32")
            quick_description_input = ui.input("Description").classes("w-full")

            def _sync_quick_from_monster_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                    normalized = _normalize_monster_payload(payload)
                except Exception as exc:
                    _set_status(f"Sync formulaire impossible: {exc}", tone="error")
                    return
                combat = normalized.get("combat") if isinstance(normalized.get("combat"), dict) else {}
                state["quick_id"] = str(normalized.get("id") or "")
                state["quick_name"] = str(normalized.get("name") or "")
                state["quick_archetype"] = str(normalized.get("archetype") or "")
                state["quick_tier"] = str(_safe_int(normalized.get("tier"), 1))
                state["quick_hp"] = str(_safe_int(combat.get("base_hp"), 18))
                state["quick_dc"] = str(_safe_int(combat.get("base_dc"), 12))
                state["quick_attack"] = str(_safe_int(combat.get("base_attack_bonus"), 3))
                state["quick_damage_min"] = str(_safe_int(combat.get("base_damage_min"), 3))
                state["quick_damage_max"] = str(_safe_int(combat.get("base_damage_max"), 6))
                state["quick_description"] = str(normalized.get("description") or "")

                quick_id_input.value = state["quick_id"]
                quick_name_input.value = state["quick_name"]
                quick_archetype_input.value = state["quick_archetype"]
                quick_tier_input.value = state["quick_tier"]
                quick_hp_input.value = state["quick_hp"]
                quick_dc_input.value = state["quick_dc"]
                quick_attack_input.value = state["quick_attack"]
                quick_damage_min_input.value = state["quick_damage_min"]
                quick_damage_max_input.value = state["quick_damage_max"]
                quick_description_input.value = state["quick_description"]
                _set_status("Formulaire monstre synchronise depuis le JSON.", tone="ok")

            def _apply_quick_to_monster_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception:
                    payload = _monster_template()

                payload["id"] = _slug(str(quick_id_input.value or payload.get("id") or "nouveau_monstre"), fallback="nouveau_monstre")
                payload["name"] = str(quick_name_input.value or payload.get("name") or "Nouveau monstre").strip() or "Nouveau monstre"
                payload["archetype"] = str(quick_archetype_input.value or payload.get("archetype") or "brute").strip() or "brute"
                payload["tier"] = max(1, min(_safe_int(quick_tier_input.value, _safe_int(payload.get("tier"), 1)), 5))
                payload["description"] = str(
                    quick_description_input.value or payload.get("description") or ""
                ).strip()[:220]
                combat = payload.get("combat") if isinstance(payload.get("combat"), dict) else {}
                combat["base_hp"] = max(6, _safe_int(quick_hp_input.value, _safe_int(combat.get("base_hp"), 18)))
                combat["base_dc"] = max(8, _safe_int(quick_dc_input.value, _safe_int(combat.get("base_dc"), 12)))
                combat["base_attack_bonus"] = max(1, _safe_int(quick_attack_input.value, _safe_int(combat.get("base_attack_bonus"), 3)))
                combat["base_damage_min"] = max(1, _safe_int(quick_damage_min_input.value, _safe_int(combat.get("base_damage_min"), 3)))
                combat["base_damage_max"] = max(combat["base_damage_min"], _safe_int(quick_damage_max_input.value, _safe_int(combat.get("base_damage_max"), 6)))
                payload["combat"] = combat

                normalized = _normalize_monster_payload(payload)
                state["json"] = _json_pretty(normalized)
                monster_editor.value = state["json"]
                _set_status("JSON monstre mis a jour depuis le formulaire.", tone="ok")

            with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
                ui.button("JSON -> formulaire", on_click=_sync_quick_from_monster_json).props("outline no-caps")
                ui.button("Formulaire -> JSON", on_click=_apply_quick_to_monster_json).props("outline no-caps")

        with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
            def _format_monster_json() -> None:
                try:
                    state["json"] = _format_json_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"Formatage impossible: {exc}", tone="error")
                    return
                monster_editor.value = state["json"]
                _set_status("JSON formate.", tone="ok")
                _sync_quick_from_monster_json()

            def _validate_monster_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                    _normalize_monster_payload(payload)
                except Exception as exc:
                    ui.notify(f"Validation echouee: {exc}", color="negative")
                    _set_status(f"Validation echouee: {exc}", tone="error")
                    return
                ui.notify("Validation monstre OK.", color="positive")
                _set_status("Validation monstre OK.", tone="ok")

            def _reset_monster_template() -> None:
                state["json"] = _json_pretty(_monster_template())
                monster_editor.value = state["json"]
                ui.notify("Template monstre reinitialise.")
                _set_status("Template monstre vierge charge.", tone="ok")
                _sync_quick_from_monster_json()

            async def _prefill_monster_ai() -> None:
                prefill_btn.disable()
                _set_status("Generation IA monstre en cours...", tone="warn")
                try:
                    current = json.loads(state["json"]) if state["json"].strip() else _monster_template()
                    if not isinstance(current, dict):
                        current = _monster_template()
                except Exception:
                    current = _monster_template()

                base = _normalize_monster_payload(current)
                prompt = (
                    "Tu es un generateur de definitions de monstres fantasy. "
                    "Reponds en JSON valide uniquement. "
                    "Pas de markdown.\n"
                    f"Brief utilisateur: {state.get('brief') or 'Aucun brief.'}\n"
                    "Retourne un unique monstre jouable avec progression de combat coherent.\n"
                    "Schema:\n"
                    f"{_json_pretty(base)}"
                )

                try:
                    ai_payload = await _ai_json(prompt)
                    merged = _deep_merge_dict(base, ai_payload)
                    normalized = _normalize_monster_payload(merged)
                    state["json"] = _json_pretty(normalized)
                    monster_editor.value = state["json"]
                    ui.notify("Pre-remplissage IA monstre termine.", color="positive")
                    _set_status("Pre-remplissage IA monstre termine.", tone="ok")
                    _sync_quick_from_monster_json()
                except Exception as exc:
                    ui.notify(f"Pre-remplissage IA monstre echoue: {exc}", color="negative")
                    _set_status(f"Pre-remplissage IA echoue: {exc}", tone="error")
                finally:
                    prefill_btn.enable()

            def _save_monster() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"JSON invalide: {exc}", tone="error")
                    return

                normalized = _normalize_monster_payload(payload)
                path = MONSTERS_DIR / f"{normalized['id']}.json"
                try:
                    _write_json(path, normalized)
                except Exception as exc:
                    ui.notify(f"Sauvegarde monstre echouee: {exc}", color="negative")
                    _set_status(f"Sauvegarde echouee: {exc}", tone="error")
                    return

                state["json"] = _json_pretty(normalized)
                monster_editor.value = state["json"]
                _refresh_monster_options(normalized["id"])
                ui.notify(f"Monstre sauvegarde: {normalized['id']}", color="positive")
                _set_status(f"Monstre sauvegarde: {path.as_posix()}", tone="ok")
                _sync_quick_from_monster_json()

            ui.button("Valider JSON", on_click=_validate_monster_json).props("outline no-caps")
            ui.button("Formater JSON", on_click=_format_monster_json).props("outline no-caps")
            ui.button("Template vierge", on_click=_reset_monster_template).props("outline no-caps")
            prefill_btn = ui.button(
                "Pre-remplir IA",
                on_click=lambda: asyncio.create_task(_prefill_monster_ai()),
            ).props("outline no-caps")
            ui.button("Sauvegarder monstre", on_click=_save_monster).props("color=primary no-caps")

        with ui.expansion("Aide schema Monstre", value=False).classes("w-full"):
            ui.label("Champs pratiques: id, name, tier, combat, boss_modifiers, media.").classes("text-sm")
            ui.label("Les valeurs combat sont nettoyees automatiquement a la sauvegarde.").classes("text-sm")

        _refresh_monster_options()
        _sync_quick_from_monster_json()


def _build_item_tab() -> None:
    state = {
        "brief": "",
        "json": _json_pretty(_item_template()),
        "filter": "",
    }

    with ui.card().classes("w-full studio-card"):
        ui.label("Items").classes("text-lg font-semibold")
        ui.label("Template vide base sur data/items + pre-remplissage IA").classes("text-xs opacity-70")
        ui.label(f"Dossier cible: {ITEMS_DIR.as_posix()}").classes("text-xs opacity-60")

        counter_label = ui.label("").classes("text-xs opacity-75")
        status_label = ui.label("JSON pret.").classes("text-xs opacity-80")

        def _set_status(message: str, *, tone: str = "") -> None:
            style = "opacity:0.86;"
            if tone == "ok":
                style = "color:#86efac; opacity:0.95;"
            elif tone == "warn":
                style = "color:#fcd34d; opacity:0.95;"
            elif tone == "error":
                style = "color:#fda4af; opacity:0.95;"
            status_label.set_text(message)
            status_label.style(style)

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            item_select = ui.select(options=[], label="Item existant").classes("w-72")
            filter_input = ui.input("Filtre fichiers").classes("w-56")

            def _refresh_item_options(selected: str | None = None) -> None:
                options = _list_json_stems(ITEMS_DIR)
                query = str(state.get("filter") or "").strip().casefold()
                if query:
                    options = [name for name in options if query in name.casefold()]
                keep = selected if selected in options else (options[0] if options else "")
                _set_select_options(item_select, options, value=keep)
                counter_label.set_text(f"{len(options)} fichier(s) visible(s)")

            def _load_selected_item() -> None:
                selected = str(item_select.value or "").strip()
                if not selected:
                    ui.notify("Selectionne un item a charger.", color="warning")
                    _set_status("Aucun item selectionne.", tone="warn")
                    return
                path = ITEMS_DIR / f"{selected}.json"
                if not path.exists():
                    ui.notify("Fichier introuvable.", color="negative")
                    _refresh_item_options()
                    _set_status("Le fichier selectionne est introuvable.", tone="error")
                    return
                try:
                    payload = _read_json_dict(path)
                except Exception as exc:
                    ui.notify(f"Lecture impossible: {exc}", color="negative")
                    _set_status(f"Lecture impossible: {exc}", tone="error")
                    return
                state["json"] = _json_pretty(payload)
                item_editor.value = state["json"]
                ui.notify(f"Item charge: {selected}", color="positive")
                _set_status(f"Charge: {path.as_posix()}", tone="ok")

            filter_input.bind_value(state, "filter")
            filter_input.on_value_change(lambda e: _refresh_item_options(item_select.value))

            ui.button("Charger", on_click=_load_selected_item).props("outline no-caps")
            ui.button("Rafraichir", on_click=lambda: _refresh_item_options(item_select.value)).props("outline no-caps")

        ui.textarea(label="Brief IA (optionnel)").bind_value(state, "brief").classes("w-full")
        _hint_box("Exemple brief: 'Potion rare de dexterite, bonus temporaire +3 pendant 5 tours'.")

        item_editor = ui.textarea(label="JSON Item").bind_value(state, "json").classes("w-full studio-editor").style(
            "min-height: 400px;"
        )

        with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
            def _format_item_json() -> None:
                try:
                    state["json"] = _format_json_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"Formatage impossible: {exc}", tone="error")
                    return
                item_editor.value = state["json"]
                _set_status("JSON formate.", tone="ok")

            def _validate_item_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                    _normalize_item_payload(payload)
                except Exception as exc:
                    ui.notify(f"Validation echouee: {exc}", color="negative")
                    _set_status(f"Validation echouee: {exc}", tone="error")
                    return
                ui.notify("Validation item OK.", color="positive")
                _set_status("Validation item OK.", tone="ok")

            def _reset_item_template() -> None:
                state["json"] = _json_pretty(_item_template())
                item_editor.value = state["json"]
                ui.notify("Template item reinitialise.")
                _set_status("Template item vierge charge.", tone="ok")

            async def _prefill_item_ai() -> None:
                prefill_btn.disable()
                _set_status("Generation IA item en cours...", tone="warn")
                try:
                    current = json.loads(state["json"]) if state["json"].strip() else _item_template()
                    if not isinstance(current, dict):
                        current = _item_template()
                except Exception:
                    current = _item_template()

                schema = _normalize_item_payload(current)
                prompt = (
                    "Tu es un generateur d'items RPG. "
                    "Reponds en JSON valide uniquement. "
                    "Pas de markdown.\n"
                    f"Brief utilisateur: {state.get('brief') or 'Aucun brief.'}\n"
                    "Produis un item coherent avec rarete, valeur et effets.\n"
                    "Schema:\n"
                    f"{_json_pretty(schema)}"
                )

                try:
                    ai_payload = await _ai_json(prompt)
                    merged = _deep_merge_dict(schema, ai_payload)
                    normalized = _normalize_item_payload(merged)
                    state["json"] = _json_pretty(normalized)
                    item_editor.value = state["json"]
                    ui.notify("Pre-remplissage IA item termine.", color="positive")
                    _set_status("Pre-remplissage IA item termine.", tone="ok")
                except Exception as exc:
                    ui.notify(f"Pre-remplissage IA item echoue: {exc}", color="negative")
                    _set_status(f"Pre-remplissage IA echoue: {exc}", tone="error")
                finally:
                    prefill_btn.enable()

            def _save_item() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"JSON invalide: {exc}", tone="error")
                    return

                normalized = _normalize_item_payload(payload)
                try:
                    saved = _items_manager.save_item(normalized)
                except ItemDataError as exc:
                    ui.notify(f"Validation item echouee: {exc}", color="negative")
                    _set_status(f"Validation item echouee: {exc}", tone="error")
                    return
                except Exception as exc:
                    ui.notify(f"Sauvegarde item echouee: {exc}", color="negative")
                    _set_status(f"Sauvegarde echouee: {exc}", tone="error")
                    return

                saved_path = ITEMS_DIR / f"{saved.id}.json"
                try:
                    reloaded = _read_json_dict(saved_path)
                except Exception:
                    reloaded = normalized

                state["json"] = _json_pretty(reloaded)
                item_editor.value = state["json"]
                _refresh_item_options(saved.id)
                ui.notify(f"Item sauvegarde: {saved.id}", color="positive")
                _set_status(f"Item sauvegarde: {saved_path.as_posix()}", tone="ok")

            ui.button("Valider JSON", on_click=_validate_item_json).props("outline no-caps")
            ui.button("Formater JSON", on_click=_format_item_json).props("outline no-caps")
            ui.button("Template vierge", on_click=_reset_item_template).props("outline no-caps")
            prefill_btn = ui.button("Pre-remplir IA", on_click=lambda: asyncio.create_task(_prefill_item_ai())).props(
                "outline no-caps"
            )
            ui.button("Sauvegarder item", on_click=_save_item).props("color=primary no-caps")

        with ui.expansion("Aide schema Item", value=False).classes("w-full"):
            ui.label("Champs essentiels: id, name, type, rarity, value_gold.").classes("text-sm")
            ui.label("Pour un consommable: renseigne effects (heal, mana ou stat_buff).").classes("text-sm")

        _refresh_item_options()


def _build_merchant_tab() -> None:
    state = {
        "brief": "",
        "json": _json_pretty(_merchant_template()),
        "filter": "",
    }

    with ui.card().classes("w-full studio-card"):
        ui.label("Marchands").classes("text-lg font-semibold")
        ui.label("Base PNJ + liste d'items vendus. Sauvegarde dans data/merchants").classes("text-xs opacity-70")
        ui.label(f"Dossier cible: {MERCHANTS_DIR.as_posix()}").classes("text-xs opacity-60")

        counter_label = ui.label("").classes("text-xs opacity-75")
        status_label = ui.label("JSON pret.").classes("text-xs opacity-80")

        def _set_status(message: str, *, tone: str = "") -> None:
            style = "opacity:0.86;"
            if tone == "ok":
                style = "color:#86efac; opacity:0.95;"
            elif tone == "warn":
                style = "color:#fcd34d; opacity:0.95;"
            elif tone == "error":
                style = "color:#fda4af; opacity:0.95;"
            status_label.set_text(message)
            status_label.style(style)

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            merchant_select = ui.select(options=[], label="Marchand existant").classes("w-72")
            filter_input = ui.input("Filtre fichiers").classes("w-56")

            def _refresh_merchant_options(selected: str | None = None) -> None:
                options = _list_json_stems(MERCHANTS_DIR)
                query = str(state.get("filter") or "").strip().casefold()
                if query:
                    options = [name for name in options if query in name.casefold()]
                keep = selected if selected in options else (options[0] if options else "")
                _set_select_options(merchant_select, options, value=keep)
                counter_label.set_text(f"{len(options)} fichier(s) visible(s)")

            def _load_selected_merchant() -> None:
                selected = str(merchant_select.value or "").strip()
                if not selected:
                    ui.notify("Selectionne un marchand a charger.", color="warning")
                    _set_status("Aucun marchand selectionne.", tone="warn")
                    return
                path = MERCHANTS_DIR / f"{selected}.json"
                if not path.exists():
                    ui.notify("Fichier introuvable.", color="negative")
                    _refresh_merchant_options()
                    _set_status("Le fichier selectionne est introuvable.", tone="error")
                    return
                try:
                    payload = _read_json_dict(path)
                except Exception as exc:
                    ui.notify(f"Lecture impossible: {exc}", color="negative")
                    _set_status(f"Lecture impossible: {exc}", tone="error")
                    return
                normalized = _normalize_merchant_payload(payload)
                state["json"] = _json_pretty(normalized)
                merchant_editor.value = state["json"]
                ui.notify(f"Marchand charge: {selected}", color="positive")
                _set_status(f"Charge: {path.as_posix()}", tone="ok")

            filter_input.bind_value(state, "filter")
            filter_input.on_value_change(lambda e: _refresh_merchant_options(merchant_select.value))

            ui.button("Charger", on_click=_load_selected_merchant).props("outline no-caps")
            ui.button("Rafraichir", on_click=lambda: _refresh_merchant_options(merchant_select.value)).props("outline no-caps")

        ui.textarea(label="Brief IA (optionnel)").bind_value(state, "brief").classes("w-full")
        _hint_box("Exemple brief: 'Marchande d'alchimie, vend surtout potions de mana et reagents rares'.")

        item_catalog_label = ui.label("Catalogue items disponible: chargement...").classes("text-xs opacity-70")

        def _refresh_item_catalog_label() -> None:
            ids = _list_json_stems(ITEMS_DIR)
            preview = ", ".join(ids[:20])
            suffix = "" if len(ids) <= 20 else f" ... (+{len(ids) - 20})"
            if not ids:
                item_catalog_label.set_text("Catalogue items disponible: aucun item trouve.")
                return
            item_catalog_label.set_text(f"Catalogue items disponible ({len(ids)}): {preview}{suffix}")

        _refresh_item_catalog_label()

        merchant_editor = ui.textarea(label="JSON Marchand").bind_value(state, "json").classes("w-full studio-editor").style(
            "min-height: 430px;"
        )

        with ui.row().classes("w-full items-center gap-2 studio-toolbar"):
            def _format_merchant_json() -> None:
                try:
                    state["json"] = _format_json_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"Formatage impossible: {exc}", tone="error")
                    return
                merchant_editor.value = state["json"]
                _set_status("JSON formate.", tone="ok")

            def _validate_merchant_json() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                    normalized = _normalize_merchant_payload(payload)
                    NPCProfile.model_validate(normalized["npc_profile"])
                except Exception as exc:
                    ui.notify(f"Validation echouee: {exc}", color="negative")
                    _set_status(f"Validation echouee: {exc}", tone="error")
                    return
                ui.notify("Validation marchand OK.", color="positive")
                _set_status("Validation marchand OK.", tone="ok")

            def _reset_merchant_template() -> None:
                state["json"] = _json_pretty(_merchant_template())
                merchant_editor.value = state["json"]
                ui.notify("Template marchand reinitialise.")
                _set_status("Template marchand vierge charge.", tone="ok")

            async def _prefill_merchant_ai() -> None:
                prefill_btn.disable()
                _set_status("Generation IA marchand en cours...", tone="warn")
                try:
                    current = json.loads(state["json"]) if state["json"].strip() else _merchant_template()
                    if not isinstance(current, dict):
                        current = _merchant_template()
                except Exception:
                    current = _merchant_template()

                base = _normalize_merchant_payload(current)
                item_ids = _list_json_stems(ITEMS_DIR)
                item_ids_preview = item_ids[:120]

                prompt = (
                    "Tu es un generateur de marchand RPG fantasy. "
                    "Reponds en JSON valide uniquement. "
                    "Pas de markdown.\n"
                    f"Brief utilisateur: {state.get('brief') or 'Aucun brief.'}\n"
                    "Le champ npc_profile doit decrire un PNJ marchand coherent.\n"
                    "inventory doit contenir des item_id existants si possible.\n"
                    f"Liste item_id disponibles: {', '.join(item_ids_preview) if item_ids_preview else 'aucun'}\n"
                    "Schema:\n"
                    f"{_json_pretty(base)}"
                )

                try:
                    ai_payload = await _ai_json(prompt)
                    merged = _deep_merge_dict(base, ai_payload)
                    normalized = _normalize_merchant_payload(merged)
                    validated_npc = NPCProfile.model_validate(normalized["npc_profile"]).model_dump(by_alias=True)
                    normalized["npc_profile"] = validated_npc
                    state["json"] = _json_pretty(normalized)
                    merchant_editor.value = state["json"]
                    ui.notify("Pre-remplissage IA marchand termine.", color="positive")
                    _set_status("Pre-remplissage IA marchand termine.", tone="ok")
                except Exception as exc:
                    ui.notify(f"Pre-remplissage IA marchand echoue: {exc}", color="negative")
                    _set_status(f"Pre-remplissage IA echoue: {exc}", tone="error")
                finally:
                    prefill_btn.enable()

            def _save_merchant() -> None:
                try:
                    payload = _validate_json_dict_text(state["json"])
                except Exception as exc:
                    ui.notify(f"JSON invalide: {exc}", color="negative")
                    _set_status(f"JSON invalide: {exc}", tone="error")
                    return

                normalized = _normalize_merchant_payload(payload)

                try:
                    validated_npc = NPCProfile.model_validate(normalized["npc_profile"]).model_dump(by_alias=True)
                    normalized["npc_profile"] = validated_npc
                except ValidationError as exc:
                    ui.notify(f"npc_profile invalide: {exc}", color="negative")
                    _set_status(f"npc_profile invalide: {exc}", tone="error")
                    return

                unknown_items: list[str] = []
                existing_items = set(_list_json_stems(ITEMS_DIR))
                for row in normalized.get("inventory", []):
                    item_id = str(row.get("item_id") or "")
                    if item_id and item_id not in existing_items:
                        unknown_items.append(item_id)

                merchant_path = MERCHANTS_DIR / f"{normalized['id']}.json"
                try:
                    _write_json(merchant_path, normalized)
                except Exception as exc:
                    ui.notify(f"Sauvegarde marchand echouee: {exc}", color="negative")
                    _set_status(f"Sauvegarde echouee: {exc}", tone="error")
                    return

                try:
                    world_anchor = validated_npc.get("world_anchor") if isinstance(validated_npc.get("world_anchor"), dict) else {}
                    location_id = _slug(str(world_anchor.get("location_id") or normalized["location"]["location_id"]))
                    _npc_manager.save_profile(
                        str(validated_npc.get("label") or normalized["name"]),
                        validated_npc,
                        location_id=location_id,
                    )
                except Exception as exc:
                    ui.notify(f"Marchand sauvegarde, mais sync PNJ echouee: {exc}", color="warning")
                    _set_status(f"Sync PNJ partiel: {exc}", tone="warn")

                state["json"] = _json_pretty(normalized)
                merchant_editor.value = state["json"]
                _refresh_merchant_options(normalized["id"])

                if unknown_items:
                    preview = ", ".join(sorted(set(unknown_items))[:10])
                    ui.notify(
                        f"Marchand sauvegarde ({normalized['id']}). Item(s) inconnus: {preview}",
                        color="warning",
                    )
                    _set_status(f"Sauvegarde partielle ({normalized['id']}) avec items inconnus.", tone="warn")
                else:
                    ui.notify(f"Marchand sauvegarde: {normalized['id']}", color="positive")
                    _set_status(f"Marchand sauvegarde: {merchant_path.as_posix()}", tone="ok")

            ui.button("Valider JSON", on_click=_validate_merchant_json).props("outline no-caps")
            ui.button("Formater JSON", on_click=_format_merchant_json).props("outline no-caps")
            ui.button("Template vierge", on_click=_reset_merchant_template).props("outline no-caps")
            prefill_btn = ui.button(
                "Pre-remplir IA",
                on_click=lambda: asyncio.create_task(_prefill_merchant_ai()),
            ).props("outline no-caps")
            ui.button("Sauvegarder marchand", on_click=_save_merchant).props("color=primary no-caps")

        with ui.expansion("Aide schema Marchand", value=False).classes("w-full"):
            ui.label("name + location + npc_profile + inventory sont les blocs principaux.").classes("text-sm")
            ui.label("inventory attend des lignes {item_id, stock, price_multiplier}.").classes("text-sm")

        _refresh_merchant_options()


def _build_cities_xy_tab() -> None:
    world_state = {
        "payload": _load_cities_xy_payload(),
        "continent_id": "",
        "continent_name": "",
        "continent_description": "",
        "city_pick": "",
        "city_id": "",
        "city_name": "",
        "city_x": "0",
        "city_y": "0",
        "city_kind": "ville",
    }
    ui_state: dict[str, Any] = {"summary_label": None, "status_label": None}

    def _set_status(message: str, *, tone: str = "") -> None:
        label = ui_state.get("status_label")
        if label is None:
            return
        style = "opacity:0.86;"
        if tone == "ok":
            style = "color:#86efac; opacity:0.95;"
        elif tone == "warn":
            style = "color:#fcd34d; opacity:0.95;"
        elif tone == "error":
            style = "color:#fda4af; opacity:0.95;"
        label.set_text(message)
        label.style(style)

    def _refresh_summary_label() -> None:
        label = ui_state.get("summary_label")
        if label is None:
            return
        payload = world_state.get("payload") if isinstance(world_state.get("payload"), dict) else {}
        continents = payload.get("continents") if isinstance(payload.get("continents"), list) else []
        continent_count = 0
        city_total = 0
        for row in continents:
            if not isinstance(row, dict):
                continue
            continent_count += 1
            cities = row.get("cities") if isinstance(row.get("cities"), list) else []
            city_total += len([city for city in cities if isinstance(city, dict)])
        label.set_text(f"Continent(s): {continent_count} | Ville(s): {city_total}")

    def _continents() -> list[dict]:
        payload = world_state.get("payload") if isinstance(world_state.get("payload"), dict) else {}
        rows = payload.get("continents") if isinstance(payload.get("continents"), list) else []
        return [row for row in rows if isinstance(row, dict)]

    def _selected_continent() -> dict | None:
        selected_id = str(world_state.get("continent_id") or "").strip()
        for continent in _continents():
            cid = str(continent.get("id") or "").strip()
            if cid == selected_id:
                return continent
        return None

    def _continent_options() -> dict[str, str]:
        options: dict[str, str] = {}
        for continent in _continents():
            cid = str(continent.get("id") or "").strip()
            cname = str(continent.get("name") or cid).strip() or cid
            if cid:
                options[cid] = f"{cname} ({cid})"
        return options

    def _city_options(continent: dict | None) -> dict[str, str]:
        if not isinstance(continent, dict):
            return {}
        rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
        out: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            city_id = str(row.get("id") or "").strip()
            city_name = str(row.get("name") or city_id).strip() or city_id
            if city_id:
                out[city_id] = f"{city_name} ({city_id})"
        return out

    def _refresh_continent_editor() -> None:
        continent = _selected_continent()
        if not isinstance(continent, dict):
            world_state["continent_name"] = ""
            world_state["continent_description"] = ""
        else:
            world_state["continent_name"] = str(continent.get("name") or "")
            world_state["continent_description"] = str(continent.get("description") or "")

        continent_name_input.value = world_state["continent_name"]
        continent_description_input.value = world_state["continent_description"]
        _refresh_city_select()
        _render_city_map.refresh()
        _render_city_list.refresh()
        _refresh_summary_label()

    def _refresh_continent_select(preferred_id: str | None = None) -> None:
        options = _continent_options()
        if not options:
            world_state["payload"] = _default_cities_xy_payload()
            options = _continent_options()

        selected = str(preferred_id or world_state.get("continent_id") or "").strip()
        if selected not in options:
            selected = next(iter(options.keys()), "")

        world_state["continent_id"] = selected
        setter = getattr(continent_select, "set_options", None)
        if callable(setter):
            setter(options)
        else:
            continent_select.options = options
        continent_select.value = selected
        _refresh_continent_editor()

    def _refresh_city_select(preferred_id: str | None = None) -> None:
        options = _city_options(_selected_continent())
        selected = str(preferred_id or world_state.get("city_pick") or "").strip()
        if selected not in options:
            selected = ""

        world_state["city_pick"] = selected
        setter = getattr(city_select, "set_options", None)
        if callable(setter):
            setter(options)
        else:
            city_select.options = options
        city_select.value = selected

    def _set_city_form(row: dict | None) -> None:
        if not isinstance(row, dict):
            world_state["city_id"] = ""
            world_state["city_name"] = ""
            world_state["city_x"] = "0"
            world_state["city_y"] = "0"
            world_state["city_kind"] = "ville"
        else:
            world_state["city_id"] = str(row.get("id") or "")
            world_state["city_name"] = str(row.get("name") or "")
            world_state["city_x"] = str(_safe_int(row.get("x"), 0))
            world_state["city_y"] = str(_safe_int(row.get("y"), 0))
            world_state["city_kind"] = str(row.get("kind") or "ville")

        city_id_input.value = world_state["city_id"]
        city_name_input.value = world_state["city_name"]
        city_x_input.value = world_state["city_x"]
        city_y_input.value = world_state["city_y"]
        city_kind_input.value = world_state["city_kind"]

    def _save_world_payload() -> None:
        payload = _sanitize_cities_xy_payload(world_state.get("payload") if isinstance(world_state.get("payload"), dict) else {})
        try:
            _write_json(CITIES_XY_PATH, payload)
        except Exception as exc:
            ui.notify(f"Sauvegarde carte monde echouee: {exc}", color="negative")
            _set_status(f"Sauvegarde echouee: {exc}", tone="error")
            return
        world_state["payload"] = payload
        _refresh_continent_select(world_state.get("continent_id"))
        ui.notify(f"Carte monde sauvegardee: {CITIES_XY_PATH.as_posix()}", color="positive")
        _set_status(f"Carte monde sauvegardee: {CITIES_XY_PATH.as_posix()}", tone="ok")

    with ui.card().classes("w-full studio-card"):
        ui.label("Carte Monde: Villes en coordonnees X/Y").classes("text-lg font-semibold")
        ui.label("Ajoute des continents, villes et positions pour preparer les futurs lieux.").classes("text-xs opacity-70")
        ui.label(f"Fichier cible: {CITIES_XY_PATH.as_posix()}").classes("text-xs opacity-60")
        _hint_box("Utilise cette page pour planifier continents et villes. Les coordonnees X/Y servent de base pour les futures cartes.")
        ui_state["summary_label"] = ui.label("").classes("text-xs opacity-80")
        ui_state["status_label"] = ui.label("JSON monde pret.").classes("text-xs opacity-85")

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            continent_select = ui.select(options={}, label="Continent").classes("w-80")

            def _on_continent_changed() -> None:
                world_state["continent_id"] = str(continent_select.value or "")
                _refresh_continent_editor()

            continent_select.on_value_change(lambda e: _on_continent_changed())
            ui.button("Sauvegarder carte monde", on_click=_save_world_payload).props("color=primary no-caps")

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            continent_id_input = ui.input("Continent id").classes("w-56")
            continent_name_input = ui.input("Continent nom").classes("w-64")
            continent_description_input = ui.input("Description").classes("w-96")

            def _upsert_continent() -> None:
                raw_id = str(continent_id_input.value or world_state.get("continent_id") or "").strip()
                cid = _slug(raw_id, fallback="continent")
                name = str(continent_name_input.value or cid).strip() or cid
                description = str(continent_description_input.value or "").strip()

                payload = world_state.get("payload") if isinstance(world_state.get("payload"), dict) else _default_cities_xy_payload()
                continents = payload.get("continents") if isinstance(payload.get("continents"), list) else []
                found = False
                for row in continents:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("id") or "") != cid:
                        continue
                    row["name"] = name
                    row["description"] = description
                    found = True
                    break
                if not found:
                    continents.append({"id": cid, "name": name, "description": description, "cities": []})

                payload["continents"] = continents
                world_state["payload"] = _sanitize_cities_xy_payload(payload)
                _refresh_continent_select(cid)
                ui.notify(f"Continent {'mis a jour' if found else 'ajoute'}: {cid}", color="positive")
                _set_status(f"Continent {'mis a jour' if found else 'ajoute'}: {cid}", tone="ok")

            def _delete_continent() -> None:
                cid = str(world_state.get("continent_id") or "").strip()
                if not cid:
                    ui.notify("Selectionne un continent.", color="warning")
                    _set_status("Selectionne un continent avant suppression.", tone="warn")
                    return
                payload = world_state.get("payload") if isinstance(world_state.get("payload"), dict) else {}
                continents = payload.get("continents") if isinstance(payload.get("continents"), list) else []
                next_continents = [row for row in continents if isinstance(row, dict) and str(row.get("id") or "") != cid]
                if len(next_continents) == len(continents):
                    ui.notify("Continent introuvable.", color="warning")
                    _set_status("Continent introuvable.", tone="warn")
                    return
                payload["continents"] = next_continents
                world_state["payload"] = _sanitize_cities_xy_payload(payload)
                _refresh_continent_select()
                _set_city_form(None)
                ui.notify(f"Continent supprime: {cid}", color="positive")
                _set_status(f"Continent supprime: {cid}", tone="ok")

            ui.button("Ajouter / MAJ continent", on_click=_upsert_continent).props("outline no-caps")
            ui.button("Supprimer continent", on_click=_delete_continent).props("outline no-caps color=negative")

        ui.separator()

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            city_select = ui.select(options={}, label="Ville existante (continent actif)").classes("w-96")

            def _load_selected_city() -> None:
                continent = _selected_continent()
                if not isinstance(continent, dict):
                    ui.notify("Selectionne un continent.", color="warning")
                    _set_status("Selectionne d'abord un continent.", tone="warn")
                    return
                city_id = str(city_select.value or "").strip()
                if not city_id:
                    ui.notify("Selectionne une ville.", color="warning")
                    _set_status("Selectionne une ville a charger.", tone="warn")
                    return
                rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("id") or "") == city_id:
                        _set_city_form(row)
                        world_state["city_pick"] = city_id
                        ui.notify(f"Ville chargee: {city_id}", color="positive")
                        _set_status(f"Ville chargee: {city_id}", tone="ok")
                        return
                ui.notify("Ville introuvable.", color="warning")
                _set_status("Ville introuvable dans ce continent.", tone="warn")

            ui.button("Charger ville", on_click=_load_selected_city).props("outline no-caps")
            ui.button("Rafraichir villes", on_click=lambda: _refresh_city_select(world_state.get("city_pick"))).props(
                "outline no-caps"
            )

        with ui.row().classes("w-full items-end gap-2 studio-toolbar"):
            city_id_input = ui.input("Ville id").classes("w-56")
            city_name_input = ui.input("Ville nom").classes("w-64")
            city_x_input = ui.input("X").classes("w-32")
            city_y_input = ui.input("Y").classes("w-32")
            city_kind_input = ui.input("Type").classes("w-40")

            def _upsert_city() -> None:
                continent = _selected_continent()
                if not isinstance(continent, dict):
                    ui.notify("Selectionne un continent.", color="warning")
                    _set_status("Selectionne d'abord un continent.", tone="warn")
                    return

                city_id = _slug(str(city_id_input.value or city_name_input.value or "ville"), fallback="ville")
                city_name = str(city_name_input.value or city_id).strip() or city_id
                city_x = _safe_int(city_x_input.value, 0)
                city_y = _safe_int(city_y_input.value, 0)
                city_kind = str(city_kind_input.value or "ville").strip() or "ville"

                rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
                found = False
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("id") or "") != city_id:
                        continue
                    row["name"] = city_name
                    row["x"] = city_x
                    row["y"] = city_y
                    row["kind"] = city_kind
                    found = True
                    break
                if not found:
                    rows.append(
                        {
                            "id": city_id,
                            "name": city_name,
                            "x": city_x,
                            "y": city_y,
                            "kind": city_kind,
                        }
                    )

                continent["cities"] = sorted(
                    [row for row in rows if isinstance(row, dict)],
                    key=lambda row: str(row.get("name") or row.get("id") or "").casefold(),
                )

                world_state["payload"] = _sanitize_cities_xy_payload(world_state.get("payload") or {})
                _set_city_form(
                    {
                        "id": city_id,
                        "name": city_name,
                        "x": city_x,
                        "y": city_y,
                        "kind": city_kind,
                    }
                )
                _refresh_city_select(city_id)
                _render_city_map.refresh()
                _render_city_list.refresh()
                ui.notify(f"Ville {'mise a jour' if found else 'ajoutee'}: {city_name}", color="positive")
                _set_status(f"Ville {'mise a jour' if found else 'ajoutee'}: {city_name}", tone="ok")

            def _delete_city() -> None:
                continent = _selected_continent()
                if not isinstance(continent, dict):
                    ui.notify("Selectionne un continent.", color="warning")
                    _set_status("Selectionne d'abord un continent.", tone="warn")
                    return
                city_id = _slug(str(city_id_input.value or city_select.value or ""), fallback="")
                if not city_id:
                    ui.notify("Selectionne une ville a supprimer.", color="warning")
                    _set_status("Selectionne une ville a supprimer.", tone="warn")
                    return

                rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
                next_rows = [row for row in rows if isinstance(row, dict) and str(row.get("id") or "") != city_id]
                if len(next_rows) == len(rows):
                    ui.notify("Ville introuvable.", color="warning")
                    _set_status("Ville introuvable.", tone="warn")
                    return

                continent["cities"] = next_rows
                world_state["payload"] = _sanitize_cities_xy_payload(world_state.get("payload") or {})
                _refresh_city_select()
                _set_city_form(None)
                _render_city_map.refresh()
                _render_city_list.refresh()
                ui.notify(f"Ville supprimee: {city_id}", color="positive")
                _set_status(f"Ville supprimee: {city_id}", tone="ok")

            def _clear_city_form() -> None:
                _set_city_form(None)

            ui.button("Ajouter / MAJ ville", on_click=_upsert_city).props("outline no-caps")
            ui.button("Supprimer ville", on_click=_delete_city).props("outline no-caps color=negative")
            ui.button("Vider formulaire", on_click=_clear_city_form).props("outline no-caps")

        @ui.refreshable
        def _render_city_map() -> None:
            continent = _selected_continent()
            if not isinstance(continent, dict):
                ui.label("Aucun continent selectionne.").classes("text-sm opacity-70")
                return

            rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
            cities = [row for row in rows if isinstance(row, dict)]
            if not cities:
                ui.label("Aucune ville sur ce continent.").classes("text-sm opacity-70")
                return

            xs = [_safe_int(row.get("x"), 0) for row in cities]
            ys = [_safe_int(row.get("y"), 0) for row in cities]

            min_x = min(xs)
            max_x = max(xs)
            min_y = min(ys)
            max_y = max(ys)

            if min_x == max_x:
                min_x -= 1
                max_x += 1
            if min_y == max_y:
                min_y -= 1
                max_y += 1

            width = 920
            height = 430
            pad = 36

            def _px_x(x: int) -> float:
                return pad + ((x - min_x) / float(max_x - min_x)) * (width - (2 * pad))

            def _px_y(y: int) -> float:
                return pad + ((y - min_y) / float(max_y - min_y)) * (height - (2 * pad))

            marks: list[str] = []
            for row in cities:
                cx = _px_x(_safe_int(row.get("x"), 0))
                cy = _px_y(_safe_int(row.get("y"), 0))
                name = str(row.get("name") or row.get("id") or "Ville")
                kind = str(row.get("kind") or "ville")
                marks.append(
                    f"<circle cx='{cx:.2f}' cy='{cy:.2f}' r='6' fill='#7dd3fc' stroke='#0f172a' stroke-width='1.4' />"
                )
                marks.append(
                    f"<text x='{cx + 9:.2f}' y='{cy - 9:.2f}' font-size='12' fill='#e2e8f0'>{name} ({kind})</text>"
                )

            svg = (
                f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' "
                "style='background:linear-gradient(180deg,#102031,#0f172a); border:1px solid rgba(255,255,255,0.15); border-radius:10px;'>"
                f"<text x='12' y='20' font-size='12' fill='#cbd5e1'>X [{min_x}..{max_x}]</text>"
                f"<text x='12' y='36' font-size='12' fill='#cbd5e1'>Y [{min_y}..{max_y}]</text>"
                f"{''.join(marks)}"
                "</svg>"
            )
            ui.html(svg).classes("w-full")

        _render_city_map()

        @ui.refreshable
        def _render_city_list() -> None:
            continent = _selected_continent()
            if not isinstance(continent, dict):
                return
            rows = continent.get("cities") if isinstance(continent.get("cities"), list) else []
            cities = [row for row in rows if isinstance(row, dict)]
            if not cities:
                ui.label("Aucune ville a lister pour ce continent.").classes("text-xs opacity-70")
                return
            ui.label("Liste rapide des villes").classes("text-sm font-semibold").style("margin-top: 4px;")

            def _load_city_from_row(row: dict) -> None:
                _set_city_form(row)
                _set_status(f"Ville chargee: {row.get('id')}", tone="ok")

            with ui.column().classes("w-full gap-1"):
                for row in cities[:120]:
                    city_id = str(row.get("id") or "")
                    city_name = str(row.get("name") or city_id)
                    city_x = _safe_int(row.get("x"), 0)
                    city_y = _safe_int(row.get("y"), 0)
                    city_kind = str(row.get("kind") or "ville")
                    label = f"{city_name} [{city_id}] - X:{city_x} Y:{city_y} ({city_kind})"
                    ui.button(
                        label,
                        on_click=lambda row=row: _load_city_from_row(row),
                    ).props("outline dense no-caps").classes("w-full").style("justify-content:flex-start; text-align:left;")

        _render_city_list()

        with ui.expansion("JSON carte monde", value=False).classes("w-full"):
            world_json = ui.textarea(label="cities_xy.json").classes("w-full studio-editor").style("min-height: 260px;")

            def _refresh_world_json() -> None:
                world_json.value = _json_pretty(world_state.get("payload") if isinstance(world_state.get("payload"), dict) else {})

            def _apply_world_json() -> None:
                try:
                    payload = json.loads(str(world_json.value or "{}"))
                    if not isinstance(payload, dict):
                        raise ValueError("JSON objet attendu")
                    world_state["payload"] = _sanitize_cities_xy_payload(payload)
                except Exception as exc:
                    ui.notify(f"JSON monde invalide: {exc}", color="negative")
                    _set_status(f"JSON monde invalide: {exc}", tone="error")
                    return
                _refresh_continent_select(world_state.get("continent_id"))
                _render_city_map.refresh()
                _render_city_list.refresh()
                ui.notify("JSON monde applique.", color="positive")
                _set_status("JSON monde applique.", tone="ok")

            with ui.row().classes("w-full items-center gap-2"):
                ui.button("Rafraichir JSON", on_click=_refresh_world_json).props("outline no-caps")
                ui.button("Appliquer JSON", on_click=_apply_world_json).props("outline no-caps")

            _refresh_world_json()

        _refresh_continent_select()
        _set_city_form(None)


@ui.page("/studio")
def studio_page() -> None:
    _build_studio_page()
