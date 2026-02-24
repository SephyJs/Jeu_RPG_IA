from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.data.item_manager import ItemDef
from app.ui.state.inventory import ItemStack


@dataclass(frozen=True)
class RecipeLine:
    item_id: str
    qty: int


@dataclass(frozen=True)
class CraftRecipe:
    recipe_id: str
    name: str
    description: str
    station: str
    inputs: tuple[RecipeLine, ...]
    outputs: tuple[RecipeLine, ...]
    required_skill: str
    required_level: int


class CraftManager:
    def __init__(self, *, data_path: str = "data/crafting_recipes.json") -> None:
        self.data_path = Path(data_path)
        self._cache: dict[str, CraftRecipe] | None = None

    def load_recipes(self) -> dict[str, CraftRecipe]:
        if isinstance(self._cache, dict):
            return self._cache

        rows: list[dict] = []
        if self.data_path.exists():
            try:
                payload = json.loads(self.data_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("recipes"), list):
                    rows = [row for row in payload.get("recipes", []) if isinstance(row, dict)]
            except Exception:
                rows = []
        if not rows:
            rows = self._fallback_recipes()

        out: dict[str, CraftRecipe] = {}
        for row in rows:
            recipe = self._normalize_recipe(row)
            if recipe is None:
                continue
            out[recipe.recipe_id] = recipe
        self._cache = out
        return out

    def list_recipes_text(self, *, item_defs: dict[str, ItemDef] | None = None) -> str:
        recipes = self.load_recipes()
        if not recipes:
            return "Aucune recette."
        defs = item_defs if isinstance(item_defs, dict) else {}

        lines: list[str] = []
        for recipe_id, recipe in sorted(recipes.items()):
            ingredients = ", ".join(f"{self._item_label(defs, row.item_id)} x{row.qty}" for row in recipe.inputs)
            results = ", ".join(f"{self._item_label(defs, row.item_id)} x{row.qty}" for row in recipe.outputs)
            station = recipe.station or "camp"
            skill_hint = ""
            if recipe.required_skill:
                skill_hint = f" | skill={recipe.required_skill} niv>={recipe.required_level}"
            lines.append(f"{recipe_id} ({station}) -> {results} | requis: {ingredients}{skill_hint}")
        return "\n".join(lines)

    def craft(
        self,
        *,
        state,
        recipe_id: str,
        qty: int,
        item_defs: dict[str, ItemDef],
        scene_title: str,
    ) -> dict:
        recipes = self.load_recipes()
        recipe = recipes.get(str(recipe_id or "").strip().casefold())
        if recipe is None:
            return {"ok": False, "lines": [f"Recette inconnue: {recipe_id}"]}

        if not self._station_ok(recipe.station, scene_title):
            return {
                "ok": False,
                "lines": [f"Station requise: {recipe.station or 'camp'} (lieu actuel: {scene_title})."],
            }

        skill_error = self._skill_requirement_error(
            state=state,
            required_skill=recipe.required_skill,
            required_level=recipe.required_level,
        )
        if skill_error:
            return {"ok": False, "lines": [skill_error]}

        requested = max(1, int(qty))
        crafted = 0
        defs = item_defs if isinstance(item_defs, dict) else {}

        for _ in range(requested):
            if not self._has_ingredients(state, recipe.inputs):
                break

            # Transaction batch: remove inputs then add outputs.
            for line in recipe.inputs:
                self._remove_item(state, line.item_id, line.qty)

            output_ok = True
            for line in recipe.outputs:
                added = self._add_item(state, line.item_id, line.qty, item_defs=defs)
                if added < line.qty:
                    # rollback outputs + inputs for this batch.
                    if added > 0:
                        self._remove_item(state, line.item_id, added)
                    for rewind in recipe.inputs:
                        self._add_item(state, rewind.item_id, rewind.qty, item_defs=defs)
                    output_ok = False
                    break

            if not output_ok:
                break
            crafted += 1

        if crafted <= 0:
            return {"ok": False, "lines": ["Echec craft: ingredients insuffisants ou inventaire plein."]}

        outputs_text = ", ".join(
            f"{self._item_label(defs, row.item_id)} x{row.qty * crafted}" for row in recipe.outputs
        )
        lines = [f"Craft reussi ({recipe.name}): {outputs_text}."]
        if crafted < requested:
            lines.append(f"Quantite ajustee: {crafted}/{requested}.")
        return {"ok": True, "lines": lines, "crafted": crafted}

    def _normalize_recipe(self, row: dict) -> CraftRecipe | None:
        recipe_id = str(row.get("id") or "").strip().casefold()
        if not recipe_id:
            return None
        name = str(row.get("name") or recipe_id).strip()[:80]
        description = str(row.get("description") or "").strip()[:180]
        station = str(row.get("station") or "camp").strip().casefold()[:24]

        inputs = self._normalize_lines(row.get("inputs"))
        outputs = self._normalize_lines(row.get("outputs"))
        if not inputs or not outputs:
            return None

        required_skill = str(row.get("required_skill") or "").strip().casefold()[:40]
        required_level = max(1, min(self._safe_int(row.get("required_level"), 1), 99))
        return CraftRecipe(
            recipe_id=recipe_id,
            name=name,
            description=description,
            station=station,
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            required_skill=required_skill,
            required_level=required_level,
        )

    def _normalize_lines(self, raw: object) -> list[RecipeLine]:
        lines: list[RecipeLine] = []
        if not isinstance(raw, list):
            return lines
        for row in raw[:10]:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("item_id") or "").strip().casefold()
            qty = max(1, min(self._safe_int(row.get("qty"), 1), 999))
            if not item_id:
                continue
            lines.append(RecipeLine(item_id=item_id, qty=qty))
        return lines

    def _station_ok(self, station: str, scene_title: str) -> bool:
        required = str(station or "camp").strip().casefold()
        title = str(scene_title or "").strip().casefold()
        if required in {"", "camp", "atelier"}:
            return True
        if required == "forge":
            return any(token in title for token in ("forge", "armurerie", "atelier", "forgeron"))
        if required == "alchimie":
            return any(token in title for token in ("alchim", "herbor", "infirmer", "laboratoire", "apoth"))
        if required == "temple":
            return any(token in title for token in ("temple", "sanctuaire", "chapelle"))
        return required in title

    def _skill_requirement_error(self, *, state, required_skill: str, required_level: int) -> str:
        skill_id = str(required_skill or "").strip().casefold()
        if not skill_id:
            return ""
        rows = state.player_skills if isinstance(state.player_skills, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("skill_id") or "").strip().casefold()
            row_name = str(row.get("name") or "").strip().casefold()
            level = max(1, self._safe_int(row.get("level"), 1))
            if skill_id not in {row_id, row_name}:
                continue
            if level >= required_level:
                return ""
            return f"Competence insuffisante: {skill_id} niveau {required_level} requis."
        return f"Competence requise absente: {skill_id} (niv {required_level})."

    def _has_ingredients(self, state, inputs: tuple[RecipeLine, ...]) -> bool:
        for row in inputs:
            if self._count_item(state, row.item_id) < row.qty:
                return False
        return True

    def _count_item(self, state, item_id: str) -> int:
        target = str(item_id or "").strip().casefold()
        total = 0
        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if stack is None:
                    continue
                if str(stack.item_id or "").strip().casefold() != target:
                    continue
                total += max(0, self._safe_int(getattr(stack, "qty", 0), 0))
        return total

    def _remove_item(self, state, item_id: str, qty: int) -> int:
        target = str(item_id or "").strip().casefold()
        remaining = max(0, int(qty))
        removed = 0
        for grid in (state.carried, state.storage):
            for idx, stack in enumerate(grid.slots):
                if remaining <= 0:
                    break
                if stack is None:
                    continue
                if str(stack.item_id or "").strip().casefold() != target:
                    continue
                stack_qty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                if stack_qty <= 0:
                    continue
                take = min(stack_qty, remaining)
                new_qty = stack_qty - take
                if new_qty > 0:
                    grid.slots[idx] = ItemStack(item_id=target, qty=new_qty)
                else:
                    grid.slots[idx] = None
                remaining -= take
                removed += take
            if remaining <= 0:
                break
        return removed

    def _add_item(self, state, item_id: str, qty: int, *, item_defs: dict[str, ItemDef]) -> int:
        target = str(item_id or "").strip().casefold()
        remaining = max(0, int(qty))
        added = 0
        item = item_defs.get(target)
        stack_max = max(1, self._safe_int(getattr(item, "stack_max", 1), 1))

        for grid in (state.carried, state.storage):
            for stack in grid.slots:
                if remaining <= 0:
                    break
                if stack is None:
                    continue
                if str(stack.item_id or "").strip().casefold() != target:
                    continue
                stack_qty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                capacity = max(0, stack_max - stack_qty)
                if capacity <= 0:
                    continue
                take = min(capacity, remaining)
                stack.qty = stack_qty + take
                remaining -= take
                added += take
            if remaining <= 0:
                break

        for grid in (state.carried, state.storage):
            while remaining > 0:
                try:
                    idx = grid.slots.index(None)
                except ValueError:
                    break
                take = min(stack_max, remaining)
                grid.slots[idx] = ItemStack(item_id=target, qty=take)
                remaining -= take
                added += take
            if remaining <= 0:
                break
        return added

    def _item_label(self, item_defs: dict[str, ItemDef], item_id: str) -> str:
        item = item_defs.get(str(item_id or "").strip().casefold()) if isinstance(item_defs, dict) else None
        name = str(getattr(item, "name", "") or "").strip()
        return name or str(item_id or "").strip()

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _fallback_recipes(self) -> list[dict]:
        return [
            {
                "id": "infusion_soin",
                "name": "Infusion de soin",
                "description": "Restaure un peu de vitalite.",
                "station": "alchimie",
                "inputs": [{"item_id": "pain_01", "qty": 2}],
                "outputs": [{"item_id": "potion_soin_01", "qty": 1}],
            },
            {
                "id": "infusion_mana",
                "name": "Infusion de mana",
                "description": "Recharge la reserve magique.",
                "station": "alchimie",
                "inputs": [{"item_id": "pain_01", "qty": 2}],
                "outputs": [{"item_id": "potion_mana_01", "qty": 1}],
            },
            {
                "id": "elixir_force",
                "name": "Elixir de force",
                "description": "Mixe soin et mana pour un boost offensif.",
                "station": "alchimie",
                "inputs": [
                    {"item_id": "potion_soin_01", "qty": 1},
                    {"item_id": "potion_mana_01", "qty": 1},
                ],
                "outputs": [{"item_id": "potion_force_01", "qty": 1}],
            },
            {
                "id": "elixir_defense",
                "name": "Elixir de defense",
                "description": "Preparation defensive pour les expeditions.",
                "station": "alchimie",
                "inputs": [
                    {"item_id": "potion_soin_01", "qty": 1},
                    {"item_id": "potion_dexterite_01", "qty": 1},
                ],
                "outputs": [{"item_id": "potion_defense_01", "qty": 1}],
            },
            {
                "id": "lame_apprenti",
                "name": "Lame d'apprenti",
                "description": "Assemblage simple a la forge.",
                "station": "forge",
                "inputs": [
                    {"item_id": "potion_force_01", "qty": 1},
                    {"item_id": "pain_01", "qty": 3},
                ],
                "outputs": [{"item_id": "epee_apprenti", "qty": 1}],
            },
            {
                "id": "elixir_agilite",
                "name": "Elixir d'agilite",
                "description": "Melange reactif pour gagner en vitesse.",
                "station": "alchimie",
                "inputs": [
                    {"item_id": "potion_mana_01", "qty": 1},
                    {"item_id": "potion_dexterite_01", "qty": 1},
                ],
                "outputs": [{"item_id": "potion_agilite_01", "qty": 1}],
            },
        ]
