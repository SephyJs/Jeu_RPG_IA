from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
import unicodedata

from app.core.data.item_manager import ItemDef, ItemsManager
from app.ui.state.inventory import ItemStack


_BUY_VERBS = (
    "acheter",
    "achete",
    "achète",
    "jachete",
    "j'achete",
    "j'achète",
)
_SELL_VERBS = ("vendre", "vends", "vend", "revendre", "revends")
_GIVE_VERBS = ("donner", "donne", "offrir", "offre", "file", "tendre", "tends")
_EXCHANGE_VERBS = ("echanger", "echange", "échange", "troquer", "troque")

_MERCHANT_HINTS = (
    "marchand",
    "marchande",
    "commercant",
    "commerçant",
    "forgeron",
    "forgeronne",
    "boutiquier",
    "boutiquiere",
    "boutiquière",
    "aubergiste",
    "tavernier",
    "taverniere",
    "tavernière",
    "vendeur",
    "vendeuse",
    "colporteur",
    "apothicaire",
)
_BEGGAR_HINTS = (
    "mendiant",
    "mendiante",
    "sans abri",
    "sdf",
    "clochard",
    "gueux",
    "vagabond",
    "vagabonde",
)

_STOPWORDS = {
    "de",
    "du",
    "des",
    "la",
    "le",
    "les",
    "un",
    "une",
    "au",
    "aux",
    "a",
    "à",
    "mon",
    "ma",
    "mes",
    "ton",
    "ta",
    "tes",
}

_TYPE_HINTS = {
    "weapon": {"arme", "epee", "épée", "dague", "lance", "arc", "hache", "marteau", "baton"},
    "armor": {"armure", "bouclier", "casque", "plastron"},
    "consumable": {"pain", "potion", "elixir", "elixir", "nourriture", "ration"},
    "accessory": {"anneau", "amulette", "talisman", "accessoire"},
    "material": {"minerai", "bois", "cuir", "materiau", "matériau"},
}


@dataclass
class TradeIntent:
    action: str
    qty: int
    item_query: str


class EconomyManager:
    def __init__(self, *, data_dir: str = "data") -> None:
        self.items = ItemsManager(data_dir=data_dir)

    def inventory_totals(self, state) -> dict[str, int]:
        out: dict[str, int] = {}
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    continue
                item_id = str(stack.item_id or "").strip().casefold()
                qty = int(getattr(stack, "qty", 0) or 0)
                if not item_id or qty <= 0:
                    continue
                out[item_id] = out.get(item_id, 0) + qty
        return out

    def inventory_summary(self, state, item_defs: dict[str, ItemDef], *, limit: int = 12) -> str:
        totals = self.inventory_totals(state)
        if not totals:
            return "vide"
        rows: list[tuple[str, int, str]] = []
        for item_id, qty in totals.items():
            item = item_defs.get(item_id)
            name = str(getattr(item, "name", "") or "").strip() or item_id
            rows.append((item_id, qty, name))
        rows.sort(key=lambda r: (-r[1], r[2]))
        return ", ".join(f"{name} x{qty}" for _, qty, name in rows[: max(1, limit)])

    def process_trade_message(
        self,
        *,
        state,
        user_text: str,
        selected_npc_name: str,
        selected_npc_profile: dict | None,
        item_defs: dict[str, ItemDef],
    ) -> dict:
        intent = self._extract_trade_intent(user_text)
        if intent is None:
            return {"attempted": False}

        action = intent.action
        qty = max(1, int(intent.qty))
        query = intent.item_query
        merchant = self._is_merchant_npc(selected_npc_name, selected_npc_profile)
        beggar = self._is_beggar_npc(selected_npc_name, selected_npc_profile)
        discount_pct = max(0, self._safe_int(state.gm_state.get("flags", {}).get("shop_discount_pct"), 0))

        item_id, score = self._resolve_item_id(query, item_defs)
        if not item_id and action == "buy" and merchant:
            created = self._ensure_trade_item_exists(query, item_defs)
            if created is not None:
                item_id, item_defs = created
                score = 10.0
                state.item_defs = item_defs

        if not item_id:
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [f"Objet non reconnu pour '{query}'. Reformule avec un nom d'objet plus précis."],
                "trade_context": {"action": action, "status": "item_unknown", "query": query},
            }

        item = item_defs.get(item_id)
        if item is None:
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [f"Objet introuvable: {item_id}."],
                "trade_context": {"action": action, "status": "item_missing", "item_id": item_id},
            }

        if action == "buy":
            return self._handle_buy(
                state=state,
                item=item,
                qty=qty,
                discount_pct=discount_pct,
                can_trade=merchant,
                resolution_score=score,
            )
        if action == "sell":
            return self._handle_sell(state=state, item=item, qty=qty, can_trade=merchant)
        if action == "exchange":
            return self._handle_exchange(
                state=state,
                item=item,
                qty=qty,
                target_name=selected_npc_name,
                target_is_beggar=beggar,
            )
        if action == "give":
            return self._handle_give(
                state=state,
                item=item,
                qty=qty,
                target_name=selected_npc_name,
                target_is_beggar=beggar,
            )
        return {"attempted": False}

    def _handle_buy(
        self,
        *,
        state,
        item: ItemDef,
        qty: int,
        discount_pct: int,
        can_trade: bool,
        resolution_score: float,
    ) -> dict:
        item_name = str(item.name or item.id)
        if not can_trade:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": ["Ce PNJ ne vend rien pour le moment."],
                "trade_context": {"action": "buy", "status": "npc_not_vendor", "item_id": item.id},
            }

        unit_price = self._buy_price(item=item, discount_pct=discount_pct)
        requested = max(1, qty)
        affordable = max(0, int(state.player.gold) // unit_price)
        if affordable <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Or insuffisant: {item_name} coute {unit_price} or/unite."],
                "trade_context": {
                    "action": "buy",
                    "status": "insufficient_gold",
                    "item_id": item.id,
                    "unit_price": unit_price,
                    "requested_qty": requested,
                },
            }

        qty_to_try = min(requested, affordable)
        added = self._add_item_to_inventory(state, item.id, qty_to_try, item_defs=state.item_defs)
        if added <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Inventaire plein: impossible d'acheter {item_name}."],
                "trade_context": {
                    "action": "buy",
                    "status": "inventory_full",
                    "item_id": item.id,
                    "unit_price": unit_price,
                    "requested_qty": requested,
                },
            }

        total_cost = added * unit_price
        state.player.gold = max(0, int(state.player.gold) - total_cost)

        lines = [f"Achat confirme: {item_name} x{added} pour {total_cost} or ({unit_price}/u)."]
        if added < requested:
            if affordable < requested:
                lines.append("Quantite ajustee selon ton or disponible.")
            else:
                lines.append("Quantite ajustee faute de place dans l'inventaire.")

        return {
            "attempted": True,
            "applied": True,
            "action": "buy",
            "system_lines": lines,
            "trade_context": {
                "action": "buy",
                "status": "ok",
                "item_id": item.id,
                "item_name": item_name,
                "qty_requested": requested,
                "qty_done": added,
                "unit_price": unit_price,
                "gold_delta": -total_cost,
                "discount_pct": discount_pct,
                "confidence": round(float(resolution_score), 3),
            },
        }

    def _handle_sell(self, *, state, item: ItemDef, qty: int, can_trade: bool) -> dict:
        item_name = str(item.name or item.id)
        if not can_trade:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": ["Ce PNJ n'achete rien pour le moment."],
                "trade_context": {"action": "sell", "status": "npc_not_buyer", "item_id": item.id},
            }

        owned = self._count_item(state, item.id)
        requested = max(1, qty)
        if owned <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": [f"Tu ne possedes pas {item_name}."],
                "trade_context": {"action": "sell", "status": "not_owned", "item_id": item.id},
            }

        qty_done = min(requested, owned)
        removed = self._remove_item_from_inventory(state, item.id, qty_done)
        if removed <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": [f"Impossible de retirer {item_name} de l'inventaire."],
                "trade_context": {"action": "sell", "status": "remove_failed", "item_id": item.id},
            }

        unit_price = self._sell_price(item=item, player_sheet=getattr(state, "player_sheet", {}))
        total_gain = removed * unit_price
        state.player.gold = max(0, int(state.player.gold) + total_gain)

        lines = [f"Vente confirmee: {item_name} x{removed} pour {total_gain} or ({unit_price}/u)."]
        if removed < requested:
            lines.append("Quantite vendue ajustee selon ton stock.")

        return {
            "attempted": True,
            "applied": True,
            "action": "sell",
            "system_lines": lines,
            "trade_context": {
                "action": "sell",
                "status": "ok",
                "item_id": item.id,
                "item_name": item_name,
                "qty_requested": requested,
                "qty_done": removed,
                "unit_price": unit_price,
                "gold_delta": total_gain,
            },
        }

    def _handle_give(self, *, state, item: ItemDef, qty: int, target_name: str, target_is_beggar: bool) -> dict:
        item_name = str(item.name or item.id)
        owned = self._count_item(state, item.id)
        requested = max(1, qty)
        if owned <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "give",
                "system_lines": [f"Tu n'as pas {item_name} a donner."],
                "trade_context": {"action": "give", "status": "not_owned", "item_id": item.id},
            }

        qty_done = min(requested, owned)
        removed = self._remove_item_from_inventory(state, item.id, qty_done)
        if removed <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "give",
                "system_lines": [f"Impossible de donner {item_name} pour le moment."],
                "trade_context": {"action": "give", "status": "remove_failed", "item_id": item.id},
            }

        lines = [f"Don effectue: {item_name} x{removed} donne a {target_name}."]
        flags = state.gm_state.setdefault("flags", {})
        total_don = max(0, self._safe_int(flags.get("charity_donations_total"), 0) + removed)
        flags["charity_donations_total"] = total_don
        if target_is_beggar:
            beggar_total = max(0, self._safe_int(flags.get("charity_donations_to_beggar"), 0) + removed)
            flags["charity_donations_to_beggar"] = beggar_total
            lines.append("Le geste est remarque dans les rues.")
        else:
            beggar_total = max(0, self._safe_int(flags.get("charity_donations_to_beggar"), 0))

        return {
            "attempted": True,
            "applied": True,
            "action": "give",
            "system_lines": lines,
            "trade_context": {
                "action": "give",
                "status": "ok",
                "item_id": item.id,
                "item_name": item_name,
                "qty_requested": requested,
                "qty_done": removed,
                "gold_delta": 0,
                "target_is_beggar": bool(target_is_beggar),
                "charity_to_beggar_total": beggar_total,
            },
            "secret_charity_candidate": bool(target_is_beggar and removed > 0),
        }

    def _handle_exchange(self, *, state, item: ItemDef, qty: int, target_name: str, target_is_beggar: bool) -> dict:
        result = self._handle_give(
            state=state,
            item=item,
            qty=qty,
            target_name=target_name,
            target_is_beggar=target_is_beggar,
        )
        if not bool(result.get("attempted")):
            return result
        result["action"] = "exchange"
        ctx = result.get("trade_context")
        if isinstance(ctx, dict):
            ctx["action"] = "exchange"
        if bool(result.get("applied")):
            lines = result.get("system_lines")
            if isinstance(lines, list):
                lines.insert(0, f"Echange valide avec {target_name}.")
        return result

    def _extract_trade_intent(self, text: str) -> TradeIntent | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        plain = self._norm(raw)
        if not plain:
            return None

        tokens = plain.split()
        if not tokens:
            return None

        action = ""
        if any(v in plain for v in _BUY_VERBS):
            action = "buy"
        elif any(v in plain for v in _SELL_VERBS):
            action = "sell"
        elif any(v in plain for v in _EXCHANGE_VERBS):
            action = "exchange"
        elif any(v in plain for v in _GIVE_VERBS):
            action = "give"
        if not action:
            return None

        qty = 1
        qty_match = re.search(r"\b(\d{1,3})\b", plain)
        if qty_match:
            try:
                qty = max(1, min(99, int(qty_match.group(1))))
            except Exception:
                qty = 1
        else:
            if re.search(r"\b(deux|deu)\b", plain):
                qty = 2
            elif re.search(r"\b(trois)\b", plain):
                qty = 3
            elif re.search(r"\b(quatre)\b", plain):
                qty = 4
            elif re.search(r"\b(cinq)\b", plain):
                qty = 5

        query = plain
        query = re.sub(r"\b\d{1,3}\b", " ", query)
        for verb in (*_BUY_VERBS, *_SELL_VERBS, *_EXCHANGE_VERBS, *_GIVE_VERBS):
            query = query.replace(verb, " ")
        query = re.sub(r"\b(je|j|veux|voudrais|souhaite|peux|peut|me|moi|te|toi)\b", " ", query)
        # coupe la cible explicite: "a/au/aux X"
        query = re.split(r"\b(?:a|au|aux)\b", query)[0]
        parts = [p for p in re.split(r"\s+", query) if p and p not in _STOPWORDS]
        if not parts:
            return None
        cleaned = " ".join(parts)
        return TradeIntent(action=action, qty=qty, item_query=cleaned)

    def _resolve_item_id(self, query: str, item_defs: dict[str, ItemDef]) -> tuple[str | None, float]:
        q = self._norm(query)
        if not q:
            return None, 0.0
        if q in item_defs:
            return q, 100.0

        by_name: dict[str, str] = {}
        for item_id, item in item_defs.items():
            by_name[self._norm(item_id)] = item_id
            by_name[self._norm(item.name)] = item_id
        if q in by_name:
            return by_name[q], 98.0

        tokens = [t for t in q.split() if t and t not in _STOPWORDS]
        best_id: str | None = None
        best_score = 0.0
        for item_id, item in item_defs.items():
            hay = " ".join(
                (
                    self._norm(item_id),
                    self._norm(item.name),
                    self._norm(item.type),
                    self._norm(item.slot),
                    self._norm(item.description),
                )
            )
            score = 0.0
            for token in tokens:
                if token and token in hay:
                    score += 2.0
            for item_type, hints in _TYPE_HINTS.items():
                if item_type == self._norm(item.type) and any(h in tokens for h in hints):
                    score += 1.5
            if score > best_score:
                best_score = score
                best_id = item_id

        if best_id and best_score >= 1.5:
            return best_id, best_score

        all_keys = list(by_name.keys())
        close = difflib.get_close_matches(q, all_keys, n=1, cutoff=0.7)
        if close:
            return by_name[close[0]], 1.0
        return None, 0.0

    def _ensure_trade_item_exists(self, query: str, item_defs: dict[str, ItemDef]) -> tuple[str, dict[str, ItemDef]] | None:
        base_text = self._norm(query)
        words = [w for w in base_text.split() if w and w not in _STOPWORDS]
        if not words:
            return None
        short = "_".join(words[:3]).strip("_") or "objet"
        item_type = "misc"
        for candidate_type, hints in _TYPE_HINTS.items():
            if any(h in words for h in hints):
                item_type = candidate_type
                break
        if item_type in {"weapon", "armor", "accessory"}:
            stack_max = 1
        elif item_type in {"consumable", "material"}:
            stack_max = 20
        else:
            stack_max = 8

        base_value = 8
        if item_type == "weapon":
            base_value = 32
        elif item_type == "armor":
            base_value = 28
        elif item_type == "accessory":
            base_value = 24
        elif item_type == "consumable":
            base_value = 10
        elif item_type == "material":
            base_value = 12

        item_id = short
        i = 1
        while item_id in item_defs:
            i += 1
            item_id = f"{short}_{i}"

        pretty = " ".join(w.capitalize() for w in words[:5]) or "Objet"
        payload = {
            "id": item_id,
            "name": pretty,
            "stack_max": stack_max,
            "type": item_type,
            "slot": "weapon" if item_type == "weapon" else ("armor" if item_type == "armor" else ("accessory" if item_type == "accessory" else "")),
            "rarity": "common",
            "description": f"Objet cree pour le commerce: {pretty}.",
            "stat_bonuses": {},
            "effects": [],
            "value_gold": base_value,
        }
        try:
            saved = self.items.save_item(payload)
        except Exception:
            return None
        updated = dict(item_defs)
        updated[saved.id] = saved
        return saved.id, updated

    def _buy_price(self, *, item: ItemDef, discount_pct: int) -> int:
        base = max(1, self._safe_int(getattr(item, "value_gold", 0), 0))
        if base <= 0:
            base = 10
        price = int(round(base * 1.2))
        if discount_pct > 0:
            price = int(round(price * max(0.35, 1.0 - (discount_pct / 100.0))))
        return max(1, price)

    def _sell_price(self, *, item: ItemDef, player_sheet: dict) -> int:
        base = max(1, self._safe_int(getattr(item, "value_gold", 0), 0))
        if base <= 0:
            base = 8
        charisme = 5
        if isinstance(player_sheet, dict):
            stats = player_sheet.get("effective_stats")
            if not isinstance(stats, dict):
                stats = player_sheet.get("stats")
            if isinstance(stats, dict):
                charisme = max(1, self._safe_int(stats.get("charisme"), 5))
        bonus = max(-0.08, min(0.16, (charisme - 5) * 0.01))
        return max(1, int(round((base * 0.52) * (1.0 + bonus))))

    def _is_merchant_npc(self, npc_name: str, npc_profile: dict | None) -> bool:
        text = [str(npc_name or "")]
        if isinstance(npc_profile, dict):
            text.append(str(npc_profile.get("role") or ""))
            text.append(str(npc_profile.get("label") or ""))
            text.append(str(npc_profile.get("char_persona") or ""))
        hay = self._norm(" ".join(text))
        return any(h in hay for h in _MERCHANT_HINTS)

    def _is_beggar_npc(self, npc_name: str, npc_profile: dict | None) -> bool:
        text = [str(npc_name or "")]
        if isinstance(npc_profile, dict):
            text.append(str(npc_profile.get("role") or ""))
            text.append(str(npc_profile.get("label") or ""))
            text.append(str(npc_profile.get("char_persona") or ""))
        hay = self._norm(" ".join(text))
        return any(h in hay for h in _BEGGAR_HINTS)

    def _count_item(self, state, item_id: str) -> int:
        total = 0
        target = str(item_id or "").strip().casefold()
        if not target:
            return 0
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    continue
                sid = str(stack.item_id or "").strip().casefold()
                if sid != target:
                    continue
                total += max(0, self._safe_int(getattr(stack, "qty", 0), 0))
        return total

    def _remove_item_from_inventory(self, state, item_id: str, qty: int) -> int:
        target = str(item_id or "").strip().casefold()
        remaining = max(0, int(qty))
        removed = 0
        if not target or remaining <= 0:
            return 0
        for grid in (state.carried, state.storage):
            for idx, stack in enumerate(grid.slots):
                if remaining <= 0:
                    break
                if stack is None:
                    continue
                sid = str(stack.item_id or "").strip().casefold()
                if sid != target:
                    continue
                sqty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                if sqty <= 0:
                    continue
                take = min(sqty, remaining)
                new_qty = sqty - take
                if new_qty > 0:
                    grid.slots[idx] = ItemStack(item_id=target, qty=new_qty)
                else:
                    grid.slots[idx] = None
                remaining -= take
                removed += take
            if remaining <= 0:
                break
        return removed

    def _add_item_to_inventory(self, state, item_id: str, qty: int, *, item_defs: dict[str, ItemDef]) -> int:
        target = str(item_id or "").strip().casefold()
        remaining = max(0, int(qty))
        added = 0
        if not target or remaining <= 0:
            return 0
        item = item_defs.get(target)
        stack_max = 1
        if item is not None:
            stack_max = max(1, self._safe_int(getattr(item, "stack_max", 1), 1))

        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if remaining <= 0:
                    break
                if stack is None:
                    continue
                sid = str(stack.item_id or "").strip().casefold()
                if sid != target:
                    continue
                sqty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                cap = max(0, stack_max - sqty)
                if cap <= 0:
                    continue
                put = min(cap, remaining)
                stack.qty = sqty + put
                remaining -= put
                added += put
            if remaining <= 0:
                break

        for grid in (state.carried, state.storage):
            while remaining > 0:
                try:
                    idx = grid.slots.index(None)
                except ValueError:
                    break
                put = min(stack_max, remaining)
                grid.slots[idx] = ItemStack(item_id=target, qty=put)
                remaining -= put
                added += put
            if remaining <= 0:
                break
        return added

    def _norm(self, text: str) -> str:
        raw = unicodedata.normalize("NFKD", str(text or "").strip()).encode("ascii", "ignore").decode("ascii")
        clean = re.sub(r"[^a-z0-9' ]+", " ", raw.lower())
        clean = clean.replace("'", " ")
        return re.sub(r"\s+", " ", clean).strip()

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default
