from __future__ import annotations

from dataclasses import dataclass
import difflib
import json
from pathlib import Path
import re
import unicodedata

from app.core.data.item_manager import ItemDef, ItemsManager
from app.gamemaster.reputation_manager import merchant_price_multiplier_from_reputation
from app.gamemaster.world_time import day_index
from app.ui.state.inventory import ItemStack


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

_PENDING_ACTIONS = {"buy", "sell", "give", "exchange"}

_QUERY_DROP_WORDS = {
    "je",
    "j",
    "tu",
    "vous",
    "moi",
    "toi",
    "nous",
    "en",
    "veux",
    "voudrais",
    "souhaite",
    "aimerais",
    "peux",
    "peut",
    "pouvez",
    "pourrais",
    "pourriez",
    "est",
    "ce",
    "que",
    "combien",
    "prix",
    "coute",
    "coutent",
    "acheter",
    "achete",
    "prendre",
    "prends",
    "vendre",
    "vend",
    "vends",
    "revendre",
    "revends",
    "vendez",
    "donner",
    "donne",
    "offrir",
    "offre",
    "echanger",
    "echange",
    "troquer",
    "troque",
    "racheter",
    "rachetez",
    "viens",
    "venir",
    "venez",
    "venu",
    "venue",
    "passe",
    "passer",
    "svp",
    "merci",
    "bonjour",
    "salut",
    "bonsoir",
    "il",
    "elle",
    "ils",
    "elles",
}

_CONFIRM_HINTS = {
    "oui",
    "ok",
    "okay",
    "daccord",
    "d accord",
    "valide",
    "confirme",
    "accepte",
    "vas y",
    "go",
}

_CANCEL_HINTS = {
    "non",
    "annule",
    "annuler",
    "stop",
    "refuse",
    "laisse tomber",
    "laisser tomber",
    "pas maintenant",
    "plus tard",
    "oublie",
}


_FIRST_PERSON_PRONOUN_FILLERS = (
    "me",
    "moi",
    "te",
    "toi",
    "vous",
    "lui",
    "leur",
    "en",
    "le",
    "la",
    "les",
    "un",
    "une",
    "des",
    "du",
    "de",
    "d",
    "l",
)

_FIRST_PERSON_BRIDGE_WORDS = tuple(
    dict.fromkeys(
        [
            *_FIRST_PERSON_PRONOUN_FILLERS,
            "veux",
            "voudrais",
            "souhaite",
            "aimerais",
            "vais",
            "peux",
            "pourrais",
            "viens",
            "venir",
            "venez",
            "compte",
            "propose",
            "juste",
            "simplement",
            "ici",
            "pour",
        ]
    )
)


@dataclass
class TradeIntent:
    action: str
    qty: int
    item_query: str
    commit_now: bool
    unit_price_hint: int | None


class EconomyManager:
    def __init__(self, *, data_dir: str = "data") -> None:
        self.data_dir = Path(data_dir)
        self.items = ItemsManager(data_dir=data_dir)
        self.merchants_dir = self.data_dir / "merchants"
        self._merchant_catalog_cache: dict[str, dict] | None = None

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
        plain = self._norm(user_text)
        if not plain:
            return {"attempted": False}

        self._restock_merchants_if_needed(state)
        merchant_entry = self._resolve_merchant_entry(
            state=state,
            selected_npc_name=selected_npc_name,
            selected_npc_profile=selected_npc_profile,
        )
        merchant = bool(merchant_entry) or self._is_merchant_npc(selected_npc_name, selected_npc_profile)
        beggar = self._is_beggar_npc(selected_npc_name, selected_npc_profile)
        discount_pct = max(0, self._safe_int(state.gm_state.get("flags", {}).get("shop_discount_pct"), 0))

        pending = self._get_pending_trade_for_npc(state, selected_npc_name)
        intent = self._extract_trade_intent(user_text)
        if pending:
            if self._is_cancel_message(plain):
                action = str(pending.get("action") or "trade")
                self._clear_pending_trade(state)
                return {
                    "attempted": True,
                    "applied": False,
                    "action": action,
                    "system_lines": ["Transaction annulee."],
                    "trade_context": {
                        "action": action,
                        "status": "canceled",
                        "item_id": str(pending.get("item_id") or ""),
                        "item_name": str(pending.get("item_name") or ""),
                    },
                }
            replace_pending = False
            if intent is not None and str(intent.item_query or "").strip():
                pending_action = str(pending.get("action") or "").strip().casefold()
                intent_action = str(intent.action or "").strip().casefold()
                if intent_action and pending_action and intent_action != pending_action:
                    replace_pending = True
                else:
                    pending_item_id = str(pending.get("item_id") or "").strip().casefold()
                    resolved_item_id, _ = self._resolve_item_id(str(intent.item_query or ""), item_defs)
                    resolved_item_id = str(resolved_item_id or "").strip().casefold()
                    if pending_item_id and resolved_item_id and resolved_item_id != pending_item_id:
                        replace_pending = True

            if pending and self._is_confirm_message(plain) and not replace_pending:
                qty_override = self._extract_qty_from_text(plain) or max(1, self._safe_int(pending.get("qty"), 1))
                unit_price_override = self._extract_unit_price_hint(plain)
                result = self._apply_pending_trade(
                    state=state,
                    pending=pending,
                    qty=qty_override,
                    unit_price_override=unit_price_override,
                    selected_npc_name=selected_npc_name,
                    selected_npc_profile=selected_npc_profile,
                    item_defs=item_defs,
                    discount_pct=discount_pct,
                    target_is_beggar=beggar,
                    can_trade=merchant,
                    merchant_entry=merchant_entry,
                )
                ctx = result.get("trade_context")
                if isinstance(ctx, dict):
                    ctx["confirmed_from_pending"] = True
                self._clear_pending_trade(state)
                return result
            if replace_pending:
                # Nouvelle transaction explicite: abandonne l'offre en attente.
                self._clear_pending_trade(state)
                pending = None

        if pending and intent is None:
            action = str(pending.get("action") or "trade").strip().casefold() or "trade"
            item_name = str(pending.get("item_name") or pending.get("item_id") or "objet").strip()
            qty_pending = max(1, self._safe_int(pending.get("qty"), 1))
            unit_price = max(0, self._safe_int(pending.get("unit_price"), 0))
            if unit_price > 0:
                total_price = qty_pending * unit_price
                recap = f"Offre en attente: {item_name} x{qty_pending} ({unit_price}/u, total {total_price} or)."
            else:
                recap = f"Offre en attente: {item_name} x{qty_pending}."
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [
                    recap,
                    "Confirme avec 'oui' ou ecris 'annuler'.",
                ],
                "trade_context": {
                    "action": action,
                    "status": "offer_pending",
                    "item_id": str(pending.get("item_id") or ""),
                    "item_name": item_name,
                    "qty_offer": qty_pending,
                    "unit_price": unit_price,
                },
            }

        if intent is None:
            return {"attempted": False}

        if pending:
            # Le joueur formule une nouvelle transaction: on remplace l'ancienne offre.
            self._clear_pending_trade(state)

        action = intent.action
        qty = max(1, int(intent.qty))
        query = intent.item_query
        unit_price_hint = intent.unit_price_hint
        if not query:
            if action == "buy":
                label = "achat"
                line = "Precise l'objet que tu veux acheter (ex: epee, armure, potion)."
            elif action == "sell":
                label = "vente"
                line = "Precise l'objet que tu veux vendre."
            elif action == "exchange":
                label = "echange"
                line = "Precise l'objet a echanger."
            else:
                label = "don"
                line = "Precise l'objet a donner."
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [line],
                "trade_context": {"action": action, "status": "missing_query", "label": label},
            }

        if action == "buy":
            return self._prepare_or_apply_buy(
                state=state,
                item_defs=item_defs,
                query=query,
                qty=qty,
                commit_now=intent.commit_now,
                discount_pct=discount_pct,
                can_trade=merchant,
                merchant_entry=merchant_entry,
                npc_name=selected_npc_name,
                unit_price_hint=unit_price_hint,
            )
        if action == "sell":
            return self._prepare_or_apply_sell(
                state=state,
                item_defs=item_defs,
                query=query,
                qty=qty,
                commit_now=intent.commit_now,
                can_trade=merchant,
                merchant_entry=merchant_entry,
                npc_name=selected_npc_name,
            )
        if action == "exchange":
            return self._prepare_or_apply_give_or_exchange(
                state=state,
                item_defs=item_defs,
                query=query,
                qty=qty,
                action="exchange",
                commit_now=intent.commit_now,
                target_name=selected_npc_name,
                target_is_beggar=beggar,
            )
        if action == "give":
            return self._prepare_or_apply_give_or_exchange(
                state=state,
                item_defs=item_defs,
                query=query,
                qty=qty,
                action="give",
                commit_now=intent.commit_now,
                target_name=selected_npc_name,
                target_is_beggar=beggar,
            )
        return {"attempted": False}

    def _prepare_or_apply_buy(
        self,
        *,
        state,
        item_defs: dict[str, ItemDef],
        query: str,
        qty: int,
        commit_now: bool,
        discount_pct: int,
        can_trade: bool,
        merchant_entry: dict | None = None,
        npc_name: str = "",
        unit_price_hint: int | None = None,
    ) -> dict:
        if not can_trade:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": ["Ce PNJ ne vend rien pour le moment."],
                "trade_context": {"action": "buy", "status": "npc_not_vendor"},
            }

        item_id, score = self._resolve_item_id(query, item_defs)
        if not item_id:
            created = self._ensure_trade_item_exists(query, item_defs)
            if created is not None:
                item_id, item_defs = created
                score = 10.0
                state.item_defs = item_defs

        if not item_id:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Objet non reconnu pour '{query}'. Reformule avec un nom d'objet plus precis."],
                "trade_context": {"action": "buy", "status": "item_unknown", "query": query},
            }

        item = item_defs.get(item_id)
        if item is None:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Objet introuvable: {item_id}."],
                "trade_context": {"action": "buy", "status": "item_missing", "item_id": item_id},
            }

        requested = max(1, qty)
        merchant_id = str(merchant_entry.get("id") or "").strip().casefold() if isinstance(merchant_entry, dict) else ""
        merchant_stock_available: int | None = None
        if merchant_id and isinstance(merchant_entry, dict):
            inventory = merchant_entry.get("inventory") if isinstance(merchant_entry.get("inventory"), dict) else {}
            row = inventory.get(item.id) if isinstance(inventory, dict) else None
            if isinstance(row, dict):
                default_stock = max(0, self._safe_int(row.get("stock"), 0))
                merchant_stock_available = self._merchant_stock(
                    state,
                    merchant_id=merchant_id,
                    item_id=item.id,
                    default_stock=default_stock,
                )
                if merchant_stock_available <= 0:
                    return {
                        "attempted": True,
                        "applied": False,
                        "action": "buy",
                        "system_lines": [f"Rupture de stock: {item.name} est indisponible chez ce marchand."],
                        "trade_context": {
                            "action": "buy",
                            "status": "out_of_stock",
                            "item_id": item.id,
                            "item_name": str(item.name or item.id),
                            "merchant_id": merchant_id,
                        },
                    }

        base_unit_price = self._buy_price(
            state=state,
            item=item,
            discount_pct=discount_pct,
            merchant_entry=merchant_entry,
        )
        unit_price = base_unit_price
        if unit_price_hint is not None:
            # Accepte une nego raisonnable (±40%) si un prix explicite est formulé.
            min_acceptable = max(1, int(round(base_unit_price * 0.6)))
            max_acceptable = max(min_acceptable, int(round(base_unit_price * 1.4)))
            proposed = max(1, int(unit_price_hint))
            if min_acceptable <= proposed <= max_acceptable:
                unit_price = proposed

        if commit_now:
            return self._handle_buy(
                state=state,
                item=item,
                qty=requested,
                discount_pct=discount_pct,
                can_trade=can_trade,
                resolution_score=score,
                merchant_entry=merchant_entry,
                unit_price_override=unit_price,
            )

        affordable = max(0, int(state.player.gold) // unit_price)
        if affordable <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Or insuffisant: {item.name} coute {unit_price} or/unite."],
                "trade_context": {
                    "action": "buy",
                    "status": "insufficient_gold",
                    "item_id": item.id,
                    "item_name": str(item.name or item.id),
                    "requested_qty": requested,
                    "unit_price": unit_price,
                },
            }

        qty_offer = min(requested, affordable)
        if merchant_stock_available is not None:
            qty_offer = min(qty_offer, max(0, int(merchant_stock_available)))
        if qty_offer <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Rupture de stock: {item.name} est indisponible chez ce marchand."],
                "trade_context": {
                    "action": "buy",
                    "status": "out_of_stock",
                    "item_id": item.id,
                    "item_name": str(item.name or item.id),
                    "merchant_id": merchant_id,
                },
            }
        total_offer = qty_offer * unit_price
        self._set_pending_trade(
            state,
            {
                "action": "buy",
                "npc_name": str(npc_name or ""),
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty": qty_offer,
                "unit_price": unit_price,
                "discount_pct": discount_pct,
                "resolution_score": round(float(score), 3),
                "merchant_id": merchant_id,
            },
        )

        lines = [f"Offre: {item.name} x{qty_offer} pour {total_offer} or ({unit_price}/u)."]
        if requested > qty_offer:
            if merchant_stock_available is not None and merchant_stock_available < requested:
                lines.append(f"Stock marchand limite: x{merchant_stock_available} disponible.")
            else:
                lines.append(f"Tu as demande x{requested}, mais ton or actuel permet au plus x{qty_offer}.")
        lines.append("Confirme avec 'oui'/'j'achete', ou ecris 'annuler'.")

        return {
            "attempted": True,
            "applied": False,
            "action": "buy",
            "system_lines": lines,
            "trade_context": {
                "action": "buy",
                "status": "offer_pending",
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty_requested": requested,
                "qty_offer": qty_offer,
                "unit_price": unit_price,
                "total_price": total_offer,
                "gold_delta": 0,
                "discount_pct": discount_pct,
                "confidence": round(float(score), 3),
                "merchant_id": merchant_id,
            },
        }

    def _prepare_or_apply_sell(
        self,
        *,
        state,
        item_defs: dict[str, ItemDef],
        query: str,
        qty: int,
        commit_now: bool,
        can_trade: bool,
        merchant_entry: dict | None = None,
        npc_name: str = "",
    ) -> dict:
        if not can_trade:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": ["Ce PNJ n'achete rien pour le moment."],
                "trade_context": {"action": "sell", "status": "npc_not_buyer"},
            }

        item_id, _score = self._resolve_item_id(query, item_defs)
        if not item_id:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": [f"Objet non reconnu pour '{query}'. Reformule avec un nom d'objet plus precis."],
                "trade_context": {"action": "sell", "status": "item_unknown", "query": query},
            }

        item = item_defs.get(item_id)
        if item is None:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": [f"Objet introuvable: {item_id}."],
                "trade_context": {"action": "sell", "status": "item_missing", "item_id": item_id},
            }

        requested = max(1, qty)
        owned = self._count_item(state, item.id)
        if owned <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "sell",
                "system_lines": [f"Tu ne possedes pas {item.name}."],
                "trade_context": {"action": "sell", "status": "not_owned", "item_id": item.id},
            }

        if commit_now:
            return self._handle_sell(
                state=state,
                item=item,
                qty=requested,
                can_trade=can_trade,
                merchant_entry=merchant_entry,
            )

        qty_offer = min(requested, owned)
        unit_price = self._sell_price(
            state=state,
            item=item,
            player_sheet=getattr(state, "player_sheet", {}),
            merchant_entry=merchant_entry,
        )
        merchant_id = str(merchant_entry.get("id") or "").strip().casefold() if isinstance(merchant_entry, dict) else ""
        total_offer = qty_offer * unit_price
        self._set_pending_trade(
            state,
            {
                "action": "sell",
                "npc_name": str(npc_name or ""),
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty": qty_offer,
                "unit_price": unit_price,
                "merchant_id": merchant_id,
            },
        )

        lines = [f"Proposition de vente: {item.name} x{qty_offer} pour {total_offer} or ({unit_price}/u)."]
        if requested > qty_offer:
            lines.append(f"Tu as demande x{requested}, mais tu n'as que x{qty_offer}.")
        lines.append("Confirme avec 'oui'/'je vends', ou ecris 'annuler'.")

        return {
            "attempted": True,
            "applied": False,
            "action": "sell",
            "system_lines": lines,
            "trade_context": {
                "action": "sell",
                "status": "offer_pending",
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty_requested": requested,
                "qty_offer": qty_offer,
                "unit_price": unit_price,
                "total_price": total_offer,
                "gold_delta": 0,
            },
        }

    def _prepare_or_apply_give_or_exchange(
        self,
        *,
        state,
        item_defs: dict[str, ItemDef],
        query: str,
        qty: int,
        action: str,
        commit_now: bool,
        target_name: str,
        target_is_beggar: bool,
    ) -> dict:
        item_id, _score = self._resolve_item_id(query, item_defs)
        if not item_id:
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [f"Objet non reconnu pour '{query}'. Reformule avec un nom d'objet plus precis."],
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

        requested = max(1, qty)
        owned = self._count_item(state, item.id)
        if owned <= 0:
            verb = "echanger" if action == "exchange" else "donner"
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [f"Tu n'as pas {item.name} a {verb}."],
                "trade_context": {"action": action, "status": "not_owned", "item_id": item.id},
            }

        if commit_now:
            if action == "exchange":
                return self._handle_exchange(
                    state=state,
                    item=item,
                    qty=requested,
                    target_name=target_name,
                    target_is_beggar=target_is_beggar,
                )
            return self._handle_give(
                state=state,
                item=item,
                qty=requested,
                target_name=target_name,
                target_is_beggar=target_is_beggar,
            )

        qty_offer = min(requested, owned)
        self._set_pending_trade(
            state,
            {
                "action": action,
                "npc_name": str(target_name or ""),
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty": qty_offer,
                "target_is_beggar": bool(target_is_beggar),
            },
        )

        if action == "exchange":
            lines = [
                f"Proposition d'echange: {item.name} x{qty_offer} avec {target_name}.",
                "Confirme avec 'oui'/'j'echange', ou ecris 'annuler'.",
            ]
        else:
            lines = [
                f"Proposition de don: {item.name} x{qty_offer} a {target_name}.",
                "Confirme avec 'oui'/'je donne', ou ecris 'annuler'.",
            ]

        return {
            "attempted": True,
            "applied": False,
            "action": action,
            "system_lines": lines,
            "trade_context": {
                "action": action,
                "status": "offer_pending",
                "item_id": item.id,
                "item_name": str(item.name or item.id),
                "qty_requested": requested,
                "qty_offer": qty_offer,
                "gold_delta": 0,
            },
        }

    def _apply_pending_trade(
        self,
        *,
        state,
        pending: dict,
        qty: int,
        unit_price_override: int | None,
        selected_npc_name: str,
        selected_npc_profile: dict | None,
        item_defs: dict[str, ItemDef],
        discount_pct: int,
        target_is_beggar: bool,
        can_trade: bool,
        merchant_entry: dict | None,
    ) -> dict:
        action = str(pending.get("action") or "").strip().casefold()
        if action not in _PENDING_ACTIONS:
            return {
                "attempted": True,
                "applied": False,
                "action": action or "trade",
                "system_lines": ["Offre invalide ou expiree."],
                "trade_context": {"action": action or "trade", "status": "pending_invalid"},
            }

        item_id = str(pending.get("item_id") or "").strip().casefold()
        item = item_defs.get(item_id)
        if item is None:
            return {
                "attempted": True,
                "applied": False,
                "action": action,
                "system_lines": [f"Objet introuvable: {item_id}."],
                "trade_context": {"action": action, "status": "item_missing", "item_id": item_id},
            }

        requested = max(1, int(qty))
        if action == "buy":
            pending_unit_price = max(0, self._safe_int(pending.get("unit_price"), 0))
            resolved_unit_price = pending_unit_price if pending_unit_price > 0 else None
            if unit_price_override is not None and pending_unit_price > 0:
                min_acceptable = max(1, int(round(pending_unit_price * 0.6)))
                max_acceptable = max(min_acceptable, int(round(pending_unit_price * 1.4)))
                proposed = max(1, int(unit_price_override))
                if min_acceptable <= proposed <= max_acceptable:
                    resolved_unit_price = proposed
            return self._handle_buy(
                state=state,
                item=item,
                qty=requested,
                discount_pct=max(0, self._safe_int(pending.get("discount_pct"), discount_pct)),
                can_trade=can_trade,
                resolution_score=float(pending.get("resolution_score") or 1.0),
                merchant_entry=merchant_entry,
                unit_price_override=resolved_unit_price,
            )
        if action == "sell":
            return self._handle_sell(
                state=state,
                item=item,
                qty=requested,
                can_trade=can_trade,
                merchant_entry=merchant_entry,
            )
        if action == "exchange":
            return self._handle_exchange(
                state=state,
                item=item,
                qty=requested,
                target_name=selected_npc_name,
                target_is_beggar=target_is_beggar,
            )
        return self._handle_give(
            state=state,
            item=item,
            qty=requested,
            target_name=selected_npc_name,
            target_is_beggar=target_is_beggar,
        )

    def _handle_buy(
        self,
        *,
        state,
        item: ItemDef,
        qty: int,
        discount_pct: int,
        can_trade: bool,
        resolution_score: float,
        merchant_entry: dict | None = None,
        unit_price_override: int | None = None,
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

        unit_price = (
            max(1, int(unit_price_override))
            if unit_price_override is not None
            else self._buy_price(
                state=state,
                item=item,
                discount_pct=discount_pct,
                merchant_entry=merchant_entry,
            )
        )
        requested = max(1, qty)
        merchant_id = str(merchant_entry.get("id") or "").strip().casefold() if isinstance(merchant_entry, dict) else ""
        merchant_stock_available: int | None = None
        if merchant_id and isinstance(merchant_entry, dict):
            inventory = merchant_entry.get("inventory") if isinstance(merchant_entry.get("inventory"), dict) else {}
            row = inventory.get(item.id) if isinstance(inventory, dict) else None
            if isinstance(row, dict):
                default_stock = max(0, self._safe_int(row.get("stock"), 0))
                merchant_stock_available = self._merchant_stock(
                    state,
                    merchant_id=merchant_id,
                    item_id=item.id,
                    default_stock=default_stock,
                )
                if merchant_stock_available <= 0:
                    return {
                        "attempted": True,
                        "applied": False,
                        "action": "buy",
                        "system_lines": [f"Rupture de stock: {item_name} est indisponible chez ce marchand."],
                        "trade_context": {
                            "action": "buy",
                            "status": "out_of_stock",
                            "item_id": item.id,
                            "merchant_id": merchant_id,
                        },
                    }
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
        if merchant_stock_available is not None:
            qty_to_try = min(qty_to_try, max(0, int(merchant_stock_available)))
        if qty_to_try <= 0:
            return {
                "attempted": True,
                "applied": False,
                "action": "buy",
                "system_lines": [f"Rupture de stock: {item_name} est indisponible chez ce marchand."],
                "trade_context": {
                    "action": "buy",
                    "status": "out_of_stock",
                    "item_id": item.id,
                    "merchant_id": merchant_id,
                },
            }
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
        if merchant_stock_available is not None and merchant_id:
            self._set_merchant_stock(
                state,
                merchant_id=merchant_id,
                item_id=item.id,
                stock=max(0, merchant_stock_available - added),
            )

        lines = [f"Achat confirme: {item_name} x{added} pour {total_cost} or ({unit_price}/u)."]
        if added < requested:
            if merchant_stock_available is not None and merchant_stock_available < requested:
                lines.append("Quantite ajustee selon le stock marchand.")
            elif affordable < requested:
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
                "merchant_id": merchant_id,
            },
        }

    def _handle_sell(
        self,
        *,
        state,
        item: ItemDef,
        qty: int,
        can_trade: bool,
        merchant_entry: dict | None = None,
    ) -> dict:
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

        unit_price = self._sell_price(
            state=state,
            item=item,
            player_sheet=getattr(state, "player_sheet", {}),
            merchant_entry=merchant_entry,
        )
        total_gain = removed * unit_price
        state.player.gold = max(0, int(state.player.gold) + total_gain)
        merchant_id = str(merchant_entry.get("id") or "").strip().casefold() if isinstance(merchant_entry, dict) else ""
        if merchant_id and isinstance(merchant_entry, dict):
            inventory = merchant_entry.get("inventory") if isinstance(merchant_entry.get("inventory"), dict) else {}
            row = inventory.get(item.id) if isinstance(inventory, dict) else None
            if isinstance(row, dict):
                default_stock = max(0, self._safe_int(row.get("stock"), 0))
                current_stock = self._merchant_stock(
                    state,
                    merchant_id=merchant_id,
                    item_id=item.id,
                    default_stock=default_stock,
                )
                max_cap = max(default_stock * 3, default_stock + removed, current_stock + removed)
                self._set_merchant_stock(
                    state,
                    merchant_id=merchant_id,
                    item_id=item.id,
                    stock=min(max_cap, current_stock + removed),
                )

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
                "merchant_id": merchant_id,
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

        action = self._detect_trade_action(plain)
        if not action:
            return None

        qty = self._extract_qty_from_text(plain)
        unit_price_hint = self._extract_unit_price_hint(plain)
        question_like = self._is_question_like(raw, plain)
        commit_now = self._is_commit_phrase(action, plain, question_like)
        query = self._extract_item_query(plain, action)

        return TradeIntent(
            action=action,
            qty=max(1, qty),
            item_query=query,
            commit_now=commit_now,
            unit_price_hint=unit_price_hint,
        )

    def _detect_trade_action(self, plain: str) -> str:
        # L'ordre est important: évite de lire "vous vendez ?" comme une vente du joueur.
        if self._is_first_person_action(plain, r"echanger|echange|troquer|troque"):
            return "exchange"

        if self._is_first_person_action(plain, r"donner|donne|offrir|offre|file|filer|tendre|tends"):
            return "give"

        if self._is_first_person_action(plain, r"vendre|revendre|vends?|revends?"):
            return "sell"
        if re.search(r"\b(?:vous|tu)\s+(?:achetez|rachetez)\b", plain):
            return "sell"

        if self._is_first_person_action(plain, r"acheter|achete|prendre|prends?"):
            return "buy"
        if re.search(r"\b(?:vous|tu)\s+(?:en\s+)?vendez?\b", plain):
            return "buy"
        if re.search(r"\b(?:combien|prix)\b", plain) and re.search(r"\b(?:coute|coutent|couter|acheter|vendre)\b", plain):
            return "buy"
        if re.search(r"\b(?:acheter|achete|prendre|prends?)\b", plain) and re.search(
            r"\b(?:possible|peux|pouvez|pourrais|pourriez|est ce que)\b",
            plain,
        ):
            return "buy"

        if re.match(r"^(?:acheter|achete|prends?)\b", plain):
            return "buy"
        if re.match(r"^(?:vendre|vends?|revendre|revends?)\b", plain):
            return "sell"
        if re.match(r"^(?:donner|donne|offrir|offre|file|filer)\b", plain):
            return "give"
        if re.match(r"^(?:echanger|echange|troquer|troque)\b", plain):
            return "exchange"

        return ""

    def _is_question_like(self, raw: str, plain: str) -> bool:
        if "?" in raw:
            return True
        return bool(
            re.search(
                r"\b(?:est ce que|combien|quel|quelle|quels|quelles|pouvez|pourrais|pourriez|possible|prix)\b",
                plain,
            )
        )

    def _is_commit_phrase(self, action: str, plain: str, is_question_like: bool) -> bool:
        if is_question_like:
            return False

        if action == "buy":
            return bool(
                self._is_first_person_direct_action(plain, r"prends?|achete|acheter")
                or self._is_first_person_direct_action(plain, r"vais\s+(?:acheter|prendre)")
                or re.match(r"^(?:acheter|achete|prends?)\b", plain)
            )
        if action == "sell":
            return bool(
                self._is_first_person_direct_action(plain, r"vends?|vendre|revendre|revends?")
                or self._is_first_person_direct_action(plain, r"vais\s+(?:vendre|revendre)")
                or re.match(r"^(?:vendre|vends?|revendre|revends?)\b", plain)
            )
        if action == "give":
            return bool(
                self._is_first_person_direct_action(plain, r"donne|donner|offre|offrir|file|filer")
                or self._is_first_person_direct_action(plain, r"vais\s+(?:donner|offrir)")
                or re.match(r"^(?:donner|donne|offrir|offre|file|filer)\b", plain)
            )
        if action == "exchange":
            return bool(
                self._is_first_person_direct_action(plain, r"echanger|echange|troquer|troque")
                or self._is_first_person_direct_action(plain, r"vais\s+(?:echanger|troquer)")
                or re.match(r"^(?:echanger|echange|troquer|troque)\b", plain)
            )
        return False

    def _extract_item_query(self, plain: str, action: str) -> str:
        query = plain
        query = re.sub(r"\b\d{1,3}\b", " ", query)
        for word in _NUMBER_WORDS:
            query = re.sub(rf"\b{re.escape(word)}\b", " ", query)

        query = re.sub(
            r"\b(?:est ce que|combien|quel|quelle|quels|quelles|prix|coute|coutent|couter|"
            r"veux|voudrais|souhaite|aimerais|peux|pouvez|pourrais|pourriez|possible|"
            r"viens|venir|venez|venu|venue|passe|passer|"
            r"acheter|achete|prendre|prends|vendre|vend|vends|revendre|revends|vendez|"
            r"donner|donne|offrir|offre|echanger|echange|troquer|troque|racheter|rachetez|"
            r"bonjour|salut|bonsoir|merci|svp)\b",
            " ",
            query,
        )

        query = re.split(r"\b(?:a|au|aux|avec)\b", query)[0]

        parts = [
            p
            for p in re.split(r"\s+", query)
            if p and len(p) > 1 and p not in _STOPWORDS and p not in _QUERY_DROP_WORDS
        ]

        hinted = self._infer_item_hint(plain)
        if hinted:
            if not parts:
                return hinted
            # Quand la phrase est longue, on prefère le hint objet explicite.
            if len(parts) >= 3:
                return hinted

        if not parts:
            return ""
        return " ".join(parts[:6])

    def _infer_item_hint(self, plain: str) -> str:
        tokens = {t for t in plain.split() if t}
        for hints in _TYPE_HINTS.values():
            for hint in hints:
                h = self._norm(hint)
                if h and h in tokens:
                    return h
        return ""

    def _extract_qty_from_text(self, plain: str) -> int:
        clean = re.sub(r"\b\d{1,4}\s*(?:or|po|gold)\b", " ", plain)
        qty_match = re.search(r"\bx\s*(\d{1,3})\b", clean)
        if qty_match:
            try:
                return max(1, min(99, int(qty_match.group(1))))
            except Exception:
                return 1

        qty_match = re.search(r"\b(\d{1,3})\b", clean)
        if qty_match:
            try:
                return max(1, min(99, int(qty_match.group(1))))
            except Exception:
                return 1

        for token, value in _NUMBER_WORDS.items():
            if re.search(rf"\b{re.escape(token)}\b", clean):
                return max(1, min(99, int(value)))
        return 1

    def _extract_unit_price_hint(self, plain: str) -> int | None:
        m = re.search(r"\b(?:a|pour)\s*(\d{1,4})\s*(?:or|po|gold)\b", plain)
        if not m:
            m = re.search(r"\b(\d{1,4})\s*(?:or|po|gold)\b", plain)
        if not m:
            return None
        try:
            value = int(m.group(1))
        except Exception:
            return None
        if value <= 0:
            return None
        return min(9999, value)

    def _is_confirm_message(self, plain: str) -> bool:
        if self._is_cancel_message(plain):
            return False

        if plain in _CONFIRM_HINTS:
            return True
        if re.search(r"\b(?:oui|ok|okay|daccord|d accord|valide|confirme|accepte|vas y|go)\b", plain):
            return True
        if self._is_first_person_action(
            plain,
            r"prends?|achete|acheter|vends?|vendre|donne|donner|echange|echanger",
        ):
            return True
        return False

    def _is_cancel_message(self, plain: str) -> bool:
        if plain in _CANCEL_HINTS:
            return True
        return bool(
            re.search(
                r"\b(?:non|annule|annuler|stop|refuse|laisse tomber|laisser tomber|pas maintenant|plus tard|oublie)\b",
                plain,
            )
        )

    def _get_pending_trade_for_npc(self, state, selected_npc_name: str) -> dict | None:
        gm_state = getattr(state, "gm_state", None)
        if not isinstance(gm_state, dict):
            return None

        pending = gm_state.get("pending_trade")
        if not isinstance(pending, dict):
            return None

        action = str(pending.get("action") or "").strip().casefold()
        if action not in _PENDING_ACTIONS:
            self._clear_pending_trade(state)
            return None

        pending_npc = str(pending.get("npc_name") or "").strip()
        if selected_npc_name and pending_npc and self._norm(pending_npc) != self._norm(selected_npc_name):
            self._clear_pending_trade(state)
            return None

        return dict(pending)

    def _set_pending_trade(self, state, payload: dict) -> None:
        gm_state = getattr(state, "gm_state", None)
        if not isinstance(gm_state, dict):
            return
        gm_state["pending_trade"] = dict(payload)

    def _clear_pending_trade(self, state) -> None:
        gm_state = getattr(state, "gm_state", None)
        if not isinstance(gm_state, dict):
            return
        gm_state.pop("pending_trade", None)

    def _merchant_flags(self, state) -> dict:
        gm_state = getattr(state, "gm_state", None)
        if not isinstance(gm_state, dict):
            return {}
        flags = gm_state.get("flags")
        if isinstance(flags, dict):
            return flags
        gm_state["flags"] = {}
        return gm_state["flags"]

    def _load_merchants_catalog(self) -> dict[str, dict]:
        if isinstance(self._merchant_catalog_cache, dict):
            return self._merchant_catalog_cache

        catalog: dict[str, dict] = {}
        if not self.merchants_dir.exists():
            self._merchant_catalog_cache = catalog
            return catalog

        for path in sorted(self.merchants_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue

            merchant_id = str(payload.get("id") or path.stem).strip().casefold()
            name = str(payload.get("name") or "").strip()
            location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
            location_id = str(location.get("location_id") or "").strip().casefold()

            npc_profile = payload.get("npc_profile") if isinstance(payload.get("npc_profile"), dict) else {}
            npc_label = str(npc_profile.get("label") or name).strip()
            npc_role = str(npc_profile.get("role") or "").strip()

            inventory_rows = payload.get("inventory") if isinstance(payload.get("inventory"), list) else []
            inventory: dict[str, dict] = {}
            for row in inventory_rows:
                if not isinstance(row, dict):
                    continue
                item_id = str(row.get("item_id") or "").strip().casefold()
                if not item_id:
                    continue
                inventory[item_id] = {
                    "stock": max(0, self._safe_int(row.get("stock"), 0)),
                    "price_multiplier": max(0.2, min(self._safe_float(row.get("price_multiplier"), 1.0), 5.0)),
                }

            if not merchant_id:
                continue
            catalog[merchant_id] = {
                "id": merchant_id,
                "name": name,
                "location_id": location_id,
                "npc_label": npc_label,
                "npc_role": npc_role,
                "inventory": inventory,
            }

        self._merchant_catalog_cache = catalog
        return catalog

    def _resolve_merchant_entry(self, *, state, selected_npc_name: str, selected_npc_profile: dict | None) -> dict | None:
        catalog = self._load_merchants_catalog()
        if not catalog:
            return None

        current_scene = getattr(state, "current_scene", None)
        scene_obj = current_scene() if callable(current_scene) else None
        current_scene_id = str(getattr(scene_obj, "id", "") or "").strip().casefold()

        npc_names = [str(selected_npc_name or "").strip()]
        if isinstance(selected_npc_profile, dict):
            npc_names.append(str(selected_npc_profile.get("label") or "").strip())
            npc_names.append(str(selected_npc_profile.get("role") or "").strip())
        normalized = {self._norm(name) for name in npc_names if name}
        normalized.discard("")

        for row in catalog.values():
            if not isinstance(row, dict):
                continue
            row_scene_id = str(row.get("location_id") or "").strip().casefold()
            if row_scene_id and current_scene_id and row_scene_id != current_scene_id:
                continue

            candidates = {
                self._norm(str(row.get("name") or "")),
                self._norm(str(row.get("npc_label") or "")),
                self._norm(str(row.get("npc_role") or "")),
            }
            candidates.discard("")
            if normalized and candidates.intersection(normalized):
                return row
        return None

    def _merchant_stock(self, state, *, merchant_id: str, item_id: str, default_stock: int = 0) -> int:
        flags = self._merchant_flags(state)
        if not isinstance(flags, dict):
            return max(0, int(default_stock))
        by_merchant = flags.get("merchant_runtime_stock")
        if not isinstance(by_merchant, dict):
            by_merchant = {}
            flags["merchant_runtime_stock"] = by_merchant
        bucket = by_merchant.get(merchant_id)
        if not isinstance(bucket, dict):
            bucket = {}
            by_merchant[merchant_id] = bucket
        if item_id not in bucket:
            bucket[item_id] = max(0, int(default_stock))
        return max(0, self._safe_int(bucket.get(item_id), 0))

    def _set_merchant_stock(self, state, *, merchant_id: str, item_id: str, stock: int) -> None:
        flags = self._merchant_flags(state)
        if not isinstance(flags, dict):
            return
        by_merchant = flags.get("merchant_runtime_stock")
        if not isinstance(by_merchant, dict):
            by_merchant = {}
            flags["merchant_runtime_stock"] = by_merchant
        bucket = by_merchant.get(merchant_id)
        if not isinstance(bucket, dict):
            bucket = {}
            by_merchant[merchant_id] = bucket
        bucket[item_id] = max(0, int(stock))

    def _restock_merchants_if_needed(self, state) -> None:
        flags = self._merchant_flags(state)
        if not isinstance(flags, dict):
            return
        now_minutes = max(0, self._safe_int(getattr(state, "world_time_minutes", 0), 0))
        today = day_index(now_minutes)
        last_day = self._safe_int(flags.get("merchant_last_restock_day"), -1)
        if today == last_day:
            return
        flags["merchant_last_restock_day"] = today
        restock_bonus_pct = self._safe_int(flags.get("merchant_restock_bonus_pct"), 0)
        if isinstance(getattr(state, "world_state", None), dict):
            restock_bonus_pct = self._safe_int(state.world_state.get("merchant_restock_bonus_pct"), restock_bonus_pct)
        restock_bonus_pct = max(-50, min(150, restock_bonus_pct))

        by_merchant = flags.get("merchant_runtime_stock")
        if not isinstance(by_merchant, dict):
            return

        catalog = self._load_merchants_catalog()
        for merchant_id, bucket in by_merchant.items():
            if not isinstance(bucket, dict):
                continue
            merchant = catalog.get(str(merchant_id))
            inv = merchant.get("inventory") if isinstance(merchant, dict) and isinstance(merchant.get("inventory"), dict) else {}
            for item_id, value in list(bucket.items()):
                current = max(0, self._safe_int(value, 0))
                base_row = inv.get(str(item_id)) if isinstance(inv, dict) else None
                base_stock = max(0, self._safe_int(base_row.get("stock"), current)) if isinstance(base_row, dict) else current
                refill = max(1, base_stock // 3) if base_stock > 0 else 2
                if restock_bonus_pct:
                    refill = max(1, int(round(refill * (1.0 + (restock_bonus_pct / 100.0)))))
                bonus_cap = max(0, int(round(base_stock * (1.0 + max(0, restock_bonus_pct) / 100.0))))
                cap = max(base_stock, current, bonus_cap)
                bucket[item_id] = min(cap, current + refill)

    def _merchant_price_multiplier(self, state, *, merchant_entry: dict | None, item_id: str) -> float:
        # Base depuis la config du marchand.
        price_mult = 1.0
        if isinstance(merchant_entry, dict):
            inventory = merchant_entry.get("inventory") if isinstance(merchant_entry.get("inventory"), dict) else {}
            row = inventory.get(str(item_id).casefold()) if isinstance(inventory, dict) else None
            if isinstance(row, dict):
                price_mult = max(0.2, min(self._safe_float(row.get("price_multiplier"), 1.0), 5.0))

        # Modif globale liee a l'evenement du jour.
        flags = self._merchant_flags(state)
        world_event_trade_mod = self._safe_int(flags.get("world_event_trade_mod_pct"), 0) if isinstance(flags, dict) else 0
        if world_event_trade_mod != 0:
            price_mult *= max(0.4, 1.0 + (world_event_trade_mod / 100.0))

        # Modif reputation.
        price_mult *= merchant_price_multiplier_from_reputation(state)
        return max(0.2, min(price_mult, 6.0))

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
                if item_type == self._norm(item.type) and any(self._norm(h) in tokens for h in hints):
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
        item_type = "misc"
        for candidate_type, hints in _TYPE_HINTS.items():
            if any(self._norm(h) in words for h in hints):
                item_type = candidate_type
                break

        generic_words = {
            "objet",
            "equipement",
            "materiel",
            "item",
            "arme",
            "armes",
            "lame",
            "armure",
            "bouclier",
            "potion",
            "consommable",
            "consumable",
            "accessoire",
            "materiau",
            "materiaux",
        }
        defaults_by_type = {
            "weapon": ("epee_apprenti", "Epee d'apprenti"),
            "armor": ("armure_legere", "Armure legere"),
            "consumable": ("ration_simple", "Ration simple"),
            "accessory": ("talisman_simple", "Talisman simple"),
            "material": ("materiau_brut", "Materiau brut"),
            "misc": ("objet_simple", "Objet simple"),
        }

        use_generic_default = len(words) <= 2 and all(w in generic_words for w in words)
        if use_generic_default:
            short, pretty = defaults_by_type.get(item_type, defaults_by_type["misc"])
        else:
            short = "_".join(words[:3]).strip("_") or "objet"
            pretty = " ".join(w.capitalize() for w in words[:5]) or "Objet"

        if item_type in {"weapon", "armor", "accessory"}:
            stack_max = 1
        elif item_type in {"consumable", "material"}:
            stack_max = 20
        else:
            stack_max = 8

        base_value = 8
        if item_type == "weapon":
            base_value = 8
        elif item_type == "armor":
            base_value = 10
        elif item_type == "accessory":
            base_value = 9
        elif item_type == "consumable":
            base_value = 10
        elif item_type == "material":
            base_value = 12

        item_id = short
        i = 1
        while item_id in item_defs:
            i += 1
            item_id = f"{short}_{i}"
        payload = {
            "id": item_id,
            "name": pretty,
            "stack_max": stack_max,
            "type": item_type,
            "slot": (
                "weapon"
                if item_type == "weapon"
                else ("armor" if item_type == "armor" else ("accessory" if item_type == "accessory" else ""))
            ),
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

    def _buy_price(self, *, state, item: ItemDef, discount_pct: int, merchant_entry: dict | None) -> int:
        base = max(1, self._safe_int(getattr(item, "value_gold", 0), 0))
        if base <= 0:
            base = 10
        price = int(round(base * 1.2))
        price = int(round(price * self._merchant_price_multiplier(state, merchant_entry=merchant_entry, item_id=item.id)))
        if discount_pct > 0:
            price = int(round(price * max(0.35, 1.0 - (discount_pct / 100.0))))
        return max(1, price)

    def _sell_price(self, *, state, item: ItemDef, player_sheet: dict, merchant_entry: dict | None) -> int:
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
        buy_mult = self._merchant_price_multiplier(state, merchant_entry=merchant_entry, item_id=item.id)
        sale_market_mult = max(0.55, min(1.35, 1.18 - ((buy_mult - 1.0) * 0.6)))
        return max(1, int(round((base * 0.52) * (1.0 + bonus) * sale_market_mult)))

    def _is_merchant_npc(self, npc_name: str, npc_profile: dict | None) -> bool:
        text = [str(npc_name or "")]
        if isinstance(npc_profile, dict):
            text.append(str(npc_profile.get("role") or ""))
            text.append(str(npc_profile.get("label") or ""))
            text.append(str(npc_profile.get("char_persona") or ""))
        hay = self._norm(" ".join(text))
        return any(self._norm(h) in hay for h in _MERCHANT_HINTS)

    def _is_beggar_npc(self, npc_name: str, npc_profile: dict | None) -> bool:
        text = [str(npc_name or "")]
        if isinstance(npc_profile, dict):
            text.append(str(npc_profile.get("role") or ""))
            text.append(str(npc_profile.get("label") or ""))
            text.append(str(npc_profile.get("char_persona") or ""))
        hay = self._norm(" ".join(text))
        return any(self._norm(h) in hay for h in _BEGGAR_HINTS)

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

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _is_first_person_action(self, plain: str, verbs_pattern: str) -> bool:
        bridge = "|".join(re.escape(token) for token in _FIRST_PERSON_BRIDGE_WORDS)
        return bool(
            re.search(
                rf"\b(?:j|je)\b(?:\s+(?:{bridge}))*\s+(?:{verbs_pattern})\b",
                plain,
            )
        )

    def _is_first_person_direct_action(self, plain: str, verbs_pattern: str) -> bool:
        fillers = "|".join(re.escape(token) for token in _FIRST_PERSON_PRONOUN_FILLERS)
        return bool(
            re.search(
                rf"\b(?:j|je)(?:\s+(?:{fillers}))*\s*(?:{verbs_pattern})\b",
                plain,
            )
        )
