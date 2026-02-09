from __future__ import annotations
import random
import re

_dice_re = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)

def roll(expr: str, rng: random.Random | None = None) -> tuple[int, str]:
    """
    Supports: d20, 2d6+1, d8-1
    Returns (total, detail)
    """
    rng = rng or random.Random()
    m = _dice_re.match(expr.replace(" ", ""))
    if not m:
        raise ValueError(f"Invalid dice expr: {expr}")

    n_str, sides_str, mod_str = m.groups()
    n = int(n_str) if n_str else 1
    sides = int(sides_str)
    mod = int(mod_str.replace(" ", "")) if mod_str else 0

    rolls = [rng.randint(1, sides) for _ in range(n)]
    total = sum(rolls) + mod

    detail = f"{n}d{sides} رول={rolls}"
    if mod:
        detail += f" mod={mod:+d}"
    detail += f" => {total}"
    return total, detail
