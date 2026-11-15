# Map key (version 1): the sample Icewind Dale map

What MapBot should read from the owner's sample map (#4423). The map itself is published art and stays on the server only; this key holds names and facts only. The JSON (`map_key.json`) is the source; this page is for reading. Scored by `python -m dmbot.devtools.map_key_check`.

- **Version:** 1 (draft: pins and distances read by eye by Supervisor from the owner's copy; correct them after the first run, in a new version, never by rewriting a scored one)
- **How to read:** What MapBot should read from the sample map (docs/design/mapbot_layers.md, sections 4 to 7). Pins are fractions across and down; a pin counts as found within 0.04 of the key in both directions. Distances are straight lines from the pins and the map's own scale; one counts within 25%. Every answer from this map says 'in a straight line' and names the map.

## The map

- Scale (must find): hex = 1 mile; bar 0 to 20 miles
- North (must find): compass rose, north up
- Legend: black dot = Large Town; white dot = Small Town; red dotted line = Road

## Pins

| Place | Form | Across, down | Must find |
|---|---|---|---|
| Bryn Shander | settlement | 0.324, 0.566 | yes |
| Targos | settlement | 0.293, 0.538 | yes |
| Termalaine | settlement | 0.366, 0.458 | yes |
| Easthaven | settlement | 0.467, 0.591 | yes |
| Lonelywood | settlement | 0.347, 0.437 | yes |
| Bremen | settlement | 0.278, 0.51 | yes |
| Caer-Dineval | settlement | 0.471, 0.515 | yes |
| Caer-Konig | settlement | 0.484, 0.462 | yes |
| Good Mead | settlement | 0.391, 0.625 | yes |
| Dougan's Hole | settlement | 0.353, 0.656 | yes |
| Kelvin's Cairn | mountain | 0.458, 0.395 | yes |
| Maer Dualdon | water | 0.316, 0.468 | yes |
| Lac Dinneshere | water | 0.484, 0.532 | yes |
| Redwaters | water | 0.404, 0.652 | yes |
| Ten-Towns | area | 0.233, 0.605 | yes |
| Icewind Dale | area | 0.5, 0.5 | no |
| Spine of the World | mountain | 0.582, 0.829 | yes |
| Sea of Moving Ice | water | 0.209, 0.272 | yes |
| Reghed Glacier | area | 0.902, 0.165 | yes |
| Dwarven Valley | area | 0.427, 0.475 | no |
| Shaengarne River | water | 0.204, 0.551 | no |
| The Redrun | water | 0.187, 0.734 | no |

## Roads

| From | To | Must find |
|---|---|---|
| Bryn Shander | Targos | yes |
| Bryn Shander | Termalaine | yes |
| Termalaine | Lonelywood | yes |
| Bryn Shander | Easthaven | yes |
| Easthaven | Caer-Dineval | yes |
| Caer-Dineval | Caer-Konig | yes |
| Good Mead | Eastway Road | no |
| Bryn Shander | Spine of the World | no |

## Where places sit

| Place | Is | Of | Must find |
|---|---|---|---|
| Targos | west of | Bryn Shander | yes |
| Termalaine | north of | Bryn Shander | yes |
| Easthaven | east of | Bryn Shander | yes |
| Caer-Konig | north of | Easthaven | yes |
| Kelvin's Cairn | north-east of | Bryn Shander | yes |
| Spine of the World | south of | Ten-Towns | yes |
| Sea of Moving Ice | north-west of | Ten-Towns | yes |
| Reghed Glacier | east of | Ten-Towns | no |
| Lonelywood | on the shore of | Maer Dualdon | yes |
| Termalaine | on the shore of | Maer Dualdon | yes |
| Targos | on the shore of | Maer Dualdon | yes |
| Bremen | on the shore of | Maer Dualdon | no |
| Caer-Konig | on the shore of | Lac Dinneshere | yes |
| Caer-Dineval | on the shore of | Lac Dinneshere | yes |
| Easthaven | on the shore of | Lac Dinneshere | yes |
| Good Mead | on the shore of | Redwaters | no |
| Dougan's Hole | on the shore of | Redwaters | yes |
| Bryn Shander | in | Ten-Towns | no |

## Straight-line distances (within 25%)

| From | To | Miles | Must find |
|---|---|---|---|
| Bryn Shander | Targos | 2.9 | yes |
| Bryn Shander | Easthaven | 11.2 | yes |
| Bryn Shander | Lonelywood | 7.3 | yes |
| Bryn Shander | Kelvin's Cairn | 14.0 | yes |
| Easthaven | Caer-Konig | 7.2 | yes |
| Targos | Kelvin's Cairn | 15.1 | yes |
| Targos | Easthaven | 13.9 | yes |
| Bryn Shander | Good Mead | 6.1 | yes |

## Layer rows

| Place | Layer | Value | Must find |
|---|---|---|---|
| Spine of the World | terrain | mountain | yes |
| Kelvin's Cairn | terrain | mountain | no |
| Reghed Glacier | terrain | arctic | no |

## Agrees with the lore and session keys

- Targos and Bryn Shander: the text says 1 hour (session, line 283); the map about 3 miles in a straight line; agree: no World note
- Targos and Kelvin's Cairn: the text says twenty miles (lore, line 6); the map about 15 miles in a straight line; text and map differ; the text's route length stays, the map answer names the map. Not a World note (that is only for two maps)

## Must not find

- a culture, population or political control row from this map (the map shows none of them outright (mapbot_layers 7.2))
- a climate row from this map (snow drawn on a map is not a stated climate; the lore's 'arctic' row comes from the text)
- weather of any kind (never stored)
- an outline (area feature) read by the AI (areas come only from the DM or an import)
- a place the map doesn't name (Hundelstone, Fireshear, Luskan, Neverwinter, Waterdeep) (not on this map; a pin for one is invented)
- a number of people for any town (population is words only, and the map gives none)
- a second entry for Easthaven, Good Mead or Eastway Road (the map's spelling must match the existing entry (aliases Eashaven, Goodmead, The Eastway))
