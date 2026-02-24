from __future__ import annotations

from dataclasses import asdict, dataclass, field
import difflib
import re
import unicodedata
from typing import Any


_TRADE_STATUSES = {"idle", "selecting", "confirming", "executing", "done", "aborted"}
_TRADE_MODES = {"buy", "sell", "barter"}
_CURRENCIES = {"gold"}
_NUMBER_WORDS = {
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
}
_STOPWORDS = {
    "je",
    "j",
    "tu",
    "vous",
    "de",
    "des",
    "du",
    "la",
    "le",
    "les",
    "un",
    "une",
    "ce",
    "cet",
    "cette",
    "ces",
    "mon",
    "ma",
    "mes",
    "ton",
    "ta",
    "tes",
    "leur",
    "leurs",
    "a",
    "au",
    "aux",
    "pour",
    "avec",
    "que",
    "qui",
    "et",
    "ou",
    "en",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: object, low: int, high: int, *, default: int = 0) -> int:
    return max(low, min(high, _safe_int(value, default)))


def _clean_text(value: object, *, max_len: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _norm(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", " ")
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_qty(plain: str) -> int | None:
    m = re.search(r"\bx\s*(\d{1,3})\b", plain)
    if m:
        return max(1, min(999, _safe_int(m.group(1), 1)))
    m = re.search(r"\b(\d{1,3})\b", plain)
    if m:
        return max(1, min(999, _safe_int(m.group(1), 1)))
    for token, qty in _NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(token)}\b", plain):
            return max(1, min(999, qty))
    return None


def _append_transcript(transcript: list[str], line: str, *, limit: int = 10) -> list[str]:
    cleaned = _clean_text(line, max_len=220)
    if not cleaned:
        return transcript[-limit:]
    if transcript and transcript[-1] == cleaned:
        return transcript[-limit:]
    out = [*transcript, cleaned]
    if len(out) > limit:
        out = out[-limit:]
    return out


def _item_name(item_def: object, item_id: str) -> str:
    name = _clean_text(getattr(item_def, "name", ""), max_len=80)
    return name or item_id


@dataclass
class LineItem:
    item_id: str
    item_name: str
    qty: int
    unit_price: int
    subtotal: int


@dataclass
class SellIntent:
    mode: str = "sell"
    item_id: str = ""
    item_name: str = ""
    qty: int | None = None
    max_qty: int = 0
    sell_all: bool = False
    one_by_one: bool = False
    ambiguous: bool = False
    query: str = ""


@dataclass
class TradeSession:
    status: str = "idle"
    npc_id: str = ""
    mode: str = "sell"
    currency: str = "gold"
    cart: list[LineItem] = field(default_factory=list)
    proposed_terms: dict[str, Any] = field(default_factory=dict)
    last_player_intent: str = ""
    pending_question: dict[str, Any] | None = None
    negotiation: dict[str, int] = field(default_factory=dict)
    transcript_short: list[str] = field(default_factory=list)
    llm_enabled: bool = False
    turn_id: int = 0
    last_llm_turn_id: int = -1
    last_action_fingerprint: str = ""


def idle_trade_session() -> TradeSession:
    return TradeSession()


def _normalize_line_item(raw: object) -> LineItem | None:
    if isinstance(raw, LineItem):
        item_id = _clean_text(raw.item_id, max_len=120)
        if not item_id:
            return None
        qty = max(1, _safe_int(raw.qty, 1))
        unit_price = max(0, _safe_int(raw.unit_price, 0))
        return LineItem(
            item_id=item_id,
            item_name=_clean_text(raw.item_name, max_len=80) or item_id,
            qty=qty,
            unit_price=unit_price,
            subtotal=max(0, qty * unit_price),
        )
    if not isinstance(raw, dict):
        return None
    item_id = _clean_text(raw.get("item_id"), max_len=120)
    if not item_id:
        return None
    qty = max(1, _safe_int(raw.get("qty"), 1))
    unit_price = max(0, _safe_int(raw.get("unit_price"), 0))
    return LineItem(
        item_id=item_id,
        item_name=_clean_text(raw.get("item_name"), max_len=80) or item_id,
        qty=qty,
        unit_price=unit_price,
        subtotal=max(0, qty * unit_price),
    )


def _normalize_pending_question(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    qtype = _clean_text(raw.get("type"), max_len=40).casefold()
    if not qtype:
        return None
    out: dict[str, Any] = {"type": qtype}
    out["item_id"] = _clean_text(raw.get("item_id"), max_len=120)
    out["item_name"] = _clean_text(raw.get("item_name"), max_len=80)
    out["max"] = max(1, _safe_int(raw.get("max"), 1))
    out["text"] = _clean_text(raw.get("text"), max_len=220)
    options_raw = raw.get("options")
    options: list[dict[str, Any]] = []
    if isinstance(options_raw, list):
        seen: set[str] = set()
        for idx, row in enumerate(options_raw[:4]):
            if not isinstance(row, dict):
                continue
            oid = _clean_text(row.get("id"), max_len=40).casefold() or f"option_{idx + 1}"
            if oid in seen:
                continue
            seen.add(oid)
            options.append(
                {
                    "id": oid,
                    "text": _clean_text(row.get("text"), max_len=120) or f"Option {idx + 1}",
                    "risk_tag": _clean_text(row.get("risk_tag"), max_len=24),
                    "effects_hint": _clean_text(row.get("effects_hint"), max_len=140),
                }
            )
    out["options"] = options
    return out


def normalize_trade_session(raw: object) -> TradeSession:
    if isinstance(raw, TradeSession):
        session = raw
    elif isinstance(raw, dict):
        cart_rows: list[LineItem] = []
        for row in (raw.get("cart") if isinstance(raw.get("cart"), list) else [])[:24]:
            parsed = _normalize_line_item(row)
            if parsed:
                cart_rows.append(parsed)
        transcript = [
            _clean_text(line, max_len=220)
            for line in (raw.get("transcript_short") if isinstance(raw.get("transcript_short"), list) else [])
            if _clean_text(line, max_len=220)
        ][-10:]
        negotiation_raw = raw.get("negotiation") if isinstance(raw.get("negotiation"), dict) else {}
        session = TradeSession(
            status=_clean_text(raw.get("status"), max_len=24).casefold(),
            npc_id=_clean_text(raw.get("npc_id"), max_len=120),
            mode=_clean_text(raw.get("mode"), max_len=20).casefold() or "sell",
            currency=_clean_text(raw.get("currency"), max_len=20).casefold() or "gold",
            cart=cart_rows,
            proposed_terms=dict(raw.get("proposed_terms") or {}) if isinstance(raw.get("proposed_terms"), dict) else {},
            last_player_intent=_clean_text(raw.get("last_player_intent"), max_len=220),
            pending_question=_normalize_pending_question(raw.get("pending_question")),
            negotiation={
                "mood": _clamp(negotiation_raw.get("mood"), 0, 100, default=50),
                "trust": _clamp(negotiation_raw.get("trust"), 0, 100, default=50),
                "greed": _clamp(negotiation_raw.get("greed"), 0, 100, default=50),
                "rep_bonus": _clamp(negotiation_raw.get("rep_bonus"), -40, 40, default=0),
            },
            transcript_short=transcript,
            llm_enabled=bool(raw.get("llm_enabled", False)),
            turn_id=max(0, _safe_int(raw.get("turn_id"), 0)),
            last_llm_turn_id=max(-1, _safe_int(raw.get("last_llm_turn_id"), -1)),
            last_action_fingerprint=_clean_text(raw.get("last_action_fingerprint"), max_len=200),
        )
    else:
        session = idle_trade_session()

    if session.status not in _TRADE_STATUSES:
        session.status = "idle"
    if session.mode not in _TRADE_MODES:
        session.mode = "sell"
    if session.currency not in _CURRENCIES:
        session.currency = "gold"

    if not isinstance(session.proposed_terms, dict):
        session.proposed_terms = {}
    session.proposed_terms["negotiated_pct"] = _clamp(session.proposed_terms.get("negotiated_pct"), -20, 20, default=0)
    session.proposed_terms["lot_discount_pct"] = _clamp(session.proposed_terms.get("lot_discount_pct"), -20, 20, default=0)
    session.proposed_terms["lot_bonus_pct"] = _clamp(session.proposed_terms.get("lot_bonus_pct"), -20, 20, default=0)

    if not isinstance(session.negotiation, dict):
        session.negotiation = {}
    session.negotiation["mood"] = _clamp(session.negotiation.get("mood"), 0, 100, default=50)
    session.negotiation["trust"] = _clamp(session.negotiation.get("trust"), 0, 100, default=50)
    session.negotiation["greed"] = _clamp(session.negotiation.get("greed"), 0, 100, default=50)
    session.negotiation["rep_bonus"] = _clamp(session.negotiation.get("rep_bonus"), -40, 40, default=0)

    session.transcript_short = [_clean_text(x, max_len=220) for x in session.transcript_short if _clean_text(x, max_len=220)][-10:]
    session.turn_id = max(0, _safe_int(session.turn_id, 0))
    session.last_llm_turn_id = max(-1, _safe_int(session.last_llm_turn_id, -1))
    session.last_action_fingerprint = _clean_text(session.last_action_fingerprint, max_len=200)

    normalized_cart: list[LineItem] = []
    for row in session.cart:
        parsed = _normalize_line_item(row)
        if parsed:
            normalized_cart.append(parsed)
    session.cart = normalized_cart[:24]

    if session.status == "idle":
        session.cart = []
        session.pending_question = None
        session.last_action_fingerprint = ""
        session.last_llm_turn_id = -1
        if not session.npc_id:
            session.mode = "sell"

    return session


def trade_session_to_dict(session: TradeSession | dict | None) -> dict[str, Any]:
    normalized = normalize_trade_session(session)
    payload = asdict(normalized)
    payload["cart"] = [asdict(row) for row in normalized.cart]
    return payload


def trade_session_from_legacy_pending_trade(raw_pending: object) -> TradeSession:
    pending = raw_pending if isinstance(raw_pending, dict) else {}
    action = _clean_text(pending.get("action"), max_len=20).casefold()
    if action not in {"buy", "sell"}:
        return idle_trade_session()
    item_id = _clean_text(pending.get("item_id"), max_len=120)
    if not item_id:
        return idle_trade_session()
    qty = max(1, _safe_int(pending.get("qty"), 1))
    unit_price = max(0, _safe_int(pending.get("unit_price"), 0))
    item_name = _clean_text(pending.get("item_name"), max_len=80) or item_id
    npc_name = _clean_text(pending.get("npc_name"), max_len=120)
    mode = "buy" if action == "buy" else "sell"
    session = TradeSession(
        status="confirming",
        npc_id=npc_name,
        mode=mode,
        currency="gold",
        cart=[LineItem(item_id=item_id, item_name=item_name, qty=qty, unit_price=unit_price, subtotal=qty * unit_price)],
        proposed_terms={"negotiated_pct": 0, "lot_discount_pct": 0, "lot_bonus_pct": 0},
        last_player_intent="legacy_pending_trade",
        pending_question=None,
        negotiation={"mood": 50, "trust": 50, "greed": 50, "rep_bonus": 0},
        transcript_short=[f"Session importee depuis ancienne sauvegarde: {mode} {item_name} x{qty}."],
        llm_enabled=False,
        turn_id=1,
    )
    return normalize_trade_session(session)


class TradeEngine:
    def __init__(self) -> None:
        self.session: TradeSession = idle_trade_session()

    def load_session(self, session: TradeSession | dict | None) -> TradeSession:
        self.session = normalize_trade_session(session)
        return self.session

    def export_session(self) -> TradeSession:
        self.session = normalize_trade_session(self.session)
        return self.session

    def start_trade(self, npc_id: str, mode: str, *, llm_enabled: bool = False) -> TradeSession:
        normalized_mode = str(mode or "sell").strip().casefold()
        if normalized_mode not in _TRADE_MODES:
            normalized_mode = "sell"
        self.session = TradeSession(
            status="selecting",
            npc_id=_clean_text(npc_id, max_len=120),
            mode=normalized_mode,
            currency="gold",
            cart=[],
            proposed_terms={"negotiated_pct": 0, "lot_discount_pct": 0, "lot_bonus_pct": 0},
            last_player_intent="",
            pending_question=None,
            negotiation={"mood": 50, "trust": 50, "greed": 50, "rep_bonus": 0},
            transcript_short=[],
            llm_enabled=bool(llm_enabled),
            turn_id=1,
            last_llm_turn_id=-1,
            last_action_fingerprint="",
        )
        self.session.transcript_short = _append_transcript(self.session.transcript_short, f"Session commerce ouverte ({normalized_mode}).")
        return self.export_session()

    def inventory_totals(self, state) -> dict[str, int]:
        out: dict[str, int] = {}
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    continue
                item_id = _clean_text(getattr(stack, "item_id", ""), max_len=120).casefold()
                qty = max(0, _safe_int(getattr(stack, "qty", 0), 0))
                if not item_id or qty <= 0:
                    continue
                out[item_id] = out.get(item_id, 0) + qty
        return out

    def detect_sell_intent(self, player_text: str, inventory: dict[str, int], item_defs: dict[str, object]) -> SellIntent | None:
        plain = _norm(player_text)
        if not plain:
            return None
        if not re.search(r"\b(vendre|vends|vend|revendre|revends|céder|ceder|liquider)\b", plain):
            return None

        qty = _extract_qty(plain)
        sell_all = bool(re.search(r"\b(tout|toutes|totalite|integralite)\b", plain))
        one_by_one = bool(re.search(r"\b(une\s+par\s+une|un\s+par\s+un)\b", plain))
        query = re.sub(r"\b(vendre|vends|vend|revendre|revends|céder|ceder|liquider)\b", " ", plain)
        query = re.sub(r"\b(tout|toutes|totalite|integralite|une\s+par\s+une|un\s+par\s+un)\b", " ", query)
        query = re.sub(r"\b\d{1,3}\b", " ", query)
        query_tokens = [tok for tok in re.split(r"\s+", query) if tok and tok not in _STOPWORDS]
        query_clean = " ".join(query_tokens[:6]).strip()

        available_items = [(item_id, qty_avail) for item_id, qty_avail in inventory.items() if qty_avail > 0]
        if not available_items:
            return SellIntent(mode="sell", query=query_clean, ambiguous=True)

        if len(available_items) == 1 and not query_clean:
            item_id, max_qty = available_items[0]
            item = item_defs.get(item_id)
            return SellIntent(
                mode="sell",
                item_id=item_id,
                item_name=_item_name(item, item_id),
                qty=qty,
                max_qty=max_qty,
                sell_all=sell_all,
                one_by_one=one_by_one,
                ambiguous=False,
                query=query_clean,
            )

        if not query_clean:
            return SellIntent(mode="sell", query=query_clean, ambiguous=True)

        best_score = 0.0
        best_item_id = ""
        for item_id, qty_avail in available_items:
            item = item_defs.get(item_id)
            item_name = _norm(getattr(item, "name", item_id))
            item_key = _norm(item_id)
            score = 0.0
            if query_clean in item_name or query_clean in item_key:
                score = 1.0
            else:
                score = max(
                    difflib.SequenceMatcher(a=query_clean, b=item_name).ratio(),
                    difflib.SequenceMatcher(a=query_clean, b=item_key).ratio(),
                )
            if score > best_score:
                best_score = score
                best_item_id = item_id
                _ = qty_avail

        if not best_item_id or best_score < 0.38:
            return SellIntent(mode="sell", query=query_clean, ambiguous=True)

        max_qty = max(0, _safe_int(inventory.get(best_item_id), 0))
        item = item_defs.get(best_item_id)
        return SellIntent(
            mode="sell",
            item_id=best_item_id,
            item_name=_item_name(item, best_item_id),
            qty=qty,
            max_qty=max_qty,
            sell_all=sell_all,
            one_by_one=one_by_one,
            ambiguous=False,
            query=query_clean,
        )

    def propose_bundle_options(self, intent: SellIntent, inventory: dict[str, int]) -> dict[str, Any] | None:
        max_qty = max(0, _safe_int(inventory.get(intent.item_id), intent.max_qty))
        if max_qty <= 1:
            return None
        if intent.sell_all:
            return None
        if intent.one_by_one:
            return None
        if intent.qty is not None:
            chosen = max(1, min(max_qty, _safe_int(intent.qty, 1)))
            if chosen != max_qty:
                return None

        return {
            "type": "choose_quantity",
            "item_id": intent.item_id,
            "item_name": intent.item_name,
            "max": max_qty,
            "text": f"Vous en avez {max_qty}. Tout vendre ou choisir une quantite ?",
            "options": [
                {"id": "sell_all", "text": "Tout vendre", "risk_tag": "moyen", "effects_hint": "Liquidation complete du lot."},
                {"id": "set_qty", "text": "Choisir quantite", "risk_tag": "faible", "effects_hint": "Definir un nombre precis."},
                {"id": "sell_one", "text": "Une par une", "risk_tag": "faible", "effects_hint": "Vendre 1 unite."},
                {"id": "cancel", "text": "Annuler", "risk_tag": "aucun", "effects_hint": "Quitter le commerce."},
            ],
        }

    def price_item(self, item: object, npc_profile: dict | None, negotiation_state: dict | None, *, mode: str, qty: int = 1) -> int:
        base_value = max(1, _safe_int(getattr(item, "value_gold", 0), 1))
        nego = negotiation_state if isinstance(negotiation_state, dict) else {}
        greed = _clamp(nego.get("greed"), 0, 100, default=50)
        trust = _clamp(nego.get("trust"), 0, 100, default=50)
        rep_bonus = _clamp(nego.get("rep_bonus"), -40, 40, default=0)
        tension_penalty = 0
        if isinstance(npc_profile, dict):
            tension_penalty = _clamp(npc_profile.get("tension_level"), 0, 100, default=0) // 10

        mode_key = str(mode or "sell").strip().casefold()
        if mode_key == "buy":
            pct = 115 + ((greed - 50) * 0.35) - ((trust - 50) * 0.2) - (rep_bonus * 0.4) + (tension_penalty * 2)
            pct = max(80, min(180, int(round(pct))))
        else:
            pct = 55 - ((greed - 50) * 0.28) + ((trust - 50) * 0.24) + (rep_bonus * 0.45) - (tension_penalty * 2)
            pct = max(25, min(95, int(round(pct))))

        unit = max(1, int(round(base_value * (pct / 100.0))))

        qty_value = max(1, _safe_int(qty, 1))
        lot_pct = 0
        if qty_value >= 10:
            lot_pct = -10 if mode_key == "buy" else 6
        elif qty_value >= 5:
            lot_pct = -5 if mode_key == "buy" else 3

        if lot_pct != 0:
            unit = max(1, int(round(unit * (1 + (lot_pct / 100.0)))))
        return unit

    def apply_markup_discount(self, session: TradeSession, rules: dict | None = None) -> TradeSession:
        self.session = normalize_trade_session(session)
        current = self.session
        rule_map = rules if isinstance(rules, dict) else {}
        negotiated_pct = _clamp(
            rule_map.get("negotiated_pct", current.proposed_terms.get("negotiated_pct", 0)),
            -20,
            20,
            default=0,
        )
        lot_discount = _clamp(rule_map.get("lot_discount_pct", current.proposed_terms.get("lot_discount_pct", 0)), -20, 20, default=0)
        lot_bonus = _clamp(rule_map.get("lot_bonus_pct", current.proposed_terms.get("lot_bonus_pct", 0)), -20, 20, default=0)
        current.proposed_terms["negotiated_pct"] = negotiated_pct
        current.proposed_terms["lot_discount_pct"] = lot_discount
        current.proposed_terms["lot_bonus_pct"] = lot_bonus
        self._recompute_cart_totals(current)
        self.session = normalize_trade_session(current)
        return self.export_session()

    def add_to_cart(
        self,
        *,
        session: TradeSession,
        item_id: str,
        qty: int,
        item_defs: dict[str, object],
        npc_profile: dict | None = None,
    ) -> TradeSession:
        self.session = normalize_trade_session(session)
        current = self.session
        item_key = _clean_text(item_id, max_len=120).casefold()
        if not item_key:
            return self.export_session()
        item = item_defs.get(item_key)
        if item is None:
            # fallback case-sensitive lookup
            item = item_defs.get(item_id)
            if item:
                item_key = str(getattr(item, "id", item_id) or item_id).strip().casefold()
        if item is None:
            return self.export_session()

        qty_value = max(1, _safe_int(qty, 1))
        unit_price = self.price_item(item, npc_profile, current.negotiation, mode=current.mode, qty=qty_value)
        negotiated_pct = _clamp(current.proposed_terms.get("negotiated_pct"), -20, 20, default=0)
        if negotiated_pct != 0:
            unit_price = max(1, int(round(unit_price * (1 + (negotiated_pct / 100.0)))))

        row_index = -1
        for idx, row in enumerate(current.cart):
            if row.item_id.casefold() == item_key:
                row_index = idx
                break
        row = LineItem(
            item_id=item_key,
            item_name=_item_name(item, item_key),
            qty=qty_value,
            unit_price=unit_price,
            subtotal=max(0, qty_value * unit_price),
        )
        if row_index >= 0:
            current.cart[row_index] = row
        else:
            current.cart.append(row)
        self._recompute_cart_totals(current)
        current.status = "selecting"
        current.pending_question = None
        self.session = normalize_trade_session(current)
        return self.export_session()

    def confirm_trade(self, session: TradeSession) -> TradeSession:
        self.session = normalize_trade_session(session)
        current = self.session
        if not current.cart:
            current.status = "selecting"
            current.transcript_short = _append_transcript(current.transcript_short, "Panier vide: ajoute un objet avant de confirmer.")
            self.session = normalize_trade_session(current)
            return self.export_session()
        current.status = "confirming"
        current.pending_question = None
        total = sum(max(0, row.subtotal) for row in current.cart)
        currency = "or" if current.currency == "gold" else current.currency
        current.transcript_short = _append_transcript(current.transcript_short, f"Recap: total {total} {currency}. En attente de confirmation.")
        self.session = normalize_trade_session(current)
        return self.export_session()

    def execute_trade(self, *, state, session: TradeSession, item_defs: dict[str, object]) -> dict[str, Any]:
        self.session = normalize_trade_session(session)
        current = self.session
        if current.status not in {"confirming", "executing"} or not current.cart:
            tx_id = self._record_trade_transaction(
                state,
                status="not_confirmed",
                mode=current.mode,
                ok=False,
                items=[],
                gold_delta=0,
                reason="not_confirmed",
            )
            return {
                "ok": False,
                "error": "not_confirmed",
                "state_patch": {},
                "trade_context": {"status": "not_confirmed", "mode": current.mode, "transaction_id": tx_id},
                "session": trade_session_to_dict(current),
            }

        current.status = "executing"
        qty_done_total = 0
        requested_qty_total = sum(max(1, int(row.qty)) for row in current.cart)
        gold_delta = 0
        lines: list[str] = []
        state_patch: dict[str, Any] = {"inventory": [], "player": {"gold_delta": 0}}
        snapshot = self._capture_inventory_snapshot(state)

        if current.mode == "sell":
            missing_rows: list[str] = []
            for row in current.cart:
                available = self._count_item_in_inventory(state, row.item_id)
                if available < max(1, int(row.qty)):
                    missing_rows.append(f"{row.item_name} ({available}/{row.qty})")
            if missing_rows:
                current.status = "confirming"
                reason = "Objets insuffisants: " + ", ".join(missing_rows[:4])
                lines.append(reason)
                current.transcript_short = _append_transcript(current.transcript_short, reason)
                self.session = normalize_trade_session(current)
                tx_id = self._record_trade_transaction(
                    state,
                    status="insufficient_items",
                    mode=current.mode,
                    ok=False,
                    items=[
                        {"item_id": str(row.item_id), "qty": max(1, int(row.qty))}
                        for row in current.cart
                    ],
                    gold_delta=0,
                    reason=reason,
                )
                return {
                    "ok": False,
                    "error": "insufficient_items",
                    "state_patch": {},
                    "trade_context": {"status": "insufficient_items", "mode": current.mode, "transaction_id": tx_id},
                    "session": trade_session_to_dict(current),
                    "lines": lines,
                }

        if current.mode == "sell":
            for row in current.cart:
                removed = self._remove_item_from_inventory(state, row.item_id, row.qty)
                if removed <= 0:
                    continue
                qty_done_total += removed
                row.qty = removed
                row.subtotal = removed * row.unit_price
                gain = row.subtotal
                gold_delta += gain
                state_patch["inventory"].append({"item_id": row.item_id, "delta": -removed})
            if gold_delta > 0:
                state.player.gold = max(0, _safe_int(getattr(state.player, "gold", 0), 0) + gold_delta)
                lines.append(f"Vente executee: +{gold_delta} or.")
        else:
            # buy/barter minimal: applique comme achat en or.
            total_cost = sum(max(0, row.subtotal) for row in current.cart)
            available = max(0, _safe_int(getattr(state.player, "gold", 0), 0))
            if total_cost > available:
                current.status = "confirming"
                lines.append(f"Or insuffisant: total {total_cost} or, disponible {available} or.")
                current.transcript_short = _append_transcript(current.transcript_short, lines[-1])
                self.session = normalize_trade_session(current)
                tx_id = self._record_trade_transaction(
                    state,
                    status="insufficient_gold",
                    mode=current.mode,
                    ok=False,
                    items=[
                        {"item_id": str(row.item_id), "qty": max(1, int(row.qty))}
                        for row in current.cart
                    ],
                    gold_delta=0,
                    reason=lines[-1],
                )
                return {
                    "ok": False,
                    "error": "insufficient_gold",
                    "state_patch": {},
                    "trade_context": {"status": "insufficient_gold", "mode": current.mode, "total": total_cost, "transaction_id": tx_id},
                    "session": trade_session_to_dict(current),
                    "lines": lines,
                }
            capacity_errors: list[str] = []
            for row in current.cart:
                free_slots = self._capacity_for_item(state, row.item_id, item_defs=item_defs)
                if free_slots < max(1, int(row.qty)):
                    capacity_errors.append(f"{row.item_name} ({free_slots}/{row.qty})")
            if capacity_errors:
                current.status = "confirming"
                reason = "Inventaire insuffisant pour l'achat: " + ", ".join(capacity_errors[:4])
                lines.append(reason)
                current.transcript_short = _append_transcript(current.transcript_short, reason)
                self.session = normalize_trade_session(current)
                tx_id = self._record_trade_transaction(
                    state,
                    status="inventory_full",
                    mode=current.mode,
                    ok=False,
                    items=[
                        {"item_id": str(row.item_id), "qty": max(1, int(row.qty))}
                        for row in current.cart
                    ],
                    gold_delta=0,
                    reason=reason,
                )
                return {
                    "ok": False,
                    "error": "inventory_full",
                    "state_patch": {},
                    "trade_context": {"status": "inventory_full", "mode": current.mode, "transaction_id": tx_id},
                    "session": trade_session_to_dict(current),
                    "lines": lines,
                }
            for row in current.cart:
                added = self._add_item_to_inventory(state, row.item_id, row.qty, item_defs=item_defs)
                if added <= 0:
                    continue
                qty_done_total += added
                row.qty = added
                row.subtotal = added * row.unit_price
                state_patch["inventory"].append({"item_id": row.item_id, "delta": added})
            total_cost_done = sum(max(0, row.subtotal) for row in current.cart)
            gold_delta = -total_cost_done
            state.player.gold = max(0, available + gold_delta)
            lines.append(f"Achat execute: -{total_cost_done} or.")

        if qty_done_total < requested_qty_total:
            self._restore_inventory_snapshot(state, snapshot)
            current.status = "confirming"
            reason = "Transaction annulee: operation partielle detectee."
            lines.append(reason)
            current.transcript_short = _append_transcript(current.transcript_short, reason)
            self.session = normalize_trade_session(current)
            tx_id = self._record_trade_transaction(
                state,
                status="atomic_rollback",
                mode=current.mode,
                ok=False,
                items=[
                    {"item_id": str(row.item_id), "qty": max(1, int(row.qty))}
                    for row in current.cart
                ],
                gold_delta=0,
                reason=reason,
            )
            return {
                "ok": False,
                "error": "atomic_rollback",
                "state_patch": {},
                "trade_context": {"status": "atomic_rollback", "mode": current.mode, "transaction_id": tx_id},
                "session": trade_session_to_dict(current),
                "lines": lines,
            }

        state_patch["player"]["gold_delta"] = gold_delta
        current.status = "done"
        current.pending_question = None
        current.last_action_fingerprint = ""
        current.transcript_short = _append_transcript(current.transcript_short, lines[0] if lines else "Transaction executee.")
        self.session = normalize_trade_session(current)
        tx_id = self._record_trade_transaction(
            state,
            status="ok",
            mode=current.mode,
            ok=True,
            items=[
                {
                    "item_id": str(row.item_id),
                    "qty": max(1, int(row.qty)),
                    "unit_price": max(0, int(row.unit_price)),
                    "subtotal": max(0, int(row.subtotal)),
                }
                for row in current.cart
            ],
            gold_delta=gold_delta,
            reason=lines[0] if lines else "",
        )
        return {
            "ok": True,
            "error": "",
            "state_patch": state_patch,
            "trade_context": {
                "status": "ok",
                "mode": current.mode,
                "qty_done": qty_done_total,
                "gold_delta": gold_delta,
                "total_price": abs(gold_delta),
                "transaction_id": tx_id,
            },
            "session": trade_session_to_dict(current),
            "lines": lines,
        }

    def abort_trade(self, session: TradeSession) -> TradeSession:
        self.session = normalize_trade_session(session)
        current = self.session
        current.status = "aborted"
        current.pending_question = None
        current.last_action_fingerprint = ""
        current.transcript_short = _append_transcript(current.transcript_short, "Transaction annulee.")
        self.session = normalize_trade_session(current)
        return self.export_session()

    def reset_to_idle(self, session: TradeSession | dict | None) -> TradeSession:
        self.session = normalize_trade_session(session)
        current = self.session
        current.status = "idle"
        current.pending_question = None
        current.cart = []
        current.last_player_intent = ""
        current.last_action_fingerprint = ""
        current.last_llm_turn_id = -1
        self.session = normalize_trade_session(current)
        return self.export_session()

    def run_action_guard(self, session: TradeSession, fingerprint: str) -> tuple[TradeSession, bool]:
        self.session = normalize_trade_session(session)
        current = self.session
        fp = _clean_text(fingerprint, max_len=200)
        if fp and current.last_action_fingerprint == fp:
            return self.export_session(), True
        current.last_action_fingerprint = fp
        current.turn_id = max(0, current.turn_id + 1)
        self.session = normalize_trade_session(current)
        return self.export_session(), False

    def apply_quantity_choice(
        self,
        *,
        session: TradeSession,
        option_id: str,
        quantity: int | None,
        item_defs: dict[str, object],
        npc_profile: dict | None = None,
    ) -> tuple[TradeSession, str]:
        self.session = normalize_trade_session(session)
        current = self.session
        pending = current.pending_question if isinstance(current.pending_question, dict) else {}
        if not pending:
            return self.export_session(), "Aucune question en attente."
        item_id = _clean_text(pending.get("item_id"), max_len=120).casefold()
        max_qty = max(1, _safe_int(pending.get("max"), 1))
        choice = _clean_text(option_id, max_len=40).casefold()
        if choice == "cancel":
            current.pending_question = None
            current.status = "aborted"
            current.transcript_short = _append_transcript(current.transcript_short, "Transaction annulee par le joueur.")
            self.session = normalize_trade_session(current)
            return self.export_session(), "Transaction annulee."
        if choice == "set_qty":
            qty_value = max(1, min(max_qty, _safe_int(quantity, 1)))
            updated = self.add_to_cart(session=current, item_id=item_id, qty=qty_value, item_defs=item_defs, npc_profile=npc_profile)
            updated = self.confirm_trade(updated)
            self.session = normalize_trade_session(updated)
            return self.export_session(), f"Quantite fixee a {qty_value}."
        if choice == "sell_one":
            updated = self.add_to_cart(session=current, item_id=item_id, qty=1, item_defs=item_defs, npc_profile=npc_profile)
            updated = self.confirm_trade(updated)
            self.session = normalize_trade_session(updated)
            return self.export_session(), "Une unite ajoutee au panier."
        if choice == "sell_all":
            updated = self.add_to_cart(session=current, item_id=item_id, qty=max_qty, item_defs=item_defs, npc_profile=npc_profile)
            updated = self.confirm_trade(updated)
            self.session = normalize_trade_session(updated)
            return self.export_session(), f"Lot complet ajoute ({max_qty})."
        return self.export_session(), "Option inconnue."

    def build_recap_text(self, session: TradeSession | dict | None) -> str:
        current = normalize_trade_session(session)
        if not current.cart:
            return "Panier vide."
        rows = [f"{row.item_name} x{row.qty} ({row.unit_price}/u)" for row in current.cart[:6]]
        total = sum(max(0, row.subtotal) for row in current.cart)
        return f"{', '.join(rows)} | Total: {total} or"

    def _recompute_cart_totals(self, session: TradeSession) -> None:
        negotiated_pct = _clamp(session.proposed_terms.get("negotiated_pct"), -20, 20, default=0)
        for row in session.cart:
            qty = max(1, _safe_int(row.qty, 1))
            unit = max(1, _safe_int(row.unit_price, 1))
            if negotiated_pct != 0:
                unit = max(1, int(round(unit * (1 + (negotiated_pct / 100.0)))))
            row.qty = qty
            row.unit_price = unit
            row.subtotal = max(0, qty * unit)

    def _capture_inventory_snapshot(self, state) -> dict[str, Any]:
        def _snapshot_grid(grid) -> list[tuple[str, int] | None]:
            rows: list[tuple[str, int] | None] = []
            for stack in grid.slots:
                if stack is None:
                    rows.append(None)
                    continue
                item_id = str(getattr(stack, "item_id", "") or "").strip().casefold()
                qty = max(0, _safe_int(getattr(stack, "qty", 0), 0))
                rows.append((item_id, qty) if item_id and qty > 0 else None)
            return rows

        return {
            "gold": max(0, _safe_int(getattr(state.player, "gold", 0), 0)),
            "carried": _snapshot_grid(state.carried),
            "storage": _snapshot_grid(state.storage),
        }

    def _restore_inventory_snapshot(self, state, snapshot: dict[str, Any]) -> None:
        from app.ui.state.inventory import ItemStack

        def _restore_grid(grid, rows: object) -> None:
            values = rows if isinstance(rows, list) else []
            slots_count = len(grid.slots)
            for idx in range(slots_count):
                row = values[idx] if idx < len(values) else None
                if not isinstance(row, tuple) or len(row) != 2:
                    grid.set(idx, None)
                    continue
                item_id = str(row[0] or "").strip().casefold()
                qty = max(0, _safe_int(row[1], 0))
                if not item_id or qty <= 0:
                    grid.set(idx, None)
                    continue
                grid.set(idx, ItemStack(item_id=item_id, qty=qty))

        _restore_grid(state.carried, snapshot.get("carried"))
        _restore_grid(state.storage, snapshot.get("storage"))
        state.player.gold = max(0, _safe_int(snapshot.get("gold"), 0))

    def _count_item_in_inventory(self, state, item_id: str) -> int:
        target_id = str(item_id or "").strip().casefold()
        if not target_id:
            return 0
        total = 0
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                total += max(0, _safe_int(getattr(stack, "qty", 0), 0))
        return total

    def _capacity_for_item(self, state, item_id: str, *, item_defs: dict[str, object]) -> int:
        target_id = str(item_id or "").strip().casefold()
        if not target_id:
            return 0
        item_def = item_defs.get(target_id) or item_defs.get(item_id)
        stack_max = max(1, _safe_int(getattr(item_def, "stack_max", 1), 1))
        free = 0
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    free += stack_max
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                current_qty = max(0, _safe_int(getattr(stack, "qty", 0), 0))
                free += max(0, stack_max - current_qty)
        return max(0, free)

    def _record_trade_transaction(
        self,
        state,
        *,
        status: str,
        mode: str,
        ok: bool,
        items: list[dict[str, Any]],
        gold_delta: int,
        reason: str = "",
    ) -> str:
        if not isinstance(getattr(state, "gm_state", None), dict):
            state.gm_state = {}
        gm_state = state.gm_state
        flags = gm_state.get("flags")
        if not isinstance(flags, dict):
            gm_state["flags"] = {}
            flags = gm_state["flags"]
        seq = max(0, _safe_int(flags.get("trade_tx_seq"), 0)) + 1
        flags["trade_tx_seq"] = seq
        tx_id = f"tx_{seq:06d}"
        row = {
            "transaction_id": tx_id,
            "status": str(status or "unknown"),
            "mode": str(mode or "sell"),
            "ok": bool(ok),
            "gold_delta": int(gold_delta),
            "reason": _clean_text(reason, max_len=220),
            "items": items[:8] if isinstance(items, list) else [],
        }
        history = gm_state.get("trade_transactions")
        if not isinstance(history, list):
            history = []
        history.append(row)
        gm_state["trade_transactions"] = history[-120:]
        return tx_id

    def _remove_item_from_inventory(self, state, item_id: str, qty: int) -> int:
        target_id = str(item_id or "").strip().casefold()
        wanted = max(0, _safe_int(qty, 0))
        if not target_id or wanted <= 0:
            return 0
        removed = 0
        for grid in (state.carried, state.storage):
            for idx, stack in enumerate(grid.slots):
                if removed >= wanted:
                    break
                if stack is None:
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                sqty = max(0, _safe_int(getattr(stack, "qty", 0), 0))
                if sqty <= 0:
                    grid.set(idx, None)
                    continue
                take = min(sqty, wanted - removed)
                left = sqty - take
                removed += take
                if left > 0:
                    stack.qty = left
                else:
                    grid.set(idx, None)
            if removed >= wanted:
                break
        return removed

    def _add_item_to_inventory(self, state, item_id: str, qty: int, *, item_defs: dict[str, object]) -> int:
        target_id = str(item_id or "").strip().casefold()
        wanted = max(0, _safe_int(qty, 0))
        if not target_id or wanted <= 0:
            return 0
        item_def = item_defs.get(target_id) or item_defs.get(item_id)
        stack_max = max(1, _safe_int(getattr(item_def, "stack_max", 1), 1))
        added = 0

        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if added >= wanted:
                    break
                if stack is None:
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                current_qty = max(0, _safe_int(getattr(stack, "qty", 0), 0))
                room = max(0, stack_max - current_qty)
                if room <= 0:
                    continue
                take = min(room, wanted - added)
                stack.qty = current_qty + take
                added += take
            if added >= wanted:
                return added

        for grid in (state.carried, state.storage):
            for idx, stack in enumerate(grid.slots):
                if added >= wanted:
                    break
                if stack is not None:
                    continue
                take = min(stack_max, wanted - added)
                if take <= 0:
                    continue
                from app.ui.state.inventory import ItemStack

                grid.set(idx, ItemStack(item_id=target_id, qty=take))
                added += take
            if added >= wanted:
                break
        return added
