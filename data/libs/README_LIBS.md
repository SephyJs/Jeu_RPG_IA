# Text Libraries (`data/libs`)

Objectif: centraliser les phrases/templates/labels hors code pour permettre l'ajout futur de textes sans modifier Python.

## Format

Chaque fichier JSON suit la structure:

```json
{
  "meta": {
    "lang": "fr",
    "version": 1,
    "description": "..."
  },
  "entries": {
    "example.key": [
      "Phrase 1",
      "Phrase 2"
    ]
  }
}
```

Rappels:
- `entries.<key>` doit etre une liste de strings.
- Variables autorisees: `{player}`, `{npc}`, `{location}`, `{item}`, `{count}` (et autres champs explicites selon le contexte).
- Les fichiers `.txt` (optionnels) utilisent `1 ligne = 1 phrase` et une cle derivee du nom de fichier.

## Loader

Module central: `app/infra/text_library.py`

API:
- `load_all_libs(root="data/libs")`
- `reload_libs(root="data/libs")`
- `get_phrases(key, category=None, lang="fr")`
- `pick(key, fallback=None, category=None, lang="fr", **vars)`
- `format_vars(text, **vars)`
- `list_keys(lang="fr")`

## Cles (premiere passe)

### UI
- `ui.button.mode_dungeon`
- `ui.button.mode_ataryxia`
- `ui.button.status`
- `ui.button.save`
- `ui.dungeon.action.enter`
- `ui.dungeon.action.combat_active`
- `ui.dungeon.action.advance`
- `ui.dungeon.action.attack`
- `ui.dungeon.action.skill`
- `ui.dungeon.action.flee`
- `ui.dungeon.action.inventory`
- `ui.dungeon.action.back`
- `ui.dungeon.actions_title`
- `ui.dungeon.skill_menu_title`
- `ui.dungeon.inventory_title`
- `ui.dungeon.inventory_empty`

### System messages
- `system.start.title`
- `system.start.current_mode` (vars: `mode`)
- `system.start.quick_choice`
- `system.save.done`
- `system.mode.ataryxia_active`
- `system.mode.dungeon_active`
- `system.trade.none_pending`
- `system.session.not_initialized`
- `system.message.empty`
- `system.dungeon.none_active`
- `system.dungeon.none_combat`
- `system.dungeon.turn_resolved`
- `system.dungeon.floor_header` (vars: `floor`, `total`)
- `system.dungeon.finished`
- `system.dungeon.state_line` (vars: `enemy`, `enemy_hp`, `enemy_max_hp`, `player_hp`, `player_max_hp`)
- `system.dungeon.combat_prompt`
- `system.dungeon.entered` (vars: `anchor`)
- `system.dungeon.profile_line` (vars: `dungeon_name`, `total_floors`)
- `system.dungeon.victory_boss`
- `system.dungeon.end_surface`
- `system.dungeon.loot_gold` (vars: `gold`)
- `system.dungeon.buff_expired` (vars: `label`)
- `system.dungeon.item_used` (vars: `item_name`)
- `system.skill.out_of_combat.header` (vars: `skill`)
- `system.skill.out_of_combat.heal` (vars: `gain`, `hp`, `max_hp`)
- `system.skill.out_of_combat.heal_full` (vars: `hp`, `max_hp`)
- `system.skill.out_of_combat.buff` (vars: `stat`, `bonus`, `turns`)
- `system.skill.out_of_combat.offensive_only`
- `system.skill.out_of_combat.rescue`
- `system.skill.out_of_combat.recovery` (vars: `hp`, `max_hp`)
- `system.travel.arrival` (vars: `destination`)
- `system.travel.npc_active` (vars: `npc`)
- `system.travel.no_npc`
- `system.travel.world_time` (vars: `world_time`)
- `system.travel.narration` (vars: `narration`)
- `system.ataryxia.fallback_continue`
- `system.bot.unsupported_npcs`
- `system.bot.unsupported_move`
- `system.bot.action_unknown`
- `system.bot.token_missing`

### Errors
- `error.invalid_move`
- `error.invalid_move_obsolete`
- `error.location_closed`
- `error.no_npc_in_scene`
- `error.npc_not_found`
- `error.combat_active_buttons`
- `error.skill.none_known`
- `error.skill.no_heal_outside`
- `error.skill.no_support_outside`
- `error.skill.no_usable_outside`
- `error.consumable.invalid`
- `error.consumable.unavailable`
- `error.consumable.not_consumable`
- `error.consumable.no_effect`
- `error.consumable.none_applied`
- `error.consumable.inventory_fail`
- `error.ai.failure` (vars: `error`)
- `error.creation.busy`
- `error.bot.chat_not_found`
- `error.bot.session_manager_unavailable`
- `error.bot.slot_invalid`
- `error.bot.profile_usage`
- `error.bot.slot_usage`

### NPC
- `npc.greeting.default`
- `npc.greeting.friendly`
- `npc.farewell.default`
- `npc.quest.accept.default`
- `npc.quest.refuse.default`
- `npc.quest.return.success`
- `npc.quest.return.partial`
- `npc.trade.pending.buy` (vars: `item`, `count`)
- `npc.trade.pending.sell` (vars: `item`, `count`)
- `npc.trade.confirm`
- `npc.trade.cancel`

### Narration
- `narration.ataryxia.idle.context` (vars: `topic`)
- `narration.ataryxia.idle.context_checkin` (vars: `topic`)
- `narration.ataryxia.idle.checkin`
- `narration.ataryxia.idle.generic`
- `narration.ataryxia.idle.fallback`
- `narration.travel.arrival` (vars: `destination`)
- `narration.travel.closed` (vars: `reason`)
- `narration.travel.default_closed`
- `narration.ambience.generic_continue`
- `narration.ambience.dungeon_recovery`
- `narration.ambience.dungeon_recovery_hp` (vars: `hp`, `max_hp`)

## Telegram extensions

Ajouts utilises par la migration Telegram (`app/telegram/*`):

- Status/session:
  - `system.status.short` (vars: `profile_key`, `slot`, `location`)
  - `system.status.weapon_none`
  - `system.status.npc_none`
  - `system.status.line.profile` (vars: `profile`)
  - `system.status.line.profile_id` (vars: `profile_id`)
  - `system.status.line.slot` (vars: `slot`)
  - `system.status.line.location` (vars: `location`)
  - `system.status.line.npc` (vars: `npc`)
  - `system.status.line.gold` (vars: `gold`)
  - `system.status.line.level_skills` (vars: `level`, `skills`)
  - `system.status.line.weapon` (vars: `weapon`)
  - `system.status.line.world_time` (vars: `world_time`)
  - `system.status.creation.ok`
  - `system.status.creation.incomplete`
  - `system.status.line.creation` (vars: `creation`)
- Travel/memory:
  - `system.travel.option.default` (vars: `destination`)
  - `system.travel.already_there` (vars: `destination`)
  - `system.memory.travel_fact` (vars: `from_location`, `to_location`, `minutes`)
- Trade:
  - `system.trade.pending.buy_full` (vars: `item`, `qty`, `total`, `unit_price`)
  - `system.trade.pending.buy` (vars: `item`, `qty`)
  - `system.trade.pending.sell_full` (vars: `item`, `qty`, `total`, `unit_price`)
  - `system.trade.pending.sell` (vars: `item`, `qty`)
  - `system.trade.pending.exchange` (vars: `item`, `qty`)
  - `system.trade.pending.give` (vars: `item`, `qty`)
  - `system.trade.pending.generic` (vars: `item`, `qty`)
  - `system.trade.cancel_command`
  - `system.trade.confirm_cmd.buy`
  - `system.trade.confirm_cmd.sell`
  - `system.trade.confirm_cmd.give`
  - `system.trade.confirm_cmd.exchange`
  - `system.trade.confirm_cmd.default`
  - `system.memory.trade_fact` (vars: `action`, `status`)
  - `system.memory.trade_fact.item` (vars: `item`)
  - `system.memory.trade_fact.item_qty` (vars: `item`, `qty`)
- NPC:
  - `system.npc.active.summary` (vars: `summary`)
  - `system.npc.active.with_first_message` (vars: `summary`, `speaker`, `line`)
  - `system.npc.active.simple` (vars: `npc`)
- Dungeon/combat/loot:
  - `system.dungeon.status.none` (vars: `anchor`)
  - `system.dungeon.status.name` (vars: `dungeon_name`)
  - `system.dungeon.status.floor` (vars: `floor`, `total`)
  - `system.dungeon.status.hp` (vars: `hp`, `max_hp`)
  - `system.dungeon.status.gold` (vars: `gold`)
  - `system.dungeon.status.combat` (vars: `enemy`, `enemy_hp`, `enemy_max_hp`)
  - `system.dungeon.status.combat_none`
  - `system.dungeon.floor_explored`
  - `system.dungeon.combat_engaged` (vars: `enemy`, `hp`)
  - `system.dungeon.reputation_prefix`
  - `system.dungeon.flee.success` (vars: `chance`)
  - `system.dungeon.flee.fail`
  - `system.dungeon.flee.defense_action`
  - `system.combat.action.attack`
  - `system.combat.action.spell`
  - `system.combat.action.heal`
  - `system.combat.action.skill_use` (vars: `skill`)
  - `system.combat.action.skill_fallback`
  - `system.dungeon.loot_prefix.obtained`
  - `system.dungeon.loot_prefix.boss`
  - `system.dungeon.loot_prefix.potion`
  - `system.dungeon.loot.lost` (vars: `prefix`, `label`, `qty`)
  - `system.dungeon.loot.new_item_suffix`
  - `system.dungeon.loot.capped_suffix`
  - `system.dungeon.potion_hint.heal`
  - `system.dungeon.potion_hint.mana`
  - `system.dungeon.potion_hint.strength`
  - `system.dungeon.potion_hint.dexterity`
  - `system.dungeon.potion_hint.agility`
  - `system.dungeon.potion_hint.defense`
  - `system.dungeon.potion_hint.wisdom`
  - `system.dungeon.potion_hint.magic`
- Creation/system display:
  - `system.prefix.system` (vars: `text`)
  - `system.creation.ready`
  - `system.creation.missing_unknown`
  - `system.creation.status` (vars: `missing`, `question`)
  - `system.creation.done.1`
  - `system.creation.done.2`
  - `system.creation.done.3`
  - `system.creation.intro.system`
  - `system.creation.intro.ataryxia`
  - `system.turn.no_response`
  - `system.message.placeholder`
- Telegram bridge:
  - `error.telegram.token_invalid`
  - `error.telegram.no_token_configured`
  - `error.telegram.bot_start_failed` (vars: `error`)
  - `error.telegram.bot_not_running`
  - `system.telegram.bot_already_running` (vars: `pid`)
  - `system.telegram.bot_started` (vars: `pid`)
  - `system.telegram.bot_stopped` (vars: `pid`)
- Additional UI:
  - `ui.dungeon.inventory_item_label` (vars: `item`, `qty`)
  - `system.bot.profile_line` (vars: `key`, `name`)
- Additional errors:
  - `error.save.failed` (vars: `error`)
  - `error.dungeon.open_failed` (vars: `error`)
  - `error.creation.update_failed` (vars: `error`)
  - `error.data.load_failed` (vars: `error`)
- Ataryxia Telegram persona:
  - `system.ataryxia.idle.topic_fallback`
  - `system.ataryxia.profile.role`
  - `system.ataryxia.profile.agenda`
  - `system.ataryxia.profile.need`
  - `system.ataryxia.profile.fear`
  - `system.ataryxia.profile.persona_directives`

## Evolution

- Incrementer `meta.version` lors de changement de schema.
- Ajouter des cles sans supprimer brutalement les anciennes (compatibilite).
- Utiliser `tools/check_texts.py` pour verifier coherence code <-> bibliotheques.

Commande utile:
- `python -m tools.check_texts`
