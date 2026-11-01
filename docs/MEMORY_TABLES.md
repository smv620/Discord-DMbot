# The story memory tables

The story memory holds the campaign's names, how they connect, and who knows what. This page lists what is in the `memory_*` tables; it does not explain why they are shaped that way.

Every table is in the one `dmbot` database, in the public schema. Every row carries `guild_id` (the Discord server) and `campaign_id`; both are the first two parts of every key and are left out of the lists below. Read the migrations in `core/src/dmbot/migrations/` for the shape of each table, and `core/src/dmbot/memory/_changes.py` for which tables Undo can reach.

Sources: the facts come from a freshly migrated database (every migration in order), so types, nulls and defaults are exact. Allowed values come from the table's `CHECK` constraints. Meanings come only from a code comment or docstring (in `models.py`, `_changes.py` or a migration), cited in brackets. Anything else is written as `Not yet documented`.

Count: 24 tables. The migrations create 24 distinct `memory_*` tables (`grep -ho "CREATE TABLE memory_[a-z_]*" core/src/dmbot/migrations/*.py | sort -u`).

## The tables

- `memory_entities`
- `memory_aliases`
- `memory_relations`
- `memory_states`
- `memory_alias_knowers`
- `memory_attitudes`
- `memory_beliefs`
- `memory_changes`
- `memory_claims`
- `memory_contradictions`
- `memory_corrections`
- `memory_differences`
- `memory_evidence`
- `memory_flags`
- `memory_heard`
- `memory_impressions`
- `memory_importance`
- `memory_knowers`
- `memory_mentions`
- `memory_perspectives`
- `memory_predicates`
- `memory_report_runs`
- `memory_rule_links`
- `memory_types`
- `memory_vectors`

## Conflicts between the code and its comments

These are for the lead to decide. The page does not pick a side.

- `memory_entities.role`: models.py, `Entity.role` comment: "player character, NPC or god"; the CHECK constraint and `ROLES` allow only `player_character` and `npc`.
- `memory_entities.party`: models.py, `Entity.party` comment: the party is "worked out in code and not stored fact by fact"; memory/party.py stores "The party" as a faction entity with source `party` (#3036), and this boolean column is still in the table.

## `memory_entities`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `type` | text | no |  | Not yet documented |
| `name` | text | no |  | Not yet documented |
| `description` | text | no |  | Not yet documented |
| `status` | text | no |  | Not yet documented |
| `merged_into` | text | yes |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `played_by` | bigint | yes |  | The Discord user playing this player character (models.py, `Entity`). |
| `role` | text | yes |  | Player character or NPC; for a character only (models.py, `Entity`, with `ROLES`). **See the conflict note below: the comment there also says "god".** |
| `needs_look` | boolean | no | `false` | Moved here from an older kind: the DM keeps, links or deletes it (models.py, `Entity`). |
| `unique_item` | boolean | yes |  | One of a kind, such as a named sword rather than a longbow. Null means nobody said, so a proper name counts as yes; the DM's switch sets it (models.py, `Entity`). |
| `audience_limited` | boolean | no | `false` | A clue only some people know; the DM's switch. Who knows it is the confirmed `knows` facts (#1649; models.py, `Entity`). |
| `party` | boolean | no | `false` | The campaign's party, one group whose members are its confirmed player characters (#1655; models.py, `Entity`). **See the conflict note below.** |
| `hidden_from_players` | boolean | no | `false` | The DM's "Players don't know this one yet" switch (#2321; models.py, `Entity`). |
| `made_before_name_gate` | boolean | no | `false` | Written before pronouns were kept out of the suggestions (#2358); set by the database for rows that already existed (models.py, `Entity`). |
| `manipulated_by` | text | yes |  | For an event, clue or item: the evidence it faked, by id (#2409; models.py, `Entity`). |
| `unnamed` | boolean | no | `false` | A person the story describes but never names (#2790; models.py, `Entity`). |
| `unnamed_anchor` | text | yes |  | The entry an unnamed person hangs off; empty for a hooded figure (models.py, `Entity`). |
| `unnamed_role` | text | yes |  | The relationship of an unnamed person to its anchor (models.py, `Entity`). |
| `unnamed_what` | text | yes |  | What the story calls an unnamed person (models.py, `Entity`). |

**Allowed values:**

- `status`: `proposed`, `confirmed`, `rejected`, `merged` (CHECK constraint on the table).
- `role`: `player_character`, `npc` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, manipulated_by` references `memory_entities`; ON DELETE CASCADE: no; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, merged_into` references `memory_entities`; ON DELETE CASCADE: no; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, unnamed_anchor` references `memory_entities`; ON DELETE CASCADE: no; ON UPDATE CASCADE: yes.

## `memory_aliases`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `entity_id` | text | no |  | Not yet documented |
| `text` | text | no |  | Not yet documented |
| `key` | text | no |  | Not yet documented |
| `kind` | text | no |  | Not yet documented |
| `used_by` | text | yes |  | Not yet documented |
| `secret` | boolean | no |  | DM-only: a secret identity (models.py, `Alias`). |
| `status` | text | no |  | Not yet documented |
| `sound_codes` | text[] | no |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `said_as` | text | yes |  | How the recap's narrator says the name (#1419); never set on a secret name (models.py, `Alias`). |

**Allowed values:**

- `kind`: `full`, `short`, `nickname`, `title`, `misheard`, `old` (CHECK constraint on the table).
- `status`: `proposed`, `confirmed`, `rejected` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, used_by` references `memory_entities`; ON DELETE CASCADE: no; ON UPDATE CASCADE: yes.

## `memory_relations`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `subject_id` | text | no |  | Not yet documented |
| `predicate` | text | no |  | Not yet documented |
| `object_id` | text | no |  | Not yet documented |
| `detail` | text | no |  | Not yet documented |
| `confidence` | double precision | no |  | Not yet documented |
| `status` | text | no |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `mention_ids` | text[] | no |  | Not yet documented |
| `from_session_at` | bigint | yes |  | Not yet documented |
| `to_session_at` | bigint | yes |  | Not yet documented |
| `from_game_time` | bigint | yes |  | Not yet documented |
| `to_game_time` | bigint | yes |  | Not yet documented |
| `secret` | boolean | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `definition_version` | integer | no | `1` | The version of the relationship's definition it was written under (#1189; models.py, `Relation`). |
| `unrevealed` | boolean | no | `false` | Kept from the players until the table has said it (#2404); a backstory connection starts so (models.py, `Relation`). |
| `directional` | boolean | no | `false` | A family connection whose detail is a role that reads one way, written by a DM (#2457; models.py, `Relation`). |
| `decided` | text | yes |  | How a pronoun was settled, in fixed words; the DM's card only, never a player's (#2790; models.py, `Relation`). |

**Allowed values:**

- `status`: `proposed`, `confirmed`, `rejected` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, object_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, subject_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_states`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `entity_id` | text | no |  | Not yet documented |
| `condition` | text | no |  | A thing's condition for a time (models.py, `State`). |
| `from_session_at` | bigint | yes |  | When the condition changed; `State.since` uses this, else `created_at` (models.py, `State`). |
| `source` | text | no |  | Who said so (models.py, `State`). |
| `created_at` | bigint | no |  | Not yet documented |
| `closed_at` | bigint | yes |  | Empty while this is the current condition; a newer one closes it and the history is kept (models.py, `State`). |

**Allowed values:**

- `condition`: `alive`, `dead`, `missing`, `captured`, `transformed`, `unknown`, `intact`, `destroyed`, `lost`, `hinted`, `open`, `active`, `resolved`, `abandoned` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_alias_knowers`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `alias_id` | text | no |  | Not yet documented |
| `perspective_id` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, alias_id` references `memory_aliases`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, perspective_id` references `memory_perspectives`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_attitudes`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `holder_id` | text | no |  | The group or place (`Opinion.holder_id`; models.py, `Opinion`). |
| `toward_id` | text | no |  | The people (`Opinion.target_id`; models.py, `Opinion`). |
| `score` | integer | no |  | Not yet documented |
| `weight` | integer | no |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `from_session_at` | bigint | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `closed_at` | bigint | yes |  | Empty while this is the current one; a newer one for the same pair closes it (models.py, `Opinion`). |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, holder_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, toward_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_beliefs`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `perspective_id` | text | no |  | Not yet documented |
| `relation_id` | text | no |  | Not yet documented |
| `confidence` | double precision | no |  | How sure this perspective is of the connection. At 1.0 (`KNOWN`) it is held as fact (models.py, `Belief.known`, `KNOWN`). |
| `status` | text | no |  | Not yet documented |
| `session_at` | bigint | yes |  | Not yet documented |
| `evidence_ids` | text[] | no | `'{}'::text[]` | The evidence the belief points at, by id, at most 20 (models.py, `Belief`, `EVIDENCE_MAX`). |
| `seq` | bigint | no |  | The row with the highest `seq` for a pair is the current belief; history is kept (models.py, `Belief`). |
| `recorded_at` | bigint | no |  | Not yet documented |
| `pinned` | boolean | no | `false` | The DM's 100% truth pin, only on The truth's belief (#2447; models.py, `Belief`). |

**Allowed values:**

- `status`: `theory`, `accusation`, `revealed_fact`, `false_evidence`, `secret_plot`, `magically_verified`, `evasion_under_magic` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, perspective_id` references `memory_perspectives`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_changes`

**Key** (after `guild_id`, `campaign_id`): `version`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `version` | bigint | no |  | Not yet documented |
| `batch` | bigint | no |  | Not yet documented |
| `table_name` | text | no |  | Not yet documented |
| `row_id` | text | no |  | Not yet documented |
| `op` | text | no |  | Not yet documented |
| `before` | jsonb | yes |  | Not yet documented |
| `after` | jsonb | yes |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `undoes` | bigint | yes |  | Not yet documented |
| `made_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `op`: `insert`, `update`, `delete` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_claims`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `subject` | text | no |  | Not yet documented |
| `relationship` | text | no |  | Not yet documented |
| `object` | text | no |  | Not yet documented |
| `details` | text | no | `''::text` | Not yet documented |
| `how` | text | no |  | How the claim was said: what the DM told the table, what a player said or believes, or what someone means to do; never a fact (models.py, comment above `HOWS`). |
| `lines` | text[] | no |  | Not yet documented |
| `speaker_ids` | bigint[] | no | `'{}'::bigint[]` | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `prompt_version` | text | no |  | Not yet documented |
| `model` | text | no |  | Not yet documented |
| `status` | text | no |  | Its decision, one of `CLAIM_STATUSES` (models.py). |
| `reason` | text | no | `''::text` | Not yet documented |
| `session_started_at` | bigint | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `decided_at` | bigint | yes |  | Not yet documented |
| `fact_id` | text | yes |  | A pointer to the fact the claim became, to follow while that fact is still there; not a database link (_changes.py, comment above `CLAIMS`). |
| `reading` | jsonb | yes |  | A claim that is a belief for the DM to place (#2412); null otherwise (_changes.py, `CLAIMS` default). |

**Allowed values:**

- `how`: `dm_said`, `player_said`, `plan` (CHECK constraint on the table).
- `status`: `proposed`, `confirmed`, `rejected`, `dropped` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_contradictions`

**Key** (after `guild_id`, `campaign_id`): `perspective_id, kind, first_relation_id, second_relation_id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `perspective_id` | text | no |  | Not yet documented |
| `kind` | text | no |  | Not yet documented |
| `first_relation_id` | text | no |  | Not yet documented |
| `second_relation_id` | text | no |  | Not yet documented |
| `grade` | text | no |  | Not yet documented |
| `reason` | text | no | `''::text` | Not yet documented |
| `times_unknown` | boolean | no | `false` | Not yet documented |
| `involves_guess` | boolean | no | `false` | Not yet documented |
| `found_at` | bigint | no |  | Not yet documented |
| `session_started_at` | bigint | yes |  | Not yet documented |
| `said_at` | bigint | yes |  | When a major one was told to the DM on the DM screen, so it is told once (migrations/0238_contradiction_grades.py). |

**Allowed values:**

- `kind`: `contradiction`, `too_many`, `held_twice` (CHECK constraint on the table).
- `grade`: `major`, `minor`, `not_graded` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, first_relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, perspective_id` references `memory_perspectives`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, second_relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_corrections`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `heard` | text | no |  | Not yet documented |
| `heard_key` | text | no |  | Not yet documented |
| `entity_id` | text | yes |  | Not yet documented |
| `action` | text | no |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `action`: `fix`, `keep` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_differences`

**Key** (after `guild_id`, `campaign_id`): `perspective_id, relation_id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `perspective_id` | text | no |  | Not yet documented |
| `relation_id` | text | no |  | Not yet documented |
| `truth_relation_id` | text | yes |  | Not yet documented |
| `kind` | text | no |  | Not yet documented |
| `sort` | text | no |  | Not yet documented |
| `reason` | text | no | `''::text` | Not yet documented |
| `times_unknown` | boolean | no | `false` | Not yet documented |
| `involves_guess` | boolean | no | `false` | Not yet documented |
| `found_at` | bigint | no |  | Not yet documented |
| `session_started_at` | bigint | yes |  | Not yet documented |

**Allowed values:**

- `kind`: `denied`, `clashes` (CHECK constraint on the table).
- `sort`: `disguised`, `misunderstood`, `false`, `not_sorted` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, perspective_id` references `memory_perspectives`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, truth_relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_evidence`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no | `replace((gen_random_uuid())::text, '-'::text, ''::text)` | Not yet documented |
| `relation_id` | text | yes |  | Not yet documented |
| `belief_id` | text | yes |  | Not yet documented |
| `kind` | text | no |  | Not yet documented |
| `target_id` | text | yes |  | Not yet documented |
| `session_started_at` | bigint | yes |  | Not yet documented |
| `added_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `kind`: `claim`, `line`, `clue`, `event` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, belief_id` references `memory_beliefs`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_flags`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `kind` | text | no |  | Not yet documented |
| `relation_id` | text | no |  | Not yet documented |
| `other_id` | text | yes |  | Not yet documented |
| `status` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `kind`: `wrong_subject`, `wrong_object`, `too_many`, `contradiction`, `held_twice`, `place_loop`, `acts_after_end` (CHECK constraint on the table).
- `status`: `open`, `resolved` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, other_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_heard`

**Key** (after `guild_id`, `campaign_id`): `session_started_at, entity_id, speaker_id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `entity_id` | text | no |  | The entry that was named (models.py, `Heard`). |
| `session_started_at` | bigint | no |  | Not yet documented |
| `speaker_id` | bigint | no |  | The speaker whose lines named it (models.py, `Heard`). |
| `times` | integer | no |  | How many of that speaker's lines named the entry in that session, kept at its end, for ranking hints (models.py, `Heard`). |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_impressions`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `viewer_id` | text | no |  | The viewer (`Opinion.holder_id`; models.py, `Opinion`). |
| `subject_id` | text | no |  | The subject (`Opinion.target_id`; models.py, `Opinion`). |
| `score` | integer | no |  | Not yet documented |
| `weight` | integer | no |  | Not yet documented |
| `source` | text | no |  | Not yet documented |
| `from_session_at` | bigint | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `closed_at` | bigint | yes |  | Empty while this is the current one; a newer one for the same pair closes it (models.py, `Opinion`). |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, subject_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, viewer_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_importance`

**Key** (after `guild_id`, `campaign_id`): `seq` (a generated, ever-rising number).

**Logged for Undo:** no (append-only; the newest row is current; not in backups, see `campaigns/tables.py`).

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `seq` | bigint | no | generated | Order of the assessments; the highest for an entry is current. |
| `entity_id` | text | no |  | The character assessed (a live, non-merged character; a merged name counts for the one that stayed). |
| `rank` | smallint | no |  | 0 background, 1 minor, 2 recurring, 3 significant, 4 major, 5 player character (importance_rank.py). Never read below the floor. |
| `basis` | text | no |  | A reason code from a fixed list (importance_rank.py `BASES`); never a word of the story. |
| `source` | text | no |  | `plotbot` (its reading of a session) or `rule`. |
| `session_started_at` | bigint | yes |  | The session the assessment came from. |
| `lines` | integer[] | no | `{}` | At most 20 line numbers of that session. Numbers only, no words. |
| `recorded_at` | bigint | no |  | When it was written. |

Internal: never shown to a player, a card or a recap (#3172).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_knowers`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `perspective_id` | text | no |  | Not yet documented |
| `about_entity_id` | text | yes |  | Not yet documented |
| `state_id` | text | yes |  | Not yet documented |
| `relation_id` | text | yes |  | Not yet documented |
| `alias_id` | text | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `confidence` | double precision | no | `1.0` | How sure that character is, from 0.0 (knows of it and believes it false) to 1.0; no row means the character does not know of it at all (migrations/0286_knowers_confidence.py). |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, about_entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, alias_id` references `memory_aliases`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, perspective_id` references `memory_perspectives`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, relation_id` references `memory_relations`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, state_id` references `memory_states`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_mentions`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `entity_id` | text | no |  | Not yet documented |
| `session_started_at` | bigint | yes |  | Not yet documented |
| `line_ref` | text | no |  | Not yet documented |
| `span_start` | integer | no |  | Not yet documented |
| `span_end` | integer | no |  | Not yet documented |
| `confidence` | double precision | no |  | Not yet documented |
| `method` | text | no |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `method`: `exact`, `sound`, `spelling`, `context`, `dm` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_perspectives`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `kind` | text | no |  | Whose beliefs these are: the truth, the party, or one character (models.py, `Perspective`, `PERSPECTIVE_KINDS`). |
| `entity_id` | text | yes |  | The character a `pc` or `npc` perspective belongs to; empty for the truth and the party (models.py, `Perspective`; the CHECK ties the two). |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `kind`: `truth`, `party`, `pc`, `npc` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_predicates`

**Key** (after `guild_id`, `campaign_id`): `key`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `key` | text | no |  | Not yet documented |
| `parent` | text | yes |  | Not yet documented |
| `label` | text | no |  | Not yet documented |
| `description` | text | no |  | Not yet documented |
| `examples` | text[] | no |  | Not yet documented |
| `reason` | text | no |  | Not yet documented |
| `subject_types` | text[] | no |  | Not yet documented |
| `object_types` | text[] | no |  | Not yet documented |
| `is_symmetric` | boolean | no |  | Not yet documented |
| `max_per_subject` | integer | yes |  | Not yet documented |
| `conflicts_with` | text[] | no |  | Not yet documented |
| `status` | text | no |  | Not yet documented |
| `replaced_by` | text | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |
| `covers` | text[] | no | `'{}'::text[]` | Not yet documented |
| `not_this` | text[] | no | `'{}'::text[]` | Not yet documented |
| `inverse_label` | text | no | `''::text` | Not yet documented |
| `synonyms` | text[] | no | `'{}'::text[]` | Not yet documented |
| `version` | integer | no | `1` | Not yet documented |
| `from_set` | text | yes |  | The set a relationship was borrowed from (#1456), if any (_changes.py, `PREDICATES` default). |

**Allowed values:**

- `status`: `active`, `deprecated` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_report_runs`

**Key** (after `guild_id`, `campaign_id`): `session_at`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `session_at` | bigint | no |  | Not yet documented |
| `state` | text | no |  | Not yet documented |
| `stage` | text | no |  | Not yet documented |
| `done` | integer | no | `0` | Not yet documented |
| `total` | integer | no | `0` | Not yet documented |
| `started_at` | bigint | no |  | Not yet documented |
| `updated_at` | bigint | no |  | Not yet documented |
| `reason` | text | no | `''::text` | Not yet documented |

**Allowed values:**

- `state`: `making`, `ready`, `failed` (CHECK constraint on the table).
- `stage`: `reading`, `grading`, `sorting` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_rule_links`

**Key** (after `guild_id`, `campaign_id`): `id`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `id` | text | no |  | Not yet documented |
| `entity_id` | text | no |  | Not yet documented |
| `kind` | text | no |  | The kind of rules link (models.py, `LINK_KINDS`). |
| `rules_source` | text | no |  | Where the rules come from, such as srd52, srd51 or homebrew; empty when not known (models.py, `RuleLink.rules_source`). |
| `name` | text | no |  | Not yet documented |
| `edition` | text | yes |  | The rules edition, 2014 or 2024, when known; 2014 is shown as [Legacy 2014] (models.py, `RuleLink.edition`). |
| `known` | boolean | no |  | True exactly when `rules_source` is set (CHECK constraint on the table). |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `kind`: `species`, `creature_type`, `stat_block`, `class`, `background` (CHECK constraint on the table).
- `edition`: `2014`, `2024` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `guild_id, campaign_id, entity_id` references `memory_entities`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_types`

**Key** (after `guild_id`, `campaign_id`): `key`.

**Logged for Undo:** yes.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `key` | text | no |  | Not yet documented |
| `parent` | text | no |  | Not yet documented |
| `label` | text | no |  | Not yet documented |
| `description` | text | no |  | Not yet documented |
| `examples` | text[] | no |  | Not yet documented |
| `reason` | text | no |  | Not yet documented |
| `status` | text | no |  | Not yet documented |
| `replaced_by` | text | yes |  | Not yet documented |
| `created_at` | bigint | no |  | Not yet documented |

**Allowed values:**

- `status`: `active`, `deprecated` (CHECK constraint on the table).

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.

## `memory_vectors`

**Key** (after `guild_id`, `campaign_id`): `owner_kind, owner_id`.

**Logged for Undo:** no.

| column | type | null? | default | meaning |
|---|---|---|---|---|
| `owner_kind` | text | no |  | Not yet documented |
| `owner_id` | text | no |  | Not yet documented |
| `line_id` | bigint | yes |  | Not yet documented |
| `model` | text | no |  | Not yet documented |
| `text_hash` | text | no |  | Not yet documented |
| `vector` | real[] | no |  | Not yet documented |
| `made_at` | bigint | no |  | Not yet documented |

**Foreign keys:**

- `campaign_id, guild_id` references `campaigns`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: yes.
- `line_id` references `transcript_lines`; ON DELETE CASCADE: yes; ON UPDATE CASCADE: no.

## Not yet documented

Every column without a meaning above is `Not yet documented`: the source files read were `core/src/dmbot/migrations/*.py`, `core/src/dmbot/memory/_changes.py`, and `core/src/dmbot/memory/models.py`. The meanings given here come from code comments and docstrings in `models.py`, `_changes.py` and the migrations listed in brackets. The store modules that write these tables were not read for this page.
