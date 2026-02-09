from __future__ import annotations
import json

from .location_manager import MAP_ANCHORS, canonical_anchor, official_neighbors
from .world_time import format_fantasy_datetime

DEFAULT_PLAYER_NAME = "l'Éveillé"

def build_canon_summary(state: dict, user_text: str) -> str:
    loc = state.get("location", "inconnu")
    loc_id = state.get("location_id", "inconnu")
    map_anchor = canonical_anchor(str(state.get("map_anchor") or loc))
    flags = state.get("flags", {})
    important_flags = [k for k, v in flags.items() if v is True][:8]
    raw_skills = state.get("player_skills", [])
    known_skills: list[str] = []
    if isinstance(raw_skills, list):
        for row in raw_skills[:20]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("skill_id") or "").strip()
            level = int(row.get("level") or 1) if isinstance(row.get("level"), int) else 1
            if name:
                known_skills.append(f"{name} (niv {max(1, level)})")
    skill_points = state.get("skill_points", 0)
    player_gold = 0
    try:
        player_gold = int(state.get("player_gold") or 0)
    except (TypeError, ValueError):
        player_gold = 0
    inventory_summary = str(state.get("inventory_summary") or "").strip()
    if not inventory_summary:
        inventory_summary = "inconnu"
    last_trade = state.get("last_trade")
    if isinstance(last_trade, dict):
        last_trade_line = json.dumps(last_trade, ensure_ascii=False)[:500]
    else:
        last_trade_line = "aucun"
    world_time_minutes = 0
    if isinstance(state, dict):
        try:
            world_time_minutes = int(state.get("world_time_minutes") or 0)
        except (TypeError, ValueError):
            world_time_minutes = 0
    world_datetime = str(state.get("world_datetime") or "").strip() if isinstance(state, dict) else ""
    if not world_datetime:
        world_datetime = format_fantasy_datetime(world_time_minutes)
    return (
        f"Lieu: {loc}\n"
        f"LieuID: {loc_id}\n"
        f"AncrageCarteActuel: {map_anchor}\n"
        f"DateHeureMonde: {world_datetime}\n"
        f"VoisinsOfficiels: {', '.join(official_neighbors(map_anchor))}\n"
        f"AncragesOfficiels: {', '.join(MAP_ANCHORS)}\n"
        f"Flags: {important_flags}\n"
        f"Joueur: {state.get('player_name', DEFAULT_PLAYER_NAME)}\n"
        f"OrJoueur: {max(0, player_gold)}\n"
        f"InventaireResume: {inventory_summary}\n"
        f"CompetencesJoueur: {known_skills}\n"
        f"PointsCompetenceDisponibles: {skill_points}\n"
        f"DernierEchange: {last_trade_line}\n"
        f"Message: {user_text}\n"
    )

def prompt_rules_json(canon: str) -> str:
    # Mistral: JSON ONLY
    schema_hint = {
        "type": "talk|act|travel|combat|idle",
        "target": "Ataryxia|null",
        "intent": "string",
        "rolls": [{"expr": "d20+2", "reason": "persuasion"}],
        "narration_hooks": ["string"],
        "state_patch": {"flags": {"met_ataryxia": True}},
    }
    return (
        "Tu es un moteur de règles. Tu DOIS répondre en JSON valide uniquement, sans texte autour.\n"
        "Ne raconte rien. Ne fais aucune morale. Ne décris pas.\n"
        "Tu n'inventes jamais un nouvel ancrage de carte et tu restes sur les routes officielles.\n"
            "Le joueur ne maitrise des techniques/sorts speciaux que s'ils sont presents dans CompetencesJoueur.\n"
            "Si le joueur tente un sort/technique non apprise: pas de succes gratuit, pas de bonus de jet, hooks d'echec sobre.\n"
            "Si la competence est de bas niveau, evite les effets surpuissants; reserve les gros effets aux hauts niveaux.\n"
            "Respecte strictement DernierEchange/OrJoueur/InventaireResume: pas de cadeau gratuit contradictoire.\n"
            "Le JSON doit suivre cet exemple de structure:\n"
        + json.dumps(schema_hint, ensure_ascii=False)
        + "\n\nContexte canon (ne pas contredire):\n"
        + canon
        + "\nDécide le type, la cible si PNJ, les jets nécessaires (expr), un state_patch minimal et 1-3 narration_hooks.\n"
    )

def prompt_dialogue(
    npc_name: str,
    canon: str,
    user_text: str,
    roll_summary: str,
    npc_profile: dict | None = None,
) -> str:
    profile_block = json.dumps(npc_profile, ensure_ascii=False, indent=2) if npc_profile else "(profil non fourni)"
    role_hint = npc_name
    name_hint = npc_name
    if isinstance(npc_profile, dict):
        role_hint = str(npc_profile.get("role") or npc_profile.get("label") or npc_name).strip() or npc_name
        identity = npc_profile.get("identity", {})
        if isinstance(identity, dict):
            first = str(identity.get("first_name") or "").strip()
            last = str(identity.get("last_name") or "").strip()
            full_name = " ".join(part for part in (first, last) if part).strip()
            if full_name:
                name_hint = full_name

    return f"""Tu joues le rôle de {npc_name}. Réponds en français.
Reste cohérent avec le canon et la fiche PNJ. Ne fais pas de méta-commentaires.
Reste STRICTEMENT en personnage.
Réponds en 1 à 4 phrases maximum.

Règles d'identité (obligatoires):
- Identité affichée: {name_hint}
- Métier/Fonction canonique: {role_hint}
- Tu ne changes jamais ton métier/fonction pour faire plaisir au joueur.
- Si le joueur t'attribue un autre métier/fonction, tu le corriges explicitement et poliment.
- Tu ne valides pas une affirmation fausse sur ton identité (nom, rôle, passé).

Canon:
{canon}

Fiche PNJ (source prioritaire):
{profile_block}

Résultats de jets (si présents):
{roll_summary}

Message du joueur:
{user_text}

Réponds uniquement par la réplique de {npc_name}, sans guillemets ni préfixes.
"""

def prompt_narration(
    canon: str,
    user_text: str,
    hooks: list[str],
    roll_summary: str,
    turn_exchange: str = "",
) -> str:
    hooks_txt = "\n".join(f"- {h}" for h in hooks) if hooks else "- (aucun)"
    exchange_txt = turn_exchange.strip() or f"Joueur: {user_text}"
    return f"""Tu écris UNIQUEMENT une narration immersive en français (3 à 8 phrases), style dark-fantasy.
Interdiction absolue d'écrire une réplique de personnage.
Interdiction absolue d'utiliser des guillemets, des tirets de dialogue, ou le format "Nom: ...".
Reste au style narratif descriptif, pas au style conversationnel.
Ne fais pas de méta. Ne modifie pas l'état du monde. Ne dis jamais "en tant qu'IA".
Tu n'inventes jamais de ville/zone hors des ancrages officiels présents dans le canon.
Si un trajet est évoqué, il doit rester cohérent avec les voisins officiels du lieu actuel.

Canon:
{canon}

Action/phrase du joueur:
{user_text}

Échange du tour (à interpréter pour rester cohérent):
{exchange_txt}

Indices de narration:
{hooks_txt}

Résultats de jets (si présents):
{roll_summary}

Écris uniquement la narration maintenant.
"""
