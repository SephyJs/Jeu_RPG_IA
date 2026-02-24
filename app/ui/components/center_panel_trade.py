from __future__ import annotations

import difflib
import re
import unicodedata

from app.core.engine import TradeEngine, normalize_trade_session, trade_session_to_dict
from app.gamemaster.models import model_for
from app.ui.state.game_state import GameState
from app.ui.state.inventory import ItemStack

_BUY_WORDS_RE = re.compile(r"\b(acheter|achete|achetes|prends|prendre|acquerir)\b", flags=re.IGNORECASE)
_SELL_WORDS_RE = re.compile(r"\b(vendre|vends|vend|revendre|revends)\b", flags=re.IGNORECASE)
_CONFIRM_WORDS_RE = re.compile(r"\b(oui|ok|daccord|d accord|valide|confirme|j accepte|je confirme)\b", flags=re.IGNORECASE)
_CANCEL_WORDS_RE = re.compile(r"\b(non|annule|annuler|stop|laisse tomber|abandon)\b", flags=re.IGNORECASE)


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
        try:
            return max(1, min(999, int(m.group(1))))
        except Exception:
            return None
    m = re.search(r"\b(\d{1,3})\b", plain)
    if not m:
        return None
    try:
        return max(1, min(999, int(m.group(1))))
    except Exception:
        return None


def _is_trade_message(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    raw_cmd = raw.casefold()
    if raw_cmd.startswith("/trade") or raw_cmd.startswith("/commerce"):
        return True

    plain = _norm(raw)
    if not plain:
        return False
    if _SELL_WORDS_RE.search(plain):
        return True
    if _BUY_WORDS_RE.search(plain):
        return True
    if "tout vendre" in plain:
        return True
    return False


def _detect_buy_item_query(plain: str) -> str:
    query = re.sub(r"\b(acheter|achete|achetes|prends|prendre|acquerir)\b", " ", plain)
    query = re.sub(r"\b\d{1,3}\b", " ", query)
    query = re.sub(r"\b(de|du|des|la|le|les|un|une|au|aux|a|pour|je|j|vous|tu|moi|en|svp|stp)\b", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query[:120]


def _match_item_for_buy(query: str, item_defs: dict[str, object]) -> tuple[str, object] | None:
    if not query:
        return None
    best_score = 0.0
    best_item_id = ""
    best_item = None
    q = _norm(query)
    for item_id, item in item_defs.items():
        name = _norm(getattr(item, "name", item_id))
        iid = _norm(item_id)
        score = 0.0
        if q in name or q in iid:
            score = 1.0
        else:
            score = max(
                difflib.SequenceMatcher(a=q, b=name).ratio(),
                difflib.SequenceMatcher(a=q, b=iid).ratio(),
            )
        if score > best_score:
            best_score = score
            best_item_id = str(item_id)
            best_item = item
    if not best_item_id or best_item is None or best_score < 0.38:
        return None
    return best_item_id, best_item


def _session_for_npc(state: GameState, selected_npc: str) -> tuple[TradeEngine, object]:
    engine = TradeEngine()
    session = normalize_trade_session(getattr(state, "trade_session", None))
    if session.status != "idle":
        current_npc = str(session.npc_id or "").strip().casefold()
        asked_npc = str(selected_npc or "").strip().casefold()
        if current_npc and asked_npc and current_npc != asked_npc:
            session = engine.reset_to_idle(session)
    engine.load_session(session)
    return engine, engine.export_session()


def _session_line_item(session, *, index: int = 0) -> dict:
    if not session.cart:
        return {}
    idx = max(0, min(len(session.cart) - 1, int(index)))
    row = session.cart[idx]
    return {
        "item_id": str(row.item_id or ""),
        "item_name": str(row.item_name or row.item_id or ""),
        "qty": max(1, int(row.qty)),
        "unit_price": max(0, int(row.unit_price)),
        "subtotal": max(0, int(row.subtotal)),
    }


def _sync_legacy_pending_trade(state: GameState, session, *, selected_npc: str) -> None:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    gm_state = state.gm_state
    if session.status == "confirming" and session.cart:
        row = session.cart[0]
        gm_state["pending_trade"] = {
            "action": "buy" if session.mode == "buy" else "sell",
            "npc_name": str(selected_npc or session.npc_id or ""),
            "item_id": str(row.item_id or ""),
            "item_name": str(row.item_name or row.item_id or ""),
            "qty": max(1, int(row.qty)),
            "unit_price": max(0, int(row.unit_price)),
        }
    else:
        gm_state.pop("pending_trade", None)


def _sync_trade_session(state: GameState, session) -> None:
    state.trade_session = normalize_trade_session(session)
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    state.gm_state["trade_session"] = trade_session_to_dict(state.trade_session)


def _trade_context_from_session(
    session,
    *,
    status: str,
    selected_npc: str,
    npc_key: str | None,
    safe_int,
    state: GameState,
    economy_manager,
) -> dict:
    line = _session_line_item(session)
    total = sum(max(0, int(row.subtotal)) for row in session.cart)
    out = {
        "action": "buy" if session.mode == "buy" else "sell",
        "mode": str(session.mode or "sell"),
        "status": str(status or "unknown"),
        "npc_name": str(selected_npc or ""),
        "qty_offer": max(1, safe_int(line.get("qty"), 1)) if line else 0,
        "unit_price": max(0, safe_int(line.get("unit_price"), 0)) if line else 0,
        "item_id": str(line.get("item_id") or ""),
        "item_name": str(line.get("item_name") or ""),
        "total_price": max(0, safe_int(total, 0)),
        "trade_turn_id": max(0, safe_int(session.turn_id, 0)),
        "gold_after": max(0, safe_int(state.player.gold, 0)),
        "inventory_after": economy_manager.inventory_summary(state, state.item_defs),
    }
    if npc_key:
        out["npc_key"] = str(npc_key)
    return out


def _local_vendor_line(session) -> str:
    def _core_completion_from_transcript() -> str:
        for raw in reversed(session.transcript_short):
            line = str(raw or "").strip()
            if not line:
                continue
            lowered = line.casefold()
            if lowered.startswith("vente executee:") or lowered.startswith("achat execute:") or lowered.startswith("transaction executee."):
                # Garde uniquement le noyau transactionnel, sans les ajouts d'ambiance.
                line = re.split(r"\s+on continue le commerce \?", line, maxsplit=1, flags=re.IGNORECASE)[0].strip()
                if line:
                    return line
            if lowered.startswith("transaction annulee"):
                line = re.split(r"[.!?]", line, maxsplit=1)[0].strip()
                if line:
                    return f"{line}."
        return ""

    recap = ", ".join(f"{row.item_name} x{row.qty}" for row in session.cart[:4])
    if session.status == "selecting":
        pending = session.pending_question if isinstance(session.pending_question, dict) else {}
        if pending:
            return str(pending.get("text") or "Je dois connaitre la quantite exacte.")
        if recap:
            return f"Je prepare le lot: {recap}. Confirme quand tu es pret."
        return "Montre-moi ce que tu veux echanger."
    if session.status == "confirming":
        total = sum(max(0, int(row.subtotal)) for row in session.cart)
        return f"Recapitulatif: {recap}. Total {total} or. Tu confirmes ?"
    if session.status == "done":
        core_line = _core_completion_from_transcript()
        if core_line:
            return f"{core_line} On continue le commerce ?"
        return "Transaction bouclee. On continue ?"
    if session.status == "aborted":
        core_line = _core_completion_from_transcript()
        if core_line:
            return core_line
        return "Entendu, on annule pour cette fois."
    return ""


async def render_trade_dialogue(
    *,
    state: GameState,
    selected_npc: str,
    selected_profile: dict | None,
    llm_client,
) -> str:
    session = normalize_trade_session(getattr(state, "trade_session", None))
    anchor_line = _local_vendor_line(session)
    if session.status == "idle":
        return ""
    if session.turn_id <= 0:
        return anchor_line
    if session.last_llm_turn_id >= session.turn_id:
        return ""
    if not session.llm_enabled or llm_client is None:
        session.last_llm_turn_id = session.turn_id
        session.transcript_short.append(anchor_line)
        session.transcript_short = session.transcript_short[-10:]
        _sync_trade_session(state, session)
        return anchor_line

    profile_summary = ""
    if isinstance(selected_profile, dict):
        role = str(selected_profile.get("role") or "").strip()
        tension = int(selected_profile.get("tension_level") or 0)
        profile_summary = f"role={role}; tension={tension}"
    recap = ", ".join(f"{row.item_name} x{row.qty} ({row.unit_price}/u)" for row in session.cart[:4]) or "panier vide"
    pending = session.pending_question if isinstance(session.pending_question, dict) else {}
    pending_text = str(pending.get("text") or "")
    instructions = (
        "Tu incarnes un marchand PNJ dans un RPG sombre. "
        "Ecris uniquement une phrase d'ambiance (4 a 18 mots) qui complete la phrase core sans changer les faits. "
        "Interdit d'inventer un prix, une quantite, un objet, ou un nouvel etat de transaction."
    )
    prompt = (
        f"{instructions}\n"
        f"PNJ: {selected_npc}\n"
        f"Profil: {profile_summary or 'standard'}\n"
        f"Etat session: status={session.status}, mode={session.mode}, turn_id={session.turn_id}\n"
        f"Panier core: {recap}\n"
        f"Question en attente: {pending_text or 'aucune'}\n"
        f"Termes core: {session.proposed_terms}\n"
        f"Phrase core a conserver telle quelle: {anchor_line}\n"
        "Retourne uniquement la phrase d'ambiance (sans citation):"
    )
    flair = ""
    try:
        flair = str(
            await llm_client.generate(
                model=model_for("dialogue"),
                prompt=prompt,
                temperature=0.55,
                num_ctx=1536,
                num_predict=45,
            )
            or ""
        ).strip()
    except Exception:
        flair = ""

    if flair:
        flair = re.sub(r"\s+", " ", flair).strip()
        flair = flair.strip("\"' ")
        if not flair.endswith((".", "!", "?")):
            flair = f"{flair}."
        flair = flair[:120]
    text = anchor_line if not flair else f"{anchor_line} {flair}"
    text = text[:280]
    if session.transcript_short and session.transcript_short[-1] == text:
        session.last_llm_turn_id = session.turn_id
        _sync_trade_session(state, session)
        return ""
    session.last_llm_turn_id = session.turn_id
    session.transcript_short.append(text)
    session.transcript_short = session.transcript_short[-10:]
    _sync_trade_session(state, session)
    return text


def apply_trade_from_player_message(
    state: GameState,
    *,
    user_text: str,
    selected_npc: str | None,
    npc_key: str | None,
    selected_profile: dict | None,
    ensure_quest_state_fn,
    ensure_item_state_fn,
    economy_manager,
    safe_int,
    maybe_unlock_secret_charity_quest_fn,
    apply_trade_reputation_fn,
) -> dict:
    ensure_quest_state_fn(state)
    if not selected_npc:
        return {"attempted": False}
    ensure_item_state_fn(state)
    gm_flags = state.gm_state.setdefault("flags", {}) if isinstance(state.gm_state, dict) else {}
    llm_enabled_default = bool(gm_flags.get("trade_llm_vendor", False))

    engine, session = _session_for_npc(state, str(selected_npc))
    raw_input = str(user_text or "").strip()
    raw_input_cf = raw_input.casefold()
    plain = _norm(user_text)
    attempted = False
    applied = False
    lines: list[str] = []
    trade_status = ""

    if session.status == "done" and not _is_trade_message(user_text):
        session = engine.reset_to_idle(session)
        _sync_trade_session(state, session)
        _sync_legacy_pending_trade(state, session, selected_npc=str(selected_npc))
        return {"attempted": False}

    if not _is_trade_message(user_text) and session.status in {"idle", "done", "aborted"}:
        _sync_trade_session(state, session)
        _sync_legacy_pending_trade(state, session, selected_npc=str(selected_npc))
        return {"attempted": False}

    fingerprint = f"{session.status}|{plain}|{str(selected_npc).casefold()}"
    session, duplicate_action = engine.run_action_guard(session, fingerprint)
    _sync_trade_session(state, session)
    if duplicate_action:
        attempted = True
        trade_status = "duplicate_ignored"
        lines.append("Action identique ignoree. Utilise les boutons de choix du commerce.")
    else:
        attempted = True
        cmd_parts = raw_input.split()
        is_trade_command = raw_input_cf.startswith("/trade") or raw_input_cf.startswith("/commerce")
        if is_trade_command and len(cmd_parts) < 2:
            trade_status = "help"
            lines.append("Usage: /trade help")
        elif is_trade_command and len(cmd_parts) >= 2:
            sub = str(cmd_parts[1] or "").strip().casefold()
            if sub in {"help", "aide", "?"}:
                trade_status = "help"
                lines.append("Commandes commerce: /trade mode sell|buy | /trade all | /trade qty <n> | /trade confirm | /trade cancel | /trade llm on|off")
            elif sub in {"status", "etat"}:
                trade_status = "status"
                lines.append(f"Etat: {session.status} | Recap: {engine.build_recap_text(session)}")
            elif sub in {"cancel", "annuler", "abort"}:
                session = engine.abort_trade(session)
                trade_status = "canceled"
                lines.append("Transaction annulee.")
            elif sub in {"close", "fermer"}:
                session = engine.reset_to_idle(session)
                trade_status = "closed"
                lines.append("Panneau commerce referme.")
            elif sub in {"confirm", "confirmer"}:
                if session.status != "confirming":
                    session = engine.confirm_trade(session)
                result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)
                session = normalize_trade_session(result.get("session"))
                trade_status = str(result.get("trade_context", {}).get("status") or "")
                lines.extend([str(x).strip() for x in result.get("lines", []) if str(x).strip()])
                applied = bool(result.get("ok"))
            elif sub in {"all", "tout"}:
                session, info = engine.apply_quantity_choice(
                    session=session,
                    option_id="sell_all",
                    quantity=None,
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                trade_status = "confirming"
                lines.append(info)
            elif sub in {"one", "une"}:
                session, info = engine.apply_quantity_choice(
                    session=session,
                    option_id="sell_one",
                    quantity=1,
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                trade_status = "confirming"
                lines.append(info)
            elif sub in {"qty", "quantite", "qte"}:
                chosen_qty = _extract_qty(_norm(raw_input)) or 1
                session, info = engine.apply_quantity_choice(
                    session=session,
                    option_id="set_qty",
                    quantity=chosen_qty,
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                trade_status = "confirming"
                lines.append(info)
            elif sub in {"mode"} and len(cmd_parts) >= 3:
                mode = cmd_parts[2]
                session = engine.start_trade(str(selected_npc), mode, llm_enabled=llm_enabled_default)
                trade_status = "started"
                mode_hint = "Dis ce que tu veux acheter (ex: j'achete potion x2)." if str(mode).strip().casefold() == "buy" else (
                    "Dis ce que tu veux vendre (ex: je vends epee x2)."
                )
                lines.append(f"Session commerce ouverte ({mode}). {mode_hint}")
            elif sub in {"llm"} and len(cmd_parts) >= 3:
                switch = str(cmd_parts[2] or "").strip().casefold()
                enabled = switch in {"on", "1", "true"}
                gm_flags["trade_llm_vendor"] = enabled
                session.llm_enabled = enabled
                trade_status = "llm_toggle"
                lines.append(f"Marchand LLM {'active' if enabled else 'desactive'}.")
            else:
                trade_status = "command_unknown"
                lines.append("Commande commerce inconnue.")
        elif session.pending_question and _CANCEL_WORDS_RE.search(plain):
            session = engine.abort_trade(session)
            trade_status = "canceled"
            lines.append("Transaction annulee.")
        elif session.status == "confirming":
            if _CONFIRM_WORDS_RE.search(plain):
                result = engine.execute_trade(state=state, session=session, item_defs=state.item_defs)
                session = normalize_trade_session(result.get("session"))
                trade_status = str(result.get("trade_context", {}).get("status") or "")
                lines.extend([str(x).strip() for x in result.get("lines", []) if str(x).strip()])
                applied = bool(result.get("ok"))
            elif _CANCEL_WORDS_RE.search(plain):
                session = engine.abort_trade(session)
                trade_status = "canceled"
                lines.append("Transaction annulee.")
            else:
                trade_status = "confirming"
                lines.append("Offre en attente. Confirme avec 'oui' ou utilise les boutons.")
        elif session.pending_question and isinstance(session.pending_question, dict):
            if "tout" in plain:
                session, info = engine.apply_quantity_choice(
                    session=session,
                    option_id="sell_all",
                    quantity=None,
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                trade_status = "confirming"
                lines.append(info)
            elif "une par une" in plain or "une" == plain:
                session, info = engine.apply_quantity_choice(
                    session=session,
                    option_id="sell_one",
                    quantity=1,
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                trade_status = "confirming"
                lines.append(info)
            elif _CANCEL_WORDS_RE.search(plain):
                session = engine.abort_trade(session)
                trade_status = "canceled"
                lines.append("Transaction annulee.")
            else:
                qty_from_text = _extract_qty(plain)
                if qty_from_text:
                    session, info = engine.apply_quantity_choice(
                        session=session,
                        option_id="set_qty",
                        quantity=qty_from_text,
                        item_defs=state.item_defs,
                        npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                    )
                    trade_status = "confirming"
                    lines.append(info)
                else:
                    trade_status = "question_pending"
                    lines.append(str(session.pending_question.get("text") or "Choisis une quantite."))
        elif _SELL_WORDS_RE.search(plain) or plain.startswith("/trade sell"):
            session = engine.start_trade(str(selected_npc), "sell", llm_enabled=llm_enabled_default)
            inventory = engine.inventory_totals(state)
            intent = engine.detect_sell_intent(user_text, inventory, state.item_defs)
            if intent is None:
                trade_status = "no_intent"
                lines.append("Precise ce que tu veux vendre.")
            elif intent.ambiguous or not intent.item_id:
                trade_status = "item_ambiguous"
                lines.append("Je n'ai pas compris l'objet. Precise l'item (ex: epee apprenti).")
            else:
                session.last_player_intent = user_text[:220]
                if isinstance(selected_profile, dict):
                    tension = safe_int(selected_profile.get("tension_level"), 30)
                    greed = safe_int(selected_profile.get("aggressiveness"), 55)
                else:
                    tension = 30
                    greed = 55
                rep_bonus = max(-20, min(20, safe_int(state.faction_reputation.get("Marchands"), 0)))
                session.negotiation = {
                    "mood": max(0, min(100, 60 - (tension // 2))),
                    "trust": max(0, min(100, 58 - (tension // 3) + rep_bonus)),
                    "greed": max(0, min(100, greed)),
                    "rep_bonus": rep_bonus,
                }
                pending = engine.propose_bundle_options(intent, inventory)
                if pending:
                    session.pending_question = pending
                    session.status = "selecting"
                    trade_status = "question_pending"
                    lines.append(str(pending.get("text") or "Choisis une quantite."))
                else:
                    qty = intent.max_qty if intent.sell_all else 1
                    if intent.one_by_one:
                        qty = 1
                    elif intent.qty is not None:
                        qty = max(1, min(intent.max_qty, safe_int(intent.qty, 1)))
                    session = engine.add_to_cart(
                        session=session,
                        item_id=intent.item_id,
                        qty=qty,
                        item_defs=state.item_defs,
                        npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                    )
                    session = engine.confirm_trade(session)
                    trade_status = "confirming"
                    lines.append("Offre preparee. Verifie le recap et confirme.")
        elif _BUY_WORDS_RE.search(plain) or plain.startswith("/trade buy"):
            session = engine.start_trade(str(selected_npc), "buy", llm_enabled=llm_enabled_default)
            query = _detect_buy_item_query(plain)
            matched = _match_item_for_buy(query, state.item_defs if isinstance(state.item_defs, dict) else {})
            if not matched:
                trade_status = "item_unknown"
                lines.append("Precise l'objet a acheter.")
            else:
                item_id, _item = matched
                qty = _extract_qty(plain) or 1
                session = engine.add_to_cart(
                    session=session,
                    item_id=item_id,
                    qty=max(1, qty),
                    item_defs=state.item_defs,
                    npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
                )
                session = engine.confirm_trade(session)
                trade_status = "confirming"
                lines.append("Achat prepare. Confirme pour executer.")
        else:
            trade_status = "idle"
            attempted = False

    _sync_trade_session(state, session)
    _sync_legacy_pending_trade(state, session, selected_npc=str(selected_npc))

    for line in lines:
        cleaned = str(line or "").strip()
        if cleaned:
            state.push("Système", cleaned, count_for_media=False)

    trade_context = _trade_context_from_session(
        session,
        status=trade_status or ("ok" if applied else "pending"),
        selected_npc=str(selected_npc),
        npc_key=npc_key,
        safe_int=safe_int,
        state=state,
        economy_manager=economy_manager,
    )
    if trade_context:
        state.gm_state["last_trade"] = dict(trade_context)

    if applied:
        if bool(trade_context.get("secret_charity_candidate")) and npc_key:
            maybe_unlock_secret_charity_quest_fn(
                state,
                npc_name=str(selected_npc),
                npc_key=str(npc_key),
                scene=state.current_scene(),
                trade_context=trade_context,
            )
        rep_lines = apply_trade_reputation_fn(
            state,
            trade_context=trade_context,
            npc_name=str(selected_npc),
            npc_profile=selected_profile if isinstance(selected_profile, dict) else None,
            map_anchor=str(state.current_scene().map_anchor or ""),
        )
        if rep_lines:
            state.push("Système", "Réputation: " + " | ".join(rep_lines), count_for_media=False)

    return {
        "attempted": bool(attempted),
        "applied": bool(applied),
        "action": str(trade_context.get("action") or ""),
        "system_lines": lines,
        "trade_context": trade_context,
        "trade_session": trade_session_to_dict(session),
    }


def find_empty_slot(state: GameState) -> tuple[str, int] | None:
    for idx, stack in enumerate(state.carried.slots):
        if stack is None:
            return ("carried", idx)
    for idx, stack in enumerate(state.storage.slots):
        if stack is None:
            return ("storage", idx)
    return None


def item_stack_max(state: GameState, item_id: str) -> int:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    try:
        value = int(getattr(item, "stack_max", 1))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 999))


def grant_item_reward(
    state: GameState,
    item_id: str,
    qty: int,
    *,
    find_empty_slot_fn,
    item_stack_max_fn,
) -> int:
    if qty <= 0:
        return 0

    remaining = qty
    granted = 0
    stack_max = item_stack_max_fn(state, item_id)

    for grid in (state.carried, state.storage):
        for stack in grid.slots:
            if remaining <= 0:
                break
            if stack is None or stack.item_id != item_id:
                continue
            capacity = max(0, stack_max - int(stack.qty))
            if capacity <= 0:
                continue
            take = min(capacity, remaining)
            stack.qty += take
            remaining -= take
            granted += take
        if remaining <= 0:
            break

    while remaining > 0:
        empty = find_empty_slot_fn(state)
        if not empty:
            break
        which, idx = empty
        grid = state.carried if which == "carried" else state.storage
        take = min(stack_max, remaining)
        grid.set(idx, ItemStack(item_id=item_id, qty=take))
        remaining -= take
        granted += take

    return granted


def apply_quest_rewards(
    state: GameState,
    quest: dict,
    *,
    safe_int,
    grant_item_reward_fn,
) -> list[str]:
    rewards = quest.get("rewards", {}) if isinstance(quest.get("rewards"), dict) else {}
    lines: list[str] = []

    gold = max(0, safe_int(rewards.get("gold"), 0))
    if gold > 0:
        state.player.gold += gold
        lines.append(f"+{gold} or")

    items_raw = rewards.get("items")
    if isinstance(items_raw, list):
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "").strip()
            qty = max(1, safe_int(item.get("qty"), 1))
            if not item_id:
                continue
            granted = grant_item_reward_fn(state, item_id, qty)
            if granted > 0:
                lines.append(f"+{item_id} x{granted}")
            else:
                lines.append(f"Inventaire plein: recompense {item_id} perdue")

    flags = state.gm_state.setdefault("flags", {})
    shop_discount = max(0, safe_int(rewards.get("shop_discount_pct"), 0))
    if shop_discount > 0:
        current = max(0, safe_int(flags.get("shop_discount_pct"), 0))
        flags["shop_discount_pct"] = max(current, shop_discount)
        lines.append(f"Reduction boutique {flags['shop_discount_pct']}%")

    temple_bonus = max(0, safe_int(rewards.get("temple_heal_bonus"), 0))
    if temple_bonus > 0:
        current = max(0, safe_int(flags.get("temple_heal_bonus"), 0))
        flags["temple_heal_bonus"] = max(current, temple_bonus)
        lines.append(f"Bonus soins temple +{flags['temple_heal_bonus']}")

    quest["reward_claimed"] = True
    return lines


def item_display_name(state: GameState, item_id: str) -> str:
    item = state.item_defs.get(item_id) if isinstance(state.item_defs, dict) else None
    name = str(getattr(item, "name", "") or "").strip()
    return name or item_id
