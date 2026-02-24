from __future__ import annotations

from dataclasses import asdict, dataclass, field
import random
import re
from typing import Any


_TRAVEL_STATUSES = {"idle", "traveling", "camping", "arrived", "aborted"}
_EVENT_TYPES = {"encounter", "ambush", "hazard", "discovery", "camp"}


@dataclass
class TravelLogEntry:
    at: int = 0
    kind: str = "info"
    text: str = ""
    progress: int = 0


@dataclass
class TravelState:
    status: str = "idle"
    from_location_id: str = ""
    to_location_id: str = ""
    route: list[str] = field(default_factory=list)
    total_distance: int = 0
    progress: int = 0
    last_tick_at: int | None = None
    danger_level: int = 20
    fatigue: int = 0
    supplies_used: dict[str, int] = field(default_factory=lambda: {"food": 0, "water": 0, "torches": 0})
    pending_event: dict[str, Any] | None = None
    event_cooldown_ticks: int = 0
    recent_event_types: list[str] = field(default_factory=list)
    log: list[TravelLogEntry] = field(default_factory=list)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: object, lo: int, hi: int, *, default: int = 0) -> int:
    return max(lo, min(hi, _safe_int(value, default)))


def _clean_id(value: object, *, max_len: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:max_len]


def _clean_status(value: object) -> str:
    raw = str(value or "").strip().casefold()
    return raw if raw in _TRAVEL_STATUSES else "idle"


def _clean_event_type(value: object) -> str:
    raw = str(value or "").strip().casefold()
    return raw if raw in _EVENT_TYPES else "encounter"


def _normalize_supplies(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "food": max(0, _safe_int(raw.get("food"), 0)),
        "water": max(0, _safe_int(raw.get("water"), 0)),
        "torches": max(0, _safe_int(raw.get("torches"), 0)),
    }


def _normalize_recent_event_types(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for row in raw[-4:]:
        event_raw = str(row or "").strip().casefold()
        if event_raw not in _EVENT_TYPES:
            continue
        event_type = event_raw
        if not event_type:
            continue
        # On conserve l'ordre recent sans doublons pour limiter les boucles.
        if event_type in seen:
            continue
        seen.add(event_type)
        out.append(event_type)
    return out


def _normalize_log(raw: object) -> list[TravelLogEntry]:
    if not isinstance(raw, list):
        return []
    out: list[TravelLogEntry] = []
    for row in raw[-80:]:
        if not isinstance(row, dict):
            continue
        out.append(
            TravelLogEntry(
                at=max(0, _safe_int(row.get("at"), 0)),
                kind=_clean_id(row.get("kind"), max_len=40) or "info",
                text=_clean_id(row.get("text"), max_len=220),
                progress=max(0, _safe_int(row.get("progress"), 0)),
            )
        )
    return out


def _normalize_pending_event(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    event_id = _clean_id(raw.get("id"), max_len=40)
    event_type = _clean_event_type(raw.get("type"))
    short_text = _clean_id(raw.get("short_text"), max_len=220)
    if not event_id or not short_text:
        return None

    choices_raw = raw.get("choices")
    option_rows: list[dict[str, Any]] = []
    if isinstance(choices_raw, list):
        seen: set[str] = set()
        for idx, row in enumerate(choices_raw[:3]):
            if not isinstance(row, dict):
                continue
            option_id = _clean_id(row.get("id"), max_len=32).casefold()
            if not option_id:
                option_id = f"opt_{idx + 1}"
            if option_id in seen:
                continue
            seen.add(option_id)
            option_rows.append(
                {
                    "id": option_id,
                    "text": _clean_id(row.get("text"), max_len=120) or f"Option {idx + 1}",
                    "risk_tag": _clean_id(row.get("risk_tag"), max_len=24) or "moyen",
                    "effects_hint": _clean_id(row.get("effects_hint"), max_len=160),
                    "state_patch": dict(row.get("state_patch") or {}) if isinstance(row.get("state_patch"), dict) else {},
                    "travel_patch": dict(row.get("travel_patch") or {}) if isinstance(row.get("travel_patch"), dict) else {},
                }
            )

    event_patch = raw.get("state_patch") if isinstance(raw.get("state_patch"), dict) else {}
    interrupt = bool(raw.get("interrupt", False))
    combat_seed = raw.get("combat_seed") if isinstance(raw.get("combat_seed"), dict) else None

    return {
        "id": event_id,
        "type": event_type,
        "short_text": short_text,
        "choices": option_rows,
        "state_patch": event_patch,
        "interrupt": interrupt,
        "combat_seed": combat_seed,
    }


def idle_travel_state() -> TravelState:
    return TravelState()


def normalize_travel_state(raw: object) -> TravelState:
    if isinstance(raw, TravelState):
        state = raw
    elif isinstance(raw, dict):
        route = raw.get("route") if isinstance(raw.get("route"), list) else []
        clean_route = [_clean_id(x, max_len=120) for x in route if _clean_id(x, max_len=120)]
        total_distance = max(0, _safe_int(raw.get("total_distance"), 0))
        progress = max(0, _safe_int(raw.get("progress"), 0))
        if total_distance <= 0 and clean_route:
            total_distance = max(20, (len(clean_route) - 1) * 30)
        progress = min(progress, total_distance) if total_distance > 0 else 0
        state = TravelState(
            status=_clean_status(raw.get("status")),
            from_location_id=_clean_id(raw.get("from_location_id"), max_len=120),
            to_location_id=_clean_id(raw.get("to_location_id"), max_len=120),
            route=clean_route,
            total_distance=total_distance,
            progress=progress,
            last_tick_at=max(0, _safe_int(raw.get("last_tick_at"), 0)) if raw.get("last_tick_at") is not None else None,
            danger_level=_clamp(raw.get("danger_level"), 0, 100, default=20),
            fatigue=_clamp(raw.get("fatigue"), 0, 100, default=0),
            supplies_used=_normalize_supplies(raw.get("supplies_used")),
            pending_event=_normalize_pending_event(raw.get("pending_event")),
            event_cooldown_ticks=_clamp(raw.get("event_cooldown_ticks"), 0, 6, default=0),
            recent_event_types=_normalize_recent_event_types(raw.get("recent_event_types")),
            log=_normalize_log(raw.get("log")),
        )
    else:
        state = idle_travel_state()

    if state.status == "idle":
        state.from_location_id = ""
        state.to_location_id = ""
        state.route = []
        state.total_distance = 0
        state.progress = 0
        state.pending_event = None
        state.last_tick_at = None
        state.fatigue = 0
        state.danger_level = _clamp(state.danger_level, 0, 100, default=20)
        state.event_cooldown_ticks = 0
        state.recent_event_types = []

    if state.total_distance <= 0 and state.status in {"traveling", "camping", "arrived"}:
        state.total_distance = max(20, (len(state.route) - 1) * 30) if state.route else 30

    state.progress = max(0, min(state.progress, max(0, state.total_distance)))
    if state.status == "arrived" and state.total_distance > 0:
        state.progress = state.total_distance
    state.event_cooldown_ticks = _clamp(state.event_cooldown_ticks, 0, 6, default=0)
    state.recent_event_types = _normalize_recent_event_types(state.recent_event_types)

    return state


def travel_state_to_dict(state: TravelState | dict | None) -> dict[str, Any]:
    normalized = normalize_travel_state(state)
    payload = asdict(normalized)
    payload["log"] = [asdict(entry) for entry in normalized.log[-80:]]
    return payload


class TravelEngine:
    def __init__(self, *, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.state: TravelState = idle_travel_state()

    def load_state(self, state: TravelState | dict | None) -> TravelState:
        self.state = normalize_travel_state(state)
        return self.state

    def export_state(self) -> TravelState:
        self.state = normalize_travel_state(self.state)
        return self.state

    def start_travel(self, from_id: str, to_id: str, options: dict | None = None) -> TravelState:
        opts = options if isinstance(options, dict) else {}
        route = opts.get("route") if isinstance(opts.get("route"), list) else []
        clean_route = [_clean_id(x, max_len=120) for x in route if _clean_id(x, max_len=120)]

        from_location_id = _clean_id(from_id, max_len=120)
        to_location_id = _clean_id(to_id, max_len=120)
        if not from_location_id and clean_route:
            from_location_id = _clean_id(clean_route[0], max_len=120)
        if not to_location_id and clean_route:
            to_location_id = _clean_id(clean_route[-1], max_len=120)

        if not clean_route:
            if from_location_id and to_location_id and from_location_id != to_location_id:
                clean_route = [from_location_id, to_location_id]
            elif from_location_id:
                clean_route = [from_location_id]

        segment_distance = max(12, _safe_int(opts.get("segment_distance"), 30))
        total_distance = max(20, _safe_int(opts.get("total_distance"), 0))
        if _safe_int(opts.get("total_distance"), 0) <= 0:
            total_distance = max(20, max(1, len(clean_route) - 1) * segment_distance)

        state = TravelState(
            status="traveling",
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            route=clean_route,
            total_distance=total_distance,
            progress=0,
            last_tick_at=None,
            danger_level=_clamp(opts.get("danger_level"), 0, 100, default=25),
            fatigue=_clamp(opts.get("fatigue"), 0, 100, default=0),
            supplies_used=_normalize_supplies(opts.get("supplies_used")),
            pending_event=None,
            event_cooldown_ticks=0,
            recent_event_types=[],
            log=[],
        )
        self.state = normalize_travel_state(state)
        self._append_log("start", f"Depart {from_location_id} -> {to_location_id}")
        return self.export_state()

    def tick_travel(self, world_state: dict | None, player_state: dict | None, *, action: str = "continue") -> tuple[TravelState, dict | None]:
        self.state = normalize_travel_state(self.state)
        state = self.state

        if state.status not in {"traveling", "camping"}:
            return self.export_state(), None

        if isinstance(state.pending_event, dict):
            return self.export_state(), state.pending_event

        action_key = str(action or "continue").strip().casefold()
        if action_key not in {"continue", "accelerate", "detour", "camp"}:
            action_key = "continue"

        world = world_state if isinstance(world_state, dict) else {}
        player = player_state if isinstance(player_state, dict) else {}
        world_tension = _clamp(world.get("global_tension"), 0, 100, default=0)
        world_instability = _clamp(world.get("instability_level"), 0, 100, default=0)
        time_of_day = str(world.get("time_of_day") or "morning").strip().casefold()
        now_minutes = max(0, _safe_int(player.get("world_time_minutes"), 0))

        if action_key == "camp":
            state.status = "camping"
            state.fatigue = max(0, state.fatigue - self.rng.randint(12, 22))
            state.danger_level = max(0, state.danger_level - self.rng.randint(2, 8))
            self._consume_supplies(state, food=1, water=1, torches=1 if time_of_day in {"night", "nightfall"} else 0)
            state.last_tick_at = now_minutes
            self._append_log("camp", "Le groupe campe pour recuperer.")
            if state.event_cooldown_ticks > 0:
                state.event_cooldown_ticks = max(0, state.event_cooldown_ticks - 1)
            else:
                event = self._maybe_route_event(
                    force_type="camp",
                    world_tension=world_tension,
                    world_instability=world_instability,
                    time_of_day=time_of_day,
                    world_bias=world.get("travel_event_bias") if isinstance(world.get("travel_event_bias"), dict) else None,
                )
                if event:
                    state.pending_event = event
                    state.event_cooldown_ticks = 1 + (1 if state.danger_level >= 70 else 0)
                    self._append_log("event", event.get("short_text") or "Evenement de camp")
                    return self.export_state(), event
            return self.export_state(), None

        if state.status == "camping":
            state.status = "traveling"

        base_speed = 12
        fatigue_penalty = state.fatigue // 25
        danger_penalty = state.danger_level // 35
        speed = base_speed - fatigue_penalty - danger_penalty
        if time_of_day in {"night", "nightfall"}:
            speed -= 2

        fatigue_gain = 4
        if action_key == "accelerate":
            speed += 6
            fatigue_gain += 6
            state.danger_level = min(100, state.danger_level + self.rng.randint(4, 8))
        elif action_key == "detour":
            speed -= 4
            fatigue_gain += 2
            state.danger_level = max(0, state.danger_level - self.rng.randint(5, 10))

        speed = max(3, speed)
        progress_gain = max(2, speed + self.rng.randint(-1, 2))
        state.progress = min(state.total_distance, state.progress + progress_gain)
        state.fatigue = min(100, state.fatigue + fatigue_gain + self.rng.randint(0, 3))
        state.danger_level = max(
            0,
            min(
                100,
                state.danger_level + (1 if action_key == "accelerate" else 0) + (1 if world_instability >= 70 else 0),
            ),
        )
        self._consume_supplies(state, food=1, water=1, torches=1 if time_of_day in {"night", "nightfall"} else 0)
        state.last_tick_at = now_minutes

        self._append_log(
            "tick",
            f"Progression +{progress_gain} ({state.progress}/{state.total_distance})",
        )

        if state.progress >= state.total_distance:
            state.status = "arrived"
            state.pending_event = None
            self._append_log("arrive", "Destination atteinte.")
            return self.export_state(), None

        if state.event_cooldown_ticks > 0:
            state.event_cooldown_ticks = max(0, state.event_cooldown_ticks - 1)
        else:
            event = self._maybe_route_event(
                force_type=None,
                world_tension=world_tension,
                world_instability=world_instability,
                time_of_day=time_of_day,
                world_bias=world.get("travel_event_bias") if isinstance(world.get("travel_event_bias"), dict) else None,
            )
            if event:
                state.pending_event = event
                state.event_cooldown_ticks = 1 + (1 if state.danger_level >= 70 else 0)
                self._append_log("event", event.get("short_text") or "Evenement de route")
                return self.export_state(), event

        return self.export_state(), None

    def resolve_travel_choice(self, choice_id: str) -> dict[str, Any]:
        self.state = normalize_travel_state(self.state)
        event = self.state.pending_event if isinstance(self.state.pending_event, dict) else None
        if not isinstance(event, dict):
            return {}

        target = str(choice_id or "").strip().casefold()
        if not target:
            return {}

        chosen: dict[str, Any] | None = None
        for row in event.get("choices") if isinstance(event.get("choices"), list) else []:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or "").strip().casefold() == target:
                chosen = row
                break
        if not isinstance(chosen, dict):
            return {}

        travel_patch = chosen.get("travel_patch") if isinstance(chosen.get("travel_patch"), dict) else {}
        progress_delta = _safe_int(travel_patch.get("progress_delta"), 0)
        fatigue_delta = _safe_int(travel_patch.get("fatigue_delta"), 0)
        danger_delta = _safe_int(travel_patch.get("danger_delta"), 0)
        status_set = _clean_status(travel_patch.get("status")) if "status" in travel_patch else ""

        self.state.progress = max(0, min(self.state.total_distance, self.state.progress + progress_delta))
        self.state.fatigue = max(0, min(100, self.state.fatigue + fatigue_delta))
        self.state.danger_level = max(0, min(100, self.state.danger_level + danger_delta))

        supplies = travel_patch.get("supplies") if isinstance(travel_patch.get("supplies"), dict) else {}
        if supplies:
            self._consume_supplies(
                self.state,
                food=max(0, _safe_int(supplies.get("food"), 0)),
                water=max(0, _safe_int(supplies.get("water"), 0)),
                torches=max(0, _safe_int(supplies.get("torches"), 0)),
            )

        if status_set in {"traveling", "camping", "aborted"}:
            self.state.status = status_set

        if self.state.progress >= self.state.total_distance and self.state.total_distance > 0:
            self.state.status = "arrived"

        self._append_log("choice", f"Choix route: {chosen.get('text') or target}")
        self.state.pending_event = None

        return dict(chosen.get("state_patch") or {}) if isinstance(chosen.get("state_patch"), dict) else {}

    def abort_travel(self) -> TravelState:
        self.state = normalize_travel_state(self.state)
        if self.state.status not in {"traveling", "camping", "arrived"}:
            return self.export_state()
        previous = self.state
        self._append_log("abort", "Trajet abandonne.")
        self.state = idle_travel_state()
        self.state.log = [
            TravelLogEntry(
                at=max(0, _safe_int(previous.last_tick_at, 0)),
                kind="aborted",
                text=f"Trajet interrompu vers {previous.to_location_id or 'destination inconnue'}.",
                progress=max(0, _safe_int(previous.progress, 0)),
            )
        ]
        return self.export_state()

    def return_back(self) -> TravelState:
        self.state = normalize_travel_state(self.state)
        if self.state.status not in {"traveling", "camping"}:
            return self.export_state()

        previous = self.state
        self._append_log("return", "Demi-tour immediat.")
        self.state = idle_travel_state()
        self.state.log = [
            TravelLogEntry(
                at=max(0, _safe_int(previous.last_tick_at, 0)),
                kind="return",
                text=f"Retour au point de depart ({previous.from_location_id or 'inconnu'}).",
                progress=max(0, _safe_int(previous.progress, 0)),
            )
        ]
        return self.export_state()

    def arrive(self) -> dict[str, Any]:
        self.state = normalize_travel_state(self.state)
        if self.state.status != "arrived":
            return {}

        destination = _clean_id(self.state.to_location_id, max_len=120)
        route = list(self.state.route)
        traveled_distance = self.state.total_distance
        fatigue = self.state.fatigue
        supplies = dict(self.state.supplies_used)
        self._append_log("arrive", f"Arrivee sur {destination}")

        self.state = idle_travel_state()
        self.state.log = [
            TravelLogEntry(
                at=0,
                kind="summary",
                text=f"Trajet termine ({traveled_distance}u, fatigue {fatigue}, vivres {supplies.get('food', 0)}/{supplies.get('water', 0)}).",
                progress=traveled_distance,
            )
        ]

        patch: dict[str, Any] = {
            "location_id": destination,
            "flags": {
                "travel_arrived": True,
                "travel_last_distance": traveled_distance,
                "travel_last_route": " -> ".join(route[:8]),
            },
            "world": {"time_passed": 8},
            "resources": {
                "food_used": max(0, _safe_int(supplies.get("food"), 0)),
                "water_used": max(0, _safe_int(supplies.get("water"), 0)),
                "torches_used": max(0, _safe_int(supplies.get("torches"), 0)),
            },
            "travel_summary": {
                "distance": traveled_distance,
                "fatigue": fatigue,
                "destination": destination,
            },
        }
        return patch

    def _append_log(self, kind: str, text: str) -> None:
        self.state.log.append(
            TravelLogEntry(
                at=max(0, _safe_int(self.state.last_tick_at, 0)),
                kind=_clean_id(kind, max_len=40) or "info",
                text=_clean_id(text, max_len=220),
                progress=max(0, _safe_int(self.state.progress, 0)),
            )
        )
        if len(self.state.log) > 80:
            self.state.log = self.state.log[-80:]

    def _consume_supplies(self, state: TravelState, *, food: int = 0, water: int = 0, torches: int = 0) -> None:
        state.supplies_used["food"] = max(0, _safe_int(state.supplies_used.get("food"), 0) + max(0, int(food)))
        state.supplies_used["water"] = max(0, _safe_int(state.supplies_used.get("water"), 0) + max(0, int(water)))
        state.supplies_used["torches"] = max(0, _safe_int(state.supplies_used.get("torches"), 0) + max(0, int(torches)))

    def _maybe_route_event(
        self,
        *,
        force_type: str | None,
        world_tension: int,
        world_instability: int,
        time_of_day: str,
        world_bias: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        danger = self.state.danger_level
        fatigue = self.state.fatigue

        if force_type:
            event_type = str(force_type).strip().casefold()
        else:
            trigger_chance = 0.07 + (danger / 240.0) + (fatigue / 420.0)
            if world_tension >= 70:
                trigger_chance += 0.06
            if world_instability >= 70:
                trigger_chance += 0.06
            if time_of_day in {"night", "nightfall"}:
                trigger_chance += 0.04
            trigger_chance = max(0.02, min(0.72, trigger_chance))
            if self.rng.random() > trigger_chance:
                return None

            event_type = self._pick_event_type(
                world_tension=world_tension,
                world_instability=world_instability,
                time_of_day=time_of_day,
                world_bias=world_bias if isinstance(world_bias, dict) else None,
            )

        builder = {
            "encounter": self._event_encounter,
            "ambush": self._event_ambush,
            "hazard": self._event_hazard,
            "discovery": self._event_discovery,
            "camp": self._event_camp,
        }.get(event_type)
        if not callable(builder):
            return None
        event = builder()
        if isinstance(event, dict):
            self._remember_event_type(str(event.get("type") or event_type))
        return event

    def _event_weights(
        self,
        *,
        world_tension: int,
        world_instability: int,
        time_of_day: str,
        world_bias: dict[str, int] | None,
    ) -> dict[str, int]:
        weights: dict[str, int] = {
            "encounter": 24,
            "hazard": 21,
            "discovery": 21,
            "ambush": 20,
            "camp": 14,
        }

        if world_tension >= 70:
            weights["ambush"] += 8
            weights["encounter"] += 4
        if world_instability >= 70:
            weights["hazard"] += 8
            weights["ambush"] += 5
            weights["discovery"] = max(1, weights["discovery"] - 4)
        if time_of_day in {"night", "nightfall"}:
            weights["ambush"] += 6
            weights["camp"] += 2

        if isinstance(world_bias, dict):
            for key, delta_raw in world_bias.items():
                event_type = str(key or "").strip().casefold()
                if event_type not in weights:
                    continue
                delta_pct = _clamp(delta_raw, -80, 180, default=0)
                base = max(1, int(weights[event_type]))
                weights[event_type] = max(1, int(round(base * (1.0 + (delta_pct / 100.0)))))

        for recent in self.state.recent_event_types[-2:]:
            event_type = _clean_event_type(recent)
            if event_type in weights:
                weights[event_type] = max(1, int(round(weights[event_type] * 0.35)))

        return {key: max(1, int(value)) for key, value in weights.items()}

    def _pick_event_type(
        self,
        *,
        world_tension: int,
        world_instability: int,
        time_of_day: str,
        world_bias: dict[str, int] | None,
    ) -> str:
        weights = self._event_weights(
            world_tension=world_tension,
            world_instability=world_instability,
            time_of_day=time_of_day,
            world_bias=world_bias,
        )
        total = sum(max(1, int(value)) for value in weights.values())
        if total <= 0:
            return "encounter"
        roll = self.rng.uniform(0.0, float(total))
        cursor = 0.0
        for event_type in ("encounter", "hazard", "discovery", "ambush", "camp"):
            cursor += float(max(1, int(weights.get(event_type, 1))))
            if roll <= cursor:
                return event_type
        return "camp"

    def _remember_event_type(self, event_type: str) -> None:
        clean = str(event_type or "").strip().casefold()
        if clean not in _EVENT_TYPES:
            return
        recent = _normalize_recent_event_types(self.state.recent_event_types)
        if recent and recent[-1] == clean:
            self.state.recent_event_types = recent[-4:]
            return
        recent.append(clean)
        self.state.recent_event_types = _normalize_recent_event_types(recent[-4:])

    def _event_encounter(self) -> dict[str, Any]:
        return {
            "id": f"enc_{self.rng.randint(1000, 9999)}",
            "type": "encounter",
            "short_text": "Une caravane armee bloque une partie du passage.",
            "interrupt": False,
            "state_patch": {"flags": {"travel_event_encounter": True}},
            "choices": [
                {
                    "id": "negotiate",
                    "text": "Negocier le passage",
                    "risk_tag": "moyen",
                    "effects_hint": "Moins de danger, possible gain de reputation.",
                    "state_patch": {"reputation": {"Marchands": 1}, "flags": {"travel_deal": True}},
                    "travel_patch": {"danger_delta": -5, "fatigue_delta": -1, "progress_delta": 2},
                },
                {
                    "id": "rush",
                    "text": "Forcer le passage",
                    "risk_tag": "eleve",
                    "effects_hint": "Progression rapide mais usante.",
                    "state_patch": {"player": {"hp_delta": -1}, "flags": {"travel_rush": True}},
                    "travel_patch": {"danger_delta": 7, "fatigue_delta": 5, "progress_delta": 5},
                },
                {
                    "id": "trade",
                    "text": "Payer pour passer",
                    "risk_tag": "faible",
                    "effects_hint": "Moins de tension, coute de l'or.",
                    "state_patch": {"player": {"gold_delta": -8}, "flags": {"travel_bribe": True}},
                    "travel_patch": {"danger_delta": -8, "progress_delta": 1},
                },
            ],
        }

    def _event_ambush(self) -> dict[str, Any]:
        return {
            "id": f"amb_{self.rng.randint(1000, 9999)}",
            "type": "ambush",
            "short_text": "Des silhouettes surgissent des fourres: embuscade.",
            "interrupt": True,
            "combat_seed": {"kind": "road_ambush", "threat": self.rng.randint(1, 4)},
            "state_patch": {"flags": {"travel_event_ambush": True}},
            "choices": [
                {
                    "id": "fight",
                    "text": "Tenir la ligne",
                    "risk_tag": "eleve",
                    "effects_hint": "Blessures possibles, gagne du terrain.",
                    "state_patch": {"player": {"hp_delta": -4}, "reputation": {"Habitants": 1}},
                    "travel_patch": {"progress_delta": 3, "fatigue_delta": 6, "danger_delta": 2},
                },
                {
                    "id": "flee",
                    "text": "Fuir vers un detour",
                    "risk_tag": "moyen",
                    "effects_hint": "Evite le pire, perd du rythme.",
                    "state_patch": {"flags": {"travel_escape": True}},
                    "travel_patch": {"progress_delta": -4, "fatigue_delta": 5, "danger_delta": -3},
                },
                {
                    "id": "surrender",
                    "text": "Lacher des ressources",
                    "risk_tag": "faible",
                    "effects_hint": "Tu passes, mais plus pauvre.",
                    "state_patch": {"player": {"gold_delta": -10}, "resources": {"food": -1, "water": -1}},
                    "travel_patch": {"danger_delta": -10, "progress_delta": 1},
                },
            ],
        }

    def _event_hazard(self) -> dict[str, Any]:
        return {
            "id": f"haz_{self.rng.randint(1000, 9999)}",
            "type": "hazard",
            "short_text": "Le chemin se fissure: pont casse et bourbiers.",
            "interrupt": True,
            "state_patch": {"flags": {"travel_event_hazard": True}},
            "choices": [
                {
                    "id": "cross",
                    "text": "Traverser vite",
                    "risk_tag": "eleve",
                    "effects_hint": "Gain de temps, risque de blessure.",
                    "state_patch": {"player": {"hp_delta": -2}},
                    "travel_patch": {"progress_delta": 4, "fatigue_delta": 4, "danger_delta": 4},
                },
                {
                    "id": "detour",
                    "text": "Contourner la zone",
                    "risk_tag": "moyen",
                    "effects_hint": "Plus lent, plus sur.",
                    "state_patch": {"flags": {"travel_safe_detour": True}},
                    "travel_patch": {"progress_delta": -2, "fatigue_delta": 2, "danger_delta": -8},
                },
                {
                    "id": "camp",
                    "text": "Camper et attendre",
                    "risk_tag": "faible",
                    "effects_hint": "Recupere, mais consomme des vivres.",
                    "state_patch": {"resources": {"food": -1, "water": -1}},
                    "travel_patch": {"status": "camping", "fatigue_delta": -10, "danger_delta": -2},
                },
            ],
        }

    def _event_discovery(self) -> dict[str, Any]:
        return {
            "id": f"dis_{self.rng.randint(1000, 9999)}",
            "type": "discovery",
            "short_text": "Des ruines discrètes apparaissent au bord de la route.",
            "interrupt": False,
            "state_patch": {"flags": {"travel_event_discovery": True}},
            "choices": [
                {
                    "id": "search",
                    "text": "Fouiller rapidement",
                    "risk_tag": "moyen",
                    "effects_hint": "Chance de gain, fatigue en hausse.",
                    "state_patch": {"player": {"gold_delta": 6}, "flags": {"travel_loot_found": True}},
                    "travel_patch": {"progress_delta": -1, "fatigue_delta": 3},
                },
                {
                    "id": "mark",
                    "text": "Noter et repartir",
                    "risk_tag": "faible",
                    "effects_hint": "Progression stable.",
                    "state_patch": {"flags": {"travel_discovery_marked": True}},
                    "travel_patch": {"progress_delta": 2, "danger_delta": -2},
                },
                {
                    "id": "shortcut",
                    "text": "Prendre le raccourci",
                    "risk_tag": "eleve",
                    "effects_hint": "Grand gain ou mauvaise surprise.",
                    "state_patch": {"player": {"hp_delta": -1}, "flags": {"travel_shortcut": True}},
                    "travel_patch": {"progress_delta": 7, "fatigue_delta": 4, "danger_delta": 6},
                },
            ],
        }

    def _event_camp(self) -> dict[str, Any]:
        return {
            "id": f"cmp_{self.rng.randint(1000, 9999)}",
            "type": "camp",
            "short_text": "Le camp est monte, mais la nuit reste nerveuse.",
            "interrupt": False,
            "state_patch": {"flags": {"travel_event_camp": True}},
            "choices": [
                {
                    "id": "rest",
                    "text": "Dormir profondement",
                    "risk_tag": "moyen",
                    "effects_hint": "Recupere beaucoup, possible incident.",
                    "state_patch": {"player": {"hp_delta": 2}},
                    "travel_patch": {"status": "camping", "fatigue_delta": -14, "danger_delta": 2},
                },
                {
                    "id": "watch",
                    "text": "Veiller a tour de role",
                    "risk_tag": "faible",
                    "effects_hint": "Moins de repos, plus de securite.",
                    "state_patch": {"flags": {"travel_guarded_camp": True}},
                    "travel_patch": {"status": "camping", "fatigue_delta": -8, "danger_delta": -6},
                },
                {
                    "id": "resume",
                    "text": "Lever le camp",
                    "risk_tag": "moyen",
                    "effects_hint": "Repart vite, fatigue moderee.",
                    "state_patch": {},
                    "travel_patch": {"status": "traveling", "progress_delta": 2, "fatigue_delta": 2},
                },
            ],
        }
