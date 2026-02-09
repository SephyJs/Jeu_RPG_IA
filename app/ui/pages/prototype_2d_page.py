from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nicegui import ui

from app.gamemaster.models import model_for
from app.gamemaster.ollama_client import OllamaClient


TILE_SIZE = 44
VIEW_COLS = 11
VIEW_ROWS = 8
MAP_GEN_WIDTH = 28
MAP_GEN_HEIGHT = 15
TILES_BASE_URL = "/assets/Tiles/TilesNamed"
CITY_BUILDINGS_CONFIG_PATH = Path("data/villes_batiments_minimums.json")
DEFAULT_CITY_KEY = "lumeria"
PROTOTYPE_EXTERIOR_SCALE_DEFAULT = 0.16
PROTOTYPE_EXTERIOR_MIN_BUILDINGS_DEFAULT = 2
PROTOTYPE_EXTERIOR_MAX_BUILDINGS_DEFAULT = 9

WORLD_MAP = [
    "############################",
    "#...........,,,,..........#",
    "#..#####....,,,,...####...#",
    "#..#...#...........#..#...#",
    "#..#...#....~~~....#..#...#",
    "#..###.#....~~~....####...#",
    "#......#...............#..#",
    "####...#####..#####....#..#",
    "#..........#..#........#..#",
    "#..,,,,....#..#..,,,,..#..#",
    "#..,,,,....#..#..,,,,..#..#",
    "#..........#..#...........#",
    "#...######.#..#.######....#",
    "#.........................#",
    "############################",
]

TILE_COLORS = {
    "#": "#594332",
    ".": "#d8c4a0",
    ",": "#7a9c61",
    "~": "#3d6ea1",
}

WALKABLE_TILES = {".", ","}
START_POS = (2, 2)
QUEST_MARKER = (20, 11)
NPCS = [
    {
        "x": 8,
        "y": 4,
        "name": "Aubergiste",
        "lines": [
            "Bienvenue voyageur. Une soupe chaude et un lit sec, ca te dit ?",
            "Les routes ne sont plus sures la nuit. Reste a l'abri.",
            "Si tu cherches des rumeurs, parle aux clients pres du comptoir.",
        ],
    },
    {
        "x": 16,
        "y": 9,
        "name": "Pretresse",
        "lines": [
            "La lumiere veille encore sur ce lieu.",
            "Les marques anciennes pres du puits cachent un passage.",
            "Si ton coeur hesite, avance quand meme. La peur ment souvent.",
        ],
    },
]

TILE_FILES_BY_SYMBOL = {
    # Sol herbeux
    ".": ["ground_grass_01.png", "ground_grass_02.png", "ground_grass_03.png"],
    # Sol terre / chemin
    ",": ["ground_path_01.png", "ground_path_02.png", "ground_path_03.png", "ground_path_04.png"],
    # Eau / zone bloquante
    "~": ["water_01.png", "water_02.png", "water_03.png", "water_04.png", "water_05.png"],
}

MAP_PRESETS = {
    "exterieur": {
        "label": "Exterieur village",
        "description": "Zone ouverte pour aller d'un batiment a l'autre, avec chemins et obstacles.",
    },
    "batiment": {
        "label": "Interieur batiment",
        "description": "Interieur de batiment (auberge/maison) avec murs, salles, tables/comptoir suggerees.",
    },
    "donjon": {
        "label": "Etage donjon",
        "description": "Piece(s) de donjon avec couloirs, salles et points de choke.",
    },
}

WALL_TILE_FILL = "wall_fill_center.png"
WALL_TILE_SIDE_TOP = "wall_side_top.png"
WALL_TILE_SIDE_BOTTOM = "wall_side_bottom.png"
WALL_TILE_SIDE_LEFT = "wall_side_left.png"
WALL_TILE_SIDE_RIGHT = "wall_side_right.png"
WALL_TILE_CORNER_TOP_LEFT = "wall_corner_outer_top_left.png"
WALL_TILE_CORNER_TOP_RIGHT = "wall_corner_outer_top_right.png"
WALL_TILE_CORNER_BOTTOM_LEFT = "wall_corner_outer_bottom_left.png"
WALL_TILE_CORNER_BOTTOM_RIGHT = "wall_corner_outer_bottom_right.png"

DEFAULT_EXTERIOR_BUILDING_STYLE = {
    "roof_left": "roof_red_01.png",
    "roof_mid": "roof_red_02.png",
    "roof_right": "roof_red_03.png",
    "roof_peak": "roof_red_peak_01.png",
    "wall_left": "wall_brown_01.png",
    "wall_mid": "wall_brown_02.png",
    "wall_right": "wall_brown_03.png",
    "door": "door_wood_03.png",
}

EXTERIOR_BUILDING_STYLE_BY_TYPE = {
    "maison": {
        "roof_left": "roof_blue_01.png",
        "roof_mid": "roof_blue_02.png",
        "roof_right": "roof_blue_03.png",
        "roof_peak": "roof_blue_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_04.png",
    },
    "auberge": {
        "roof_left": "roof_red_01.png",
        "roof_mid": "roof_red_02.png",
        "roof_right": "roof_red_03.png",
        "roof_peak": "roof_red_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_03.png",
    },
    "taverne": {
        "roof_left": "roof_red_01.png",
        "roof_mid": "roof_red_02.png",
        "roof_right": "roof_red_03.png",
        "roof_peak": "roof_red_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_01.png",
    },
    "temple": {
        "roof_left": "roof_blue_01.png",
        "roof_mid": "roof_blue_02.png",
        "roof_right": "roof_blue_03.png",
        "roof_peak": "roof_blue_peak_01.png",
        "wall_left": "wall_blue_01.png",
        "wall_mid": "wall_blue_02.png",
        "wall_right": "wall_blue_03.png",
        "door": "door_metal_01.png",
    },
    "forge": {
        "roof_left": "roof_red_01.png",
        "roof_mid": "roof_red_02.png",
        "roof_right": "roof_red_03.png",
        "roof_peak": "roof_red_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_02.png",
    },
    "marchand_general": {
        "roof_left": "roof_blue_01.png",
        "roof_mid": "roof_blue_02.png",
        "roof_right": "roof_blue_03.png",
        "roof_peak": "roof_blue_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_01.png",
    },
    "marchand_esclaves": {
        "roof_left": "roof_red_01.png",
        "roof_mid": "roof_red_02.png",
        "roof_right": "roof_red_03.png",
        "roof_peak": "roof_red_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_02.png",
    },
    "herboriste": {
        "roof_left": "roof_blue_01.png",
        "roof_mid": "roof_blue_02.png",
        "roof_right": "roof_blue_03.png",
        "roof_peak": "roof_blue_peak_01.png",
        "wall_left": "wall_brown_01.png",
        "wall_mid": "wall_brown_02.png",
        "wall_right": "wall_brown_03.png",
        "door": "door_wood_04.png",
    },
    "caserne": {
        "roof_left": "roof_blue_01.png",
        "roof_mid": "roof_blue_02.png",
        "roof_right": "roof_blue_03.png",
        "roof_peak": "roof_blue_peak_01.png",
        "wall_left": "wall_blue_01.png",
        "wall_mid": "wall_blue_02.png",
        "wall_right": "wall_blue_03.png",
        "door": "door_metal_01.png",
    },
    "bibliotheque": {
        "roof_left": "roof_red_01.png",
        "roof_mid": "roof_red_02.png",
        "roof_right": "roof_red_03.png",
        "roof_peak": "roof_red_peak_01.png",
        "wall_left": "wall_blue_01.png",
        "wall_mid": "wall_blue_02.png",
        "wall_right": "wall_blue_03.png",
        "door": "door_wood_03.png",
    },
}

EXTERIOR_BUILDING_SIZE_BY_TYPE = {
    "maison": (4, 4),
    "auberge": (6, 5),
    "taverne": (6, 5),
    "temple": (7, 6),
    "forge": (5, 5),
    "marchand_general": (4, 4),
    "marchand_esclaves": (4, 4),
    "herboriste": (4, 4),
    "caserne": (6, 5),
    "bibliotheque": (6, 5),
}

ALLOWED_MAP_SYMBOLS = {"#", ".", ",", "~"}
SYMBOL_ALIASES = {
    "0": ".",
    "1": "#",
    "2": ",",
    "3": "~",
    "x": "#",
    "X": "#",
    "w": "~",
    "W": "~",
    " ": ".",
    "-": ".",
}


def _clamp(value: int, minimum: int, maximum: int) -> int:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _to_non_negative_int(value: object, fallback: int = 0) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return max(0, int(fallback))
    return max(0, ivalue)


def _to_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _city_label_from_key(city_key: str) -> str:
    return str(city_key or "inconnue").replace("_", " ").strip().title()


def _read_city_buildings_config() -> dict:
    try:
        raw = json.loads(CITY_BUILDINGS_CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _city_options_from_config(config: dict) -> dict[str, str]:
    cities_raw = config.get("cities")
    cities = cities_raw if isinstance(cities_raw, dict) else {}
    if not cities:
        return {DEFAULT_CITY_KEY: _city_label_from_key(DEFAULT_CITY_KEY)}

    options: dict[str, str] = {}
    for key in sorted(cities.keys()):
        city_raw = cities.get(key)
        city = city_raw if isinstance(city_raw, dict) else {}
        label = str(city.get("label") or _city_label_from_key(str(key)))
        options[str(key)] = label
    return options


def _sample_building_plan(expanded: list[str], target_count: int) -> list[str]:
    if target_count <= 0:
        return []
    if not expanded:
        return ["maison" for _ in range(target_count)]
    if len(expanded) <= target_count:
        result = list(expanded)
        while len(result) < target_count:
            result.append(expanded[-1])
        return result

    step = len(expanded) / float(target_count)
    sampled: list[str] = []
    for i in range(target_count):
        idx = min(len(expanded) - 1, int(i * step))
        sampled.append(expanded[idx])
    return sampled


def _compute_city_context(config: dict, city_key: str) -> dict[str, object]:
    cities_raw = config.get("cities")
    cities = cities_raw if isinstance(cities_raw, dict) else {}
    selected_city_key = str(city_key or "")
    if (selected_city_key not in cities) and cities:
        selected_city_key = sorted(cities.keys())[0]
    if not selected_city_key:
        selected_city_key = DEFAULT_CITY_KEY

    city_raw = cities.get(selected_city_key)
    city_cfg = city_raw if isinstance(city_raw, dict) else {}
    city_label = str(city_cfg.get("label") or _city_label_from_key(selected_city_key))

    size = str(city_cfg.get("size") or "petite_ville")
    size_profiles_raw = config.get("size_profiles")
    size_profiles = size_profiles_raw if isinstance(size_profiles_raw, dict) else {}
    size_profile_raw = size_profiles.get(size)
    size_profile = size_profile_raw if isinstance(size_profile_raw, dict) else {}

    override_raw = city_cfg.get("minimum_buildings_override")
    override = override_raw if isinstance(override_raw, dict) else {}

    forbidden_by_size_raw = config.get("forbidden_by_size")
    forbidden_by_size = forbidden_by_size_raw if isinstance(forbidden_by_size_raw, dict) else {}
    forbidden_raw = forbidden_by_size.get(size)
    forbidden = {str(v) for v in forbidden_raw} if isinstance(forbidden_raw, list) else set()

    catalog_raw = config.get("building_catalog")
    catalog = [str(v) for v in catalog_raw] if isinstance(catalog_raw, list) else []

    all_keys = set(catalog)
    all_keys.update(str(k) for k in size_profile.keys())
    all_keys.update(str(k) for k in override.keys())
    if not all_keys:
        all_keys = {"maison"}

    merge_strategy = str(config.get("merge_strategy") or "max_profile_and_override")
    minimums: dict[str, int] = {}
    for key in sorted(all_keys):
        profile_value = _to_non_negative_int(size_profile.get(key), 0)
        override_value = _to_non_negative_int(override.get(key), 0)

        if merge_strategy == "profile_plus_override":
            value = profile_value + override_value
        elif merge_strategy == "override_only":
            value = override_value if key in override else profile_value
        else:
            value = max(profile_value, override_value)

        if key in forbidden:
            value = 0
        minimums[key] = value

    nonzero_minimums = [(k, v) for k, v in minimums.items() if v > 0]
    nonzero_minimums.sort(key=lambda item: (-item[1], item[0]))

    expanded: list[str] = []
    for building_type, count in nonzero_minimums:
        expanded.extend([building_type for _ in range(count)])
    required_total = len(expanded)

    scale = _to_float(config.get("prototype_exterieur_scale"), PROTOTYPE_EXTERIOR_SCALE_DEFAULT)
    min_buildings = _to_non_negative_int(config.get("prototype_exterieur_min_buildings"), PROTOTYPE_EXTERIOR_MIN_BUILDINGS_DEFAULT)
    max_buildings = _to_non_negative_int(config.get("prototype_exterieur_max_buildings"), PROTOTYPE_EXTERIOR_MAX_BUILDINGS_DEFAULT)
    if min_buildings <= 0:
        min_buildings = PROTOTYPE_EXTERIOR_MIN_BUILDINGS_DEFAULT
    if max_buildings < min_buildings:
        max_buildings = min_buildings

    if not expanded:
        expanded = ["maison" for _ in range(min_buildings)]
        required_total = len(expanded)

    scaled_total = int(round(required_total * scale))
    target_total = _clamp(scaled_total, min_buildings, max_buildings)
    building_plan = _sample_building_plan(expanded, target_total)

    return {
        "city_key": selected_city_key,
        "city_label": city_label,
        "size": size,
        "minimums": minimums,
        "nonzero_minimums": nonzero_minimums,
        "required_total": required_total,
        "target_total": target_total,
        "building_plan": building_plan,
    }


def _minimums_to_text(nonzero_minimums: list[tuple[str, int]], limit: int = 8) -> str:
    if not nonzero_minimums:
        return "aucun minimum specifique"
    parts = [f"{k}={v}" for k, v in nonzero_minimums[:limit]]
    return ", ".join(parts)


def _build_prototype_page() -> None:
    llm = OllamaClient()
    city_buildings_config = _read_city_buildings_config()
    city_options = _city_options_from_config(city_buildings_config)
    initial_city_key = DEFAULT_CITY_KEY if DEFAULT_CITY_KEY in city_options else next(iter(city_options))
    initial_city_context = _compute_city_context(city_buildings_config, initial_city_key)

    active_preset_label = None
    city_summary_label = None

    state = {
        "x": START_POS[0],
        "y": START_POS[1],
        "hint": "Utilise fleches, ZQSD ou WASD.",
        "npc_line_index": {},
        "map_rows": list(WORLD_MAP),
        "map_preset": "exterieur",
        "building_tiles": {},
        "city_buildings_config": city_buildings_config,
        "city_options": city_options,
        "city_key": initial_city_context["city_key"],
        "city_context": initial_city_context,
        "last_placed_buildings": 0,
    }

    def _city_summary_line(context: dict[str, object]) -> str:
        city_label = str(context.get("city_label") or _city_label_from_key(str(context.get("city_key") or DEFAULT_CITY_KEY)))
        size = str(context.get("size") or "inconnue")
        required_total = _to_non_negative_int(context.get("required_total"), 0)
        target_total = _to_non_negative_int(context.get("target_total"), 0)
        minimums = context.get("nonzero_minimums")
        minimums_text = _minimums_to_text(minimums if isinstance(minimums, list) else [])
        return (
            f"Ville: {city_label} ({size}) | Min declares: {required_total} | "
            f"Cible prototype: {target_total} | {minimums_text}"
        )

    def _refresh_city_context(city_key: str | None = None) -> dict[str, object]:
        if city_key is None:
            city_key = str(state.get("city_key") or DEFAULT_CITY_KEY)
        context = _compute_city_context(state["city_buildings_config"], str(city_key))
        state["city_key"] = str(context.get("city_key") or DEFAULT_CITY_KEY)
        state["city_context"] = context
        return context

    def _update_city_ui_labels() -> None:
        context = state.get("city_context")
        if not isinstance(context, dict):
            context = _refresh_city_context()
        summary = _city_summary_line(context)
        if city_summary_label is not None:
            city_summary_label.set_text(summary)

        if active_preset_label is not None:
            preset_key = str(state.get("map_preset") or "exterieur")
            preset = MAP_PRESETS.get(preset_key, MAP_PRESETS["exterieur"])
            if preset_key == "exterieur":
                city_label = str(context.get("city_label") or _city_label_from_key(str(context.get("city_key") or "")))
                active_preset_label.set_text(f"Preset actif: {preset['label']} | Ville: {city_label}")
            else:
                active_preset_label.set_text(f"Preset actif: {preset['label']}")

    def map_size() -> tuple[int, int]:
        rows = state["map_rows"]
        width = max(len(row) for row in rows) if rows else 0
        height = len(rows)
        return width, height

    def _critical_points() -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = [(int(START_POS[0]), int(START_POS[1])), (int(QUEST_MARKER[0]), int(QUEST_MARKER[1]))]
        for npc in NPCS:
            try:
                points.append((int(npc.get("x", -1)), int(npc.get("y", -1))))
            except Exception:
                continue
        return points

    def tile_at(x: int, y: int) -> str:
        rows = state["map_rows"]
        if 0 <= y < len(rows):
            row = rows[y]
            if 0 <= x < len(row):
                return row[x]
        return "#"

    def tile_sprite_url(symbol: str, x: int, y: int) -> str:
        building_override = state["building_tiles"].get((x, y))
        if isinstance(building_override, str) and building_override:
            return f"{TILES_BASE_URL}/{building_override}"

        if symbol == "#":
            tile_file = _wall_tile_file(x, y)
            return f"{TILES_BASE_URL}/{tile_file}"

        options = TILE_FILES_BY_SYMBOL.get(symbol) or TILE_FILES_BY_SYMBOL["."]
        idx = (x * 73856093 + y * 19349663) % len(options)
        tile_file = options[idx]
        return f"{TILES_BASE_URL}/{tile_file}"

    def _is_wall(x: int, y: int) -> bool:
        return tile_at(x, y) == "#"

    def _wall_tile_file(x: int, y: int) -> str:
        top = _is_wall(x, y - 1)
        bottom = _is_wall(x, y + 1)
        left = _is_wall(x - 1, y)
        right = _is_wall(x + 1, y)
        open_top = not top
        open_bottom = not bottom
        open_left = not left
        open_right = not right
        open_count = int(open_top) + int(open_bottom) + int(open_left) + int(open_right)

        # Le tileset n'a pas de sprite pour les "caps" ou les T-junctions.
        # On force dans ces cas le remplissage central pour eviter les faux coins.
        if open_count >= 3:
            return WALL_TILE_FILL

        if open_count == 2:
            if open_top and open_left and bottom and right:
                return WALL_TILE_CORNER_TOP_LEFT
            if open_top and open_right and bottom and left:
                return WALL_TILE_CORNER_TOP_RIGHT
            if open_bottom and open_left and top and right:
                return WALL_TILE_CORNER_BOTTOM_LEFT
            if open_bottom and open_right and top and left:
                return WALL_TILE_CORNER_BOTTOM_RIGHT
            return WALL_TILE_FILL

        if open_count == 1:
            if open_top:
                return WALL_TILE_SIDE_TOP
            if open_bottom:
                return WALL_TILE_SIDE_BOTTOM
            if open_left:
                return WALL_TILE_SIDE_LEFT
            if open_right:
                return WALL_TILE_SIDE_RIGHT

        return WALL_TILE_FILL

    def camera_origin() -> tuple[int, int]:
        map_width, map_height = map_size()
        max_cam_x = max(0, map_width - VIEW_COLS)
        max_cam_y = max(0, map_height - VIEW_ROWS)
        cam_x = _clamp(state["x"] - (VIEW_COLS // 2), 0, max_cam_x)
        cam_y = _clamp(state["y"] - (VIEW_ROWS // 2), 0, max_cam_y)
        return cam_x, cam_y

    def is_walkable(x: int, y: int) -> bool:
        return tile_at(x, y) in WALKABLE_TILES

    def npc_on_tile(x: int, y: int) -> dict | None:
        for npc in NPCS:
            if (int(npc["x"]), int(npc["y"])) == (x, y):
                return npc
        return None

    def nearby_npc() -> dict | None:
        for npc in NPCS:
            if abs(state["x"] - int(npc["x"])) + abs(state["y"] - int(npc["y"])) <= 1:
                return npc
        return None

    def marker_for(world_x: int, world_y: int) -> tuple[str, str] | None:
        if world_x == state["x"] and world_y == state["y"]:
            return ("J", "#ff5f7a")
        if (world_x, world_y) == QUEST_MARKER:
            return ("Q", "#f5d061")
        for npc in NPCS:
            if (world_x, world_y) == (int(npc["x"]), int(npc["y"])):
                return ("N", "#7f6df2")
        return None

    def _coerce_rows(raw_tiles: object) -> list[str]:
        if isinstance(raw_tiles, str):
            lines = [line.rstrip() for line in raw_tiles.splitlines() if line.strip()]
            return lines

        if isinstance(raw_tiles, list):
            rows: list[str] = []
            for row in raw_tiles:
                if isinstance(row, str):
                    rows.append(row)
                elif isinstance(row, list):
                    joined = "".join(str(cell)[:1] if str(cell) else "." for cell in row)
                    rows.append(joined)
            return rows
        return []

    def _normalize_row_text(row: str) -> str:
        out: list[str] = []
        for ch in str(row or ""):
            mapped = SYMBOL_ALIASES.get(ch, ch)
            out.append(mapped if mapped in ALLOWED_MAP_SYMBOLS else ".")
        return "".join(out)

    def _normalize_generated_rows(rows: list[str]) -> list[str]:
        normalized_rows: list[str] = []
        for raw in rows[:MAP_GEN_HEIGHT]:
            row = _normalize_row_text(raw)[:MAP_GEN_WIDTH]
            if len(row) < MAP_GEN_WIDTH:
                row = row + ("#" * (MAP_GEN_WIDTH - len(row)))
            normalized_rows.append(row)

        while len(normalized_rows) < MAP_GEN_HEIGHT:
            normalized_rows.append("#" * MAP_GEN_WIDTH)

        grid = [list(r) for r in normalized_rows]

        # Bordure fermée
        for x in range(MAP_GEN_WIDTH):
            grid[0][x] = "#"
            grid[-1][x] = "#"
        for y in range(MAP_GEN_HEIGHT):
            grid[y][0] = "#"
            grid[y][-1] = "#"

        # Points essentiels ouverts + un peu d'espace autour
        for x, y in _critical_points():
            xi, yi = int(x), int(y)
            if 0 < xi < MAP_GEN_WIDTH - 1 and 0 < yi < MAP_GEN_HEIGHT - 1:
                grid[yi][xi] = "."
                for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    xx, yy = xi + ox, yi + oy
                    if 0 < xx < MAP_GEN_WIDTH - 1 and 0 < yy < MAP_GEN_HEIGHT - 1 and grid[yy][xx] == "#":
                        grid[yy][xx] = "."

        return ["".join(row) for row in grid]

    def _wall_neighbor_count(grid: list[list[str]], x: int, y: int) -> int:
        count = 0
        for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            xx, yy = x + ox, y + oy
            if 0 <= yy < MAP_GEN_HEIGHT and 0 <= xx < MAP_GEN_WIDTH and grid[yy][xx] == "#":
                count += 1
        return count

    def _wall_diagonal_neighbor_count(grid: list[list[str]], x: int, y: int) -> int:
        count = 0
        for ox, oy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            xx, yy = x + ox, y + oy
            if 0 <= yy < MAP_GEN_HEIGHT and 0 <= xx < MAP_GEN_WIDTH and grid[yy][xx] == "#":
                count += 1
        return count

    def _cleanup_wall_islands(rows: list[str], preset_key: str) -> list[str]:
        grid = [list(r) for r in rows]
        replacement = "," if preset_key == "batiment" else "."

        # 3 passes pour nettoyer davantage les murs "iles".
        for _ in range(3):
            to_clear: list[tuple[int, int]] = []
            for y in range(1, MAP_GEN_HEIGHT - 1):
                for x in range(1, MAP_GEN_WIDTH - 1):
                    if grid[y][x] != "#":
                        continue
                    n = _wall_neighbor_count(grid, x, y)
                    d = _wall_diagonal_neighbor_count(grid, x, y)
                    # Supprime les murs isoles, pointes et connexions uniquement diagonales.
                    if n == 0 or (n == 1 and d <= 1):
                        to_clear.append((x, y))
            for x, y in to_clear:
                grid[y][x] = replacement

        return ["".join(r) for r in grid]

    def _regularize_wall_shapes(rows: list[str], preset_key: str) -> list[str]:
        grid = [list(r) for r in rows]
        replacement = "," if preset_key == "batiment" else "."
        protected = set(_critical_points())

        # Ferme les micro-trous de murs et retire les formes peu lisibles.
        for _ in range(2):
            to_wall: list[tuple[int, int]] = []
            to_floor: list[tuple[int, int]] = []

            for y in range(1, MAP_GEN_HEIGHT - 1):
                for x in range(1, MAP_GEN_WIDTH - 1):
                    cell = grid[y][x]
                    neighbors = _wall_neighbor_count(grid, x, y)

                    if cell == "#":
                        diagonals = _wall_diagonal_neighbor_count(grid, x, y)
                        if neighbors == 0 or (neighbors == 1 and diagonals == 0):
                            to_floor.append((x, y))
                        continue

                    if (x, y) in protected:
                        continue
                    if neighbors >= 3:
                        to_wall.append((x, y))

            for x, y in to_floor:
                grid[y][x] = replacement
            for x, y in to_wall:
                grid[y][x] = "#"

        return ["".join(r) for r in grid]

    def _wall_artifacts(rows: list[str]) -> tuple[int, int]:
        grid = [list(r) for r in rows]
        isolated = 0
        spikes = 0
        for y in range(1, MAP_GEN_HEIGHT - 1):
            for x in range(1, MAP_GEN_WIDTH - 1):
                if grid[y][x] != "#":
                    continue
                neighbors = _wall_neighbor_count(grid, x, y)
                if neighbors == 0:
                    isolated += 1
                elif neighbors == 1:
                    spikes += 1
        return isolated, spikes

    def _stable_seed(value: str) -> int:
        total = 0
        for idx, ch in enumerate(value):
            total += (idx + 1) * ord(ch)
        return total

    def _stamp_exterior_buildings(
        grid: list[list[str]],
        city_context: dict[str, object],
    ) -> tuple[dict[tuple[int, int], str], int]:
        if not grid or not grid[0]:
            return {}, 0

        map_h = len(grid)
        map_w = len(grid[0])
        building_tiles: dict[tuple[int, int], str] = {}
        protected = set(_critical_points())
        occupied = set(protected)

        plan_raw = city_context.get("building_plan")
        building_plan = [str(v) for v in plan_raw] if isinstance(plan_raw, list) else []
        if not building_plan:
            building_plan = ["maison", "maison"]

        city_key = str(city_context.get("city_key") or DEFAULT_CITY_KEY)
        placed_count = 0

        def _can_place(bx: int, by: int, bw: int, bh: int) -> bool:
            if bw < 4 or bh < 4:
                return False
            if bx <= 0 or by <= 0 or (bx + bw) >= (map_w - 1) or (by + bh) >= (map_h - 1):
                return False

            # Vérifie l'emprise + une marge d'une case pour éviter les bâtiments collés.
            for yy in range(by - 1, by + bh + 1):
                for xx in range(bx - 1, bx + bw + 1):
                    if xx <= 0 or yy <= 0 or xx >= (map_w - 1) or yy >= (map_h - 1):
                        return False
                    if (xx, yy) in occupied:
                        return False
            return True

        for idx, building_type in enumerate(building_plan):
            bw, bh = EXTERIOR_BUILDING_SIZE_BY_TYPE.get(building_type, EXTERIOR_BUILDING_SIZE_BY_TYPE["maison"])
            style = EXTERIOR_BUILDING_STYLE_BY_TYPE.get(building_type, DEFAULT_EXTERIOR_BUILDING_STYLE)

            candidates: list[tuple[int, int]] = []
            for by in range(1, map_h - bh - 1):
                for bx in range(1, map_w - bw - 1):
                    candidates.append((bx, by))
            if not candidates:
                continue

            seed = _stable_seed(f"{city_key}:{building_type}:{idx}")
            start = seed % len(candidates)
            ordered = candidates[start:] + candidates[:start]

            chosen: tuple[int, int] | None = None
            for bx, by in ordered:
                if _can_place(bx, by, bw, bh):
                    chosen = (bx, by)
                    break
            if chosen is None:
                continue

            bx, by = chosen
            door_x = bx + (bw // 2)
            door_y = by + bh - 1

            # Imprime un batiment compact (bloquant), sauf porte.
            for yy in range(by, by + bh):
                for xx in range(bx, bx + bw):
                    grid[yy][xx] = "#"

            for yy in range(by, by + bh):
                for xx in range(bx, bx + bw):
                    lx = xx - bx
                    ly = yy - by

                    if ly == 0:
                        if lx == 0:
                            sprite = style["roof_left"]
                        elif lx == bw - 1:
                            sprite = style["roof_right"]
                        else:
                            sprite = style["roof_mid"]
                    elif ly == 1:
                        if lx == 0:
                            sprite = style["roof_left"]
                        elif lx == bw - 1:
                            sprite = style["roof_right"]
                        elif lx == (bw // 2):
                            sprite = style["roof_peak"]
                        else:
                            sprite = style["roof_mid"]
                    else:
                        if lx == 0:
                            sprite = style["wall_left"]
                        elif lx == bw - 1:
                            sprite = style["wall_right"]
                        else:
                            sprite = style["wall_mid"]

                    building_tiles[(xx, yy)] = sprite
                    occupied.add((xx, yy))

            # Réserve la marge pour les prochains bâtiments.
            for yy in range(by - 1, by + bh + 1):
                for xx in range(bx - 1, bx + bw + 1):
                    if 0 <= xx < map_w and 0 <= yy < map_h:
                        occupied.add((xx, yy))

            # Ouvre la porte et trace un petit acces en chemin.
            if (door_x, door_y) not in protected:
                grid[door_y][door_x] = "."
                building_tiles[(door_x, door_y)] = style["door"]
                for yy in range(door_y + 1, min(map_h - 1, door_y + 4)):
                    if grid[yy][door_x] == "#":
                        grid[yy][door_x] = ","

                # Petit repère visuel pour les commerces.
                if building_type in {"marchand_general", "marchand_esclaves", "herboriste"}:
                    sign_x = min(map_w - 2, door_x + 1)
                    sign_y = door_y
                    if (sign_x, sign_y) not in protected:
                        building_tiles[(sign_x, sign_y)] = "sign_post_01.png"
                        if grid[sign_y][sign_x] == "#":
                            grid[sign_y][sign_x] = "."

            placed_count += 1

        return building_tiles, placed_count

    def _enforce_preset_style(rows: list[str], preset_key: str) -> list[str]:
        # Garde-fou: on travaille toujours sur une grille compacte MAP_GEN_WIDTH x MAP_GEN_HEIGHT.
        normalized_rows = _normalize_generated_rows(rows)
        grid = [list(r) for r in normalized_rows]
        state["building_tiles"] = {}

        if preset_key == "batiment":
            # Intérieur: pas d'eau, dominante sol intérieur.
            for y in range(1, MAP_GEN_HEIGHT - 1):
                for x in range(1, MAP_GEN_WIDTH - 1):
                    if grid[y][x] == "~":
                        grid[y][x] = ","
            # Renforce quelques séparations de salles si la map est trop ouverte.
            wall_count = sum(1 for y in range(MAP_GEN_HEIGHT) for x in range(MAP_GEN_WIDTH) if grid[y][x] == "#")
            if wall_count < 95:
                for x in range(5, MAP_GEN_WIDTH - 5):
                    grid[5][x] = "#"
                for x in range(8, MAP_GEN_WIDTH - 8):
                    grid[10][x] = "#"
                for y in range(2, MAP_GEN_HEIGHT - 2):
                    grid[y][14] = "#"
                for door in ((14, 4), (14, 8), (14, 12)):
                    grid[door[1]][door[0]] = ","

        elif preset_key == "donjon":
            # Donjon: très peu d'herbe/terre, dominante pierre et murs.
            for y in range(1, MAP_GEN_HEIGHT - 1):
                for x in range(1, MAP_GEN_WIDTH - 1):
                    if grid[y][x] in {",", "~"}:
                        grid[y][x] = "."
            wall_ratio = sum(1 for y in range(MAP_GEN_HEIGHT) for x in range(MAP_GEN_WIDTH) if grid[y][x] == "#") / (
                MAP_GEN_WIDTH * MAP_GEN_HEIGHT
            )
            if wall_ratio < 0.38:
                for y in range(3, MAP_GEN_HEIGHT - 3):
                    if y % 2 == 0:
                        for x in range(3, MAP_GEN_WIDTH - 3):
                            if x % 5 != 0:
                                grid[y][x] = "#"
                # Ouvre des couloirs verticaux réguliers.
                for x in (4, 9, 14, 19, 24):
                    for y in range(2, MAP_GEN_HEIGHT - 2):
                        grid[y][x] = "."

        else:
            # Extérieur: pas trop de murs compacts.
            wall_ratio = sum(1 for y in range(MAP_GEN_HEIGHT) for x in range(MAP_GEN_WIDTH) if grid[y][x] == "#") / (
                MAP_GEN_WIDTH * MAP_GEN_HEIGHT
            )
            if wall_ratio > 0.45:
                for y in range(2, MAP_GEN_HEIGHT - 2):
                    for x in range(2, MAP_GEN_WIDTH - 2):
                        if grid[y][x] == "#" and (x + y) % 3 != 0:
                            grid[y][x] = "."

        # Reforce bordures et points clés.
        for x in range(MAP_GEN_WIDTH):
            grid[0][x] = "#"
            grid[-1][x] = "#"
        for y in range(MAP_GEN_HEIGHT):
            grid[y][0] = "#"
            grid[y][-1] = "#"

        for x, y in _critical_points():
            xi, yi = int(x), int(y)
            if 0 < xi < MAP_GEN_WIDTH - 1 and 0 < yi < MAP_GEN_HEIGHT - 1:
                if grid[yi][xi] == "#":
                    grid[yi][xi] = "."

        styled = ["".join(r) for r in grid]
        styled = _cleanup_wall_islands(styled, preset_key)
        styled = _regularize_wall_shapes(styled, preset_key)
        styled = _cleanup_wall_islands(styled, preset_key)
        styled_grid = [list(r) for r in styled]
        if preset_key == "exterieur":
            city_context = state.get("city_context")
            if not isinstance(city_context, dict):
                city_context = _refresh_city_context()
            building_tiles, placed_count = _stamp_exterior_buildings(styled_grid, city_context)
            state["building_tiles"] = building_tiles
            state["last_placed_buildings"] = placed_count
        else:
            state["last_placed_buildings"] = 0
        for x, y in _critical_points():
            xi, yi = int(x), int(y)
            if 0 < xi < MAP_GEN_WIDTH - 1 and 0 < yi < MAP_GEN_HEIGHT - 1:
                styled_grid[yi][xi] = "."
                for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    xx, yy = xi + ox, yi + oy
                    if 0 < xx < MAP_GEN_WIDTH - 1 and 0 < yy < MAP_GEN_HEIGHT - 1 and styled_grid[yy][xx] == "#":
                        styled_grid[yy][xx] = "."
        styled = ["".join(r) for r in styled_grid]
        return styled

    def _looks_like_preset(rows: list[str], preset_key: str) -> bool:
        rows = _normalize_generated_rows(rows)
        total = MAP_GEN_WIDTH * MAP_GEN_HEIGHT
        walls = sum(1 for r in rows for ch in r if ch == "#")
        waters = sum(1 for r in rows for ch in r if ch == "~")
        dirt = sum(1 for r in rows for ch in r if ch == ",")
        wall_ratio = walls / total
        isolated, spikes = _wall_artifacts(rows)

        if preset_key == "batiment":
            return 0.25 <= wall_ratio <= 0.70 and waters <= 8 and dirt >= 20 and isolated == 0 and spikes <= 24
        if preset_key == "donjon":
            return wall_ratio >= 0.35 and waters <= 6 and dirt <= 20 and isolated == 0 and spikes <= 18
        return wall_ratio <= 0.55 and isolated == 0 and spikes <= 28

    def _extract_rows_from_text(text: str) -> list[str]:
        rows: list[str] = []
        for line in (text or "").splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if all((ch in ALLOWED_MAP_SYMBOLS) or (ch in SYMBOL_ALIASES) for ch in candidate):
                rows.append(candidate)
        return rows

    def _fallback_rows_for_preset(preset_key: str) -> list[str]:
        if preset_key == "batiment":
            grid = [["#" for _ in range(MAP_GEN_WIDTH)] for _ in range(MAP_GEN_HEIGHT)]
            for y in range(1, MAP_GEN_HEIGHT - 1):
                for x in range(1, MAP_GEN_WIDTH - 1):
                    grid[y][x] = ","
            for x in range(4, 24):
                grid[4][x] = "#"
            for x in range(8, 20):
                grid[9][x] = "#"
            for y in range(1, MAP_GEN_HEIGHT - 2):
                grid[y][13] = "#"
            for door_y in (3, 7, 11):
                grid[door_y][13] = "."
            return ["".join(r) for r in grid]

        if preset_key == "donjon":
            grid = [["#" for _ in range(MAP_GEN_WIDTH)] for _ in range(MAP_GEN_HEIGHT)]
            for x in range(2, 26):
                grid[2][x] = "."
            for y in range(2, 13):
                grid[y][2] = "."
            for x in range(2, 21):
                grid[12][x] = "."
            for y in range(5, 13):
                grid[y][20] = "."
            for x in range(6, 21):
                grid[5][x] = "."
            for y in range(5, 11):
                grid[y][10] = "."
            for y in range(7, 10):
                for x in range(22, 26):
                    grid[y][x] = ","
            return ["".join(r) for r in grid]

        return list(WORLD_MAP)

    def _valid_generated_rows(rows: list[str]) -> bool:
        if len(rows) != MAP_GEN_HEIGHT:
            return False
        allowed = {"#", ".", ",", "~"}
        for row in rows:
            if not isinstance(row, str) or len(row) != MAP_GEN_WIDTH:
                return False
            if any(ch not in allowed for ch in row):
                return False

        # Bordure fermée
        top = rows[0]
        bottom = rows[-1]
        if any(ch != "#" for ch in top) or any(ch != "#" for ch in bottom):
            return False
        for r in rows:
            if r[0] != "#" or r[-1] != "#":
                return False

        # Cases importantes jouables
        for x, y in _critical_points():
            if rows[int(y)][int(x)] == "#":
                return False
        return True

    async def _generate_map_with_mistral() -> None:
        preset_key = str(state.get("map_preset") or "exterieur")
        preset = MAP_PRESETS.get(preset_key, MAP_PRESETS["exterieur"])
        city_context = _refresh_city_context(str(state.get("city_key") or DEFAULT_CITY_KEY))
        state["city_context"] = city_context

        state["hint"] = f"Generation {preset['label']} par Mistral en cours..."
        hint_label.set_text(state["hint"])

        schema = {
            "width": MAP_GEN_WIDTH,
            "height": MAP_GEN_HEIGHT,
            "tiles": ["#" * MAP_GEN_WIDTH for _ in range(MAP_GEN_HEIGHT)],
        }
        structure_hint = {
            "exterieur": "Place des blocs de murs compacts (maisons/enceintes), evite les murs en zigzag.",
            "batiment": "Utilise des pieces rectangulaires et des couloirs droits avec portes ouvertes.",
            "donjon": "Compose des salles/couloirs orthogonaux, avec intersections lisibles.",
        }.get(preset_key, "Structure lisible en vue de dessus.")
        extra_constraint = ""
        if preset_key == "exterieur":
            city_label = str(city_context.get("city_label") or _city_label_from_key(str(city_context.get("city_key") or "")))
            city_size = str(city_context.get("size") or "inconnue")
            required_text = _minimums_to_text(
                city_context.get("nonzero_minimums") if isinstance(city_context.get("nonzero_minimums"), list) else [],
                limit=10,
            )
            target_total = _to_non_negative_int(city_context.get("target_total"), 2)
            extra_constraint = (
                f"- Ville cible: {city_label} (taille: {city_size}).\n"
                f"- Minimums declares de la ville: {required_text}.\n"
                f"- Sur ce prototype, prevoir au moins {target_total} emprises de batiments (rectangles de #) avec acces.\n"
            )
        prompt = (
            "Tu generes une carte 2D vue de dessus pour un RPG.\n"
            "Reponds en JSON valide UNIQUEMENT, sans markdown.\n"
            f"Dimensions obligatoires: width={MAP_GEN_WIDTH}, height={MAP_GEN_HEIGHT}.\n"
            f"Type de map voulu: {preset['label']}.\n"
            f"Objectif de scene: {preset['description']}\n"
            f"Style attendu: {structure_hint}\n"
            "Symboles autorises:\n"
            "- # = mur (bloquant)\n"
            "- . = sol praticable\n"
            "- , = sol praticable (variante)\n"
            "- ~ = eau (bloquant)\n"
            "Contraintes:\n"
            "- La bordure exterieure doit etre 100% en #.\n"
            "- Cases qui doivent etre praticables (pas #): (2,2), (20,11), (8,4), (16,9).\n"
            "- Les murs doivent etre orthogonaux (pas de liaison diagonale seule).\n"
            "- Evite les murs isoles (# sans voisin cardinal).\n"
            f"{extra_constraint}"
            "- Produire une structure lisible correspondant au type de map.\n"
            "Schema:\n"
            f"{json.dumps(schema)}\n"
        )

        try:
            raw = await llm.generate(
                model=model_for("rules"),
                prompt=prompt,
                temperature=0.35,
                num_ctx=4096,
                num_predict=900,
            )

            text = (raw or "").strip()
            if not (text.startswith("{") and text.endswith("}")):
                start = text.find("{")
                end = text.rfind("}")
                text = text[start : end + 1] if start != -1 and end != -1 and end > start else "{}"

            parse_note = ""
            try:
                payload = json.loads(text)
                rows_raw: list[str] = []
                for key in ("tiles", "map", "rows", "layout"):
                    rows_raw = _coerce_rows(payload.get(key))
                    if rows_raw:
                        break
                if not rows_raw:
                    raise ValueError("Aucune ligne exploitable dans le JSON")
            except Exception:
                rows_raw = _extract_rows_from_text(raw or "")
                if rows_raw:
                    parse_note = " (format libre normalise)"
                else:
                    rows_raw = _fallback_rows_for_preset(preset_key)
                    parse_note = " (fallback local applique)"

            strict_ok = _valid_generated_rows(rows_raw)
            rows = rows_raw if strict_ok else _normalize_generated_rows(rows_raw)
            rows = _enforce_preset_style(rows, preset_key)

            if not _looks_like_preset(rows, preset_key):
                rows = _enforce_preset_style(_fallback_rows_for_preset(preset_key), preset_key)
                parse_note = f"{parse_note} (style preset force)"

            state["map_rows"] = rows
            state["x"], state["y"] = START_POS
            if strict_ok:
                state["hint"] = f"Nouvelle map '{preset['label']}' generee par Mistral."
            else:
                state["hint"] = f"Nouvelle map '{preset['label']}' generee (normalisation auto){parse_note}."
            if preset_key == "exterieur":
                placed = _to_non_negative_int(state.get("last_placed_buildings"), 0)
                target = _to_non_negative_int(city_context.get("target_total"), 0)
                city_label = str(city_context.get("city_label") or _city_label_from_key(str(city_context.get("city_key") or "")))
                state["hint"] = f"{state['hint']} | Ville: {city_label} | Batiments poses: {placed}/{target}"
            hint_label.set_text(state["hint"])
            npc_speech_card.set_visibility(False)
            update_npc_ui()
            render_world.refresh()
            _update_city_ui_labels()
        except Exception as e:
            try:
                rows = _enforce_preset_style(_fallback_rows_for_preset(preset_key), preset_key)
                state["map_rows"] = rows
                state["x"], state["y"] = START_POS
                state["hint"] = f"Echec generation map: {e} | fallback '{preset['label']}' applique."
                hint_label.set_text(state["hint"])
                npc_speech_card.set_visibility(False)
                update_npc_ui()
                render_world.refresh()
                _update_city_ui_labels()
            except Exception as e2:
                state["hint"] = f"Echec generation map: {e} (fallback KO: {e2})"
                hint_label.set_text(state["hint"])
                render_world.refresh()

    def generate_with_preset(preset_key: str) -> None:
        if preset_key == "auberge":
            preset_key = "batiment"
        if preset_key not in MAP_PRESETS:
            preset_key = "exterieur"
        state["map_preset"] = preset_key
        _refresh_city_context(str(state.get("city_key") or DEFAULT_CITY_KEY))
        _update_city_ui_labels()
        asyncio.create_task(_generate_map_with_mistral())

    def set_city(city_key: str) -> None:
        options = state.get("city_options")
        if isinstance(options, dict) and city_key in options:
            state["city_key"] = city_key
        else:
            state["city_key"] = next(iter(options)) if isinstance(options, dict) and options else DEFAULT_CITY_KEY
        _refresh_city_context(str(state.get("city_key") or DEFAULT_CITY_KEY))
        _update_city_ui_labels()

    with ui.column().classes("w-full items-center gap-3"):
        ui.label("Prototype 2D - Vue du dessus").classes("text-2xl font-semibold")
        ui.label("Page test pour visualiser un debut de gameplay 2D.").classes("opacity-80")
        ui.link("Retour au jeu principal (/game)", "/game")

        with ui.row().classes("w-full justify-center gap-2"):
            ui.button("Reset position", on_click=lambda: reset_player()).props("outline dense no-caps")
            ui.button("Retour /game", on_click=lambda: ui.navigate.to("/game")).props("outline dense no-caps")
        with ui.row().classes("w-full justify-center items-center gap-2"):
            ui.button(
                "Generer Exterieur",
                on_click=lambda: generate_with_preset("exterieur"),
            ).props("outline dense no-caps")
            ui.button(
                "Generer Batiment",
                on_click=lambda: generate_with_preset("batiment"),
            ).props("outline dense no-caps")
            ui.button(
                "Generer Donjons",
                on_click=lambda: generate_with_preset("donjon"),
            ).props("outline dense no-caps")
        with ui.row().classes("w-full justify-center items-center gap-2"):
            ui.label("Ville:").classes("text-sm")
            ui.select(
                options=state["city_options"],
                value=state["city_key"],
                on_change=lambda e: set_city(str(e.value or state["city_key"])),
            ).props("outlined dense")
        active_preset_label = ui.label("").classes("text-xs opacity-70")
        city_summary_label = ui.label("").classes("text-xs opacity-70")

        hint_label = ui.label(state["hint"]).classes("text-sm opacity-80")
        pos_label = ui.label("").classes("text-xs opacity-70")

        @ui.refreshable
        def render_world() -> None:
            cam_x, cam_y = camera_origin()
            with ui.element("div").style(
                "display:grid;"
                f"grid-template-columns: repeat({VIEW_COLS}, {TILE_SIZE}px);"
                f"grid-template-rows: repeat({VIEW_ROWS}, {TILE_SIZE}px);"
                "gap:2px; padding:8px; border-radius:12px; background:#0f1115; border:2px solid #2b313a;"
            ):
                for view_y in range(VIEW_ROWS):
                    for view_x in range(VIEW_COLS):
                        world_x = cam_x + view_x
                        world_y = cam_y + view_y
                        tile = tile_at(world_x, world_y)
                        tile_color = TILE_COLORS.get(tile, "#1e1e1e")
                        tile_url = tile_sprite_url(tile, world_x, world_y)
                        marker = marker_for(world_x, world_y)

                        with ui.element("div").style(
                            f"width:{TILE_SIZE}px; height:{TILE_SIZE}px; background:{tile_color};"
                            f"background-image:url('{tile_url}'); background-size:cover; background-position:center;"
                            "image-rendering: pixelated;"
                            "border-radius:4px; display:flex; align-items:center; justify-content:center;"
                            "box-shadow: inset 0 0 0 1px rgba(0,0,0,.22);"
                        ):
                            if marker:
                                letter, color = marker
                                with ui.element("div").style(
                                    "width:22px; height:22px; border-radius:999px;"
                                    f"background:{color}; display:flex; align-items:center; justify-content:center;"
                                    "color:#111; font-weight:700; font-size:11px;"
                                ):
                                    ui.label(letter)

            pos_label.set_text(f"Position: ({state['x']}, {state['y']}) | Camera: ({cam_x}, {cam_y})")

        with ui.row().classes("items-center gap-2"):
            with ui.column().classes("items-center gap-1"):
                ui.button("Haut", on_click=lambda: move_player(0, -1)).props("dense no-caps")
                with ui.row().classes("gap-1"):
                    ui.button("Gauche", on_click=lambda: move_player(-1, 0)).props("dense no-caps")
                    ui.button("Bas", on_click=lambda: move_player(0, 1)).props("dense no-caps")
                    ui.button("Droite", on_click=lambda: move_player(1, 0)).props("dense no-caps")

            with ui.column().classes("gap-1 text-xs opacity-70"):
                ui.label("Legende:")
                ui.label("J: Joueur")
                ui.label("N: PNJ")
                ui.label("Q: Quete")

        talk_button = ui.button("Parler", on_click=lambda: talk_to_nearby_npc()).props("outline dense no-caps")
        talk_button.set_visibility(False)

        with ui.card().classes("w-full max-w-xl rounded-xl shadow-sm") as npc_speech_card:
            npc_speaker_label = ui.label("").classes("font-semibold")
            npc_message_label = ui.label("").classes("text-sm")
        npc_speech_card.set_visibility(False)

    def update_npc_ui() -> None:
        npc = nearby_npc()
        if npc:
            talk_button.set_text(f"Parler a {npc['name']}")
            talk_button.set_visibility(True)
        else:
            talk_button.set_visibility(False)

    def talk_to_nearby_npc() -> None:
        npc = nearby_npc()
        if not npc:
            state["hint"] = "Aucun PNJ a portee."
            hint_label.set_text(state["hint"])
            update_npc_ui()
            return

        npc_name = str(npc["name"])
        lines = npc.get("lines", [])
        if not isinstance(lines, list) or not lines:
            line = "Le PNJ te regarde en silence."
        else:
            idx = int(state["npc_line_index"].get(npc_name, 0))
            line = str(lines[idx % len(lines)])
            state["npc_line_index"][npc_name] = idx + 1

        state["hint"] = f"{npc_name} te parle."
        hint_label.set_text(state["hint"])
        npc_speaker_label.set_text(f"{npc_name} :")
        npc_message_label.set_text(line)
        npc_speech_card.set_visibility(True)
        update_npc_ui()

    def move_player(dx: int, dy: int) -> None:
        nx = state["x"] + dx
        ny = state["y"] + dy

        blocking_npc = npc_on_tile(nx, ny)
        if blocking_npc:
            state["hint"] = f"{blocking_npc['name']} bloque le passage. Utilise Parler."
            hint_label.set_text(state["hint"])
            update_npc_ui()
            render_world.refresh()
            return

        if not is_walkable(nx, ny):
            state["hint"] = "Obstacle: deplacement bloque."
            hint_label.set_text(state["hint"])
            update_npc_ui()
            render_world.refresh()
            return

        state["x"], state["y"] = nx, ny
        state["hint"] = "Exploration en cours."

        if (nx, ny) == QUEST_MARKER:
            state["hint"] = "Point de quete atteint: debut de systeme de quete possible."

        npc = nearby_npc()
        if npc:
            state["hint"] = f"PNJ proche: {npc['name']} (bouton Parler)"

        hint_label.set_text(state["hint"])
        update_npc_ui()
        render_world.refresh()

    def reset_player() -> None:
        state["x"], state["y"] = START_POS
        state["hint"] = "Position reinitialisee."
        hint_label.set_text(state["hint"])
        npc_speech_card.set_visibility(False)
        update_npc_ui()
        render_world.refresh()

    def on_key(e) -> None:
        if e.key.arrow_up or e.key == "z" or e.key == "w":
            move_player(0, -1)
        elif e.key.arrow_down or e.key == "s":
            move_player(0, 1)
        elif e.key.arrow_left or e.key == "q" or e.key == "a":
            move_player(-1, 0)
        elif e.key.arrow_right or e.key == "d":
            move_player(1, 0)

    ui.keyboard(on_key=on_key)
    _refresh_city_context(str(state.get("city_key") or DEFAULT_CITY_KEY))
    _update_city_ui_labels()
    update_npc_ui()
    render_world()


@ui.page("/prototype-2d")
def prototype_2d_page() -> None:
    _build_prototype_page()


@ui.page("/prototypege")
def prototype_alias_page() -> None:
    _build_prototype_page()
