# Design basis: MapBot's map layers

**Status:** ruled by Supervisor on 2026-11-12 (issue #4236: the Expert's design note and the ruling
under it). Once this file is merged it is binding for whoever builds the layers: a design note or PR
that departs from it says so and needs Supervisor's ruling.

**Who builds it:** GraphEngineer owns the layers (they extend the places work of #4173) and files
one build issue per slice. WebDev builds the website half of each slice that has a page.

**Read with:** `docs/graph_design_basis.md` (sections 8, 9 and 15), issue #4173 (places, levels,
routes, MapBot) and issue #4229 (languages, cultures, biases).

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [The `map_facts` row](#3-the-map_facts-row)
4. [The layers](#4-the-layers)
5. [Sources, precedence and change](#5-sources-precedence-and-change)
6. [Who sees a layer row](#6-who-sees-a-layer-row)
7. [What the layers are used for](#7-what-the-layers-are-used-for)
8. [Never](#8-never)
9. [Alternatives rejected](#9-alternatives-rejected)
10. [Rules developers must keep](#10-rules-developers-must-keep)
11. [Out of scope](#11-out-of-scope)
12. [Slice order](#12-slice-order)
13. [Open questions](#13-open-questions)

---

## 1. Problem and context

MapBot will read the DM's maps and keep spatial knowledge of the campaign's world. The owner named
the kinds of map it needs: **terrain, climate, routes, political control and culture**, with
**population lineage** beside culture. The story graph, continuity checks, recaps and travel
questions must all read one consistent picture.

**What was already decided before this design:**

- **Places** (#4173): each place has a size level (built, #4186) and a `form` (area, water, route).
  Places nest with `located_in`, touch with `next_to` and are reachable with `route_to` (a means,
  and a `route_state` of known, travelled or planned). The readers keep a "where the story is"
  chain and give new places an assumed parent.
- **Maps** (#4173, slice 6b): MapBot creates or confirms spatial links with `source = 'map'`.
  Precedence is the DM's own word > map > text > assumed. A map correction is a private note to the
  DM with Undo.
- **Cultures** (#4229): cultures and lineages live in a per-campaign catalogue beside the graph.
  Cultural influence, political control and population lineage live in a "MapBot layer", off the
  place tree, with words and never percentages. Political control points at a faction entry.
  Biases live in `memory_attitudes`.
- **Guesses** (the owner on #4229): DMbot makes its best guess, the DM reviews it after the
  session, and nothing is asked during play.
- **Map notes** (#2450; `core/src/dmbot/map_notes.py`): when a map is posted in the table's chat,
  DMbot keeps a description of it in words. It never keeps the picture.

---

## 2. The decision

1. **Topology with words, no geometry.** Every layer fact hangs on a place entry, or on a route for
   its condition. There are no polygons and no coordinates on place entries.
2. **One table for all the layers, beside the graph: `map_facts`.** A row says: this place, in this
   layer, has this value, in this way, from this point in the story, according to this source. It is
   the "MapBot layer" of #4229. It does not need MapBot: the text readers and the DM fill it first.
3. **The layer is the atlas; the graph is the plot.** A layer row holds only what a map or gazetteer
   of that world could show. Anything secret stays a graph statement with knowers and beliefs.
4. **Nothing in a layer repeats the place tree or the routes.** Containment, adjacency and
   reachability live only in `located_in`, `next_to` and `route_to`.
5. **Values are read up the place tree, never copied down.**

**Why, in order of weight:**

- **There is no picture to hang geometry on.** `map_notes` keeps words only, and keeping published
  maps would run into the IP rule. A coordinate only means something on an image DMbot doesn't keep.
- **What the AI reads reliably from a map is relations** (inside, next to, apart). An outline of a
  hand-drawn region is not reliable, and a wrong outline looks more certain than a wrong word.
- **Fantasy maps share no scale or projection.** A world map, a regional map and a town plan can't
  be laid over each other. The place tree already joins them.
- **Every use needs lengths along routes and "what is this place inside",** never areas.
- **One table, because everything around the value is the same for every layer:** precedence, Undo,
  the after-session review, the backup section, isolation and the players' view.
- **Secrets stay in the graph because the graph already has the gate** (`memory/gate.py`, knower
  lists, beliefs per point of view). A second, weaker secrecy model in the layer would leak.

---

## 3. The `map_facts` row

One row is one fact about one place (or one route) in one layer.

| Field | Meaning |
|---|---|
| campaign scope | `guild_id` and `campaign_id`, as every memory table |
| `id` | the row's id |
| target | **exactly one of:** a place entry, or a `route_to` statement. A statement is allowed only for layer `condition`, and only a route that exists (`route_state` known), never a travelled or planned leg. A named route is a place (`form = route`) and is targeted as a place |
| `layer` | `terrain`, `climate`, `control`, `culture`, `population` or `condition` |
| value | **exactly one of:** a word from that layer's fixed list (with the text's own words when the word is "other"), a faction entry, a catalogue culture, or a catalogue lineage. Which one is fixed by the layer (section 4) |
| `how` | one word from that layer's short list: the role or the share. Empty where the layer has none |
| `part` | optional free words when the fact covers only part of the place: "the northern half", "along the coast", "the old quarter" |
| story time | `from_session_at`, `to_session_at` and game minutes, exactly as connections carry them (`docs/graph_design_basis.md`, section 8). No start means "since before the story" |
| `status` | `read` (DMbot's guess, not yet checked), `confirmed` (the DM checked or set it) or `rejected` (it was wrong) |
| `source` | the highest source that has stated it: `dm`, `map` or `text` |
| `map_note_id` | the map note that gave or confirmed it. Nullable |
| `also_text` | true when the text also stated it. It decides what "rests only on a map note" means (section 5.5) |
| `table_knows` | true when the table has heard or seen it (section 6) |
| `created_at`, `closed_at` | when DMbot wrote the row and when it stopped being current |

**Storage rules:**

- **Per campaign:** forced row-level security, composite foreign keys that carry `guild_id` with
  `ON UPDATE CASCADE`, and an entry in `campaigns/tables.py`.
- **In the backup.** The table cannot be rebuilt from `map_notes`: it also holds what the text gave
  and what the DM set, and a campaign keeps at most 200 map notes (`map_notes.py`, `KEEP`).
- **Every write goes through `memory_changes`,** so Undo and the audit work as for connections.
- **Merging two place entries** moves their rows to the kept entry. Two open rows that then say the
  same thing become one, keeping the higher source.
- **Deleting a place, faction or catalogue entry** deletes the rows that hang on it or point at it.
- **One open row per target, layer, value and part.** The same fact stated again confirms the row.
  It never adds a second one.

---

## 4. The layers

| Layer | Target | Value | `how` words | A different value means |
|---|---|---|---|---|
| **Terrain** | a place with `form = area` | a fixed word: arctic, coastal, desert, forest, grassland, hill, mountain, swamp, underdark, urban, waterborne, or "other" with words | mostly, partly | a **correction** |
| **Climate** | a place at region level or larger | a fixed word: arctic, cold, temperate, warm, hot, with an optional second word dry or wet, or "other" with words | none | **history** |
| **Route condition** | a route place or a `route_to` statement | a fixed word: open, hard going, closed, gone, or "other" with words | none | **history** |
| **Political control** | a place | a **faction entry** | rules, claims, occupies, protects | **history** |
| **Culture** | a place (often a district) | a catalogue culture | strong, some, traces, once | **history** |
| **Population** | a place | a catalogue lineage | almost all, most, many, some, few | **history** |

**Routes are not a layer.** They are `route_to` links and route places in the graph, as designed on
#4173. This design adds two things to them:

- **A stated length** (part of #4173's slice 6d): a `route_to` link or a route place keeps its
  length as the source gave it, in the source's words ("two days by cart"), with an optional amount
  and unit (hours, days, miles) when the words give one. It is never measured from a picture.
- **The condition layer** above. `route_state` stays about journeys (known, travelled, planned).
  Whether a road can be used is its condition. Keeping them apart stops "the party took the pass in
  session 3" from fighting "the pass is closed now".

**Rules for each layer:**

- **Terrain**
  - The word list is the SRD 5.2.1's Travel Terrain table, already in the rules index
    (`core/src/dmbot/rules/data/srd52/toolbox.json`), so a terrain word finds its rules entry.
  - **A named feature is a place, never a terrain row.** The Spine of the World is a place whose
    terrain is "mountain". A lake is `form = water` and a road is `form = route`: neither is a
    terrain value. A terrain row with `part` words covers an unnamed stretch ("forest in the south").
  - Geography doesn't change in the story. A burned forest is an event in the graph, and a new row
    only when the DM wants one.
- **Climate**
  - It can start at a point in the story: an unnatural winter that begins mid-campaign is a climate
    row with a start.
  - **Seasons are not stored.** A season comes from the campaign's calendar
    (`core/src/dmbot/timebot/calendar.py`) when it has one. With no calendar there is no season.
  - **Weather is never stored and DMbot never makes any.** Weather is what the DM says at the table.
- **Political control**
  - **A realm is two things joined by a control row:** the territory is a place and the crown is a
    faction. Never put a place `located_in` a faction, and never use a control row to say what a
    place is inside.
  - **A border between two realms is `next_to` with the word "borders"** between the two places. The
    layer stores no borders.
  - At most one open "rules" row per place is expected. A second open one is never refused and never
    an alert: it goes to World notes. Several "claims" rows are normal (a disputed land).
  - **A faction's seat is not control.** "The Zhentarim are based in…" stays the graph's
    `located_in`.
  - **Secret control is never a layer row** (section 6).
- **Culture**
  - "once" is for what a people left behind: a ruin built by a people long gone.
  - A culture enters the catalogue only when it is named as one (#4229). A culture row never makes
    a `people` entry.
- **Population**
  - Words only, from the list. Never a number or a percentage.

**Reading up the tree:**

- **Terrain, climate and control are read up the place tree.** A place with no row of its own shows
  the nearest larger place's value, marked with where it came from: "arctic (from Icewind Dale)". A
  row on the place itself always wins.
- **Culture, population and condition are not read up.** A dwarven hold inside an elven kingdom is
  not "mostly elves". They show only on the place or route that has the row.
- **Climate is never read across the plane level.** Another plane has its own or none.
- **Nothing read up is ever stored.**

---

## 5. Sources, precedence and change

### 5.1 Precedence
The DM's own word > a map > the text (lore and session talk) > a value read up the tree. This is
#4173's order, unchanged.

### 5.2 How each source writes
- **The DM** sets or changes a row on the place's card, in Discord and on the website. The row is
  `confirmed` with `source = dm`.
- **The text readers** write a row only from set phrases beside a named place, as `read`:

  | Phrase shape | Layer |
  |---|---|
  | "an elven city", "the dwarven quarter" | **culture** only (the weakest claim) |
  | "mostly halflings", "populated by", "home to many" | **population** |
  | "ruled by", "held by", "under the banner of", "occupied by" | **control** |
  | "the frozen north", "a desert kingdom", "the marsh town of" | **terrain** or **climate**, by the word |
  | "the pass is snowed in", "the bridge is out" | **condition** |

  One phrase fills one layer, never three. A phrase that points at a faction or culture DMbot
  doesn't have yet proposes that name in the usual way and writes the row only once it exists.
- **A map** (slice 5) gives a layer row only when it shows the fact outright: a legend, a labelled
  border, a named colour key, a terrain symbol. MapBot never works out control from a banner drawn
  near a town. Not sure means nothing is written.

### 5.3 When a new statement disagrees with an open row
| Layer | The new statement is | Result |
|---|---|---|
| terrain | from a higher source | **correction:** the old row becomes `rejected`, the new one is written, and the DM gets the private note with Undo |
| any other layer | words of change ("fell to", "was abandoned", "has been snowed in since") | **history:** the old row closes at that story time and the new one opens |
| any other layer | from a higher source, with no words of change | **correction**, as for terrain |
| any layer | from the same or a lower source | **nothing changes.** The new statement is kept as a `read` row and both go to World notes |
| any layer | against a row the DM set | **nothing changes.** It goes to World notes |

- A correction or a change is never silent: it goes through the change log and into the DM's
  private note or World notes.
- None of this raises an alert or a question during play.

### 5.4 Maps are snapshots
- A map's rows start at the story time the map was shown, unless the map states a date.
- **A map never ends a row by leaving something out.** "Not on the map" is never "not there".
- One map's changes are one grouped private note, with counts and one Undo for the whole map
  (the #4173 pattern).

### 5.5 When a map note is deleted (Supervisor's ruling on #4236, point f)
A person who deletes their own lines also deletes the map notes made from their posts. When that
happens, in the same transaction:

- **A `read` row that rests only on that map note is deleted** (`source = map`, that note, and
  `also_text` false). A guess that came only from a person's post doesn't outlive the post.
- **A `read` row the text also stated stays,** as a text row: `source = text`, no map note.
- **A `confirmed` row stays,** as the DM's word, with `map_note_id` set to NULL
  (`ON DELETE SET NULL`).
- A test covers all three.

This is the same line as readings and claims in `docs/STORY_MEMORY.md`, section 9.

### 5.6 World notes
- New guesses and unsettled disagreements are listed in the after-session "World notes" (#4229),
  grouped by layer, each with ✅ / ✏️ / 🚫.
- ✅ makes a row `confirmed`. 🚫 makes it `rejected`. ✏️ opens the same edit as the place's card.
- Logs hold counts only.

---

## 6. Who sees a layer row

**The DM sees every row.**

**A player sees a row only when all three hold:**
1. the place (or both ends of the route) is shown to that player;
2. what the row points at is shown to that player (the faction; a catalogue culture or lineage
   counts as shown);
3. the row is marked `table_knows`.

**`table_knows` starts as:**

| Where the row came from | Starts as |
|---|---|
| said at the table in a session | true |
| a map posted in the table's chat (everyone there saw it) | true |
| the DM's uploaded lore or prep, or a map inside it | false |
| the DM's own edit | false |

The DM can flip it in one tap. Players never see the words "read", "map" or "table knows".

**A value read up the tree** is shown to a player only when the row it comes from passes the three
tests, and the place it comes from is shown to them.

**Secrets:** a layer row has no knower list. So anything that only some characters know, or that
is hidden behind a public face ("the Zhentarim really run Targos"), is a graph statement and never
a layer row. The public ruler is the layer row.

**Routes:** an answer about a route for a player is worked out over only the places and routes that
player can see. A path must never hint at a hidden waypoint.

---

## 7. What the layers are used for

In the order they are built (slice 6, except the first):

- **The place's card** (slice 2): one line for the DM, for example "Mountain · arctic (from Icewind
  Dale) · ruled by Ten-Towns".
- **Travel questions:** DMbot lists the route between two places leg by leg, with each leg's stated
  length and condition, and says which legs have no stated length. It adds the lengths up only when
  every leg is stated in the same unit. For pace it cites the SRD's Travel Pace and Travel Terrain
  entries. Those are DM-side (`Entry.dm_side`), so a player's table question gets "That's one for
  your DM."
- **Recap colour:** the recap writer may be given the terrain, climate and ruler of where the story
  was, as facts it can use and the fact check can support. Only rows with `table_knows`.
- **Plausibility, after the session:** "The party reached Bryn Shander in an hour; the road was
  given as half a day." A private World note in the style of `memory/continuity.py`: the level is
  set by a rule, nothing runs during play, and the DM chooses.
- **Weather continuity, after the session:** weather said at the table against the climate and,
  when the calendar gives one, the season. It is worded as a check, never a correction, because
  magic exists. It runs only when the innermost open place of "where the story is" is a district or
  larger (not indoors).
- **First meetings** (#4229): a place's own culture and population rows help pick which attitudes
  apply.

---

## 8. Never

- **Never make up** weather, borders, populations, travel times or a ruler. A missing value is "not
  known", said plainly.
- **Never infer a layer from who was met** ("three dwarves in the tavern" is not a dwarven town) **or
  from who won a fight.** A conquest is the DM's to state; DMbot proposes it in World notes.
- **Never numbers or percentages** for culture or population.
- **Never ask during play.**
- **Never a built-in gazetteer of a published setting,** and never one campaign's layers used for
  another, even for the same world. Only the SRD's own vocabulary is shared.
- **Never post any of it to players as a ruling.**
- **Never keep the picture,** and never put a coordinate on a place entry.

---

## 9. Alternatives rejected

| Alternative | Why not |
|---|---|
| **Polygons and coordinates now** | No picture is kept; outlines read by an AI aren't reliable; maps share no scale; no use needs areas |
| **A grid or hex model** | Most maps have none, and a published adventure's own hexes are published content |
| **Six tables, one per layer** | Six copies of precedence, Undo, review, backup, isolation and the players' rule to build and keep level |
| **Layer rows as graph statements** | Culture and terrain would need vocabulary nodes, drawn on every Story Graph. The owner ruled cultures out of the graph on #4229 |
| **Values as JSON on the place entry** | No source, no history and no Undo for one value |
| **A full knower list on each layer row** | It would copy the graph's gate for facts that are public by nature. Secrets go to the graph |
| **Copying a parent's value down to its children** | It goes stale the moment the parent changes |
| **One conflict rule for every layer** | Terrain is corrected and control has history. One rule either loses a conquest or invents one |
| **Putting a road's condition in `route_state`** | It mixes "can it be used now" with "the party took it" |
| **Waiting for MapBot before building any layer** | The text and the DM can fill terrain, climate and control now, and the card line is useful at once |

---

## 10. Rules developers must keep

1. **A layer row never states containment, adjacency or reachability.** Those are `located_in`,
   `next_to` and `route_to`.
2. **Every reader of `map_facts` goes through one function** that applies section 6, with a CI test
   like `test_relation_readers.py`, so a new reader can't skip it.
3. **Vocabularies are fixed lists in code,** each with "other" plus the text's words. The read
   report counts `unknown` per layer, as it does for place levels.
4. **A reader writes `read`, never `confirmed`.** Only the DM confirms.
5. **Every write goes through the change log,** and one map's or one upload's rows are one Undo.
6. **Nothing read up the tree is stored.**
7. **Per-campaign isolation, the backup section and `ON UPDATE CASCADE`** as in section 3.
8. **The map-note deletion rule** of section 5.5, with its test.
9. **Discord and the website stay level:** each slice with something the DM sees ships both halves,
   or files the other half, and updates `docs/discord-web-parity.md`.
10. **Plain words for users.** The DM never sees "layer", "topology", `map_facts` or a status word.
    The ux-critic checks each slice's words before code.
11. **Each slice adds sample keys and is scored per layer:** rows found, wrong rows and false notes.
    A slice that makes any of those worse needs a stated reason.

---

## 11. Out of scope

- **Drawing maps.** This design stores what is known. Drawing is a later MapBot feature and reads
  the same rows.
- **The culture catalogue, languages and biases:** designed on #4229.
- **The place tree, `next_to`, `route_to`, the story chain and assumed parents:** designed on #4173.
  Only the stated length is added here.
- **Encounter tables, foraging and navigation checks.** The SRD entries are cited; DMbot rolls and
  decides nothing.
- **Economy, trade goods, resources and religion as layers.** Each would be a new `layer` value
  with its own word list, and each needs a ruling first.

**Kept open for geometry, and nothing more:** the map read of slice 5 keeps, on the map note only,
a rough position for each labelled place on that one map (two numbers from 0 to 1) and the map's
scale words and north when it shows them. They are never put on a place entry and never merged
across maps. Nothing reads them yet.

---

## 12. Slice order

Each slice is its own PR with tests, sample keys and a migration number claimed on #2475.

| # | Slice | Needs | What the DM gets |
|---|---|---|---|
| 1 | **Route length** as stated words, on `route_to` and route places | part of #4173 slice 6d | "two days by cart" on the route |
| 2 | **`map_facts` with terrain, climate and control:** the table, the set-phrase readers writing `read`, the DM's edit on the place card (Discord and website), the card line read up the tree, the players' rule and its CI test, the backup section, World notes | place levels (built, #4186). It does not wait for MapBot | the "where are we" line on every place |
| 3 | **Culture and population** | #4229's catalogue | "strong dwarven culture", "mostly halflings" on a place |
| 4 | **Route condition** | slice 1 and #4173 slice 6d | "closed" on the pass, with its history |
| 5 | **MapBot reads layers from maps:** the structured map read of #4173 slice 6b also gives what the map shows outright, with `source = map`, the snapshot rule, the deletion rule and the rough positions | #4173 slice 6b | layers filled from a posted map, one note, one Undo |
| 6 | **Uses, in this order:** the travel answer, recap colour, then the after-session plausibility and weather notes | slices 1 to 4; the recap change is scored as `docs/RECAP_STANDARD.md` requires | answers and notes |

The plausibility and weather notes come last because they are the likeliest to raise false alarms.
They ship only with their false-note count on the sample.

---

## 13. Open questions

1. **A map note dropped because the campaign passed 200 notes.** That isn't a person deleting their
   words. Proposed: the rows stay and only lose the pointer. For Supervisor.
2. **Maps inside the DM's uploaded documents** (PLAN, "Pictures in uploaded documents"). They would
   give rows that start with `table_knows` false. Whether they make map notes at all, or write
   rows straight from the document read, is for GraphEngineer to propose at slice 5.
3. **The catalogue's ids.** The value columns for culture and lineage take their final shape when
   #4229's catalogue is built. Slice 2 must not guess them.
4. **Adding up a journey.** The DM sidebar "does no arithmetic" for encounter budgets today (PLAN,
   Combat Encounters). The travel answer adds stated lengths only when every leg has the same unit.
   Supervisor confirms or narrows this before slice 6.
5. **The words on the place card and in World notes** for each layer and each `how` word. The
   ux-critic settles them slice by slice.
