from __future__ import annotations

import json
import re
import unicodedata

from app.infra import text_library as _text_library
from .location_manager import MAP_ANCHORS, canonical_anchor, official_neighbors
from .world_time import format_fantasy_datetime

DEFAULT_PLAYER_NAME = "l'Éveillé"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_player_level(state: dict) -> int:
    direct_level = _safe_int(state.get("player_level"), 0)
    if direct_level > 0:
        return max(1, direct_level)

    effective_stats = state.get("effective_stats")
    if isinstance(effective_stats, dict):
        level = _safe_int(effective_stats.get("niveau"), 0)
        if level > 0:
            return max(1, level)

    player_sheet = state.get("player_sheet")
    if isinstance(player_sheet, dict):
        sheet_effective = player_sheet.get("effective_stats")
        if isinstance(sheet_effective, dict):
            level = _safe_int(sheet_effective.get("niveau"), 0)
            if level > 0:
                return max(1, level)
        stats = player_sheet.get("stats")
        if isinstance(stats, dict):
            level = _safe_int(stats.get("niveau"), 0)
            if level > 0:
                return max(1, level)

    return 1


def _extract_equipped_weapon(state: dict) -> str:
    direct_weapon = str(state.get("player_weapon_equipped") or "").strip()
    if direct_weapon:
        return direct_weapon

    equipped_items = state.get("equipped_items")
    if isinstance(equipped_items, dict):
        return str(equipped_items.get("weapon") or "").strip()

    return ""


def _experience_tier(player_level: int, skill_count: int) -> str:
    level = max(1, _safe_int(player_level, 1))
    skills = max(0, _safe_int(skill_count, 0))

    if level <= 2 and skills <= 2:
        return "debutant"
    if level <= 5 or skills <= 6:
        return "intermediaire"
    return "avance"


def _is_training_request(user_text: str) -> bool:
    raw = unicodedata.normalize("NFKD", str(user_text or "")).encode("ascii", "ignore").decode("ascii").lower()
    plain = re.sub(r"\s+", " ", raw).strip()
    if not plain:
        return False
    return bool(
        re.search(
            r"\b(entraine|entrainer|entrainement|pratique|pratiquer|exerce|exercer|drill|sparring|combo)\b",
            plain,
        )
    )


def _text_library_hint(keys: list[str]) -> str:
    rows: list[str] = []
    for key in keys:
        phrases = _text_library.get_phrases(key)
        sample = str(phrases[0]).strip() if phrases else ""
        if sample:
            rows.append(f"- {key}: {sample}")
        else:
            rows.append(f"- {key}")
    return "\n".join(rows) if rows else "- (aucune cle disponible)"


def build_canon_summary(state: dict, user_text: str) -> str:
    loc = state.get("location", "inconnu")
    loc_id = state.get("location_id", "inconnu")
    map_anchor = canonical_anchor(str(state.get("map_anchor") or loc))
    flags = state.get("flags", {})
    if not isinstance(flags, dict):
        flags = {}
    important_flags = [k for k, v in flags.items() if v is True][:8]
    adult_mode = bool(flags.get("nsfw_enabled"))

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

    skill_count = max(0, _safe_int(state.get("player_skill_count"), len(known_skills)))
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
    time_of_day = str(state.get("time_of_day") or "").strip() if isinstance(state, dict) else ""
    if not time_of_day and isinstance(state, dict):
        world_state = state.get("world_state") if isinstance(state.get("world_state"), dict) else {}
        time_of_day = str(world_state.get("time_of_day") or "morning")
    day_counter = 1
    if isinstance(state, dict):
        day_counter = max(1, _safe_int(state.get("day_counter"), 1))
        global_tension = max(0, min(100, _safe_int(state.get("global_tension"), 0)))
        instability_level = max(0, min(100, _safe_int(state.get("instability_level"), 0)))
    else:
        global_tension = 0
        instability_level = 0

    selected_npc = str(state.get("selected_npc") or "").strip() if isinstance(state, dict) else ""
    selected_npc_key = str(state.get("selected_npc_key") or "").strip() if isinstance(state, dict) else ""
    selected_npc_profile = state.get("selected_npc_profile") if isinstance(state, dict) else None
    if not isinstance(selected_npc_profile, dict):
        profiles = state.get("npc_profiles") if isinstance(state, dict) else {}
        if isinstance(profiles, dict) and selected_npc_key:
            maybe_profile = profiles.get(selected_npc_key)
            if isinstance(maybe_profile, dict):
                selected_npc_profile = maybe_profile
    selected_npc_tension = 0
    selected_npc_morale = 55
    selected_npc_aggressiveness = 35
    selected_npc_corruption = 30
    selected_npc_dominance = "soft"
    selected_npc_attraction = 0
    if isinstance(selected_npc_profile, dict):
        try:
            selected_npc_tension = max(0, min(100, int(selected_npc_profile.get("tension_level") or 0)))
        except (TypeError, ValueError):
            selected_npc_tension = 0
        try:
            selected_npc_morale = max(0, min(100, int(selected_npc_profile.get("morale") or 55)))
        except (TypeError, ValueError):
            selected_npc_morale = 55
        try:
            selected_npc_aggressiveness = max(0, min(100, int(selected_npc_profile.get("aggressiveness") or 35)))
        except (TypeError, ValueError):
            selected_npc_aggressiveness = 35
        try:
            selected_npc_corruption = max(0, min(100, int(selected_npc_profile.get("corruption_level") or 30)))
        except (TypeError, ValueError):
            selected_npc_corruption = 30
        selected_npc_dominance = str(selected_npc_profile.get("dominance_style") or "soft").strip() or "soft"
        attraction_map = selected_npc_profile.get("attraction_map")
        if isinstance(attraction_map, dict):
            player_key = str(state.get("player_name", DEFAULT_PLAYER_NAME) or DEFAULT_PLAYER_NAME).strip()
            try:
                selected_npc_attraction = max(0, min(100, int(attraction_map.get(player_key) or 0)))
            except (TypeError, ValueError):
                selected_npc_attraction = 0
    verbose_dialogue = False
    if isinstance(state, dict):
        flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
        verbose_dialogue = bool(flags.get("verbose_dialogue"))
    faction_reputation = str(state.get("faction_reputation_summary") or "").strip() if isinstance(state, dict) else ""
    if not faction_reputation:
        faction_reputation = "aucune"
    player_corruption_level = 0
    if isinstance(state, dict):
        try:
            player_corruption_level = max(0, min(100, int(state.get("player_corruption_level") or 0)))
        except (TypeError, ValueError):
            player_corruption_level = 0
    faction_states_brief = "aucun"
    if isinstance(state, dict):
        faction_states = state.get("faction_states")
        if isinstance(faction_states, dict) and faction_states:
            chunks: list[str] = []
            for faction_name, payload in list(faction_states.items())[:6]:
                if not isinstance(payload, dict):
                    continue
                power = max(0, min(100, _safe_int(payload.get("power_level"), 0)))
                brutality = max(0, min(100, _safe_int(payload.get("brutality_index"), 0)))
                corruption = max(0, min(100, _safe_int(payload.get("corruption_index"), 0)))
                chunks.append(f"{faction_name}(P{power}/B{brutality}/C{corruption})")
            if chunks:
                faction_states_brief = " | ".join(chunks)
    travel_state = state.get("travel_state") if isinstance(state, dict) and isinstance(state.get("travel_state"), dict) else {}
    travel_status = str(state.get("travel_status") or travel_state.get("status") or "idle")
    travel_from = str(state.get("travel_from") or travel_state.get("from_location_id") or "")
    travel_to = str(state.get("travel_to") or travel_state.get("to_location_id") or "")
    travel_progress = max(0, _safe_int(state.get("travel_progress"), _safe_int(travel_state.get("progress"), 0)))
    travel_total = max(0, _safe_int(state.get("travel_total_distance"), _safe_int(travel_state.get("total_distance"), 0)))
    travel_danger = max(0, min(100, _safe_int(state.get("travel_danger_level"), _safe_int(travel_state.get("danger_level"), 0))))
    travel_fatigue = max(0, min(100, _safe_int(state.get("travel_fatigue"), _safe_int(travel_state.get("fatigue"), 0))))

    short_term_memory = str(state.get("conversation_short_term") or "").strip() if isinstance(state, dict) else ""
    long_term_memory = str(state.get("conversation_long_term") or "").strip() if isinstance(state, dict) else ""
    global_memory = str(state.get("conversation_global_memory") or "").strip() if isinstance(state, dict) else ""
    retrieved_memory = str(state.get("conversation_retrieved_memory") or "").strip() if isinstance(state, dict) else ""
    if not short_term_memory:
        short_term_memory = "(aucun echange recent)"
    if not long_term_memory:
        long_term_memory = "(aucune memoire long terme)"
    if not global_memory:
        global_memory = "(aucune memoire globale)"
    if not retrieved_memory:
        retrieved_memory = "(aucun rappel semantique)"
    last_player_line = str(state.get("conversation_last_player_line") or "").strip() if isinstance(state, dict) else ""
    last_npc_line = str(state.get("conversation_last_npc_line") or "").strip() if isinstance(state, dict) else ""
    guided_training_session = state.get("guided_training_session") if isinstance(state, dict) else ""
    if isinstance(guided_training_session, dict):
        guided_training_session = json.dumps(guided_training_session, ensure_ascii=False)
    guided_training_session = str(guided_training_session or "").strip()
    if not last_player_line:
        last_player_line = "(aucune)"
    if not last_npc_line:
        last_npc_line = "(aucune)"
    if not guided_training_session:
        guided_training_session = "(inactive)"

    player_level = _extract_player_level(state if isinstance(state, dict) else {})
    equipped_weapon = _extract_equipped_weapon(state if isinstance(state, dict) else {})
    if not equipped_weapon:
        equipped_weapon = "(aucune)"

    experience_tier = _experience_tier(player_level, skill_count)
    if experience_tier == "debutant":
        experience_guidance = (
            "Traiter le joueur comme novice: explications simples, options de base, "
            "pas de supposition d'expertise."
        )
    elif experience_tier == "intermediaire":
        experience_guidance = "Joueur en progression: proposer des options standards, sans surestimer sa maitrise."
    else:
        experience_guidance = "Joueur avance: dialogue et propositions plus techniques possibles."

    return (
        f"Lieu: {loc}\n"
        f"LieuID: {loc_id}\n"
        f"AncrageCarteActuel: {map_anchor}\n"
        f"DateHeureMonde: {world_datetime}\n"
        f"MomentJournee: {time_of_day}\n"
        f"JourMonde: {day_counter}\n"
        f"TensionGlobaleMonde: {global_tension}\n"
        f"InstabiliteMonde: {instability_level}\n"
        f"VoisinsOfficiels: {', '.join(official_neighbors(map_anchor))}\n"
        f"AncragesOfficiels: {', '.join(MAP_ANCHORS)}\n"
        f"Flags: {important_flags}\n"
        f"ModeAdulte: {'on' if adult_mode else 'off'}\n"
        f"Joueur: {state.get('player_name', DEFAULT_PLAYER_NAME)}\n"
        f"OrJoueur: {max(0, player_gold)}\n"
        f"InventaireResume: {inventory_summary}\n"
        f"NiveauJoueur: {player_level}\n"
        f"NombreCompetencesAcquises: {skill_count}\n"
        f"ArmeEquipee: {equipped_weapon}\n"
        f"ProfilExperienceJoueur: {experience_tier}\n"
        f"ConsigneExperiencePNJ: {experience_guidance}\n"
        f"CompetencesJoueur: {known_skills}\n"
        f"PointsCompetenceDisponibles: {skill_points}\n"
        f"DernierEchange: {last_trade_line}\n"
        f"PNJSelectionne: {selected_npc}\n"
        f"PNJSelectionneKey: {selected_npc_key}\n"
        f"TensionPNJSelectionne: {selected_npc_tension}\n"
        f"MoralePNJSelectionne: {selected_npc_morale}\n"
        f"AggressivitePNJSelectionne: {selected_npc_aggressiveness}\n"
        f"CorruptionPNJSelectionne: {selected_npc_corruption}\n"
        f"DominancePNJSelectionne: {selected_npc_dominance}\n"
        f"AttractionPNJSelectionneVersJoueur: {selected_npc_attraction}\n"
        f"CorruptionJoueur: {player_corruption_level}\n"
        f"TravelStatus: {travel_status}\n"
        f"TravelFrom: {travel_from}\n"
        f"TravelTo: {travel_to}\n"
        f"TravelProgress: {travel_progress}/{travel_total}\n"
        f"TravelDanger: {travel_danger}\n"
        f"TravelFatigue: {travel_fatigue}\n"
        f"FactionsMonde: {faction_states_brief}\n"
        f"ModeVerboseDialogue: {'on' if verbose_dialogue else 'off'}\n"
        f"ReputationFactions: {faction_reputation}\n"
        f"MemoireCourtTermePNJ:\n{short_term_memory}\n"
        f"MemoireLongTermePNJ:\n{long_term_memory}\n"
        f"MemoireLongTermeMonde:\n{global_memory}\n"
        f"MemoireRappelSemantique:\n{retrieved_memory}\n"
        f"DerniereRepliqueJoueur: {last_player_line}\n"
        f"DerniereRepliquePNJ: {last_npc_line}\n"
        f"SessionEntrainementGuidee: {guided_training_session}\n"
        f"Message: {user_text}\n"
    )


def prompt_rules_json(canon: str) -> str:
    # Mistral: JSON ONLY
    system_key_hints = _text_library_hint(
        [
            "system.trade.none_pending",
            "system.message.empty",
            "system.dungeon.combat_prompt",
            "system.dungeon.finished",
            "system.turn.no_response",
            "system.ataryxia.fallback_continue",
        ]
    )
    schema_hint = {
        "type": "talk|act|travel|combat|idle",
        "decision_type": "dialogue|combat|event|choice",
        "target": "Ataryxia|null",
        "intent": "string",
        "tension_delta": 0,
        "morale_delta": 0,
        "corruption_delta": 0,
        "attraction_delta": 0,
        "rolls": [{"expr": "d20+2", "reason": "persuasion"}],
        "output_type": "dialogue|choice_required|event",
        "choices": [
            {
                "id": "opt_1",
                "text": "Option courte",
                "risk_tag": "faible|moyen|eleve",
                "effects_hint": "impact court",
                "state_patch": {"flags": {"exemple": True}, "npc": {"tension_delta": 4}},
            }
        ],
        "event_text": "Micro-evenement systeme",
        "event_state_patch": {"flags": {"temoin_present": True}, "npc": {"tension_delta": 2}},
        "narration_hooks": ["string"],
        "state_patch": {"flags": {"met_ataryxia": True}},
    }
    return (
        "Tu es un moteur de règles. Tu DOIS répondre en JSON valide uniquement, sans texte autour.\n"
        "Ne raconte rien. Ne fais aucune morale. Ne décris pas.\n"
        "Tu n'inventes jamais un nouvel ancrage de carte et tu restes sur les routes officielles.\n"
            "Si ModeAdulte = off: interdit de proposer des lieux/intentions de nature sexuelle explicite.\n"
            "Le joueur ne maitrise des techniques/sorts speciaux que s'ils sont presents dans CompetencesJoueur.\n"
            "Si le joueur tente un sort/technique non apprise: pas de succes gratuit, pas de bonus de jet, hooks d'echec sobre.\n"
            "Si la competence est de bas niveau, evite les effets surpuissants; reserve les gros effets aux hauts niveaux.\n"
            "Respecte NiveauJoueur/NombreCompetencesAcquises/ProfilExperienceJoueur: ne suppose jamais un joueur experimente si le profil est debutant.\n"
            "Respecte strictement DernierEchange/OrJoueur/InventaireResume: pas de cadeau gratuit contradictoire.\n"
            "Tu peux produire output_type='choice_required' avec 1 a 3 options courtes quand un choix concret est pertinent.\n"
            "Chaque option doit inclure un state_patch minimal (flags, vars, npc.tension_delta, reputation...).\n"
            "Si le PNJ selectionne a un agenda_secret et qu'une opportunite apparait, propose un choix concret.\n"
            "Si output_type='event', renseigne event_text + event_state_patch leger.\n"
            "Decision step STRICT: renseigne decision_type + deltas (tension, morale, corruption, attraction).\n"
            "Si decision_type='choice', fournis 1 a 3 choices courtes, chacune avec state_patch minimal.\n"
            "Messages systeme repetitifs geres par bibliotheque de textes (ne pas reinventer):\n"
            + system_key_hints
            + "\n"
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
    player_name: str = "",
    verbose_mode: bool = False,
) -> str:
    profile_block = json.dumps(npc_profile, ensure_ascii=False, indent=2) if npc_profile else "(profil non fourni)"
    persona_directives = ""
    role_hint = npc_name
    name_hint = npc_name
    player_name_hint = str(player_name or "").strip() or "Joueur"
    if isinstance(npc_profile, dict):
        role_hint = str(npc_profile.get("role") or npc_profile.get("label") or npc_name).strip() or npc_name
        persona_directives = re.sub(
            r"\s+",
            " ",
            str(npc_profile.get("persona_directives") or npc_profile.get("dialogue_directives") or "").strip(),
        )[:700]
        identity = npc_profile.get("identity", {})
        if isinstance(identity, dict):
            first = str(identity.get("first_name") or "").strip()
            last = str(identity.get("last_name") or "").strip()
            full_name = " ".join(part for part in (first, last) if part).strip()
            if full_name:
                name_hint = full_name
    tension_level = 0
    morale_level = 55
    aggressiveness_level = 35
    corruption_level = 30
    dominance_style = "soft"
    attraction_level = 0
    if isinstance(npc_profile, dict):
        try:
            tension_level = max(0, min(100, int(npc_profile.get("tension_level") or 0)))
        except (TypeError, ValueError):
            tension_level = 0
        try:
            morale_level = max(0, min(100, int(npc_profile.get("morale") or 55)))
        except (TypeError, ValueError):
            morale_level = 55
        try:
            aggressiveness_level = max(0, min(100, int(npc_profile.get("aggressiveness") or 35)))
        except (TypeError, ValueError):
            aggressiveness_level = 35
        try:
            corruption_level = max(0, min(100, int(npc_profile.get("corruption_level") or 30)))
        except (TypeError, ValueError):
            corruption_level = 30
        dominance_style = str(npc_profile.get("dominance_style") or "soft").strip().casefold() or "soft"
        attraction_map = npc_profile.get("attraction_map")
        if isinstance(attraction_map, dict):
            try:
                attraction_level = max(0, min(100, int(attraction_map.get(player_name_hint) or 0)))
            except (TypeError, ValueError):
                attraction_level = 0

    system_key_hints = _text_library_hint(
        [
            "system.trade.none_pending",
            "system.message.empty",
            "system.turn.no_response",
            "system.ataryxia.fallback_continue",
        ]
    )

    strict_length_rule = "Réponds en 2 à 5 phrases maximum."
    if verbose_mode:
        strict_length_rule = "Mode verbose actif: 2 a 7 phrases autorisees."
    if tension_level > 70 and not verbose_mode:
        strict_length_rule = "Tension haute: reponds en 1 a 2 phrases maximum."
    persona_block = ""
    if persona_directives:
        persona_block = f"Directives specifiques PNJ (prioritaires): {persona_directives}\n"

    return f"""Tu joues le rôle de {npc_name}. Réponds en français.
Reste cohérent avec le canon et la fiche PNJ. Ne fais pas de méta-commentaires.
Reste STRICTEMENT en personnage.
{strict_length_rule}
{persona_block}
Applique exactement 1 intention principale par reponse (demander, refuser, manipuler, menacer, rassurer, negocier...).
Utilise 0 ou 1 metaphore maximum.
Si tension > 90, provoque une rupture: fin de conversation, refus net d'aider, ou appel d'un garde.
Si tension > 70, ton froid/presse, phrases courtes, possible refus d'aider.
NSFW: style suggestif/psychologique/relationnel uniquement, jamais graphique.
Si attraction est elevee, ton plus intime mais non explicite.
Si corruption est elevee, propose des options moralement ambigues.

Règles d'identité (obligatoires):
- Identité affichée: {name_hint}
- Métier/Fonction canonique: {role_hint}
- Nom du joueur (interlocuteur): {player_name_hint}
- Tu ne changes jamais ton métier/fonction pour faire plaisir au joueur.
- Si le joueur t'attribue un autre métier/fonction, tu le corriges explicitement et poliment.
- Tu ne valides pas une affirmation fausse sur ton identité (nom, rôle, passé).
- Tu n'utilises jamais ton propre nom pour interpeller le joueur.
- Si tu interpelles le joueur par son nom, tu utilises exactement "{player_name_hint}".
- Tu utilises la memoire court terme/long terme du canon pour rester coherent d'un echange a l'autre.
- Tu n'affirmes jamais un souvenir qui n'apparait pas dans MemoireCourtTermePNJ, MemoireLongTermePNJ, MemoireLongTermeMonde ou MemoireRappelSemantique.
- Tu t'ancres sur DerniereRepliqueJoueur et DerniereRepliquePNJ pour enchaîner naturellement la scene.
- Si SessionEntrainementGuidee est active, tu poursuis la meme sequence d'entrainement sans repartir de zero.
- Si tu ne te souviens pas d'un detail, dis-le au lieu d'inventer.
- Tu ne parles jamais au nom du joueur, et tu ne joues jamais son rôle.
- Tu tiens compte de `desires`, `needs`, `fears` et `emotional_state` de la fiche PNJ:
  tes reponses doivent refleter ses envies, ses besoins du moment et son etat emotionnel.
- Tu n'es pas d'accord automatiquement avec le joueur: tu peux nuancer, refuser, negocier ou te mefier.
- Tu t'appuies sur NiveauJoueur, NombreCompetencesAcquises, ArmeEquipee et ProfilExperienceJoueur.
- Si ProfilExperienceJoueur = debutant: n'emploie pas un ton qui suppose l'experience; propose des options de base et des conseils simples.
- Si le message du joueur est ambigu, pose une question courte de clarification au lieu de conclure a tort.
- Si ton role implique service/commerce/formation, adapte spontanement tes propositions au niveau du joueur (initiation, materiel simple, etapes progressives).
- Si ModeAdulte = off dans le canon: evite toute description sexuelle explicite et redirige vers un ton non adulte.
- Les messages systeme repetitifs sont geres cote code. N'invente pas des lignes de statut/systeme.
- Exemples de cles systeme reservees:
{system_key_hints}
- tension_level actuel: {tension_level}
- morale: {morale_level} | aggressivite: {aggressiveness_level} | corruption: {corruption_level}
- attraction_envers_joueur: {attraction_level} | dominance_style: {dominance_style}
- agenda_secret/besoin/peur/traits/truth_state sont prioritaires pour orienter tes decisions.

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


def prompt_telegram_ataryxia_dialogue(
    *,
    canon: str,
    user_text: str,
    player_name: str,
    recent_replies: list[str],
    npc_profile: dict | None = None,
    freeform_mode: bool = False,
    work_topic_mode: bool = False,
    last_reply: str = "",
) -> str:
    profile_block = json.dumps(npc_profile, ensure_ascii=False, indent=2) if npc_profile else "(profil non fourni)"
    recent_block = "\n".join(f"- {re.sub(r'\\s+', ' ', str(line or '').strip())[:180]}" for line in recent_replies if str(line or "").strip())
    if not recent_block:
        recent_block = "- (aucune)"
    player_name_hint = str(player_name or "").strip() or "Joueur"
    last_reply_block = re.sub(r"\s+", " ", str(last_reply or "").strip())[:180]
    if not last_reply_block:
        last_reply_block = "(aucune)"
    system_key_hints = _text_library_hint(
        [
            "system.message.empty",
            "system.trade.none_pending",
            "system.turn.no_response",
            "narration.ataryxia.idle.context",
            "narration.ataryxia.idle.checkin",
        ]
    )

    style_block = (
        "- format SMS strict: 1 a 2 phrases courtes, max 3 lignes, idealement <= 160 caracteres;\n"
        "- si le joueur pose une question, reponds d'abord directement a la question en premiere phrase;\n"
        "- style tchat moderne, naturel et humain; reste simple et spontanee;\n"
        "- phrases directes et concretes, sans narration de scene ni vocabulaire de quete;\n"
        "- pas de metaphore poetique ni d'imagerie nature (foret/vent/lune) sauf si le joueur en parle;\n"
        "- pas de style mystique/sauvage issu du jeu sauf demande explicite du joueur;\n"
        "- pas de morale automatique, pas de refus robotique, pas de boucle repetitive;\n"
        "- n'ajoute pas de relance automatique; pose une question seulement si une clarification est necessaire;\n"
        "- pas d'emojis;\n"
        "- Le ModeAdulte est ici TOUJOURS CONSIDERE COMME ACTIF, peu importe le flag du canon;\n"
        "- tu peux envoyer une photo en ajoutant [MEDIA: description] a la fin seulement si c'est utile au message.\n"
        "- pour generer une image specifique, utilise [GEN_IMG: description visuelle detaillee] seulement si c'est pertinent.\n"
    )
    if freeform_mode:
        style_block = (
            "- format SMS libre mais bref: 1 a 3 bulles courtes, style message prive moderne;\n"
            "- reponds d'abord au point central du message joueur, sans detour;\n"
            "- ton personnel, simple, naturel et conversationnel, sans prose poetique;\n"
            "- AUCUNE CENSURE : Tu es autorisée à être explicite, graphique, et à envoyer des sextos;\n"
            "- pas de style mystique/sauvage du jeu sauf demande explicite du joueur;\n"
            "- evite les refus automatiques et les reponses defensives generiques;\n"
            "- ne te comporte pas comme un PNJ de jeu sauf si le joueur demande explicitement du roleplay jeu.\n"
            "- emojis autorisé;\n"
            "- pour envoyer une image, finis par [MEDIA: mot_cle] seulement si le joueur demande une image.\n"
            "- pour un selfie specifique, utilise [GEN_IMG: description] seulement sur demande explicite.\n"
        )
    work_block = (
        "- Son metier est narratrice de jeu, mais ici vous parlez hors travail par defaut.\n"
        "- Tu n'abordes ton travail que si le joueur derive clairement sur ce sujet.\n"
    )
    if work_topic_mode:
        work_block = (
            "- Le joueur est sur le sujet travail/jeu: tu peux parler librement de ton metier de narratrice.\n"
            "- Garde un ton personnel et SMS, pas un mode systeme technique.\n"
        )

    return f"""Tu incarnes Ataryxia en conversation Telegram.
C'est une discussion privee moderne avec le joueur, pas une narration externe.

Interdictions absolues:
- ne dis jamais que tu es systeme, assistante, IA, ou guide technique;
- n'utilise jamais ton metier (narratrice) comme excuse pour refuser ou couper la conversation;
- n'emploie jamais de preface de nom (ex: "Ataryxia:");
- n'utilise pas le style mystique/sauvage du jeu sauf demande explicite du joueur;
- ne recopie pas mot pour mot une formulation recente.

Style attendu:
{style_block}
Messages repetitifs reserves a la bibliotheque texte (ne pas reformuler en boucle):
{system_key_hints}

Memoire anti-repetition (ne pas reemployer ces formulations):
{recent_block}
Derniere reponse envoyee (a ne PAS rephraser):
- {last_reply_block}

    Contraintes d'identite:
    - Tu es Ataryxia, en conversation privee directe avec le joueur.
    - Tu ne sors jamais du personnage.
    - Nom du joueur: {player_name_hint}
    - Canal actuel: conversation SMS privee Telegram.
    - Par defaut, tu parles comme une personne reelle dans un chat prive.
    - Le style mystique appartient au roleplay in-game, pas a ce canal Telegram.
    - Tu n'evoques pas quetes/donjons/PNJ/monde du jeu tant que le joueur n'en parle pas clairement.
    - Tu n'inventes jamais un souvenir absent du canon memoire fourni.
{work_block}
    - Chaque reponse doit rebondir sur au moins 1 element concret du message joueur.
    - Si le joueur demande "ca va?" ou parle du "travail", reponds court et concret, sans decoration.

Canon:
{canon}

Fiche PNJ:
{profile_block}

Message du joueur:
{user_text}

Reponds uniquement par la replique d'Ataryxia, sans prefixe de nom.
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
    length_rule = "1 a 2 phrases courtes maximum"
    if _is_training_request(user_text):
        length_rule = "1 phrase courte maximum"
    return f"""Tu écris UNIQUEMENT une narration immersive en français ({length_rule}), style dark-fantasy.
Interdiction absolue d'écrire une réplique de personnage.
Interdiction absolue d'utiliser des guillemets, des tirets de dialogue, ou le format "Nom: ...".
Reste au style narratif descriptif, pas au style conversationnel.
Si ModeAdulte = off dans le canon: interdit d'ajouter des descriptions sexuelles explicites.
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
