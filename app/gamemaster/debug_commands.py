from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass(frozen=True)
class DebugChoice:
    forced_model: Optional[str]   # "mistral" | "qwen" | "dolphin" | None
    enabled: bool                 # debug mode ON/OFF
    cleaned_text: str             # message sans commande

def parse_debug_command(text: str, debug_enabled: bool) -> Tuple[DebugChoice, bool]:
    """
    Retourne (choice, handled)
    - handled=True si c'était une commande pure (/debug on|off) et qu'il ne faut pas appeler l'IA.
    - sinon handled=False et on continue normalement.
    """
    t = text.strip()

    # Commande /debug on|off (ne lance pas l'IA)
    if t.lower() in ("/debug on", "/debug off"):
        new_enabled = (t.lower() == "/debug on")
        return DebugChoice(forced_model=None, enabled=new_enabled, cleaned_text=""), True

    # Commandes de forçage modèle (ne changent pas debug_enabled automatiquement)
    # Si debug est OFF, on ignore le forçage (ça évite les surprises)
    if not debug_enabled:
        return DebugChoice(forced_model=None, enabled=debug_enabled, cleaned_text=text), False

    # /mistral bla bla
    for prefix, model in (("/mistral", "mistral"), ("/qwen", "qwen"), ("/dolphin", "dolphin"), ("/auto", None)):
        if t.lower().startswith(prefix):
            cleaned = t[len(prefix):].lstrip()
            # /auto sans texte -> cleaned_text = ""
            return DebugChoice(forced_model=model, enabled=debug_enabled, cleaned_text=cleaned), False

    return DebugChoice(forced_model=None, enabled=debug_enabled, cleaned_text=text), False
