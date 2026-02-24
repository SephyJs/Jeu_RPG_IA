import random

from app.gamemaster.location_manager import LocationManager


def test_extract_location_hints_school() -> None:
    manager = LocationManager(None)
    hints = manager.extract_location_hints("Si tu veux progresser, va a l'ecole des magiciens de Lumeria.")
    assert hints
    assert any("Ecole des Magiciens" in hint for hint in hints)


def test_suggest_hint_location_title_skips_existing_location() -> None:
    manager = LocationManager(None)
    title = manager.suggest_hint_location_title(
        text="Tu devrais aller a l'ecole des magiciens.",
        existing_titles=["Lumeria - Ecole des Magiciens"],
    )
    assert title is None


def test_extract_location_hints_ignores_non_location_sentence() -> None:
    manager = LocationManager(None)
    hints = manager.extract_location_hints("Je m'entraine a l'epee avant le donjon.")
    assert hints == []


def test_choose_hint_anchor_prefers_explicit_anchor() -> None:
    manager = LocationManager(None)
    anchor = manager.choose_hint_anchor(
        current_anchor="Lumeria",
        text="Cette ecole se trouve a Valedor, pres des remparts.",
        rng=random.Random(42),
    )
    assert anchor == "Valedor"


def test_choose_hint_anchor_can_pick_neighbor_with_cue() -> None:
    manager = LocationManager(None)
    anchor = manager.choose_hint_anchor(
        current_anchor="Lumeria",
        text="Tu trouveras cette academie dans la ville voisine.",
        rng=random.Random(1),
    )
    assert anchor in {"Lumeria", *{"Forêt Murmurante", "Sylve d'Ancaria", "Sylvaën", "Dun'Khar", "Ruines de Lethar"}}
    assert anchor != "Lumeria"


def test_choose_hint_anchor_keeps_regular_merchant_local() -> None:
    manager = LocationManager(None)
    anchor = manager.choose_hint_anchor(
        current_anchor="Lumeria",
        text="Tu peux trouver un marchand au marche couvert de la ville.",
        hint_title="Marche Couvert",
        rng=random.Random(1),
    )
    assert anchor == "Lumeria"


def test_choose_hint_anchor_can_place_special_merchant_in_other_city() -> None:
    manager = LocationManager(None)
    anchor = manager.choose_hint_anchor(
        current_anchor="Lumeria",
        text="Un marchand arcane rare tient une echoppe specialisee.",
        hint_title="Echoppe Arcane",
        rng=random.Random(1),
    )
    assert anchor != "Lumeria"
    assert manager.is_city_anchor(anchor)


def test_choose_hint_anchor_major_structure_can_leave_city() -> None:
    manager = LocationManager(None)
    anchor = manager.choose_hint_anchor(
        current_anchor="Lumeria",
        text="Une academie des mages forme les aventuriers.",
        hint_title="Academie des Mages",
        rng=random.Random(1),
    )
    assert anchor != "Lumeria"
