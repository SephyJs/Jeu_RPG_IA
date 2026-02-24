import asyncio

from app.core.data.item_manager import ItemDef
from app.gamemaster.loot_manager import LootManager


class _SequenceRng:
    def __init__(self, random_values: list[float], randint_values: list[int] | None = None) -> None:
        self._random_values = list(random_values)
        self._randint_values = list(randint_values or [])

    def random(self) -> float:
        if self._random_values:
            value = float(self._random_values.pop(0))
            return max(0.0, min(1.0, value))
        return 0.5

    def randint(self, a: int, b: int) -> int:
        if self._randint_values:
            value = int(self._randint_values.pop(0))
            return max(a, min(b, value))
        return max(a, min(b, a))

    def choice(self, seq):
        if not seq:
            raise IndexError("empty sequence")
        return seq[0]


def test_generate_loot_fallback_uses_hint_for_new_item() -> None:
    manager = LootManager(None, data_dir="data")
    loot = asyncio.run(
        manager.generate_loot(
            source_type="treasure",
            floor=6,
            anchor="Lumeria",
            known_items={},
            hint_text="Livre de nécromancie",
        )
    )

    assert isinstance(loot, dict)
    assert str(loot.get("item_id") or "").startswith("livre_de_necromancie")
    assert isinstance(loot.get("new_item"), dict)
    assert str(loot["new_item"].get("name") or "").lower().startswith("livre")


def test_generate_loot_fallback_uses_existing_item_from_hint() -> None:
    manager = LootManager(None, data_dir="data")
    known = {
        "livre_de_necromancie": ItemDef(
            id="livre_de_necromancie",
            name="Livre de necromancie",
            stack_max=2,
            type="misc",
            slot="",
            rarity="rare",
            description="",
            stat_bonuses={"magie": 1},
            effects=[],
            value_gold=30,
        )
    }
    loot = asyncio.run(
        manager.generate_loot(
            source_type="treasure",
            floor=9,
            anchor="Lumeria",
            known_items=known,
            hint_text="Livre de nécromancie",
        )
    )

    assert isinstance(loot, dict)
    assert loot.get("item_id") == "livre_de_necromancie"
    assert loot.get("new_item") is None


def test_fallback_loot_can_generate_potion_drop() -> None:
    manager = LootManager(None, data_dir="data")
    loot = asyncio.run(
        manager.generate_loot(
            source_type="monster",
            floor=10,
            anchor="Lumeria",
            known_items={},
            hint_text="potion de force",
        )
    )

    assert isinstance(loot, dict)
    new_item = loot.get("new_item")
    assert isinstance(new_item, dict)
    assert new_item.get("type") == "consumable"
    effects = new_item.get("effects")
    assert isinstance(effects, list) and len(effects) >= 1
    kinds = {str(effect.get("kind") or "") for effect in effects if isinstance(effect, dict)}
    assert kinds.intersection({"heal", "mana", "stat_buff"})


def test_normalize_item_payload_adds_rarity_effect_for_equipment() -> None:
    manager = LootManager(None, data_dir="data")
    payload = manager._normalize_item_payload(  # noqa: SLF001 - tested intentionally
        {
            "id": "lame_rare_test",
            "name": "Lame rare test",
            "type": "weapon",
            "slot": "weapon",
            "rarity": "rare",
            "description": "Prototype",
            "stack_max": 1,
            "stat_bonuses": {"force": 2},
            "effects": [],
            "value_gold": 80,
        },
        floor=12,
        forced_rarity="rare",
    )

    assert payload["type"] == "weapon"
    assert isinstance(payload.get("effects"), list)
    assert len(payload["effects"]) >= 1


def test_fallback_loot_with_large_catalog_can_still_create_new_item() -> None:
    manager = LootManager(None, data_dir="data")
    manager.rng = _SequenceRng([0.99, 0.99, 0.20, 0.10], [1])
    known = {
        "potion_soin_01": ItemDef(id="potion_soin_01", name="Potion de soin", stack_max=8, type="consumable", rarity="common"),
        "potion_mana_01": ItemDef(id="potion_mana_01", name="Potion de mana", stack_max=8, type="consumable", rarity="common"),
        "pain_01": ItemDef(id="pain_01", name="Pain", stack_max=12, type="consumable", rarity="common"),
        "epee_apprenti": ItemDef(id="epee_apprenti", name="Epee d'apprenti", stack_max=1, type="weapon", slot="weapon", rarity="common"),
        "potion_force_01": ItemDef(id="potion_force_01", name="Potion de force", stack_max=8, type="consumable", rarity="uncommon"),
    }

    loot = asyncio.run(
        manager.generate_loot(
            source_type="treasure",
            floor=12,
            anchor="Lumeria",
            known_items=known,
            hint_text="",
        )
    )

    assert isinstance(loot, dict)
    assert isinstance(loot.get("new_item"), dict)
    assert str(loot.get("item_id") or "") not in known
    assert str(loot["new_item"].get("type") or "") != "consumable"


def test_diversity_guard_replaces_llm_consumable_on_boss_loot() -> None:
    class _FakeLlm:
        async def generate(self, **kwargs) -> str:
            return '{"item_id":"potion_soin_01","qty":1,"rarity":"common","new_item":null}'

    manager = LootManager(_FakeLlm(), data_dir="data")
    manager.rng = _SequenceRng([0.10, 0.20, 0.30, 0.20, 0.10, 0.10, 0.20], [1])
    known = {
        "potion_soin_01": ItemDef(id="potion_soin_01", name="Potion de soin", stack_max=8, type="consumable", rarity="common"),
        "epee_apprenti": ItemDef(id="epee_apprenti", name="Epee d'apprenti", stack_max=1, type="weapon", slot="weapon", rarity="common"),
    }

    loot = asyncio.run(
        manager.generate_loot(
            source_type="boss",
            floor=18,
            anchor="Lumeria",
            known_items=known,
            hint_text="relique de domination",
        )
    )

    assert isinstance(loot, dict)
    if isinstance(loot.get("new_item"), dict):
        assert str(loot["new_item"].get("type") or "") != "consumable"
    else:
        item_id = str(loot.get("item_id") or "")
        assert str(known[item_id].type or "") != "consumable"


def test_diversity_guard_breaks_repeated_same_item_drop_from_llm() -> None:
    class _FakeLlm:
        async def generate(self, **kwargs) -> str:
            return '{"item_id":"epee_apprenti","qty":1,"rarity":"common","new_item":null}'

    manager = LootManager(_FakeLlm(), data_dir="data")
    known = {
        "epee_apprenti": ItemDef(
            id="epee_apprenti",
            name="Epee d'apprenti",
            stack_max=1,
            type="weapon",
            slot="weapon",
            rarity="common",
        ),
    }

    first = asyncio.run(
        manager.generate_loot(
            source_type="monster",
            floor=7,
            anchor="Lumeria",
            known_items=known,
            hint_text="goule cendreuse",
        )
    )
    second = asyncio.run(
        manager.generate_loot(
            source_type="monster",
            floor=7,
            anchor="Lumeria",
            known_items=known,
            hint_text="goule cendreuse",
        )
    )

    assert first.get("item_id") == "epee_apprenti"
    assert second.get("item_id") != "epee_apprenti"
