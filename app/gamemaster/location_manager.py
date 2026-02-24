from __future__ import annotations

from collections import deque
import json
import random
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.models import Choice, Scene
from .models import model_for
from .world_time import format_hour_label, minute_of_day


MAP_ANCHORS = [
    "Valedor",
    "Forêt Murmurante",
    "Brumefeu",
    "Bois Sépulcral",
    "Ruines de Lethar",
    "Lumeria",
    "Sylve d'Ancaria",
    "Sylvaën",
    "Dun'Khar",
    "Pics de Khar",
    "Temple Ensablé",
    "Temple de Cendre",
    "Ile d'Astra'Nyx",
]

ANCHOR_NEIGHBORS = {
    "Lumeria": ["Forêt Murmurante", "Sylve d'Ancaria", "Sylvaën", "Dun'Khar", "Ruines de Lethar"],
    "Forêt Murmurante": ["Valedor", "Brumefeu", "Lumeria", "Ruines de Lethar"],
    "Brumefeu": ["Forêt Murmurante", "Bois Sépulcral"],
    "Bois Sépulcral": ["Brumefeu", "Sylve d'Ancaria"],
    "Ruines de Lethar": ["Forêt Murmurante", "Lumeria", "Temple Ensablé"],
    "Sylve d'Ancaria": ["Bois Sépulcral", "Lumeria", "Sylvaën"],
    "Sylvaën": ["Sylve d'Ancaria", "Dun'Khar", "Pics de Khar"],
    "Dun'Khar": ["Lumeria", "Sylvaën", "Temple Ensablé", "Pics de Khar"],
    "Temple Ensablé": ["Ruines de Lethar", "Dun'Khar", "Temple de Cendre"],
    "Temple de Cendre": ["Temple Ensablé", "Dun'Khar"],
    "Pics de Khar": ["Dun'Khar", "Sylvaën", "Ile d'Astra'Nyx"],
    "Ile d'Astra'Nyx": ["Pics de Khar"],
    "Valedor": ["Forêt Murmurante"],
}

_URBAN_ANCHORS = {
    "Lumeria",
    "Valedor",
    "Brumefeu",
    "Sylvaën",
    "Dun'Khar",
}

_CITY_LAYOUT_PRESETS: dict[str, dict[str, object]] = {
    "Lumeria": {
        "center_scene_id": "village_center_01",
        # Lumina/Lumeria: graphe en toile avec quartiers relies + liens transverses.
        "edges": [
            ("village_center_01", "quartier_artisanal_01"),
            ("village_center_01", "quartier_admin_noblesse_01"),
            ("village_center_01", "quartier_magique_01"),
            ("village_center_01", "quartier_spirituel_01"),
            ("village_center_01", "quartier_militaire_01"),
            ("village_center_01", "taverne_01"),
            ("village_center_01", "marche_central_01"),
            ("quartier_admin_noblesse_01", "quartier_militaire_01"),
            ("quartier_admin_noblesse_01", "quartier_magique_01"),
            ("quartier_admin_noblesse_01", "quartier_spirituel_01"),
            ("quartier_artisanal_01", "quartier_militaire_01"),
            ("quartier_artisanal_01", "quartier_magique_01"),
            ("quartier_artisanal_01", "quartier_spirituel_01"),
            ("quartier_magique_01", "quartier_spirituel_01"),
            ("quartier_militaire_01", "quartier_spirituel_01"),
            ("quartier_artisanal_01", "taverne_01"),
            ("quartier_artisanal_01", "forge_01"),
            ("quartier_artisanal_01", "boutique_01"),
            ("quartier_artisanal_01", "marche_central_01"),
            ("quartier_artisanal_01", "ateliers_divers_01"),
            ("quartier_artisanal_01", "guildes_01"),
            ("quartier_artisanal_01", "banque_nains_01"),
            ("quartier_artisanal_01", "maison_de_plaisir_01"),
            ("quartier_spirituel_01", "infirmerie_01"),
            ("quartier_spirituel_01", "temple_01"),
            ("quartier_spirituel_01", "grand_temple_01"),
            ("quartier_spirituel_01", "sanctuaire_quartier_01"),
            ("quartier_spirituel_01", "monastere_01"),
            ("quartier_spirituel_01", "necropole_01"),
            ("quartier_spirituel_01", "hospice_01"),
            ("quartier_militaire_01", "prison_01"),
            ("quartier_militaire_01", "caserne_01"),
            ("quartier_militaire_01", "armurerie_01"),
            ("quartier_militaire_01", "terrain_entrainement_01"),
            ("quartier_militaire_01", "tours_guet_01"),
            ("quartier_militaire_01", "citadelle_01"),
            ("quartier_admin_noblesse_01", "palais_royal_01"),
            ("quartier_admin_noblesse_01", "conseil_anciens_01"),
            ("quartier_admin_noblesse_01", "tribunal_justice_01"),
            ("quartier_admin_noblesse_01", "hotel_monnaies_01"),
            ("quartier_admin_noblesse_01", "archives_historiques_01"),
            ("quartier_admin_noblesse_01", "villas_manoirs_01"),
            ("quartier_magique_01", "academie_magie_01"),
            ("quartier_magique_01", "laboratoire_alchimie_01"),
            ("quartier_magique_01", "observatoire_01"),
            ("quartier_magique_01", "herboristerie_01"),
            ("quartier_magique_01", "menagerie_exotique_01"),
            ("quartier_magique_01", "scriptoria_01"),
            ("hotel_monnaies_01", "banque_nains_01"),
            ("archives_historiques_01", "scriptoria_01"),
            ("tribunal_justice_01", "prison_01"),
            ("palais_royal_01", "citadelle_01"),
            ("armurerie_01", "forge_01"),
            ("laboratoire_alchimie_01", "herboristerie_01"),
            ("observatoire_01", "monastere_01"),
            ("marche_central_01", "boutique_01"),
            ("infirmerie_01", "herboristerie_01"),
            ("grand_temple_01", "temple_01"),
            ("hospice_01", "infirmerie_01"),
            ("guildes_01", "conseil_anciens_01"),
            ("citadelle_01", "tours_guet_01"),
        ],
    }
}

_CITY_DISTRICT_TEMPLATES: list[dict[str, object]] = [
    {
        "title": "Ruelle des Lanternes",
        "narrator": "Ataryxia : Des lanternes de cuivre balancent au-dessus d'une rue étroite, entre ombre et rumeurs.",
        "npcs": ["Passant pressé", "Rôdeur de quartier"],
    },
    {
        "title": "Auberge du Carrefour",
        "narrator": "Ataryxia : Une auberge serrée entre deux murs de pierre, où le bois humide craque sous les bottes.",
        "npcs": ["Aubergiste", "Serveur"],
    },
    {
        "title": "Forge de Quartier",
        "narrator": "Ataryxia : Le fer rouge pulse dans la nuit comme un coeur battant sous la pluie.",
        "npcs": ["Forgeron", "Apprenti forgeron"],
    },
    {
        "title": "Marché Couvert",
        "narrator": "Ataryxia : Sous des toiles sombres, les étals bruissent de marchandages et de promesses douteuses.",
        "npcs": ["Marchand", "Cliente encapuchonnée"],
    },
    {
        "title": "Sanctuaire de Rue",
        "narrator": "Ataryxia : Une chapelle discrète, noyée d'encens froid, protège les âmes fatiguées.",
        "npcs": ["Acolyte", "Pèlerin silencieux"],
    },
    {
        "title": "Cour des Artisans",
        "narrator": "Ataryxia : Des ateliers ouvrent sur une cour pavée, frappée de marteaux et de poussière claire.",
        "npcs": ["Artisane", "Livreur"],
    },
]

_SETTLEMENT_CITY_TEMPLATES: list[dict[str, object]] = [
    {
        "title": "La Place du Village",
        "narrator": "Ataryxia : Une grande place de pierre ou convergent les rumeurs, les affaires et les querelles du jour.",
        "npcs": ["Crieur public", "Marchande", "Garde de ronde"],
    },
    {
        "title": "L'Auberge du Relais",
        "narrator": "Ataryxia : Bois ancien, feu vivant et voyageurs fatigues font de cette auberge un noeud de recits et de contrats.",
        "npcs": ["Aubergiste", "Serveuse", "Mercenaire de passage"],
    },
    {
        "title": "La Maison Commune / Mairie",
        "narrator": "Ataryxia : Registres, sceaux et decrets s'empilent derriere les portes de l'administration locale.",
        "npcs": ["Maire", "Secretaire", "Percepteur"],
    },
    {
        "title": "La Halle couverte",
        "narrator": "Ataryxia : Sous les poutres noircies de la halle, les etals changent de main au rythme des saisons.",
        "npcs": ["Commercante", "Porteur", "Courtier"],
    },
    {
        "title": "La Forge de Village",
        "narrator": "Ataryxia : Le chant du marteau frappe l'air comme une cloche de guerre.",
        "npcs": ["Forgeron", "Apprenti forgeron"],
    },
    {
        "title": "Le Moulin",
        "narrator": "Ataryxia : Les pales grincent ou la roue claque, transformant grain et sueur en survie quotidienne.",
        "npcs": ["Meuniere", "Livreur de farine"],
    },
    {
        "title": "La Scierie",
        "narrator": "Ataryxia : Sciures volantes et poutres fraiches alimentent les charpentes de la cite.",
        "npcs": ["Maitre scieur", "Ouvriere du bois"],
    },
    {
        "title": "La Tannerie / Cordonnerie",
        "narrator": "Ataryxia : Odeur acre, cuir humide et bottes en cours de couture emplissent l'atelier.",
        "npcs": ["Tanneur", "Cordonnier"],
    },
    {
        "title": "Le Magasin General",
        "narrator": "Ataryxia : Outils, vivres et petits secrets s'echangent derriere un comptoir surcharge.",
        "npcs": ["Boutiquier", "Commis"],
    },
    {
        "title": "La Maison de la Garde",
        "narrator": "Ataryxia : Casques alignes, rapports griffonnes et vigilance constante contre les ennuis de nuit.",
        "npcs": ["Caporal", "Garde"],
    },
    {
        "title": "La Petite Chapelle",
        "narrator": "Ataryxia : Quelques cierges suffisent a tenir la peur a distance quand la nuit tombe.",
        "npcs": ["Acolyte", "Devote"],
    },
    {
        "title": "La Maison de la Guerisseuse / Herboriste",
        "narrator": "Ataryxia : Herbes sechees, decoctions ambrées et remedes de terrain sauvent plus de vies que les discours.",
        "npcs": ["Guerisseuse", "Herboriste"],
    },
]

_SETTLEMENT_VILLAGE_TEMPLATES: list[dict[str, object]] = [
    {
        "title": "Etable / Bergerie / Porcherie",
        "narrator": "Ataryxia : Betes nerveuses, odeur de paille et de fumier; la richesse du hameau se compte ici.",
        "npcs": ["Berger", "Eleveuse"],
    },
    {
        "title": "Forge de campagne",
        "narrator": "Ataryxia : Une petite forge robuste ou l'on repare l'essentiel avant l'hiver ou la guerre.",
        "npcs": ["Forgeron de campagne"],
    },
    {
        "title": "Four a pain communal",
        "narrator": "Ataryxia : Chaleur du four, mains farinees et rituels quotidiens soudent la communaute.",
        "npcs": ["Boulangere", "Villageois"],
    },
    {
        "title": "Bucherie / Depot de bois",
        "narrator": "Ataryxia : Buches empilees, copeaux et haches usées preparent les longues nuits froides.",
        "npcs": ["Bucheron", "Livreur de bois"],
    },
    {
        "title": "Fumoir",
        "narrator": "Ataryxia : Poissons et viandes pendent dans une fumee epaisse qui prolonge la survie.",
        "npcs": ["Fumier", "Pecheur"],
    },
    {
        "title": "Atelier du vannier",
        "narrator": "Ataryxia : Osier trempe, paniers solides et gestes repetes depuis des generations.",
        "npcs": ["Vannier"],
    },
    {
        "title": "Maison du Bailli",
        "narrator": "Ataryxia : Comptes du domaine, impots et arbitrages ruraux se traitent derriere cette porte.",
        "npcs": ["Bailli", "Clerc local"],
    },
    {
        "title": "Tour de guet en bois",
        "narrator": "Ataryxia : Une tour rustique qui scrute route et lisiere pour anticiper les mauvaises surprises.",
        "npcs": ["Sentinelle"],
    },
    {
        "title": "Palissade de pieux",
        "narrator": "Ataryxia : L'enceinte de bois marque la frontiere fragile entre le foyer et le dehors.",
        "npcs": ["Garde rural"],
    },
    {
        "title": "Pont de pierre ou de bois",
        "narrator": "Ataryxia : Ce passage tient le village relie au monde, malgre la riviere et les crues.",
        "npcs": ["Passeur", "Voyageur"],
    },
    {
        "title": "Auberge de route",
        "narrator": "Ataryxia : Unique lieu de boisson, d'informations et de repos avant la prochaine etape.",
        "npcs": ["Aubergiste de route", "Routier"],
    },
    {
        "title": "Cimetiere de campagne",
        "narrator": "Ataryxia : Un muret de pierre entoure les morts du hameau, gardes par le silence.",
        "npcs": ["Fossoyeuse"],
    },
    {
        "title": "Hutte de la guerisseuse",
        "narrator": "Ataryxia : A la lisiere des bois, une hutte de remedes et de vieilles superstitions.",
        "npcs": ["Guerisseuse des bois"],
    },
    {
        "title": "Cabane de trappeur",
        "narrator": "Ataryxia : Peaux sechant au vent et pieges alignes racontent une vie rude et discrète.",
        "npcs": ["Trappeur"],
    },
]

_SETTLEMENT_CITY_HINT_TOKENS = ("ville", "cite", "cité", "quartier", "capitale", "metropole", "métropole")
_SETTLEMENT_VILLAGE_HINT_TOKENS = ("village", "hameau", "bourg", "bourgade")

_BUILDING_TITLE_TOKENS = (
    "taverne",
    "auberge",
    "forge",
    "boutique",
    "temple",
    "prison",
    "infirmerie",
    "sanctuaire",
    "atelier",
    "maison",
    "caserne",
    "bibliotheque",
    "bibliothèque",
    "comptoir",
    "salle",
    "marche couvert",
    "marché couvert",
    "palais",
    "chateau",
    "château",
    "conseil",
    "tribunal",
    "hotel des monnaies",
    "hôtel des monnaies",
    "archives",
    "manoir",
    "armurerie",
    "terrain d'entrainement",
    "terrain d'entraînement",
    "tour de guet",
    "tours de guet",
    "citadelle",
    "academie",
    "académie",
    "laboratoire",
    "observatoire",
    "herboristerie",
    "menagerie",
    "ménagerie",
    "scriptoria",
    "marche central",
    "marché central",
    "guilde",
    "guildes",
    "banque",
    "monastere",
    "monastère",
    "necropole",
    "nécropole",
    "hospice",
    "chapelle",
    "mairie",
    "maison commune",
    "halle",
    "moulin",
    "scierie",
    "tannerie",
    "cordonnerie",
    "magasin general",
    "magasin général",
    "etable",
    "étable",
    "bergerie",
    "porcherie",
    "four a pain",
    "four à pain",
    "fumoir",
    "vannier",
    "bailli",
    "cimetiere",
    "cimetière",
    "hutte",
    "cabane",
    "trappeur",
    "bucherie",
    "depot de bois",
    "dépôt de bois",
    "maison de plaisir",
    "bordel",
    "lupanar",
)

_STREET_TITLE_TOKENS = (
    "ruelle",
    "rue",
    "allee",
    "allée",
    "carrefour",
    "place",
    "quartier",
    "porte",
    "sentier",
    "chemin",
    "avenue",
    "village",
)

_ROAMING_STREET_NPCS = [
    "Marchand ambulant",
    "Colporteuse",
    "Messager essoufflé",
    "Mendiant",
    "Passante pressée",
    "Vieil ouvrier",
    "Garde en ronde",
    "Barde itinérant",
    "Enfant des rues",
    "Artisane de passage",
    "Chasseur urbain",
    "SDF du quartier",
]

_SCENE_HOURS_BY_ID: dict[str, tuple[int, int, str]] = {
    "boutique_01": (8, 18, "La boutique"),
    "forge_01": (7, 19, "La forge"),
    "taverne_01": (6, 2, "La taverne"),
    "temple_01": (6, 22, "Le temple"),
    "infirmerie_01": (0, 0, "L'infirmerie"),
    "prison_01": (0, 0, "La prison"),
    "marche_central_01": (7, 20, "Le marche"),
    "banque_nains_01": (8, 17, "La banque des nains"),
    "palais_royal_01": (8, 20, "Le palais royal"),
    "citadelle_01": (0, 0, "La citadelle"),
    "grand_temple_01": (6, 23, "Le grand temple"),
    "monastere_01": (6, 21, "Le monastere"),
    "hospice_01": (0, 0, "L'hospice"),
    "academie_magie_01": (7, 22, "L'academie de magie"),
    "maison_de_plaisir_01": (19, 4, "La maison de plaisir"),
}

_SCENE_HOURS_BY_TITLE: list[tuple[tuple[str, ...], tuple[int, int, str]]] = [
    (("boutique", "marchand", "comptoir", "marche couvert", "marché couvert"), (8, 18, "Le commerce")),
    (("marche central", "marché central", "guilde", "guildes", "banque"), (8, 20, "Le commerce")),
    (("forge", "atelier", "scierie", "tannerie", "cordonnerie", "vannier"), (7, 19, "Les ateliers")),
    (("moulin", "four a pain", "four à pain", "fumoir"), (6, 20, "Les ressources du village")),
    (("mairie", "maison commune", "bailli", "halle"), (8, 18, "L'administration locale")),
    (("auberge", "taverne"), (6, 2, "L'auberge")),
    (("temple", "sanctuaire"), (6, 22, "Le sanctuaire")),
    (("palais", "conseil", "tribunal", "archives", "monnaies"), (8, 20, "Les institutions")),
    (("academie", "laboratoire", "observatoire", "scriptoria", "menagerie"), (7, 22, "Le quartier savant")),
    (("infirmerie",), (0, 0, "L'infirmerie")),
    (("hospice",), (0, 0, "L'hospice")),
    (("prison", "caserne", "citadelle", "armurerie", "tour de guet"), (0, 0, "Le poste de garde")),
]

_GENERIC_LOCATION_SUFFIXES = {
    "salle principale",
    "salle commune",
    "salle centrale",
    "hall principal",
    "grand hall",
    "zone centrale",
    "zone principale",
    "entree",
    "annexe",
    "couloir principal",
    "rue principale",
    "place centrale",
}

_GENERIC_LOCATION_WORDS = {
    "salle",
    "zone",
    "hall",
    "entree",
    "annexe",
    "couloir",
    "rue",
    "place",
    "quartier",
    "principal",
    "principale",
    "centrale",
    "commun",
    "commune",
}

_LOCATION_RESONANCE_RULES: list[dict[str, object]] = [
    {
        "key": "taverne",
        "label": "Taverne",
        "tokens": ("taverne", "auberge", "cabaret"),
        "flavors": [
            "Le Gobelet Fendu",
            "La Chope Noircie",
            "Le Banc du Corbeau",
            "L'Atre des Voyageurs",
            "Le Chene Ivre",
        ],
    },
    {
        "key": "forge",
        "label": "Forge",
        "tokens": ("forge", "atelier", "fonderie"),
        "flavors": [
            "L'Enclume Rouge",
            "Le Soufflet Noir",
            "La Frappe des Cendres",
            "Le Marteau Creux",
            "La Braise des Fers",
        ],
    },
    {
        "key": "temple",
        "label": "Temple",
        "tokens": ("temple", "sanctuaire", "chapelle"),
        "flavors": [
            "Le Choeur des Murmures",
            "La Nef des Veilleurs",
            "L'Autel Fracture",
            "Le Reliquaire Sourd",
            "Le Voile de Cendre",
        ],
    },
    {
        "key": "boutique",
        "label": "Boutique",
        "tokens": ("boutique", "comptoir", "marche", "echoppe"),
        "flavors": [
            "Les Mille Fioles",
            "Le Comptoir des Brumes",
            "L'Etal des Rumeurs",
            "Le Sac et la Lame",
            "La Corde et le Cuivre",
        ],
    },
    {
        "key": "infirmerie",
        "label": "Infirmerie",
        "tokens": ("infirmerie", "apothic", "hopital", "dispensaire"),
        "flavors": [
            "L'Onguent Gris",
            "Le Repos des Blesses",
            "La Salle des Cataplasmes",
            "Le Bocal d'Ambre",
            "La Main Calmee",
        ],
    },
    {
        "key": "prison",
        "label": "Prison",
        "tokens": ("prison", "caserne", "geole", "poste"),
        "flavors": [
            "Les Barreaux d'Etain",
            "Le Couloir des Cles",
            "La Garde Cendree",
            "Le Verrou Sombre",
            "La Pierre des Aveux",
        ],
    },
    {
        "key": "rue",
        "label": "Ruelle",
        "tokens": ("ruelle", "rue", "allee", "place", "carrefour", "quartier", "sentier", "chemin"),
        "flavors": [
            "La Traverse des Ombres",
            "Le Carrefour des Lanternes",
            "La Ruelle des Cendres",
            "Le Passage des Veilleurs",
            "Le Detour des Corbeaux",
        ],
    },
    {
        "key": "bordel",
        "label": "Maison de Plaisir",
        "tokens": ("bordel", "lupanar", "maison de plaisir", "maison close"),
        "flavors": [
            "Le Baiser de Velours",
            "La Rose Nocturne",
            "Les Soupirs de Soie",
            "L'Etreinte d'Or",
            "Le Jardin des Delices",
        ],
    },
]

_LOCATION_HINT_KEYWORDS = (
    "ecole",
    "academie",
    "guilde",
    "ordre",
    "tour",
    "bibliotheque",
    "temple",
    "sanctuaire",
    "arene",
    "caserne",
    "atelier",
    "marche",
    "port",
    "laboratoire",
    "observatoire",
    "fort",
    "citadelle",
    "ecole",
)

_LOCATION_HINT_CONTEXT_WORDS = (
    "a",
    "au",
    "aux",
    "dans",
    "vers",
    "direction",
    "rejoins",
    "rends toi",
    "va",
)

_LOCATION_HINT_SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "d"}
_LOCATION_HINT_TRAILING_STOPWORDS = {
    "pour",
    "afin",
    "si",
    "et",
    "ou",
    "mais",
    "car",
    "donc",
    "alors",
    "que",
    "qui",
    "quoi",
    "quand",
    "lorsque",
    "avec",
    "sans",
}

_LOCATION_HINT_NEIGHBOR_CUES = (
    "ville voisine",
    "ville d a cote",
    "a cote",
    "dans la ville voisine",
    "prochaine ville",
    "non loin",
    "plus loin",
    "hors de la ville",
    "dans une autre ville",
)

_LOCATION_HINT_MAJOR_STRUCTURE_TOKENS = (
    "ecole",
    "academie",
    "guilde",
    "ordre",
    "temple",
    "sanctuaire",
    "citadelle",
    "fort",
    "tour",
    "observatoire",
    "laboratoire",
    "bibliotheque",
    "arene",
)

_LOCATION_HINT_MERCHANT_TOKENS = (
    "marchand",
    "boutique",
    "echoppe",
    "comptoir",
    "marche",
)

_LOCATION_HINT_SPECIAL_MERCHANT_TOKENS = (
    "special",
    "specialise",
    "rare",
    "exotique",
    "arcane",
    "artefact",
    "relique",
    "alchim",
    "mage",
    "noir",
    "itinerant",
    "nomade",
)


def _norm_anchor_token(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")


_ANCHOR_BY_NORM = {_norm_anchor_token(anchor): anchor for anchor in MAP_ANCHORS}


def canonical_anchor(anchor: str, *, default: str = "Lumeria") -> str:
    return _ANCHOR_BY_NORM.get(_norm_anchor_token(anchor), default)


def official_neighbors(anchor: str) -> list[str]:
    current = canonical_anchor(anchor)
    neighbors: set[str] = set(ANCHOR_NEIGHBORS.get(current, []))

    # Tolère les graphes non strictement symétriques.
    for source, targets in ANCHOR_NEIGHBORS.items():
        if current in targets:
            neighbors.add(source)

    ordered: list[str] = []
    for candidate in MAP_ANCHORS:
        if candidate in neighbors:
            ordered.append(candidate)
    return ordered


def official_shortest_path(start_anchor: str, end_anchor: str) -> list[str]:
    start = canonical_anchor(start_anchor)
    goal = canonical_anchor(end_anchor)
    if start == goal:
        return [start]

    queue: deque[list[str]] = deque([[start]])
    visited = {start}

    while queue:
        path = queue.popleft()
        node = path[-1]
        for nxt in official_neighbors(node):
            if nxt in visited:
                continue
            next_path = [*path, nxt]
            if nxt == goal:
                return next_path
            visited.add(nxt)
            queue.append(next_path)

    return [start, goal]


def _norm_text_token(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", raw.lower()).strip()


def is_building_scene_title(title: str) -> bool:
    norm = _norm_text_token(title)
    return any(token in norm for token in _BUILDING_TITLE_TOKENS)


def is_street_scene(scene: Scene) -> bool:
    if not isinstance(scene, Scene):
        return False
    title_norm = _norm_text_token(scene.title)
    if is_building_scene_title(scene.title):
        return False
    return any(token in title_norm for token in _STREET_TITLE_TOKENS)


def refresh_roaming_street_npcs(
    scene: Scene,
    *,
    max_total: int = 5,
    roaming_candidates: list[str] | None = None,
) -> bool:
    if not is_street_scene(scene):
        return False

    pool: list[str] = []
    if isinstance(roaming_candidates, list):
        for row in roaming_candidates:
            name = str(row or "").strip()
            if name and name not in pool:
                pool.append(name)
    for name in _ROAMING_STREET_NPCS:
        if name not in pool:
            pool.append(name)

    if not pool:
        return False

    pool_set = set(pool)
    fixed = [name for name in scene.npc_names if name not in _ROAMING_STREET_NPCS and name not in pool_set]
    room = max(0, max_total - len(fixed))
    if room <= 0:
        return False

    extra_count = random.randint(0, min(room, 2))
    extras = random.sample(pool, k=min(extra_count, len(pool))) if extra_count > 0 else []

    merged: list[str] = []
    for name in [*fixed, *extras]:
        clean = str(name or "").strip()
        if not clean or clean in merged:
            continue
        merged.append(clean)

    merged = merged[:max_total]
    if merged == scene.npc_names:
        return False
    scene.npc_names = merged
    return True


def scene_opening_window(scene: Scene) -> tuple[int, int, str] | None:
    if not isinstance(scene, Scene):
        return None
    if not is_building_scene_title(scene.title):
        return None

    scene_id = str(scene.id or "").strip()
    if scene_id in _SCENE_HOURS_BY_ID:
        return _SCENE_HOURS_BY_ID[scene_id]

    title_norm = _norm_text_token(scene.title)
    for tokens, window in _SCENE_HOURS_BY_TITLE:
        if any(token in title_norm for token in tokens):
            return window
    return None


def _is_open_now(*, open_hour: int, close_hour: int, world_time_minutes: int) -> bool:
    start = (int(open_hour) % 24) * 60
    end = (int(close_hour) % 24) * 60
    now = minute_of_day(world_time_minutes)

    # Meme heure de debut/fin => ouvert en continu.
    if start == end:
        return True
    if start < end:
        return start <= now < end
    # Fenetre de nuit (ex: 18h -> 02h).
    return now >= start or now < end


def scene_open_status(scene: Scene, world_time_minutes: int) -> tuple[bool, str]:
    window = scene_opening_window(scene)
    if window is None:
        return True, ""

    open_hour, close_hour, label = window
    status = _is_open_now(
        open_hour=open_hour,
        close_hour=close_hour,
        world_time_minutes=world_time_minutes,
    )

    if int(open_hour) % 24 == int(close_hour) % 24:
        schedule = "ouvert en permanence"
    else:
        schedule = f"ouvert de {format_hour_label(open_hour)} a {format_hour_label(close_hour)}"

    if status:
        return True, f"{label} est {schedule}."
    return False, f"{label} est ferme ({schedule})."


class LocationDraft(BaseModel):
    title: str
    narrator_text: str
    npcs: list[str] = Field(default_factory=list)
    travel_label_from_current: str = ""


class LocationManager:
    def __init__(self, llm: Any):
        self.llm = llm

    def is_city_anchor(self, anchor: str) -> bool:
        return canonical_anchor(anchor) in _URBAN_ANCHORS

    def seed_static_anchors(self, scenes: dict[str, Scene]) -> None:
        static_ids = {
            "village_center_01",
            "quartier_admin_noblesse_01",
            "quartier_militaire_01",
            "quartier_magique_01",
            "quartier_artisanal_01",
            "quartier_spirituel_01",
            "taverne_01",
            "forge_01",
            "boutique_01",
            "infirmerie_01",
            "temple_01",
            "prison_01",
            "palais_royal_01",
            "conseil_anciens_01",
            "tribunal_justice_01",
            "hotel_monnaies_01",
            "archives_historiques_01",
            "villas_manoirs_01",
            "caserne_01",
            "armurerie_01",
            "terrain_entrainement_01",
            "tours_guet_01",
            "citadelle_01",
            "academie_magie_01",
            "laboratoire_alchimie_01",
            "observatoire_01",
            "herboristerie_01",
            "menagerie_exotique_01",
            "scriptoria_01",
            "marche_central_01",
            "ateliers_divers_01",
            "guildes_01",
            "banque_nains_01",
            "maison_de_plaisir_01",
            "grand_temple_01",
            "sanctuaire_quartier_01",
            "monastere_01",
            "necropole_01",
            "hospice_01",
        }
        for scene_id in static_ids:
            scene = scenes.get(scene_id)
            if scene and not scene.map_anchor:
                scene.map_anchor = "Lumeria"
        self.apply_city_street_layouts(scenes)

    def apply_city_street_layouts(self, scenes: dict[str, Scene]) -> None:
        anchors = sorted(
            {
                scene.map_anchor
                for scene in scenes.values()
                if scene.map_anchor in MAP_ANCHORS and self.is_city_anchor(scene.map_anchor)
            }
        )
        for anchor in anchors:
            self.apply_city_street_layout(scenes, anchor)

    def apply_city_street_layout(
        self,
        scenes: dict[str, Scene],
        anchor: str,
        *,
        prefer_center_scene_id: str | None = None,
    ) -> None:
        anchor_name = canonical_anchor(anchor)
        if not self.is_city_anchor(anchor_name) and anchor_name not in _CITY_LAYOUT_PRESETS:
            return
        local_ids = [sid for sid, scene in scenes.items() if scene.map_anchor == anchor_name]
        if len(local_ids) < 2:
            return

        local_set = set(local_ids)
        external_choices: dict[str, list[Choice]] = {}
        for sid in local_ids:
            scene = scenes[sid]
            kept: list[Choice] = []
            for choice in scene.choices:
                nxt = choice.next_scene_id
                if not nxt or nxt not in local_set:
                    kept.append(choice)
            external_choices[sid] = self._dedupe_choices(kept)

        center_id = self._pick_city_center_scene_id(
            scenes,
            local_ids,
            prefer_center_scene_id=prefer_center_scene_id,
        )
        edges = self._build_local_edges(
            scenes,
            anchor_name,
            local_ids,
            center_id=center_id,
        )

        adjacency: dict[str, set[str]] = {sid: set() for sid in local_ids}
        for a, b in edges:
            if a not in local_set or b not in local_set or a == b:
                continue
            adjacency[a].add(b)
            adjacency[b].add(a)

        for sid in local_ids:
            scene = scenes[sid]
            merged = list(external_choices.get(sid, []))
            for target_id in sorted(adjacency.get(sid, set()), key=lambda t: scenes[t].title.casefold()):
                merged.append(
                    Choice(
                        id=f"street_{target_id}",
                        label=self._street_label(
                            source=scene,
                            target=scenes[target_id],
                            center_scene_id=center_id,
                        ),
                        next_scene_id=target_id,
                    )
                )
            scene.choices = self._dedupe_choices(merged)

    def settlement_kind_for_scene(self, scene: Scene, *, anchor: str) -> str:
        anchor_name = canonical_anchor(anchor)
        if self.is_city_anchor(anchor_name):
            return "city"

        title_norm = _norm_text_token(scene.title if isinstance(scene, Scene) else "")
        if any(token in title_norm for token in _SETTLEMENT_CITY_HINT_TOKENS):
            return "city"
        if any(token in title_norm for token in _SETTLEMENT_VILLAGE_HINT_TOKENS):
            return "village"
        # Par defaut, toute nouvelle zone habitee non-urbaine est traitee comme un village.
        return "village"

    def generate_settlement_map_for_new_anchor(
        self,
        *,
        anchor: str,
        center_scene: Scene,
        existing_scenes: dict[str, Scene],
    ) -> tuple[str, list[Scene]]:
        anchor_name = canonical_anchor(anchor)
        settlement_kind = self.settlement_kind_for_scene(center_scene, anchor=anchor_name)
        templates = list(_SETTLEMENT_CITY_TEMPLATES if settlement_kind == "city" else _SETTLEMENT_VILLAGE_TEMPLATES)
        rng = random.Random(f"settlement_layout::{anchor_name}::{settlement_kind}")
        rng.shuffle(templates)

        existing_ids = set(existing_scenes.keys())
        existing_titles = {scene.title for scene in existing_scenes.values()}
        local_scenes: dict[str, Scene] = {center_scene.id: center_scene}
        new_scenes: list[Scene] = []
        for template in templates:
            title = self._unique_title(f"{anchor_name} - {str(template['title'])}", existing_titles)
            scene_id = self._unique_scene_id(anchor_name, title, existing_ids)
            existing_ids.add(scene_id)
            existing_titles.add(title)

            narrator = str(template.get("narrator") or "").strip()
            fallback_narrator = "Une rue nouvelle s'ouvre sous vos pas."
            if not narrator.startswith("Ataryxia"):
                narrator = f"Ataryxia : {narrator or fallback_narrator}"
            npcs_raw = template.get("npcs")
            npc_names = [str(n).strip() for n in npcs_raw if isinstance(n, str) and str(n).strip()] if isinstance(npcs_raw, list) else []

            generated_scene = Scene(
                id=scene_id,
                title=title,
                narrator_text=narrator,
                map_anchor=anchor_name,
                generated=True,
                npc_names=npc_names[:4],
                choices=[],
            )
            local_scenes[generated_scene.id] = generated_scene
            new_scenes.append(generated_scene)

        edges = self._build_settlement_edges(local_scenes, center_id=center_scene.id, settlement_kind=settlement_kind)
        if edges:
            self._register_settlement_layout_preset(
                anchor_name=anchor_name,
                center_scene_id=center_scene.id,
                edges=edges,
            )

        return settlement_kind, new_scenes

    def generate_city_map_for_new_anchor(
        self,
        *,
        anchor: str,
        center_scene: Scene,
        existing_scenes: dict[str, Scene],
    ) -> list[Scene]:
        settlement_kind, scenes = self.generate_settlement_map_for_new_anchor(
            anchor=anchor,
            center_scene=center_scene,
            existing_scenes=existing_scenes,
        )
        if settlement_kind != "city":
            return []
        return scenes

    def _build_settlement_edges(
        self,
        scenes: dict[str, Scene],
        *,
        center_id: str,
        settlement_kind: str,
    ) -> set[tuple[str, str]]:
        local_ids = [sid for sid in scenes.keys()]
        if len(local_ids) < 2:
            return set()

        if center_id not in scenes:
            center_id = local_ids[0]

        street_ids = [
            sid
            for sid in local_ids
            if not is_building_scene_title(scenes[sid].title)
        ]
        building_ids = [
            sid
            for sid in local_ids
            if is_building_scene_title(scenes[sid].title)
        ]

        out: set[tuple[str, str]] = set()

        def add_edge(a: str, b: str) -> None:
            if not a or not b or a == b:
                return
            out.add(tuple(sorted((a, b))))

        primary_hub = center_id
        if is_building_scene_title(scenes[center_id].title):
            if street_ids:
                primary_hub = sorted(street_ids, key=lambda sid: (scenes[sid].title.casefold(), sid))[0]
                add_edge(center_id, primary_hub)
        elif center_id not in street_ids:
            street_ids.append(center_id)

        if primary_hub not in street_ids:
            street_ids.append(primary_hub)
        street_ids = sorted(set(street_ids), key=lambda sid: (scenes[sid].title.casefold(), sid))

        # Maille de rues.
        for sid in street_ids:
            if sid != primary_hub:
                add_edge(primary_hub, sid)
        for idx in range(len(street_ids) - 1):
            add_edge(street_ids[idx], street_ids[idx + 1])
        if len(street_ids) >= 3:
            add_edge(street_ids[0], street_ids[-1])

        # Accroche chaque batiment a une rue.
        hubs = street_ids[:] if street_ids else [primary_hub]
        ordered_buildings = sorted(building_ids, key=lambda sid: (scenes[sid].title.casefold(), sid))
        for idx, bid in enumerate(ordered_buildings):
            hub = hubs[idx % len(hubs)]
            add_edge(bid, hub)

        # Quelques liens transverses pour un rendu "toile".
        step = 3 if settlement_kind == "city" else 4
        for idx in range(0, len(ordered_buildings) - step, step):
            add_edge(ordered_buildings[idx], ordered_buildings[idx + step])

        return out

    def _register_settlement_layout_preset(
        self,
        *,
        anchor_name: str,
        center_scene_id: str,
        edges: set[tuple[str, str]],
    ) -> None:
        if not edges:
            return
        rows = sorted(tuple(sorted((a, b))) for a, b in edges if a and b and a != b)
        _CITY_LAYOUT_PRESETS[anchor_name] = {
            "center_scene_id": center_scene_id,
            "edges": rows,
        }


    async def generate_next_scene(self, current_scene: Scene, existing_scenes: dict[str, Scene]) -> tuple[Scene, str]:
        current_anchor = canonical_anchor(self._infer_anchor(current_scene))
        neighbors = official_neighbors(current_anchor)
        target_anchor = self._pick_target_anchor(current_anchor, neighbors, existing_scenes)
        existing_titles = [s.title for s in existing_scenes.values()]
        existing_ids = set(existing_scenes.keys())

        prompt = self._build_prompt(
            current_scene=current_scene,
            current_anchor=current_anchor,
            target_anchor=target_anchor,
            existing_titles=existing_titles,
        )

        raw = await self.llm.generate(
            model=model_for("rules"),
            prompt=prompt,
            temperature=0.35,
            num_ctx=4096,
            num_predict=700,
            stop=None,
        )

        draft = self._parse_draft(raw, target_anchor=target_anchor)
        location_id = self._unique_scene_id(target_anchor, draft.title, existing_ids)
        title = self._unique_title(draft.title, set(existing_titles))
        narration = draft.narrator_text.strip() or f"Ataryxia : Le vent tourne à {title}, et la route se fait plus lourde."
        npcs = [n.strip() for n in draft.npcs if isinstance(n, str) and n.strip()][:4]

        scene = Scene(
            id=location_id,
            title=title,
            narrator_text=narration,
            map_anchor=target_anchor,
            generated=True,
            npc_names=npcs,
            choices=[],
        )

        travel_label = draft.travel_label_from_current.strip() or f"Prendre la route vers {title}"
        return scene, travel_label

    def _dedupe_choices(self, choices: list[Choice]) -> list[Choice]:
        out: list[Choice] = []
        seen_targets: set[str] = set()
        seen_labels: set[str] = set()
        for choice in choices:
            if not isinstance(choice, Choice):
                continue
            label = str(choice.label or "").strip()
            target = str(choice.next_scene_id or "").strip()
            if target:
                if target in seen_targets:
                    continue
                seen_targets.add(target)
            if not target:
                key = label.casefold()
                if key in seen_labels:
                    continue
                seen_labels.add(key)
            out.append(choice)
        return out

    def _pick_city_center_scene_id(
        self,
        scenes: dict[str, Scene],
        local_ids: list[str],
        *,
        prefer_center_scene_id: str | None,
    ) -> str:
        if prefer_center_scene_id and prefer_center_scene_id in local_ids:
            return prefer_center_scene_id

        sample_scene = scenes[local_ids[0]]
        anchor = canonical_anchor(sample_scene.map_anchor)
        preset = _CITY_LAYOUT_PRESETS.get(anchor)
        preset_center = str((preset or {}).get("center_scene_id") or "").strip()
        if preset_center and preset_center in local_ids:
            return preset_center

        center_tokens = ("centre", "center", "place", "plaza", "carrefour", "coeur", "cœur")
        scored: list[tuple[int, str]] = []
        for sid in local_ids:
            scene = scenes[sid]
            title_norm = self._norm(scene.title)
            sid_norm = self._norm(sid)
            score = 0
            if not scene.generated:
                score += 2
            if any(tok in title_norm or tok in sid_norm for tok in center_tokens):
                score += 6
            if "village_center" in sid_norm:
                score += 8
            scored.append((score, sid))
        scored.sort(key=lambda row: (-row[0], scenes[row[1]].title.casefold(), row[1]))
        return scored[0][1]

    def _build_local_edges(
        self,
        scenes: dict[str, Scene],
        anchor: str,
        local_ids: list[str],
        *,
        center_id: str,
    ) -> set[tuple[str, str]]:
        local_set = set(local_ids)
        preset = _CITY_LAYOUT_PRESETS.get(anchor)
        if preset and isinstance(preset.get("edges"), list):
            out: set[tuple[str, str]] = set()
            for row in preset["edges"]:
                if not (isinstance(row, (list, tuple)) and len(row) == 2):
                    continue
                a = str(row[0] or "").strip()
                b = str(row[1] or "").strip()
                if a in local_set and b in local_set and a != b:
                    out.add(tuple(sorted((a, b))))
            if out and self._all_nodes_reachable(local_ids, out, center_id=center_id):
                return out

        others = [sid for sid in local_ids if sid != center_id]
        others.sort(key=lambda sid: (scenes[sid].generated, scenes[sid].title.casefold(), sid))

        out: set[tuple[str, str]] = set()

        # Depuis le centre, on n'expose que 2 points max pour garder l'effet "ruelles".
        fanout = min(2, len(others))
        for sid in others[:fanout]:
            out.add(tuple(sorted((center_id, sid))))

        # Le reste se découvre en progressant dans les rues.
        for idx in range(len(others) - 1):
            out.add(tuple(sorted((others[idx], others[idx + 1]))))

        # Petite boucle secondaire pour éviter un tracé trop linéaire.
        if len(others) >= 4:
            out.add(tuple(sorted((others[1], others[3]))))

        return out

    def _all_nodes_reachable(
        self,
        local_ids: list[str],
        edges: set[tuple[str, str]],
        *,
        center_id: str,
    ) -> bool:
        if len(local_ids) < 2:
            return True
        adjacency: dict[str, set[str]] = {sid: set() for sid in local_ids}
        for a, b in edges:
            if a in adjacency and b in adjacency:
                adjacency[a].add(b)
                adjacency[b].add(a)
        if not adjacency.get(center_id):
            return False
        stack = [center_id]
        seen = {center_id}
        while stack:
            node = stack.pop()
            for nxt in adjacency.get(node, set()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return len(seen) == len(local_ids)

    def _street_label(self, *, source: Scene, target: Scene, center_scene_id: str) -> str:
        if target.id == center_scene_id:
            return "Revenir vers le centre"
        if source.id == center_scene_id:
            return f"Prendre la ruelle vers {self._short_scene_title(target.title)}"
        return f"Continuer vers {self._short_scene_title(target.title)}"

    def _short_scene_title(self, title: str) -> str:
        text = str(title or "").strip()
        if " - " in text:
            return text.split(" - ", 1)[1].strip()
        return text

    def _build_prompt(
        self,
        *,
        current_scene: Scene,
        current_anchor: str,
        target_anchor: str,
        existing_titles: list[str],
    ) -> str:
        schema = {
            "title": "Nom du nouveau lieu",
            "narrator_text": "Texte narrateur 1-3 phrases",
            "npcs": ["Nom PNJ 1", "Nom PNJ 2"],
            "travel_label_from_current": "Libellé du choix de voyage",
        }
        return (
            "Tu génères un nouveau lieu d'exploration pour un RPG dark-fantasy.\n"
            "Réponds en JSON valide uniquement, sans markdown.\n"
            "Le monde est AELYNDAR. Tu dois rester STRICTEMENT cohérent avec cette carte et ses routes officielles.\n"
            f"- Ancrages: {', '.join(MAP_ANCHORS)}\n"
            f"- Zone actuelle: {current_scene.title} (ancrage: {current_anchor})\n"
            f"- Destination imposée sur la route officielle: {target_anchor}\n"
            f"- Voisins officiels depuis {current_anchor}: {', '.join(official_neighbors(current_anchor))}\n"
            f"- Le lieu généré doit être un point du trajet entre {current_anchor} et {target_anchor}, pas ailleurs.\n"
            "- Evite de régénérer un lieu déjà existant.\n"
            f"- Lieux existants: {', '.join(existing_titles[:60])}\n"
            "Contraintes:\n"
            "- N'invente JAMAIS de nouvel ancrage de carte.\n"
            "- N'invente JAMAIS un chemin hors graphe officiel.\n"
            "- title: court, évocateur, pas de doublon exact avec les lieux existants.\n"
            "- Si le lieu est une ville ou un village, le title doit le mentionner clairement.\n"
            "- Evite les titres generiques ('Salle principale', 'Zone centrale', 'Rue principale').\n"
            "- Pour les lieux importants (taverne, temple, forge, boutique, etc.), prefere un sous-titre evocateur.\n"
            "- narrator_text: commence par 'Ataryxia :', 1 à 3 phrases, ambiance sombre.\n"
            "- npcs: 0 à 4 PNJ crédibles pour le lieu.\n"
            "- travel_label_from_current: phrase actionnable pour un bouton.\n"
            "Schéma:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
        )

    def _parse_draft(self, raw: str, *, target_anchor: str) -> LocationDraft:
        json_str = self._extract_json(raw)
        try:
            data = json.loads(json_str)
            draft = LocationDraft.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            draft = LocationDraft(
                title=f"Sentier Oublié vers {target_anchor}",
                narrator_text=f"Ataryxia : La piste se déchire sous vos pas, et la route vers {target_anchor} semble retenir son souffle.",
                npcs=[],
                travel_label_from_current="Explorer un sentier oublié",
            )

        if not draft.title.strip():
            draft.title = f"Sentier Oublié vers {target_anchor}"
        draft.title = self._retone_generated_title(draft.title, target_anchor=target_anchor)

        if not draft.narrator_text.strip().startswith("Ataryxia"):
            draft.narrator_text = f"Ataryxia : {draft.narrator_text.strip() or 'Un nouveau lieu s’ouvre devant vous.'}"

        return draft

    def _pick_target_anchor(self, current_anchor: str, neighbors: list[str], existing_scenes: dict[str, Scene]) -> str:
        if not neighbors:
            return current_anchor

        scene_counts: dict[str, int] = {}
        for scene in existing_scenes.values():
            if scene.map_anchor:
                scene_counts[scene.map_anchor] = scene_counts.get(scene.map_anchor, 0) + 1

        unseen = [a for a in neighbors if scene_counts.get(a, 0) == 0]
        if unseen:
            return random.choice(unseen)

        return min(neighbors, key=lambda a: (scene_counts.get(a, 0), a))

    def _infer_anchor(self, scene: Scene) -> str:
        if scene.map_anchor in MAP_ANCHORS:
            return scene.map_anchor

        title_norm = self._norm(scene.title)
        for anchor in MAP_ANCHORS:
            if self._norm(anchor) in title_norm or title_norm in self._norm(anchor):
                return anchor

        if any(k in title_norm for k in ("village", "taverne", "prison", "temple")):
            return "Lumeria"

        return "Lumeria"

    def _unique_scene_id(self, anchor: str, title: str, existing_ids: set[str]) -> str:
        base = f"gen_{self._slug(anchor)}_{self._slug(title)}"
        candidate = base
        index = 2
        while candidate in existing_ids:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _unique_title(self, title: str, existing_titles: set[str]) -> str:
        candidate = title.strip()
        if candidate not in existing_titles:
            return candidate
        index = 2
        while f"{candidate} ({index})" in existing_titles:
            index += 1
        return f"{candidate} ({index})"

    def _slug(self, text: str) -> str:
        raw = unicodedata.normalize("NFKD", (text or "").strip()).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        return slug or "lieu"

    def _norm(self, text: str) -> str:
        return self._slug(text)

    def extract_location_hints(self, text: str) -> list[str]:
        raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
        raw = re.sub(r"[’']", " ", raw)
        plain = re.sub(r"\s+", " ", raw).strip()
        if not plain:
            return []
        if not any(keyword in plain for keyword in _LOCATION_HINT_KEYWORDS):
            return []

        found_raw: list[str] = []
        keyword_group = "|".join(re.escape(k) for k in _LOCATION_HINT_KEYWORDS)
        context_group = "|".join(re.escape(c) for c in _LOCATION_HINT_CONTEXT_WORDS)
        # Cherche d'abord des segments contextualisés ("va a l'ecole des magiciens").
        contextual_pattern = re.compile(
            rf"\b(?:{context_group})\b\s+(?:l\s+|la\s+|le\s+|les\s+|du\s+|de\s+la\s+|de\s+l\s+|des\s+)?"
            rf"(?P<name>(?:{keyword_group})(?:\s+[a-z0-9]+){{0,7}})",
            flags=re.IGNORECASE,
        )
        for m in contextual_pattern.finditer(plain):
            value = str(m.group("name") or "").strip()
            if value:
                found_raw.append(value)

        # Fallback: tout groupe lexical démarrant par un mot-clé de lieu.
        broad_pattern = re.compile(
            rf"\b(?P<name>(?:{keyword_group})(?:\s+[a-z0-9]+){{0,7}})",
            flags=re.IGNORECASE,
        )
        for m in broad_pattern.finditer(plain):
            value = str(m.group("name") or "").strip()
            if value:
                found_raw.append(value)

        out: list[str] = []
        seen: set[str] = set()
        for candidate in found_raw:
            cleaned = self._clean_location_hint_phrase(candidate)
            if not cleaned:
                continue
            key = _norm_text_token(cleaned)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(cleaned)
        return out[:4]

    def suggest_hint_location_title(self, *, text: str, existing_titles: list[str]) -> str | None:
        hints = self.extract_location_hints(text)
        if not hints:
            return None

        existing_norms = [_norm_text_token(str(title or "")) for title in (existing_titles or [])]
        anchor_norms = {_norm_text_token(anchor) for anchor in MAP_ANCHORS}

        for hint in hints:
            hint_norm = _norm_text_token(hint)
            if not hint_norm:
                continue
            if hint_norm in anchor_norms:
                continue
            if self._is_hint_already_known(hint_norm, existing_norms):
                continue
            return hint
        return None

    def choose_hint_anchor(
        self,
        *,
        current_anchor: str,
        text: str,
        hint_title: str | None = None,
        rng: random.Random | None = None,
    ) -> str:
        current = canonical_anchor(current_anchor or "Lumeria")
        mentions = self.extract_anchor_mentions(text)
        if mentions:
            for anchor in mentions:
                if anchor != current:
                    return anchor
            return mentions[0]

        neighbors = official_neighbors(current)
        if not neighbors:
            return current

        combined_plain = _norm_text_token(" ".join(part for part in (text, hint_title or "") if part))
        has_neighbor_cue = any(cue in combined_plain for cue in _LOCATION_HINT_NEIGHBOR_CUES)

        is_merchant = any(token in combined_plain for token in _LOCATION_HINT_MERCHANT_TOKENS)
        is_major_structure = any(token in combined_plain for token in _LOCATION_HINT_MAJOR_STRUCTURE_TOKENS)
        is_special_merchant = is_merchant and any(
            token in combined_plain for token in _LOCATION_HINT_SPECIAL_MERCHANT_TOKENS
        )

        if is_major_structure:
            neighbor_probability = 0.20
        elif is_special_merchant:
            neighbor_probability = 0.14
        elif is_merchant:
            neighbor_probability = 0.03
        else:
            neighbor_probability = 0.07

        if has_neighbor_cue:
            if is_major_structure:
                neighbor_probability = max(neighbor_probability, 0.55)
            elif is_special_merchant:
                neighbor_probability = max(neighbor_probability, 0.28)
            else:
                neighbor_probability = max(neighbor_probability, 0.18)

        picker = rng or random
        try:
            roll = float(picker.random())
        except Exception:
            roll = random.random()
        if roll >= neighbor_probability:
            return current

        candidate_anchors = neighbors
        if is_special_merchant:
            city_pool = [anchor for anchor in MAP_ANCHORS if anchor != current and self.is_city_anchor(anchor)]
            if city_pool:
                candidate_anchors = city_pool

        try:
            return str(picker.choice(candidate_anchors))
        except Exception:
            return random.choice(candidate_anchors)

    def extract_anchor_mentions(self, text: str) -> list[str]:
        plain = _norm_text_token(text)
        if not plain:
            return []
        found: list[str] = []
        for anchor in MAP_ANCHORS:
            anchor_norm = _norm_text_token(anchor)
            if not anchor_norm:
                continue
            if anchor_norm in plain:
                found.append(anchor)
        return found[:3]

    def _is_hint_already_known(self, hint_norm: str, existing_norms: list[str]) -> bool:
        hint_tokens = [t for t in hint_norm.split(" ") if t and t not in _LOCATION_HINT_SMALL_WORDS]
        if not hint_tokens:
            return True
        for existing in existing_norms:
            if not existing:
                continue
            if hint_norm in existing or existing in hint_norm:
                return True
            existing_tokens = {t for t in existing.split(" ") if t and t not in _LOCATION_HINT_SMALL_WORDS}
            overlap = sum(1 for token in hint_tokens if token in existing_tokens)
            if overlap >= max(2, len(hint_tokens) - 1):
                return True
        return False

    def _clean_location_hint_phrase(self, text: str) -> str:
        tokens = [t for t in str(text or "").strip().split(" ") if t]
        if not tokens:
            return ""

        while tokens and tokens[-1] in _LOCATION_HINT_TRAILING_STOPWORDS:
            tokens.pop()
        if len(tokens) < 2:
            return ""

        out_words: list[str] = []
        for idx, token in enumerate(tokens[:8]):
            if idx > 0 and token in _LOCATION_HINT_SMALL_WORDS:
                out_words.append(token)
            else:
                out_words.append(token.capitalize())
        return " ".join(out_words).strip()

    def _retone_generated_title(self, title: str, *, target_anchor: str) -> str:
        raw_title = str(title or "").strip()
        if not raw_title:
            return raw_title

        norm = _norm_text_token(raw_title)

        prefix = raw_title
        suffix = ""
        if " - " in raw_title:
            prefix, suffix = raw_title.split(" - ", 1)
            prefix = prefix.strip()
            suffix = suffix.strip()

        category: dict[str, object] | None = None
        for rule in _LOCATION_RESONANCE_RULES:
            tokens = rule.get("tokens")
            if not isinstance(tokens, tuple):
                continue
            if any(str(token) in norm for token in tokens):
                category = rule
                break
        if category is None:
            return raw_title

        suffix_norm = _norm_text_token(suffix)
        title_norm = _norm_text_token(raw_title)
        short_core = suffix_norm if suffix else title_norm
        bland = False

        if suffix and suffix_norm in _GENERIC_LOCATION_SUFFIXES:
            bland = True
        if not suffix and title_norm in _GENERIC_LOCATION_SUFFIXES:
            bland = True

        if suffix:
            words = [w for w in suffix_norm.split(" ") if w]
            if words and len(words) <= 3 and all(w in _GENERIC_LOCATION_WORDS for w in words):
                bland = True

        category_tokens = category.get("tokens")
        if isinstance(category_tokens, tuple):
            if any(short_core == str(token) for token in category_tokens):
                bland = True

        if not bland:
            return raw_title

        category_key = str(category.get("key") or "lieu")
        category_label = str(category.get("label") or prefix or "Lieu").strip() or "Lieu"
        flavors = category.get("flavors")
        if not isinstance(flavors, list) or not flavors:
            return raw_title

        stable_prefix = prefix or category_label
        stable_suffix = str(suffix or short_core or "").strip()
        seed = f"loc_title::{category_key}::{target_anchor}::{stable_prefix.casefold()}::{stable_suffix.casefold()}"
        rng = random.Random(seed)
        flavored = str(flavors[rng.randrange(len(flavors))]).strip()
        if not flavored:
            return raw_title
        return f"{stable_prefix} - {flavored}".strip(" -")

    def _extract_json(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1]
        return "{}"
