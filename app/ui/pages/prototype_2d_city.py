from __future__ import annotations

import json
from pathlib import Path


def clamp(value: int, minimum: int, maximum: int) -> int:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def to_non_negative_int(value: object, fallback: int = 0) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return max(0, int(fallback))
    return max(0, ivalue)


def to_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def city_label_from_key(city_key: str) -> str:
    return str(city_key or "inconnue").replace("_", " ").strip().title()


def read_city_buildings_config(config_path: Path) -> dict:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def city_options_from_config(config: dict, *, default_city_key: str = "lumeria") -> dict[str, str]:
    cities_raw = config.get("cities")
    cities = cities_raw if isinstance(cities_raw, dict) else {}
    if not cities:
        return {default_city_key: city_label_from_key(default_city_key)}

    options: dict[str, str] = {}
    for key in sorted(cities.keys()):
        city_raw = cities.get(key)
        city = city_raw if isinstance(city_raw, dict) else {}
        label = str(city.get("label") or city_label_from_key(str(key)))
        options[str(key)] = label
    return options


def sample_building_plan(expanded: list[str], target_count: int) -> list[str]:
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


def compute_city_context(
    config: dict,
    city_key: str,
    *,
    default_city_key: str = "lumeria",
    exterior_scale_default: float = 0.16,
    exterior_min_buildings_default: int = 2,
    exterior_max_buildings_default: int = 9,
) -> dict[str, object]:
    cities_raw = config.get("cities")
    cities = cities_raw if isinstance(cities_raw, dict) else {}
    selected_city_key = str(city_key or "")
    if (selected_city_key not in cities) and cities:
        selected_city_key = sorted(cities.keys())[0]
    if not selected_city_key:
        selected_city_key = default_city_key

    city_raw = cities.get(selected_city_key)
    city_cfg = city_raw if isinstance(city_raw, dict) else {}
    city_label = str(city_cfg.get("label") or city_label_from_key(selected_city_key))

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
        profile_value = to_non_negative_int(size_profile.get(key), 0)
        override_value = to_non_negative_int(override.get(key), 0)

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

    scale = to_float(config.get("prototype_exterieur_scale"), exterior_scale_default)
    min_buildings = to_non_negative_int(config.get("prototype_exterieur_min_buildings"), exterior_min_buildings_default)
    max_buildings = to_non_negative_int(config.get("prototype_exterieur_max_buildings"), exterior_max_buildings_default)
    if min_buildings <= 0:
        min_buildings = exterior_min_buildings_default
    if max_buildings < min_buildings:
        max_buildings = min_buildings

    if not expanded:
        expanded = ["maison" for _ in range(min_buildings)]
        required_total = len(expanded)

    scaled_total = int(round(required_total * scale))
    target_total = clamp(scaled_total, min_buildings, max_buildings)
    building_plan = sample_building_plan(expanded, target_total)

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


def minimums_to_text(nonzero_minimums: list[tuple[str, int]], limit: int = 8) -> str:
    if not nonzero_minimums:
        return "aucun minimum specifique"
    parts = [f"{k}={v}" for k, v in nonzero_minimums[:limit]]
    return ", ".join(parts)
