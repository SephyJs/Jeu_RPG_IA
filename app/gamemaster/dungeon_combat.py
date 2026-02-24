from __future__ import annotations

import random
import re
import unicodedata
from typing import Any


COMBAT_EVENT_TYPES = {"monster", "mimic", "boss"}
_STAT_KEYS = (
    "force",
    "intelligence",
    "magie",
    "defense",
    "sagesse",
    "agilite",
    "dexterite",
    "chance",
    "charisme",
)
_ACTION_STAT_PRIORITY = {
    "attack": ("force", "dexterite", "agilite"),
    "spell": ("magie", "intelligence", "sagesse"),
    "heal": ("sagesse", "magie", "intelligence"),
    "defend": ("defense", "sagesse", "agilite"),
}
_OUTCOME_LABEL = {
    "critical_success": "COUP CRITIQUE",
    "success": "SUCCES",
    "failure": "ECHEC",
    "critical_failure": "ECHEC CRITIQUE",
}

_HEAL_RE = re.compile(
    r"\b(soin\w*|soign\w*|heal\w*|gueri\w*|regene\w*|restaur\w*)\b",
    flags=re.IGNORECASE,
)
_DEFEND_RE = re.compile(r"\b(defen|parad|protec|bouclier|esquiv|garde)\b", flags=re.IGNORECASE)
_SPELL_RE = re.compile(r"\b(sort|magie|arcane|rituel|incant|mana)\b", flags=re.IGNORECASE)
_ATTACK_RE = re.compile(r"\b(attaque|attaquer|frappe|frapper|tape|coup|charge|taillade)\b", flags=re.IGNORECASE)
_STATUS_EFFECTS = {"poison", "bleed", "weakened"}
_GORE_DEFEAT_FINISHERS = {
    "boss": (
        "vous brise la cage thoracique d'un impact titanesque.",
        "vous cloue au sol et vous ecrase les membres un a un.",
        "vous ouvre le ventre et disperse vos entrailles sur les dalles.",
    ),
    "mimic": (
        "referme sa gueule sur votre torse et vous arrache des morceaux de chair.",
        "vous broie les os dans son coffre vivant avant de vous recracher en lambeaux.",
        "vous happe puis vous demembre dans un craquement de vertebres.",
    ),
    "monster": (
        "vous demembre dans une pluie de sang.",
        "vous arrache un bras et vous devore vivant.",
        "vous ouvre la gorge et vous laisse vous vider sur la pierre.",
    ),
}
_GORE_DEFEAT_AFTERMATH = (
    "Vos allies ne recuperent que des restes et vous trainent hors du donjon.",
    "Le sol reste couvre de sang pendant qu'on evacue ce qu'il reste de vous.",
    "On vous ramasse en pieces, puis on vous recoud a la hate hors combat.",
)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_status_map(raw: object) -> dict[str, int]:
    out = {"poison": 0, "bleed": 0, "weakened": 0}
    if not isinstance(raw, dict):
        return out
    for key in _STATUS_EFFECTS:
        out[key] = max(0, min(8, _safe_int(raw.get(key), 0)))
    return out


def _enemy_ability_for(*, event_type: str, archetype: str) -> str:
    kind = str(event_type or "").strip().casefold()
    arch = str(archetype or "").strip().casefold()
    if kind == "mimic":
        return "poison_claw"
    if kind == "boss":
        return "guard_break"
    if "tank" in arch:
        return "guard_break"
    if "trick" in arch:
        return "poison_claw"
    if "brute" in arch:
        return "bleed_strike"
    if "spectral" in arch:
        return "weaken_aura"
    return ""


def _relic_bonus(run_relic: dict | None, effect: str) -> int:
    if not isinstance(run_relic, dict):
        return 0
    kind = str(run_relic.get("effect") or "").strip().casefold()
    if kind != str(effect or "").strip().casefold():
        return 0
    return max(0, min(12, _safe_int(run_relic.get("bonus"), 0)))


def _apply_status_damage(
    *,
    hp: int,
    statuses: dict[str, int],
    rng: random.Random,
) -> tuple[int, list[str], dict[str, int]]:
    next_hp = max(0, _safe_int(hp, 0))
    lines: list[str] = []
    status_map = _normalize_status_map(statuses)

    for key in ("poison", "bleed"):
        turns = max(0, _safe_int(status_map.get(key), 0))
        if turns <= 0:
            continue
        if key == "poison":
            damage = rng.randint(1, 3)
            lines.append(f"Effet poison: -{damage} PV.")
        else:
            damage = rng.randint(1, 2)
            lines.append(f"Effet saignement: -{damage} PV.")
        next_hp = max(0, next_hp - damage)
        status_map[key] = max(0, turns - 1)

    weakened = max(0, _safe_int(status_map.get("weakened"), 0))
    if weakened > 0:
        status_map["weakened"] = max(0, weakened - 1)
    return next_hp, lines, status_map


def is_combat_event(event: dict | None) -> bool:
    if not isinstance(event, dict):
        return False
    return str(event.get("type") or "").strip().casefold() in COMBAT_EVENT_TYPES


def wants_repeat_heal_until_full(action_text: str) -> bool:
    plain = unicodedata.normalize("NFKD", str(action_text or "")).encode("ascii", "ignore").decode("ascii").lower()
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return False
    if not _HEAL_RE.search(plain):
        return False
    repeat_tokens = (
        "jusqu",
        "tant que",
        "until",
        "full",
        "complet",
        "completement",
        "au max",
        "maximum",
        "100%",
    )
    return any(token in plain for token in repeat_tokens)


def _pick_random_line(lines: tuple[str, ...], rng: random.Random) -> str:
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    try:
        idx = rng.randint(0, len(lines) - 1)
    except Exception:
        idx = 0
    return lines[max(0, min(len(lines) - 1, int(idx)))]


def build_gore_defeat_lines(*, enemy_name: str, event_type: str, rng: random.Random | None = None) -> list[str]:
    source_rng = rng or random.Random()
    enemy = str(enemy_name or "L'ennemi").strip() or "L'ennemi"
    kind = str(event_type or "").strip().casefold()
    finishers = _GORE_DEFEAT_FINISHERS.get(kind) or _GORE_DEFEAT_FINISHERS["monster"]
    finisher = _pick_random_line(finishers, source_rng)
    aftermath = _pick_random_line(_GORE_DEFEAT_AFTERMATH, source_rng)
    lines: list[str] = []
    if finisher:
        lines.append(f"{enemy} {finisher}")
    if aftermath:
        lines.append(aftermath)
    return lines


def build_combat_state(
    event: dict,
    *,
    rng: random.Random | None = None,
    monster_manager: Any = None,
) -> dict:
    rng = rng or random.Random()
    event_type = str(event.get("type") or "monster").strip().casefold()
    floor = max(1, _safe_int(event.get("floor"), 1))
    enemy_name = str(event.get("name") or "Adversaire").strip() or "Adversaire"
    run_relic = event.get("run_relic") if isinstance(event.get("run_relic"), dict) else {}

    if monster_manager is not None:
        try:
            profile = monster_manager.combat_profile_for_event(event)
        except Exception:
            profile = None
        if isinstance(profile, dict):
            enemy_name = str(profile.get("enemy_name") or enemy_name).strip() or enemy_name
            enemy_hp = max(1, _safe_int(profile.get("enemy_hp"), 12))
            dc = max(8, _safe_int(profile.get("dc"), 12))
            attack_bonus = max(1, _safe_int(profile.get("enemy_attack_bonus"), 3))
            damage_min = max(1, _safe_int(profile.get("enemy_damage_min"), 3))
            damage_max = max(damage_min, _safe_int(profile.get("enemy_damage_max"), 6))
            return {
                "active": True,
                "event_type": event_type,
                "floor": floor,
                "enemy_name": enemy_name[:120],
                "enemy_hp": enemy_hp,
                "enemy_max_hp": enemy_hp,
                "dc": dc,
                "enemy_attack_bonus": attack_bonus,
                "enemy_damage_min": damage_min,
                "enemy_damage_max": damage_max,
                "guard": 0,
                "round": 1,
                "monster_id": str(profile.get("monster_id") or event.get("monster_id") or "").strip().casefold(),
                "monster_base_name": str(profile.get("base_name") or "").strip()[:120],
                "monster_archetype": str(profile.get("archetype") or "").strip()[:40],
                "monster_tier": max(1, _safe_int(profile.get("tier"), 1)),
                "monster_description": str(profile.get("description") or "").strip()[:220],
                "monster_image": str(profile.get("media_image") or "").strip()[:240],
                "monster_clip": str(profile.get("media_clip") or "").strip()[:240],
                "enemy_ability": _enemy_ability_for(
                    event_type=event_type,
                    archetype=str(profile.get("archetype") or ""),
                ),
                "player_status": {"poison": 0, "bleed": 0, "weakened": 0},
                "enemy_status": {"poison": 0, "bleed": 0, "weakened": 0},
                "player_fail_streak": 0,
                "heal_streak": 0,
                "run_relic": run_relic,
            }

    if event_type == "boss":
        enemy_hp = rng.randint(28, 40) + floor
        dc = min(22, 14 + (floor // 5))
        attack_bonus = 5 + (floor // 5)
        damage_min, damage_max = 6, 12
    elif event_type == "mimic":
        enemy_hp = rng.randint(18, 30) + (floor // 2)
        dc = min(20, 13 + (floor // 6))
        attack_bonus = 4 + (floor // 7)
        damage_min, damage_max = 4, 9
    else:
        enemy_hp = rng.randint(14, 24) + (floor // 2)
        dc = min(19, 12 + (floor // 7))
        attack_bonus = 3 + (floor // 8)
        damage_min, damage_max = 3, 8

    return {
        "active": True,
        "event_type": event_type,
        "floor": floor,
        "enemy_name": enemy_name[:120],
        "enemy_hp": max(1, enemy_hp),
        "enemy_max_hp": max(1, enemy_hp),
        "dc": max(8, dc),
        "enemy_attack_bonus": max(1, attack_bonus),
        "enemy_damage_min": max(1, damage_min),
        "enemy_damage_max": max(damage_min, damage_max),
        "guard": 0,
        "round": 1,
        "enemy_ability": _enemy_ability_for(event_type=event_type, archetype=""),
        "player_status": {"poison": 0, "bleed": 0, "weakened": 0},
        "enemy_status": {"poison": 0, "bleed": 0, "weakened": 0},
        "player_fail_streak": 0,
        "heal_streak": 0,
        "run_relic": run_relic,
    }


def resolve_combat_turn(
    *,
    combat_state: dict,
    action_text: str,
    player_hp: int,
    player_max_hp: int,
    player_sheet: dict | None,
    known_skills: list[dict] | None,
    runtime_stat_bonuses: dict[str, int] | None = None,
    skill_manager: Any = None,
    rng: random.Random | None = None,
    run_relic: dict | None = None,
) -> dict:
    rng = rng or random.Random()
    if not isinstance(combat_state, dict):
        hp = max(0, _safe_int(player_hp, 1))
        max_hp = max(1, _safe_int(player_max_hp, max(1, hp)))
        hp = min(hp, max_hp)
        return {
            "combat": {},
            "player_hp": hp,
            "player_max_hp": max_hp,
            "enemy_hp": 0,
            "enemy_max_hp": 0,
            "outcome": "failure",
            "enemy_outcome": "",
            "action_kind": "attack",
            "used_skill_ids": [],
            "used_skill_names": [],
            "victory": False,
            "defeat": False,
            "lines": ["Combat indisponible."],
        }

    combat = dict(combat_state)
    enemy_hp = max(0, _safe_int(combat.get("enemy_hp"), 0))
    enemy_max_hp = max(1, _safe_int(combat.get("enemy_max_hp"), max(1, enemy_hp)))
    combat_relic = run_relic if isinstance(run_relic, dict) else (
        combat.get("run_relic") if isinstance(combat.get("run_relic"), dict) else {}
    )
    player_max_hp = max(1, _safe_int(player_max_hp, 20) + _relic_bonus(combat_relic, "max_hp"))
    player_hp = min(max(0, _safe_int(player_hp, player_max_hp)), player_max_hp)
    player_before = player_hp
    guard = max(0, _safe_int(combat.get("guard"), 0))
    dc = max(8, _safe_int(combat.get("dc"), 12))
    player_status = _normalize_status_map(combat.get("player_status"))

    used_skill_ids, used_skill_rows = _detect_used_skills(
        action_text=action_text,
        known_skills=known_skills if isinstance(known_skills, list) else [],
        skill_manager=skill_manager,
    )
    action_kind = _resolve_action_kind(action_text, used_skill_rows)
    stat_bonus = _stat_bonus(
        player_sheet,
        _ACTION_STAT_PRIORITY.get(action_kind, _ACTION_STAT_PRIORITY["attack"]),
        runtime_stat_bonuses=runtime_stat_bonuses,
    )
    skill_bonus = _skill_bonus(used_skill_rows)
    total_bonus = stat_bonus + skill_bonus

    lines: list[str] = []
    player_hp, status_lines, player_status = _apply_status_damage(
        hp=player_hp,
        statuses=player_status,
        rng=rng,
    )
    for line in status_lines:
        lines.append(line)
    if player_hp <= 0:
        lines.append("Les effets persistants vous mettent a terre.")
        combat["player_status"] = player_status
        combat["active"] = False
        return {
            "combat": combat,
            "player_hp": 0,
            "player_max_hp": player_max_hp,
            "enemy_hp": max(0, enemy_hp),
            "enemy_max_hp": enemy_max_hp,
            "outcome": "failure",
            "enemy_outcome": "",
            "action_kind": action_kind,
            "used_skill_ids": used_skill_ids,
            "used_skill_names": [],
            "victory": False,
            "defeat": True,
            "lines": lines,
            "player_delta": -max(0, player_before),
        }

    if _safe_int(player_status.get("weakened"), 0) > 0:
        total_bonus -= 2
        lines.append("Affaiblissement: -2 au jet joueur.")

    previous_fail_streak = max(0, _safe_int(combat.get("player_fail_streak"), 0))
    fail_streak_bonus = min(4, previous_fail_streak)
    if fail_streak_bonus > 0:
        total_bonus += fail_streak_bonus
        lines.append(f"Reprise tactique: +{fail_streak_bonus} (serie d'echecs).")

    heal_streak = max(0, _safe_int(combat.get("heal_streak"), 0))
    if action_kind == "heal":
        heal_streak += 1
        if heal_streak > 1:
            heal_penalty = min(4, heal_streak - 1)
            total_bonus -= heal_penalty
            lines.append(f"Fatigue de canalisation: -{heal_penalty} au soin.")
    else:
        heal_streak = 0

    relic_attack_bonus = _relic_bonus(combat_relic, "attack")
    relic_defense_bonus = _relic_bonus(combat_relic, "defense")
    relic_heal_bonus = _relic_bonus(combat_relic, "heal")
    if action_kind in {"attack", "spell"} and relic_attack_bonus > 0:
        total_bonus += relic_attack_bonus
        lines.append(f"Relique offensive: +{relic_attack_bonus} au jet.")
    skill_names = [str(row.get("name") or row.get("skill_id") or "").strip() for row in used_skill_rows]
    skill_names = [name for name in skill_names if name]
    if skill_names:
        lines.append("Competence(s) mobilisee(s): " + ", ".join(skill_names[:2]))

    roll_raw = rng.randint(1, 20)
    roll_total = roll_raw + total_bonus
    player_dc = dc
    if action_kind == "heal":
        player_dc = max(8, dc - 2)
    elif action_kind == "defend":
        player_dc = max(8, dc - 1)

    outcome = _classify_roll(roll_raw, roll_total, player_dc)
    lines.append(
        f"Jet joueur: d20={roll_raw} + bonus={total_bonus} => {roll_total} vs DD {player_dc} ({_OUTCOME_LABEL.get(outcome, outcome)})."
    )

    if action_kind == "heal":
        player_hp, enemy_hp, guard, player_line = _resolve_heal_action(
            outcome=outcome,
            player_hp=player_hp,
            player_max_hp=player_max_hp,
            enemy_hp=enemy_hp,
            guard=guard,
            bonus=total_bonus + relic_heal_bonus,
            rng=rng,
        )
        if player_line:
            lines.append(player_line)
    elif action_kind == "defend":
        player_hp, enemy_hp, guard, player_line = _resolve_defend_action(
            outcome=outcome,
            player_hp=player_hp,
            enemy_hp=enemy_hp,
            guard=guard,
            bonus=total_bonus,
            rng=rng,
        )
        if player_line:
            lines.append(player_line)
    else:
        player_hp, enemy_hp, guard, player_line = _resolve_attack_action(
            outcome=outcome,
            action_kind=action_kind,
            player_hp=player_hp,
            enemy_hp=enemy_hp,
            guard=guard,
            bonus=total_bonus,
            near_miss=(outcome == "failure" and roll_total >= (player_dc - 2)),
            rng=rng,
        )
        if player_line:
            lines.append(player_line)

    player_fail_streak = (
        min(6, previous_fail_streak + 1)
        if outcome in {"failure", "critical_failure"}
        else 0
    )

    enemy_outcome = ""
    if enemy_hp > 0 and player_hp > 0:
        defense_bonus = _stat_bonus(
            player_sheet,
            ("defense", "agilite", "dexterite"),
            runtime_stat_bonuses=runtime_stat_bonuses,
        )
        defense_dc = 11 + defense_bonus + guard + max(0, relic_defense_bonus)
        enemy_attack_bonus = max(1, _safe_int(combat.get("enemy_attack_bonus"), 3))
        round_no = max(1, _safe_int(combat.get("round"), 1))
        enemy_pressure = max(0, round_no - 6) // 3
        if enemy_pressure > 0:
            enemy_attack_bonus += enemy_pressure
            lines.append(f"Pression du combat prolonge: ennemi +{enemy_pressure}.")
        if heal_streak >= 2:
            heal_pressure = min(3, heal_streak - 1)
            enemy_attack_bonus += heal_pressure
            lines.append(f"Ouverture sur soin repetitif: ennemi +{heal_pressure}.")
        enemy_roll_raw = rng.randint(1, 20)
        enemy_roll_total = enemy_roll_raw + enemy_attack_bonus
        enemy_outcome = _classify_roll(enemy_roll_raw, enemy_roll_total, defense_dc)
        lines.append(
            f"Jet ennemi: d20={enemy_roll_raw} + bonus={enemy_attack_bonus} => {enemy_roll_total} vs DD {defense_dc} ({_OUTCOME_LABEL.get(enemy_outcome, enemy_outcome)})."
        )

        damage_min = max(1, _safe_int(combat.get("enemy_damage_min"), 3))
        damage_max = max(damage_min, _safe_int(combat.get("enemy_damage_max"), 8))
        damage = 0
        if enemy_outcome == "critical_success":
            damage = rng.randint(damage_max - 1, damage_max + 4)
        elif enemy_outcome == "success":
            damage = rng.randint(damage_min, damage_max)
        elif enemy_outcome == "critical_failure":
            damage = 0

        enemy_label = str(combat.get("enemy_name") or "L'ennemi")
        if damage > 0:
            damage = max(1, damage - min(guard, 4))
            player_hp = max(0, player_hp - damage)
            lines.append(f"{enemy_label} vous touche: -{damage} PV.")
            ability_line, guard, enemy_hp, player_status = _apply_enemy_ability(
                ability=str(combat.get("enemy_ability") or "").strip().casefold(),
                outcome=enemy_outcome,
                damage_done=damage,
                guard=guard,
                enemy_hp=enemy_hp,
                enemy_max_hp=enemy_max_hp,
                player_status=player_status,
                rng=rng,
            )
            if ability_line:
                lines.append(ability_line)
        else:
            lines.append(f"{enemy_label} rate son attaque.")

        if guard > 0:
            guard = max(0, guard - 1)

    victory = enemy_hp <= 0
    defeat = player_hp <= 0
    if victory:
        enemy_label = str(combat.get("enemy_name") or "L'ennemi")
        lines.append(f"{enemy_label} est vaincu.")
    elif defeat:
        lines.extend(
            build_gore_defeat_lines(
                enemy_name=str(combat.get("enemy_name") or "L'ennemi"),
                event_type=str(combat.get("event_type") or ""),
                rng=rng,
            )
        )

    combat["enemy_hp"] = max(0, enemy_hp)
    combat["enemy_max_hp"] = enemy_max_hp
    combat["guard"] = max(0, guard)
    combat["player_status"] = player_status
    combat["player_fail_streak"] = player_fail_streak
    combat["heal_streak"] = heal_streak
    combat["run_relic"] = combat_relic
    combat["round"] = max(1, _safe_int(combat.get("round"), 1) + 1)
    combat["active"] = not (victory or defeat)

    return {
        "combat": combat,
        "player_hp": max(0, min(player_hp, player_max_hp)),
        "player_max_hp": player_max_hp,
        "enemy_hp": max(0, enemy_hp),
        "enemy_max_hp": enemy_max_hp,
        "outcome": outcome,
        "enemy_outcome": enemy_outcome,
        "action_kind": action_kind,
        "used_skill_ids": used_skill_ids,
        "used_skill_names": skill_names,
        "victory": victory,
        "defeat": defeat,
        "lines": lines,
        "player_delta": max(0, min(player_hp, player_max_hp)) - player_before,
    }


def _resolve_action_kind(action_text: str, used_skill_rows: list[dict]) -> str:
    text = str(action_text or "")
    has_attack = bool(_ATTACK_RE.search(text))
    if _HEAL_RE.search(text):
        if has_attack:
            return "attack"
        return "heal"
    if _DEFEND_RE.search(text):
        return "defend"
    if _SPELL_RE.search(text):
        return "spell"

    for row in used_skill_rows:
        category = str(row.get("category") or "").strip().casefold()
        if any(key in category for key in ("soin", "sacre", "heal")):
            return "heal"
        if any(key in category for key in ("defense", "bouclier", "parade")):
            return "defend"
        if any(key in category for key in ("magie", "arcane")):
            return "spell"
    return "attack"


def _resolve_attack_action(
    *,
    outcome: str,
    action_kind: str,
    player_hp: int,
    enemy_hp: int,
    guard: int,
    bonus: int,
    near_miss: bool,
    rng: random.Random,
) -> tuple[int, int, int, str]:
    if outcome == "critical_success":
        damage = rng.randint(8, 12) + max(1, bonus)
        if action_kind == "spell":
            damage += 2
        enemy_hp = max(0, enemy_hp - damage)
        return player_hp, enemy_hp, guard, f"Impact critique: {damage} degats infliges."
    if outcome == "success":
        damage = rng.randint(4, 8) + max(0, bonus // 2)
        if action_kind == "spell":
            damage += 1
        enemy_hp = max(0, enemy_hp - damage)
        return player_hp, enemy_hp, guard, f"Attaque reussie: {damage} degats."
    if outcome == "critical_failure":
        backlash = rng.randint(1, 4) + max(0, bonus // 3)
        player_hp = max(0, player_hp - backlash)
        return player_hp, enemy_hp, guard, f"Faux mouvement: vous perdez {backlash} PV."
    if near_miss:
        glancing = 1 + (1 if action_kind == "spell" and bonus >= 4 else 0)
        enemy_hp = max(0, enemy_hp - glancing)
        return player_hp, enemy_hp, guard, f"Attaque partielle: {glancing} degats."
    return player_hp, enemy_hp, guard, "Votre action ne passe pas."


def _resolve_heal_action(
    *,
    outcome: str,
    player_hp: int,
    player_max_hp: int,
    enemy_hp: int,
    guard: int,
    bonus: int,
    rng: random.Random,
) -> tuple[int, int, int, str]:
    if outcome == "critical_success":
        heal = rng.randint(8, 12) + max(0, bonus)
        next_hp = min(player_max_hp, player_hp + heal)
        gained = max(0, next_hp - player_hp)
        return next_hp, enemy_hp, guard, f"Soin critique: +{gained} PV."
    if outcome == "success":
        heal = rng.randint(4, 8) + max(0, bonus // 2)
        next_hp = min(player_max_hp, player_hp + heal)
        gained = max(0, next_hp - player_hp)
        return next_hp, enemy_hp, guard, f"Soin applique: +{gained} PV."
    if outcome == "critical_failure":
        backlash = rng.randint(1, 4)
        next_hp = max(0, player_hp - backlash)
        return next_hp, enemy_hp, guard, f"Canalisation ratee: -{backlash} PV."
    return player_hp, enemy_hp, guard, "Le soin echoue."


def _resolve_defend_action(
    *,
    outcome: str,
    player_hp: int,
    enemy_hp: int,
    guard: int,
    bonus: int,
    rng: random.Random,
) -> tuple[int, int, int, str]:
    if outcome == "critical_success":
        gain = 4 + max(0, bonus // 2)
        return player_hp, enemy_hp, guard + gain, f"Garde parfaite: +{gain} defense temporaire."
    if outcome == "success":
        gain = 2 + max(0, bonus // 3)
        return player_hp, enemy_hp, guard + gain, f"Garde stable: +{gain} defense temporaire."
    if outcome == "critical_failure":
        backlash = rng.randint(1, 3)
        next_hp = max(0, player_hp - backlash)
        return next_hp, enemy_hp, 0, f"Posture brisee: -{backlash} PV."
    return player_hp, enemy_hp, max(0, guard - 1), "Vous n'arrivez pas a vous mettre en garde."


def _apply_enemy_ability(
    *,
    ability: str,
    outcome: str,
    damage_done: int,
    guard: int,
    enemy_hp: int,
    enemy_max_hp: int,
    player_status: dict[str, int],
    rng: random.Random,
) -> tuple[str, int, int, dict[str, int]]:
    if outcome not in {"success", "critical_success"} or damage_done <= 0:
        return "", guard, enemy_hp, player_status

    status_map = _normalize_status_map(player_status)
    kind = str(ability or "").strip().casefold()
    if kind == "poison_claw":
        chance = 0.45 if outcome == "success" else 0.8
        if _roll_chance(rng, chance):
            status_map["poison"] = max(status_map["poison"], 2)
            return "Effet ennemi: vous etes empoisonne.", guard, enemy_hp, status_map
        return "", guard, enemy_hp, status_map

    if kind == "bleed_strike":
        chance = 0.40 if outcome == "success" else 0.75
        if _roll_chance(rng, chance):
            status_map["bleed"] = max(status_map["bleed"], 2)
            return "Effet ennemi: vous saignez.", guard, enemy_hp, status_map
        return "", guard, enemy_hp, status_map

    if kind == "guard_break":
        lost = max(1, min(3, 1 + (1 if outcome == "critical_success" else 0)))
        next_guard = max(0, guard - lost)
        if next_guard < guard:
            return f"Effet ennemi: votre garde cede (-{guard - next_guard}).", next_guard, enemy_hp, status_map
        return "", guard, enemy_hp, status_map

    if kind == "weaken_aura":
        chance = 0.35 if outcome == "success" else 0.65
        if _roll_chance(rng, chance):
            status_map["weakened"] = max(status_map["weakened"], 2)
            return "Effet ennemi: affaiblissement temporaire.", guard, enemy_hp, status_map
        return "", guard, enemy_hp, status_map

    if kind == "vampiric":
        heal = max(1, min(6, damage_done // 2))
        next_hp = min(enemy_max_hp, enemy_hp + heal)
        if next_hp > enemy_hp:
            return f"Effet ennemi: il se regenere de {next_hp - enemy_hp} PV.", guard, next_hp, status_map
        return "", guard, enemy_hp, status_map

    return "", guard, enemy_hp, status_map


def _roll_chance(rng: random.Random, chance: float) -> bool:
    threshold = max(0.0, min(1.0, float(chance)))
    random_fn = getattr(rng, "random", None)
    if callable(random_fn):
        try:
            return float(random_fn()) <= threshold
        except Exception:
            pass
    roll = _safe_int(getattr(rng, "randint")(1, 100), 100)
    return roll <= int(round(threshold * 100))


def _classify_roll(raw_roll: int, total_roll: int, dc: int) -> str:
    if raw_roll >= 20:
        return "critical_success"
    if raw_roll <= 1:
        return "critical_failure"
    if total_roll >= dc:
        return "success"
    return "failure"


def _detect_used_skills(
    *,
    action_text: str,
    known_skills: list[dict],
    skill_manager: Any,
) -> tuple[list[str], list[dict]]:
    if not isinstance(known_skills, list) or not known_skills:
        return [], []

    used_ids: list[str] = []
    if skill_manager is not None:
        try:
            detected = skill_manager.detect_used_skill_ids(str(action_text or ""), known_skills)
            if isinstance(detected, list):
                for raw in detected:
                    clean = str(raw or "").strip().casefold()
                    if clean and clean not in used_ids:
                        used_ids.append(clean)
        except Exception:
            used_ids = []

    if not used_ids:
        text = str(action_text or "").strip().casefold()
        for row in known_skills:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or "").strip().casefold()
            name = str(row.get("name") or "").strip().casefold()
            if not skill_id:
                continue
            if (name and name in text) or skill_id.replace("_", " ") in text:
                used_ids.append(skill_id)
            if len(used_ids) >= 3:
                break

    by_id = {
        str(row.get("skill_id") or "").strip().casefold(): row
        for row in known_skills
        if isinstance(row, dict) and str(row.get("skill_id") or "").strip()
    }
    used_rows = [by_id[sid] for sid in used_ids if sid in by_id]
    return used_ids[:3], used_rows[:3]


def _skill_bonus(used_skill_rows: list[dict]) -> int:
    bonus = 0
    for row in used_skill_rows:
        if not isinstance(row, dict):
            continue
        level = max(1, _safe_int(row.get("level"), 1))
        rank = max(1, _safe_int(row.get("rank"), 1))
        row_bonus = (level - 1) // 8
        row_bonus += max(0, rank - 1)
        bonus = max(bonus, row_bonus)
    return min(8, max(0, bonus))


def _stats_from_sheet(
    player_sheet: dict | None,
    *,
    runtime_stat_bonuses: dict[str, int] | None = None,
) -> dict:
    if not isinstance(player_sheet, dict):
        stats: dict = {}
    else:
        effective = player_sheet.get("effective_stats")
        if isinstance(effective, dict):
            stats = dict(effective)
        else:
            raw = player_sheet.get("stats")
            stats = dict(raw) if isinstance(raw, dict) else {}

    if not isinstance(runtime_stat_bonuses, dict) or not runtime_stat_bonuses:
        return stats

    for key, value in runtime_stat_bonuses.items():
        stat = str(key or "").strip().casefold()
        if stat not in _STAT_KEYS:
            continue
        delta = _safe_int(value, 0)
        if delta == 0:
            continue
        stats[stat] = _safe_int(stats.get(stat), 5) + delta
    return stats


def _stat_bonus(
    player_sheet: dict | None,
    keys: tuple[str, ...],
    *,
    runtime_stat_bonuses: dict[str, int] | None = None,
) -> int:
    stats = _stats_from_sheet(player_sheet, runtime_stat_bonuses=runtime_stat_bonuses)
    values: list[int] = []
    for key in keys:
        if key in _STAT_KEYS:
            values.append(max(1, _safe_int(stats.get(key), 5)))
    if not values:
        return 0
    average = sum(values) / float(len(values))
    return max(-2, min(8, int((average - 5.0) // 2)))
