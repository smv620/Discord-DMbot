# Design basis: MapBot's maps, positions and layers

**Status:** revision 2, 2026-11-12, on the owner's direction. It replaces revision 1 (merged the
same day, issue #4236). **Revision 2 is not binding until Supervisor approves it.** What changed is
listed in [section 15](#15-what-changed-from-revision-1).

**Who builds it:** GraphEngineer owns the layers and the map reading (they extend the places work
of #4173). CloudDev-lead owns the kept pictures the maps are stored in (`core/src/dmbot/pictures/`).
WebDev builds the website's map page. Each slice says whose it is.

**Read with:** `docs/graph_design_basis.md` (sections 8, 9 and 15), issue #4173 (places, levels,
routes, MapBot), issue #4229 (languages, cultures, biases) and `docs/PLAN.md`, "Images posted
there" and "Pictures are kept, and shared by link".

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [Maps: where they come from, the question, and how they are kept](#3-maps-where-they-come-from-the-question-and-how-they-are-kept)
4. [Positions on a map](#4-positions-on-a-map)
5. [The `map_facts` row](#5-the-map_facts-row)
6. [The layers](#6-the-layers)
7. [Sources, precedence and change](#7-sources-precedence-and-change)
8. [Who sees what](#8-who-sees-what)
9. [What MapBot answers, and how](#9-what-mapbot-answers-and-how)
10. [Map tools: what MapBot uses and what it reads from](#10-map-tools-what-mapbot-uses-and-what-it-reads-from)
11. [Never](#11-never)
12. [Alternatives rejected](#12-alternatives-rejected)
13. [Rules developers must keep](#13-rules-developers-must-keep)
14. [Slice order](#14-slice-order)
15. [What changed from revision 1](#15-what-changed-from-revision-1)
16. [Open questions](#16-open-questions)

---

## 1. Problem and context

MapBot is DMbot's geospatial part. It must:

- **take maps from both places:** a picture posted in Discord, and a map added to the campaign on
  the website;
- **ask, each time a map is found,** whether it is a picture the DM is allowed to use and save.
  DMbot can't know what agreements anyone has with a picture's author, so it relies on the answer
  and does not police it;
- **keep a copy of the map,** so it can work out distances, travel times and directions, and so it
  can bring the map back with a link when the table's talk raises a question the map answers;
- **keep the layers** the owner named: terrain, climate, routes, political control and culture,
  with population lineage beside culture.

The story graph, continuity checks, recaps and travel questions must all read one picture.

**What was already decided:**

- **Places** (#4173): each place has a size level (built, #4186) and a `form` (area, water, route).
  Places nest with `located_in`, touch with `next_to` and are reachable with `route_to` (a means,
  and a `route_state` of known, travelled or planned). The readers keep a "where the story is"
  chain.
- **Map reading** (#4173, slice 6b): MapBot creates or confirms spatial links with `source = 'map'`.
  Precedence is the DM's own word > map > text > assumed.
- **Cultures** (#4229): a per-campaign catalogue beside the graph. Influence, control and
  population live in a MapBot layer, with words and never percentages.
- **Pictures are kept** (owner, 2026-11-02, #3268; `core/src/dmbot/pictures/`,
  `campaign_pictures`): a picture posted in the table's chat is stored in private object storage,
  per campaign, with a permanent link, and is in the backup.
- **Map descriptions** (#2450; `core/src/dmbot/map_notes.py`): a map's layout in words.

**A correction to revision 1.** Revision 1 chose "words only, no coordinates" mainly because it
read `map_notes.py` ("only words are kept, never the picture") as the whole story. That was already
out of date: pictures have been kept since #3268. With the picture kept, and with the owner asking
for distances and directions, words alone are not enough: a distance needs two positions and a
scale. Revision 2 keeps positions.

---

## 2. The decision

1. **A map is a kept picture with a record of its own** (`campaign_maps`). It comes from Discord or
   the website, and it is used as a map only after its uploader says yes to the right-to-use
   question.
2. **Positions are kept per map, never per place.** A place has a pin on each map that shows it
   (`map_features`), in that map's own frame. A place entry never has a coordinate of its own.
3. **Each map has its own scale and north.** Distances and directions are worked out from one map
   at a time, shown as "about", and always say which map and which scale they rest on.
4. **The graph keeps the meaning; the maps keep the measurements.** What is inside what, what
   touches what and what is reachable stay in `located_in`, `next_to` and `route_to`. A map adds
   where things are and how far apart.
5. **One table for all the layers, beside the graph: `map_facts`** (unchanged from revision 1).
6. **The layer is the atlas; the graph is the plot** (unchanged): secrets stay in the graph.
7. **MapBot keeps, reads and marks up maps. It does not paint them.** DMs make maps in the tools
   they already use. MapBot reads any picture, reads the data files of the tools that export data,
   and lets the DM add pins, paths, areas and a scale on the website.

**Why positions per map, and not one set of world coordinates:**

- **Fantasy maps disagree and share no scale.** A world map, a regional map and a town plan can't
  be laid over each other. Forcing one frame would invent precision the maps don't have.
- **The place tree already joins the maps:** the town's pin on the regional map and the town's own
  plan are tied by the same place entry.
- **Every question the owner listed can be answered inside one map:** "how far", "which way" and
  "show me" each pick the most detailed map that shows both places.
- **It stays on stock Postgres** (`docs/graph_design_basis.md`, section 2, item 7): positions are
  plain numbers, and the arithmetic (length, bearing, a point inside an outline) is a few lines of
  Python. No spatial extension is needed.

---

## 3. Maps: where they come from, the question, and how they are kept

### 3.1 Where a map comes from
| Way in | Who | Starts as |
|---|---|---|
| **Posted in the table's chat** in Discord | anyone who agreed to pictures | shown to the table (everyone there saw it) |
| **Sent to DMbot privately** in Discord, or with a command | a DM | DM only |
| **Added on the website,** on the campaign's Maps page | a DM | DM only |
| **Inside an uploaded document** (PLAN, "Pictures in uploaded documents") | a DM | DM only |
| **A data file from a map tool** (section 10) | a DM, on the website | DM only |

Discord and the website stay level: every map appears in both, whichever way it came in.

### 3.2 Finding out that a picture is a map
- **In Discord,** the existing picture read says so (`dmbot.images`, kind `map`).
- **On the Maps page,** the DM says so by adding it there. No detection is needed.
- **In a document,** the page-picture read says so.

### 3.3 The right-to-use question
Every time a map is found, DMbot asks the person who uploaded it, once for that map:

> **Is this a map you're allowed to use and save in this campaign?**
> DMbot can't check who made a picture or what you've agreed with them, so it goes by your answer.
> **[Yes, keep it as a map]** **[No, don't keep it]**

- **Where it is asked:** in Discord, in that person's private chat with DMbot (#3210). On the
  website, in the upload step itself, before anything is sent.
- **Yes:** who said yes and when is recorded on the map. Only then is the map read closely, given
  positions and used for answers.
- **No:** the picture is removed from storage. Only its short transcript line stays, when it was
  posted in the table's chat.
- **Not answered:** it stays an ordinary kept picture under the pictures rules, and is not used as
  a map. The question can be answered later from the picture's page.
- **It is one tap, asked once per map,** never again for the same picture in that campaign (the
  store already knows a picture by its hash).
- **A DM can always remove a map** someone else said yes to.
- **DMbot does no review of its own** and never refuses a map for looking published. The website's
  terms say the same in plain words (the wording needs the owner's sign-off, section 16).
- This replaces, for maps, the campaign-wide Shared story confirmation that `map_notes` needs
  today.

### 3.4 How a map is kept
- **The file** goes where kept pictures go (`campaign_pictures`: private object storage, filed per
  server and campaign, one copy per campaign by hash, a preview, location data stripped). It counts
  toward the campaign's storage.
- **Size:** the full-size file is kept, up to the pictures limit (25 MB, `pictures/prepare.py`).
  The AI is sent a smaller copy (its limit is about 3.75 MB, `images.py`). A dense map is read in
  overlapping parts so small labels stay readable.
- **`campaign_maps`,** one row per map:

| Field | Meaning |
|---|---|
| campaign scope | `guild_id`, `campaign_id` |
| picture | the kept picture it is |
| title | from the map's own title, or the DM's |
| covers | the place entry the map is a map of (Icewind Dale, Targos, the keep). Nullable |
| kind | world, region, settlement, building, battle or other |
| width, height | of the full-size picture, in pixels |
| scale | how much one pixel is, with its unit (feet or miles), and where it came from: `read` (a scale bar or a grid note), `dm` or `import`. Nullable: many maps have none |
| grid | none, square or hex, with the cell's size in pixels and in distance, when the map has one |
| north | the angle of north on the picture, and where it came from: `read` (a compass rose), `dm`, or `assumed` (up) |
| audience | `dm` or `table` (section 8) |
| came from | discord, website, document or import, with the tool's name for an import |
| right to use | who said yes, and when. Empty until answered |
| as of | the story time the map shows, when it says or the DM sets one |
| replaces | an older version of the same map, when the DM uploads a new one |

- **The words description stays** (`map_notes`): it is the map's layout in words, used for search
  and for the AI. It now belongs to its map.
- **Rules:** per campaign with forced row-level security and `ON UPDATE CASCADE`; in
  `campaigns/tables.py`; in the backup (kept pictures already are); deleted with the campaign;
  deleted when its poster deletes their own pictures; never shared between campaigns.

---

## 4. Positions on a map

### 4.1 `map_features`
One row is one thing marked on one map.

| Field | Meaning |
|---|---|
| campaign scope, `id` | as every table |
| map | the `campaign_maps` row |
| shape | `pin` (one point), `path` (a line of points) or `area` (an outline) |
| points | positions on the picture as fractions from 0 to 1 across and down, so they survive a resize |
| what it marks | **at most one of:** a place entry, a `route_to` statement, or a `map_facts` row (an area for a layer). None means a label DMbot read but hasn't matched to a name |
| label | the words as written on the map |
| `status` | `read`, `confirmed` or `rejected` |
| `source` | `read` (the AI), `dm` (placed on the website) or `import` (from a tool's data file) |

- **A place can have a pin on many maps.** The same place entry ties them.
- **A place entry never has a coordinate.** Positions are always "on this map".
- **Deleting a map deletes its features.** Merging two places moves their pins to the kept entry.
- Every write goes through `memory_changes`, so Undo works.

### 4.2 Where positions come from
| Source | What it gives | How good |
|---|---|---|
| **The AI's reading of the picture** | a pin for each labelled place; the scale bar, grid note and compass when shown | pins are close enough for "which way" and for a straight-line distance; never trusted for outlines or the bends of a road |
| **The DM, on the website's map page** | drags a pin, traces a road, draws an area, sets the scale by clicking two points and typing the distance between them | exact, and it wins |
| **A map tool's data file** (section 10) | pins, routes, outlines and the scale exactly as the tool has them | exact |

- **A path the AI didn't trace is a straight line between two pins,** and every answer from it says
  "in a straight line". A road's real length needs the DM's traced path, an import, or a length
  stated in the story.
- **Areas come only from the DM or an import.** The AI never draws an outline.

### 4.3 Scale and north
- **No scale, no distance.** DMbot says so and offers the fix: "This map has no scale. Tell me one
  distance on it and I can work out the rest."
- **A grid is a scale:** "1 hex = 6 miles" and the hex size give it directly.
- **North is assumed to be up** unless the map shows a compass or the DM sets it. An answer that
  rests on the assumption says "on the map, …".

### 4.4 More than one map
- **Which map answers:** the most detailed map (the smallest `covers`) that has both places and a
  scale. With no scale on that one, the next larger map that has both.
- **Two places on no shared map:** DMbot says no map shows both, and falls back to stated route
  lengths (section 9).
- **Maps that disagree** are never averaged. The answer names the map it used. A large difference
  between two maps for the same pair is a World note for the DM.
- **A newer version of a map** (`replaces`) is used in place of the old one, and the old one's
  DM-placed pins are offered for carrying over.

---

## 5. The `map_facts` row

One row is one fact about one place (or one route) in one layer. This is unchanged from
revision 1 except for the pointer to the map.

| Field | Meaning |
|---|---|
| campaign scope, `id` | as every table |
| target | **exactly one of:** a place entry, or a `route_to` statement (only for layer `condition`, and only a route that exists, never a travelled or planned leg). A named route is a place (`form = route`) |
| `layer` | `terrain`, `climate`, `control`, `culture`, `population` or `condition` |
| value | **exactly one of:** a word from that layer's fixed list (with the text's own words for "other"), a faction entry, a catalogue culture, or a catalogue lineage |
| `how` | one word from that layer's short list: the role or the share |
| `part` | optional free words when it covers only part of the place: "the northern half". An `area` feature on a map can show the same part as an outline |
| story time | `from_session_at`, `to_session_at` and game minutes, as connections carry them. No start means "since before the story" |
| `status` | `read`, `confirmed` or `rejected` |
| `source` | the highest source that has stated it: `dm`, `map` or `text` |
| `map_id` | the map that gave or confirmed it. Nullable. (Revision 1 called this `map_note_id`; #4173's map links use the same pointer) |
| `also_text` | true when the text also stated it (section 7.5) |
| `table_knows` | true when the table has heard or seen it (section 8) |

**Storage rules:** per campaign, forced row-level security, `ON UPDATE CASCADE`, in
`campaigns/tables.py`, in the backup, every write through `memory_changes`. Merging two places
moves their rows; deleting a place, faction or catalogue entry deletes the rows on it. One open row
per target, layer, value and part.

---

## 6. The layers

| Layer | Target | Value | `how` words | A different value means |
|---|---|---|---|---|
| **Terrain** | a place with `form = area` | arctic, coastal, desert, forest, grassland, hill, mountain, swamp, underdark, urban, waterborne, or "other" with words | mostly, partly | a **correction** |
| **Climate** | a place at region level or larger | arctic, cold, temperate, warm, hot, with an optional dry or wet, or "other" with words | none | **history** |
| **Route condition** | a route place or a `route_to` statement | open, hard going, closed, gone, or "other" with words | none | **history** |
| **Political control** | a place | a **faction entry** | rules, claims, occupies, protects | **history** |
| **Culture** | a place (often a district) | a catalogue culture | strong, some, traces, once | **history** |
| **Population** | a place | a catalogue lineage | almost all, most, many, some, few | **history** |

**Routes are not a layer.** They are `route_to` links and route places in the graph (#4173). A
route has:

- **a stated length** (part of #4173's slice 6d): the length as the source gave it, in its own
  words ("two days by cart"), with an amount and unit when the words give one;
- **a measured length,** never stored: worked out from a traced path on a map when one exists;
- **a condition,** the layer above. `route_state` stays about journeys.

**Rules for each layer:**

- **Terrain:** the word list is the SRD 5.2.1's Travel Terrain table, already in the rules index
  (`core/src/dmbot/rules/data/srd52/toolbox.json`). A named feature is a place, never a terrain
  row: the Spine of the World is a place whose terrain is "mountain"; a lake is `form = water`.
- **Climate:** it can start at a point in the story (an unnatural winter). Seasons are not stored:
  they come from the campaign's calendar (`core/src/dmbot/timebot/calendar.py`) when it has one.
  **Weather is never stored and DMbot never makes any.**
- **Political control:** a realm is two things joined by a control row (the territory is a place,
  the crown is a faction). A border between two realms is `next_to` with "borders". A second open
  "rules" row goes to World notes and is never an alert. A faction's seat is not control. Secret
  control is never a layer row.
- **Culture:** "once" is for what a people left behind. A culture row never makes a `people` entry.
- **Population:** words only, never a number or a percentage.

**Reading up the tree:** terrain, climate and control are read up the place tree and marked with
where they came from ("arctic (from Icewind Dale)"). Culture, population and condition are not.
Climate is never read across the plane level. Nothing read up is stored.

---

## 7. Sources, precedence and change

### 7.1 Precedence
The DM's own word > a map > the text (lore and session talk) > a value read up the tree. Inside
"a map": a tool's data file and a DM-placed feature > the AI's reading of a picture.

### 7.2 How each source writes
- **The DM** sets a layer row on the place's card, and a position on the website's map page.
  Both are `confirmed`.
- **The text readers** write layer rows as `read`, only from set phrases beside a named place:

  | Phrase shape | Layer |
  |---|---|
  | "an elven city", "the dwarven quarter" | **culture** only |
  | "mostly halflings", "populated by", "home to many" | **population** |
  | "ruled by", "held by", "under the banner of", "occupied by" | **control** |
  | "the frozen north", "a desert kingdom" | **terrain** or **climate**, by the word |
  | "the pass is snowed in", "the bridge is out" | **condition** |

- **A map read by the AI** gives pins, the scale and north, the spatial links of #4173, and a layer
  row only when the map shows the fact outright (a legend, a labelled border, a colour key, a
  terrain symbol). Not sure means nothing written.
- **A tool's data file** gives everything it holds, as `read` for the DM's review, in one batch
  with one Undo.

### 7.3 When a new statement disagrees with an open layer row
| Layer | The new statement is | Result |
|---|---|---|
| terrain | from a higher source | **correction:** the old row becomes `rejected`; the DM gets the private note with Undo |
| any other | words of change ("fell to", "was abandoned") | **history:** the old row closes at that story time and the new one opens |
| any other | from a higher source, with no words of change | **correction** |
| any | from the same or a lower source, or against the DM's own row | **nothing changes;** both go to World notes |

None of this raises an alert or a question during play.

### 7.4 Maps are snapshots
- A map's rows start at the story time the map was shown, unless the map states a date or the DM
  sets one.
- **A map never ends anything by leaving it out.** "Not on the map" is never "not there".
- One map's changes are one grouped private note, with counts and one Undo.

### 7.5 When a map is deleted
A map goes when its poster deletes their own pictures, when a DM removes it, or when the answer to
the right-to-use question is "No". In the same transaction:

- its features (pins, paths, areas) are deleted;
- **a `read` layer row or link that rests only on that map is deleted** (`source = map`, that map,
  `also_text` false);
- **a `read` row the text also stated stays,** as a text row;
- **a `confirmed` row stays,** as the DM's word, with the map pointer set to NULL;
- a test covers each case.

A map dropped only to make room in storage is different: its rows stay and lose the pointer
(Supervisor, on #4240).

### 7.6 World notes
New guesses and unsettled disagreements are listed after the session in "World notes" (#4229),
grouped by layer, each with ✅ / ✏️ / 🚫. Logs hold counts only.

---

## 8. Who sees what

**The DM sees every map, feature and layer row.**

### 8.1 A map's audience
| Audience | Who may open the picture and its link |
|---|---|
| `dm` | a signed-in DM of that campaign only |
| `table` | whoever may read that campaign's transcripts |

- **A map posted in the table's chat starts as `table`.** Every other map starts as `dm`.
- **"Show to the table"** changes a `dm` map to `table`, after one warning: "Players will see
  everything drawn on this map." A DM who has a players' version uploads that one as its own map.
- **A `dm` map's link is never public,** whatever the pictures default is, and is only ever posted
  on the DM screen or to the DM privately. It still asks for a DM's sign-in, so a player peeking at
  the DM screen can't open it.
- **Showing a map to the table makes the names written on it known to the party,** as a picture
  posted in the table's chat already does.

### 8.2 Features
- A feature DMbot **read from the picture** is seen by whoever may see the map: it is on the
  picture anyway.
- A feature the **DM added or a file imported** is an overlay. A player sees it only when the map
  is `table` **and** the place it marks is shown to that player. A secret lair pinned on a public
  map stays hidden.

### 8.3 Layer rows
A player sees a layer row only when all three hold: the place is shown to them, what the row
points at is shown to them, and the row is marked `table_knows`. `table_knows` starts true for
what was said at the table or shown on a `table` map, and false for everything else. The DM can
flip it.

### 8.4 Answers
- **An answer for a player uses only `table` maps and what that player can see.** A route or a
  distance must never hint at a hidden place.
- Every reader of `campaign_maps`, `map_features` and `map_facts` goes through one function that
  applies this section, with a CI test like `test_relation_readers.py`.

---

## 9. What MapBot answers, and how

Every answer is short, says "about", names the map it used, and carries the map's link.

| Question | How it is answered | Example |
|---|---|---|
| **How far is it?** | 1. a length the DM set; 2. a length stated in the story; 3. the length of a traced path on a map; 4. a straight line between two pins on a map with a scale | "About 40 miles in a straight line, on the Icewind Dale map. 🗺️ link" |
| **Which way is it?** | the bearing between two pins and the map's north, as one of eight directions | "North-east of Targos, on the map." |
| **How long will it take?** | the distance above, and the SRD's Travel Pace and Travel Terrain entries, cited. It shows its working and is an estimate for the DM | "About 40 miles. SRD Travel Pace, p. 192: … so about 2 days at a Normal pace. Your call." |
| **Where is it?** / **Show me** | the most detailed map with a pin for it, opened at that pin; in Discord, a cut-out of the map around the pin | "Here on the Ten-Towns map. 🗺️ link" |
| **What's near?** / **What's on the way?** | pins close to a place, or along a path | "On the way: Bremen, then Termalaine." |
| **What is it like there?** | the layer rows, read up the tree | "Mountain · arctic (from Icewind Dale)" |

**Rules for answers:**

- **Measured is never exact.** Numbers are rounded, and a straight line is called a straight line.
- **A stated length beats a measured one.** When they differ a lot, the DM gets a World note after
  the session, never a correction during play.
- **Travel time is for the DM.** The SRD's travel entries are DM-side (`Entry.dm_side`), so a
  player's table question about travel time gets "That's one for your DM." A player may ask how
  far and which way, from `table` maps.
- **No map, or no scale:** DMbot says what is missing and what would fix it. It never guesses.

**Bringing a map back during play:**

- **When asked** ("Hey DMbot, how far is Bryn Shander?"): the answer goes where table questions and
  the DM sidebar already go, with the link.
- **Unasked, on the DM screen only:** when the table's talk asks a where, how far or which way
  question about a place that has a pin, DMbot posts one line with the map's link. It is quiet
  about the same pair of places for the rest of the session, as the live heads-up is
  (`memory/live_heads_up.py`). Code decides this from the words and the pins; it costs no AI call.
- **The link** opens the website's map page at that spot. In Discord it shows a small cut-out.

**After the session** (as in revision 1): a plausibility note ("reached Bryn Shander in an hour;
the map makes it about 40 miles"), a weather note against climate and season, recap colour from
`table_knows` rows, and layer rows for first meetings (#4229).

---

## 10. Map tools: what MapBot uses and what it reads from

### 10.1 The recommendation
**MapBot is a map keeper, not a map painter.** DMs already make maps in tools they like, and the
good ones took years to build. MapBot:

1. **reads any picture,** whatever made it;
2. **reads the data file of tools that export one,** which gives exact positions and often whole
   layers;
3. **lets the DM mark up a map on the website:** pins, paths, areas, the scale and north;
4. **never makes a map picture with AI,** because that would invent geography.

### 10.2 What MapBot itself is built with
| Need | Choice | Why |
|---|---|---|
| The website's map page: pan, zoom, pins, paths, areas | **Leaflet** with its flat image mode (`CRS.Simple` and an image overlay), plus a drawing add-on such as Leaflet-Geoman | Small, open source, made for pictures that aren't real-world maps, and its drawing add-ons give pins, lines and outlines without writing an editor |
| Cut-outs and previews for Discord | **Pillow,** already a dependency of `core` | A crop with a marker needs nothing new |
| Storing positions | plain numbers in Postgres | Stock Postgres, no extension |
| The arithmetic | a small pure-Python module | Length, bearing and point-in-outline are a few lines, and testable without a database |

Looked at and not chosen: **OpenSeadragon** with an annotation add-on (better for huge pictures
served as tiles; worth revisiting only if 25 MB maps feel slow), **OpenLayers** and **MapLibre**
(made for real-world maps; heavier for the same result), **PostGIS** (an extension self-hosters may
not be able to install, for arithmetic this small).

### 10.3 Tools DMs make maps in
| Tool | What it is | What MapBot can take from it | Fit |
|---|---|---|---|
| **Azgaar's Fantasy Map Generator** | free, open-source world and region generator that runs in the browser | a full data export (JSON), GeoJSON of routes, rivers, markers and zones, a list of towns, and pictures. It models states, cultures, biomes, towns and routes with a scale | **The best fit.** One file fills pins, routes with real lengths, and the control, culture and terrain layers, all for the DM's review. It has no service to call, so it is a file the DM uploads, and uploads again after changes |
| **Dungeondraft** (and other tools that write Universal VTT files) | building and battle-map painter | a Universal VTT file: the picture plus the grid's size in pixels, walls and doors | **Good for local maps:** the grid gives an exact scale with no question to the DM |
| **Watabou's generators** (cities, villages, dungeons) | free generators in the browser | a picture, or a data export (GeoJSON) of streets, buildings and walls | **Good for towns:** districts and streets with positions. Its licence wasn't confirmed, so MapBot only reads what the DM exports |
| **Inkarnate** | the most used map painter, in the browser | pictures only (up to very large sizes). No service for other programs was found | The picture path, which every DM can use |
| **Wonderdraft** | desktop world-map painter | pictures only | The picture path |
| **Kanka** | campaign manager with maps and markers | it documents a service with map markers: a position, a shape (pin, circle, outline) and the entry it belongs to | **A later import:** pins already tied to names. It needs the DM's own key, kept like other customer keys |
| **World Anvil, LegendKeeper** | campaign managers with pinned maps | World Anvil has a service, but map access through it wasn't confirmed. LegendKeeper has a JSON export that includes maps, and no public service yet | Later, and only from the DM's own export |
| **Foundry VTT, Roll20** | virtual tabletops | Foundry scenes hold a grid distance, units and map notes, but only add-ons inside Foundry can reach them. Roll20 reads Universal VTT files | Later. Foundry would need a DMbot add-on of its own |

**Order of integration:** any picture first; then Azgaar's export; then Universal VTT; then
Watabou's export; then Kanka. Each is its own slice, and each import is `read` until the DM
checks it.

**What an import must respect:**
- it is the DM's own file, added on the website, after the same right-to-use question;
- it stays in that one campaign;
- a second import of the same world matches by the tool's own ids and by name, and lists what
  changed for the DM instead of overwriting DM-set rows;
- names from a generator are proposals like any other name, and never crowd out the story's own.

### 10.4 A sketch when there is no map
For a campaign with no map at all, MapBot can later draw a plain sketch from the graph: places as
dots, routes as lines, labelled "a sketch, not to scale". It is built last, and it never becomes a
source of positions.

---

## 11. Never

- **Never make up** a position, a scale, weather, a border, a population, a travel time or a ruler.
  A missing value is "not known", said plainly, with what would fix it.
- **Never present a measurement as exact,** and never without the map it came from.
- **Never put a coordinate on a place entry,** and never merge positions across maps.
- **Never use a picture as a map before its uploader said yes** to the right-to-use question.
- **Never judge who owns a picture.** DMbot asks, records the answer and acts on it.
- **Never make a map picture with AI.**
- **Never show a player a `dm` map,** its link, or anything worked out from it.
- **Never infer a layer from who was met or who won a fight.**
- **Never numbers or percentages** for culture or population.
- **Never ask a story question during play.** The right-to-use question is the one exception: it is
  one tap, in private, about a file and not about the story.
- **Never a built-in gazetteer or built-in maps of a published setting,** and never one campaign's
  maps or layers used for another.
- **Never post any of it to players as a ruling.**

---

## 12. Alternatives rejected

| Alternative | Why not |
|---|---|
| **Words only, no positions** (revision 1) | It can't give a distance or a direction, which the owner asks for, and its main reason (no picture kept) was already untrue |
| **One set of world coordinates for every place** | Maps disagree and most have no scale; it invents precision. Per-map positions answer the same questions honestly |
| **A spatial database extension** | Self-hosters on stock Postgres; the arithmetic is small |
| **Trusting the AI for outlines and road bends** | A wrong outline looks more certain than a wrong word. Pins are close enough; outlines come from the DM or a file |
| **Building a map painter** | Years of work that others have done well, and not what DMbot is for |
| **Making maps with an image model** | It invents geography: DMbot never invents story |
| **One right-to-use yes per campaign** | The owner asked for the question when a map is found. A yes for one picture says nothing about the next |
| **Keeping maps outside the pictures store** | A second store means a second set of deletion, backup, storage and link rules |
| **Calling map tools' services live** | Most have none. A file the DM uploads works for all of them and keeps keys out of it |
| **Six layer tables; layer rows as graph statements; values as JSON on the place; a knower list per layer row; copying values down the tree; one conflict rule for all layers; road condition in `route_state`** | As in revision 1: each either multiplies the work, pollutes the graph, loses history or leaks |

---

## 13. Rules developers must keep

1. **Positions live on a map's features.** No coordinate on a place entry, and nothing measured is
   stored: distances, bearings and travel times are worked out when asked.
2. **A layer row never states containment, adjacency or reachability.**
3. **Nothing is treated as a map before the right-to-use yes,** and who said yes and when is on the
   map.
4. **One reader function** for maps, features and layer rows applies section 8, with a CI test.
5. **A `dm` map's link always needs a DM's sign-in.**
6. **Vocabularies are fixed lists in code,** each with "other" plus words, and the read report
   counts `unknown` per layer.
7. **A reader or an import writes `read`, never `confirmed`.** Only the DM confirms.
8. **Every write goes through the change log,** and one map, one upload or one import is one Undo.
9. **The measuring module is pure Python,** tested without a database or a network.
10. **Every wait shows progress** (reading a map, an import): stage, how far, how long, and what
    to do if it stops.
11. **Reading a map is metered AI use,** with the cost shown before a large or many-part read.
12. **Per-campaign isolation, the backup and `ON UPDATE CASCADE`** for every new table, and maps
    deleted by the rule in section 7.5 with its tests.
13. **Discord and the website stay level,** and `docs/discord-web-parity.md` is updated each slice.
    Placing and tracing on a map is website-only on purpose (Discord has no way to point at a
    picture), and the PR says so.
14. **Plain words for users.** The DM never sees "feature", "layer", "calibration" or a status
    word. The ux-critic checks each slice's words before code.
15. **Each slice adds sample keys and is scored:** pins found and how far off, layer rows found and
    wrong, distances against the key, and false notes.

---

## 14. Slice order

Each slice is its own PR with tests, sample keys and a migration number claimed on #2475.

| # | Slice | Whose | Needs | What the DM gets |
|---|---|---|---|---|
| 1 | **Route length** as stated words on `route_to` and route places | GraphEngineer | part of #4173 slice 6d | "two days by cart" on the route |
| 2 | **`map_facts` with terrain, climate and control:** the table, the set-phrase readers, the DM's edit, the card line, the players' rule, World notes | GraphEngineer, WebDev | place levels (built) | the "where are we" line on every place |
| 3 | **Maps are kept as maps:** `campaign_maps`, the right-to-use question in Discord and on the website, the Maps page with upload, the DM-only audience and its link rule, "Show to the table" | CloudDev-lead (store), WebDev (page), GraphEngineer (the record) | the kept pictures (built) | every map in one place, with a link, from Discord or the website |
| 4 | **Positions:** `map_features`, the AI's pins, scale and north from the map read of #4173 slice 6b, and the measuring module | GraphEngineer | slice 3; #4173 slice 6b | "how far", "which way" and "where is it", with the link |
| 5 | **The map page becomes a workbench:** drag a pin, trace a road, set the scale with two clicks, draw an area | WebDev | slice 4 | exact distances along roads |
| 6 | **Culture and population** | GraphEngineer, WebDev | #4229's catalogue | "strong dwarven culture" on a place |
| 7 | **Route condition** | GraphEngineer | slice 1 | "closed" on the pass, with its history |
| 8 | **Layers from maps:** what a map shows outright, as layer rows, with the snapshot and deletion rules | GraphEngineer | slices 2 and 4 | layers filled from a posted map |
| 9 | **Bringing a map back during play:** the unasked DM-screen line and the Discord cut-out | GraphEngineer | slice 4 | the right map at the right moment |
| 10 | **Travel time, and the after-session notes** (plausibility, weather), then recap colour | GraphEngineer | slices 4 and 7; recap scored as `docs/RECAP_STANDARD.md` requires | estimates and notes |
| 11 | **Imports, one tool per PR:** Azgaar, then Universal VTT, then Watabou, then Kanka | GraphEngineer, WebDev | slices 4 and 8 | a whole world in one upload |
| 12 | **The sketch from the graph** | WebDev | slice 4 | a picture where there is no map |

Slices 1 and 2 are unchanged from revision 1 and need not wait. Slice 3 is the first new one, and
it is useful alone: the DM gets a Maps page and links before any position exists.

---

## 15. What changed from revision 1

| Revision 1 | Revision 2 | Why |
|---|---|---|
| Topology only; no coordinates anywhere except rough positions on a map note | Positions per map (`map_features`), with a scale and north per map | The owner: MapBot must resolve distances, travel times and directions |
| "DMbot never keeps the picture" | A map is a kept picture with its own record | The owner; and pictures were already kept (#3268), which revision 1 missed |
| Maps come from the table's chat | Maps come from Discord or the website, and from documents and tool files | The owner |
| The campaign's Shared story confirmation covers maps | A right-to-use question for every map, answered by its uploader | The owner |
| Layer rows point at a map note | Layer rows, links and features point at the map | The map is now the thing kept |
| The travel answer only adds up stated lengths in one unit (Supervisor's narrowing on #4240) | DMbot also measures from a map and estimates travel time, showing its working | The owner. A stated length still wins |
| Deletion rule keyed on the map note | The same rule, keyed on the map, and also for a DM's removal and a "No" | The map is now the thing deleted |
| No tools named | Section 10 | The owner asked for the investigation |

Unchanged: `map_facts` and its layers, "the atlas is not the plot", reading up the tree,
correction against history, World notes, the players' three-part rule for layer rows, and slices 1
and 2.

---

## 16. Open questions

1. **The terms wording** that DMbot relies on the uploader's answer and does no review of its own
   is a legal sign-off: for the owner (`needs: owner`).
2. **A player's yes.** As written, whoever uploads answers, so a player who posts a map in the
   table's chat answers for it, and a DM can remove it. If only a DM's yes should count, a player's
   map waits for a DM. For the owner.
3. **The unasked DM-screen line** (slice 9): on by default, or a setting the DM turns on? Proposed:
   on, with one tap to turn it off for the campaign. For Supervisor.
4. **Storage.** Maps are large and DMs will want them kept for good. Proposed: a map with a yes is
   marked permanent, so the rolling removal of old pictures never takes it, and the plan's storage
   limit is the only cap. For Supervisor, with the plan rules.
5. **A players' version of a DM's map.** Revision 2 treats it as a separate map. Tying the two so a
   pin placed on one appears on the other is left for after slice 5.
6. **Maps inside uploaded documents:** whether each becomes a map with its own question, or the
   document's one confirmation covers them. Proposed: the document's confirmation covers them,
   since the DM answered for the whole file. For Supervisor.
7. **The catalogue's ids** (#4229) fix the final shape of the culture and lineage values.
8. **Facts not confirmed in this investigation:** Azgaar's licence text (its documentation says
   MIT), whether World Anvil's service reaches maps, and Watabou's licence. Each import slice
   checks its tool's current terms before building.
