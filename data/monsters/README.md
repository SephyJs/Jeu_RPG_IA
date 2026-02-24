# Monster JSON Format

Each monster is defined in its own JSON file (`one file per monster type`).

Required top-level fields:
- `id`: stable technical id (slug)
- `name`: display name
- `aliases`: list of alternative names for lookup
- `archetype`: free label (`brute`, `tank`, `caster`, etc.)
- `tier`: difficulty tier (1-5)
- `description`: short text

Combat section:
- `combat.base_hp`
- `combat.base_dc`
- `combat.base_attack_bonus`
- `combat.base_damage_min`
- `combat.base_damage_max`
- `combat.hp_per_floor`
- `combat.dc_per_5_floors`
- `combat.attack_per_6_floors`
- `combat.damage_per_8_floors`

Boss modifiers:
- `boss_modifiers.hp_mult`
- `boss_modifiers.damage_mult`
- `boss_modifiers.dc_bonus`
- `boss_modifiers.attack_bonus`

Media placeholders:
- `media.image`: image path or URL
- `media.clip`: video path or URL
