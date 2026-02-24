from __future__ import annotations

from collections import deque

from app.gamemaster.location_manager import LocationManager, is_building_scene_title
from app.ui.state.game_state import Scene


def _reachable_ids(scenes: dict[str, Scene], start_id: str, local_ids: set[str]) -> set[str]:
    seen: set[str] = set()
    queue = deque([start_id])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for choice in scenes[node].choices:
            nxt = str(choice.next_scene_id or "").strip()
            if nxt and nxt in local_ids and nxt not in seen:
                queue.append(nxt)
    return seen


def _local_undirected_adjacency(scenes: dict[str, Scene], local_ids: set[str]) -> dict[str, set[str]]:
    adjacency = {sid: set() for sid in local_ids}
    for sid in local_ids:
        for choice in scenes[sid].choices:
            nxt = str(choice.next_scene_id or "").strip()
            if nxt and nxt in local_ids:
                adjacency[sid].add(nxt)
                adjacency[nxt].add(sid)
    return adjacency


def test_generate_city_settlement_map_from_template() -> None:
    manager = LocationManager(None)
    center = Scene(
        id="gen_valedor_entree",
        title="Valedor - Porte nord",
        narrator_text="Ataryxia : Les remparts de Valedor se dressent devant vous.",
        map_anchor="Valedor",
        generated=True,
        choices=[],
    )
    scenes = {center.id: center}

    kind, extra_scenes = manager.generate_settlement_map_for_new_anchor(
        anchor="Valedor",
        center_scene=center,
        existing_scenes=scenes,
    )

    assert kind == "city"
    assert len(extra_scenes) == 12

    expected_fragments = {
        "La Place du Village",
        "L'Auberge du Relais",
        "La Maison Commune / Mairie",
        "La Halle couverte",
        "La Forge de Village",
        "Le Moulin",
        "La Scierie",
        "La Tannerie / Cordonnerie",
        "Le Magasin General",
        "La Maison de la Garde",
        "La Petite Chapelle",
        "La Maison de la Guerisseuse / Herboriste",
    }
    titles = {scene.title.split(" - ", 1)[1] if " - " in scene.title else scene.title for scene in extra_scenes}
    assert expected_fragments.issubset(titles)

    for scene in extra_scenes:
        scenes[scene.id] = scene

    manager.apply_city_street_layout(scenes, "Valedor", prefer_center_scene_id=center.id)

    local_ids = {sid for sid, scene in scenes.items() if scene.map_anchor == "Valedor"}
    assert _reachable_ids(scenes, center.id, local_ids) == local_ids

    adjacency = _local_undirected_adjacency(scenes, local_ids)
    for sid in local_ids:
        scene = scenes[sid]
        if not is_building_scene_title(scene.title):
            continue
        assert any(not is_building_scene_title(scenes[n].title) for n in adjacency[sid])


def test_generate_village_settlement_map_from_template() -> None:
    manager = LocationManager(None)
    center = Scene(
        id="gen_foret_village",
        title="Forêt Murmurante - Village des lisières",
        narrator_text="Ataryxia : Un petit bourg tient bon contre la foret.",
        map_anchor="Forêt Murmurante",
        generated=True,
        choices=[],
    )
    scenes = {center.id: center}

    kind, extra_scenes = manager.generate_settlement_map_for_new_anchor(
        anchor="Forêt Murmurante",
        center_scene=center,
        existing_scenes=scenes,
    )

    assert kind == "village"
    assert len(extra_scenes) == 14

    expected_fragments = {
        "Etable / Bergerie / Porcherie",
        "Forge de campagne",
        "Four a pain communal",
        "Bucherie / Depot de bois",
        "Fumoir",
        "Atelier du vannier",
        "Maison du Bailli",
        "Tour de guet en bois",
        "Palissade de pieux",
        "Pont de pierre ou de bois",
        "Auberge de route",
        "Cimetiere de campagne",
        "Hutte de la guerisseuse",
        "Cabane de trappeur",
    }
    titles = {scene.title.split(" - ", 1)[1] if " - " in scene.title else scene.title for scene in extra_scenes}
    assert expected_fragments.issubset(titles)

    for scene in extra_scenes:
        scenes[scene.id] = scene

    manager.apply_city_street_layout(scenes, "Forêt Murmurante", prefer_center_scene_id=center.id)

    local_ids = {sid for sid, scene in scenes.items() if scene.map_anchor == "Forêt Murmurante"}
    assert _reachable_ids(scenes, center.id, local_ids) == local_ids

    adjacency = _local_undirected_adjacency(scenes, local_ids)
    for sid in local_ids:
        scene = scenes[sid]
        if not is_building_scene_title(scene.title):
            continue
        assert any(not is_building_scene_title(scenes[n].title) for n in adjacency[sid])
