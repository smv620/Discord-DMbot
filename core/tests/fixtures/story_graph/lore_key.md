# Lore answer key (sample campaign)

What the graph should hold after the sample lore document is read: [Icewind Dale Background & Premise](https://docs.google.com/document/d/1zbt7g3r6uFfiSYr3fl_gyaxARhwJPizauI0jmiEa2aw/edit?usp=sharing) (about 2,200 words). The player characters and many other names are invented for testing; some towns, gods and non-player characters come from a published adventure (names only, never its text). The players agreed to its use as a public example. The document is linked in the [README](README.md), not copied here. **The line numbers** are lines of the document's plain-text export (blank lines count), so you can find each source.

**Test players** (made-up usernames):

| Player character | Username |
|---|---|
| Cerric Vanya'elen | `player_ist` |
| Belleros Stormgrace | `player_jac` |
| Okthandom Korran | `player_eri` |
| Claude Ambere | `player_owe` |

**Tiers:** `stated` the words are in the text · `inferred` only implied, offered as a suggestion and never extracted · `by_rule` made by a rule, not read. **Must find: no** means a good find if present, not a miss if absent. An `inferred` tie marked must-find is scored on whether DMbot *offers* it to the DM. **Current: no** is history: it was true and is not now.

**Totals:** 89 entries, 122 connections (80 must-find), 19 events.

## Connections

### Family (16)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l001 | Roceran | parent_of | Cerric Vanya'elen | stated | yes | yes | 18 |  |
| l002 | Shafina | parent_of | Cerric Vanya'elen | stated | yes | yes | 18 |  |
| l003 | Roceran | parent_of | Valyra | inferred | no | yes | 18 | 'his younger sister, Valyra': the same parents |
| l004 | Shafina | parent_of | Valyra | inferred | no | yes | 18 |  |
| l005 | Cerric Vanya'elen | sibling | Valyra | stated | yes | yes | 18 | younger sister |
| l006 | Cerric's paternal grandfather | grandparent_of | Cerric Vanya'elen | stated | yes | yes | 24 | died in a severe storm |
| l007 | Qinric | sibling | Roceran | stated | yes | yes | 24 | 'Roceran's brother Qinric'; the owner asked for this tie directly |
| l008 | Qinric | uncle_of | Cerric Vanya'elen | inferred | yes | yes | 24 | never said as 'uncle': Roceran's brother |
| l031 | Okthandom Korran | sibling | Vaelrith | stated | yes | yes | 33 | elder brother, vanished at 105 |
| l032 | Okthandom Korran | sibling | Nyssara | stated | yes | yes | 33 | younger sister; a player character's family must be kept |
| l033 | Vaelrith | sibling | Nyssara | inferred | no | yes | 33 |  |
| l034 | Okthandom Korran | parent_of | Okthandom's child | stated | no | yes | 36 | 'his child', named once in passing |
| l039 | Lee | parent_of | Claude Ambere | stated | yes | yes | 48 | 'Griz, son of Lee'; the owner flagged this as missing |
| l082 | Brewner | parent_of | Brewner's daughter | stated | no | yes | 73 |  |
| l093 | Bruni Stonewake | sibling | Thorin | stated | yes | yes | 84 | 'Thorin, his miner brother Bruni' |
| l115 | Garret Velryn | spouse | Keegan Velryn | stated | yes | yes | 102 | 'whose husband' |

### Teaching and work (19)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l009 | Qinric | member | Lord's Alliance | stated | yes | yes | 24 | a war mage evoker in it |
| l010 | Valyra | apprentice_of | Kelfir Ama'Kiir | stated | yes | yes | 18 |  |
| l011 | Cerric Vanya'elen | works_for | Syllar Sparkwalker | stated | yes | yes | 24 | Keeper of the Sparkwalker Spire under him |
| l012 | Cerric Vanya'elen | leads | Sparkwalker Spire | stated | yes | yes | 24 | Keeper of it (leads; works_in is also true and kept as optional, Supervisor 2026-11-04) |
| l015 | Syllar Sparkwalker | mentor_of | Emmerich | inferred | no | yes | 24 | Cerric's fellow apprentices |
| l016 | Syllar Sparkwalker | mentor_of | Wit | inferred | no | yes | 24 |  |
| l037 | Belleros Stormgrace | member | Explorer's League | stated | yes | yes | 42 | 'a scout associated with the Explorer's League' |
| l069 | The party | works_for | Hlin Trollbane | stated | yes | yes | 59 | 100 gold to capture or eliminate Kaltro (line 73) |
| l071 | Sephek Kaltro | works_for | Torrga Icevein | stated | yes | yes | 63 | her bodyguard (also line 61) |
| l072 | Torrga Icevein | leads | Torg's Caravan | stated | yes | yes | 63 | under her own heading on line 62 |
| l073 | Sephek Kaltro | member | Torg's Caravan | inferred | no | yes | 61 | accompanies it |
| l075 | Speaker Masthew | leads | Termalaine | stated | yes | yes | 65 |  |
| l076 | The party | works_for | Speaker Masthew | stated | yes | yes | 84 | 50 gold, then 250 for the mine (line 93) |
| l083 | Cerric Vanya'elen | member | The party | stated | yes | yes | 73 | 'our four adventurers' |
| l084 | Belleros Stormgrace | member | The party | stated | yes | yes | 73 | 'our four adventurers' |
| l085 | Okthandom Korran | member | The party | stated | yes | yes | 73 | 'our four adventurers' |
| l086 | Claude Ambere | member | The party | stated | yes | yes | 73 | 'our four adventurers' |
| l116 | Garret Velryn | member | Explorer's League | stated | yes | yes | 102 | 'a noted member' |
| l118 | The party | works_for | Keegan Velryn | stated | yes | yes | 102 | 50 gold and a route map to find Garret |

### Feelings and opposition (15)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l017 | Emmerich | rival_of | Cerric Vanya'elen | stated | yes | yes | 24 | 'rival apprentices Emmerich and Wit'; the owner flagged backstory rivals as missing |
| l018 | Wit | rival_of | Cerric Vanya'elen | stated | no | yes | 24 |  |
| l028 | Syllar Sparkwalker | knows | Danneth | inferred | no | yes | 27 | the note is signed S |
| l040 | Claude Ambere | held_captive_by | The circus | stated | yes | no | 48 | before he escaped |
| l070 | Hlin Trollbane | enemy_of | Sephek Kaltro | inferred | no | yes | 59 | she hires against him; she is not his servant (#3256) |
| l081 | Belleros Stormgrace | knows (wary) | Brewner | stated | no | yes | 73 | a heated debate |
| l087 | Cerric Vanya'elen | knows | Belleros Stormgrace | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l088 | Cerric Vanya'elen | knows | Okthandom Korran | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l089 | Cerric Vanya'elen | knows | Claude Ambere | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l090 | Belleros Stormgrace | knows | Okthandom Korran | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l091 | Belleros Stormgrace | knows | Claude Ambere | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l092 | Okthandom Korran | knows | Claude Ambere | by_rule | yes | yes | 73 | player characters who play together know each other (#3036) |
| l098 | Okthandom Korran | enemy_of | Trex | stated | no | yes | 90 | killed him with an arrow |
| l109 | Ice troll | enemy_of | The minotaur captain | stated | no | yes | 96 | devoured him and shattered his ship |
| l111 | Belleros Stormgrace | knows (friendly) | Boy | stated | no | yes | 102 | befriended him |

### Things (16)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l023 | Cerric Vanya'elen | carries | Azure tome | stated | yes | yes | 27 | grabbed his master's locked tome |
| l024 | Syllar Sparkwalker | owns | Azure tome | stated | yes | yes | 27 | 'his master's' |
| l029 | Cerric Vanya'elen | owns | Glyph | stated | yes | yes | 27 | snowy owl familiar |
| l036 | Okthandom Korran | owns | Ancestral journal | inferred | no | yes | 33 | found the curse in it |
| l041 | Claude Ambere | found | Wolf figurine | inferred | yes | no | 48 | the owner: Claude found it first (line 48: he carries a unique wolf figurine; line 96: he sells it). Offered to the DM, never extracted |
| l077 | Veron Braig | owns | The Blue Clam | stated | yes | yes | 67 |  |
| l097 | Trex | carries | Penolopi | stated | no | yes | 90 | the plant was in his backpack |
| l099 | Cerric Vanya'elen | carries | Wand of Conducting | stated | no | yes | 90 | Okthandom found it and gave it to him |
| l100 | The party | owns | Lantern of Greater Tracking | stated | no | yes | 90 | found in the mine |
| l101 | The party | owns | Chardalyn spear | stated | no | yes | 90 | spoils of the mine |
| l102 | The party | owns | Heart of chardalyn | stated | no | yes | 90 | hidden under the bucket lift |
| l103 | Cerric Vanya'elen | carries | Psi crystal | inferred | yes | yes | 93 | studied it; the party took it from the skull |
| l106 | Belleros Stormgrace | owns | Wolf figurine | stated | yes | yes | 96 | bought it from Claude for 75 gold |
| l107 | Belleros Stormgrace | carries | Wolf figurine | inferred | yes | yes | 96 | the owner: he owns it and carries it now; the text says only that he bought it |
| l108 | Fizzlebomb | owns | Aurora's Wind | stated | yes | yes | 96 | 'Fizzlebomb's vessel'; the owner flagged this as missing |
| l114 | Garret Velryn | owns | Boy | inferred | yes | yes | 102 | Boy led the party to Keegan's home: the owner says he is Garret's dog. The text never says it; offer it to the DM, never extract it |

### Places and other ties (53)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l013 | Cerric Vanya'elen | works_in | Sparkwalker Spire | stated | no | yes | 24 | Keeper of it |
| l014 | Syllar Sparkwalker | works_in | Blackstaff Tower | inferred | no | yes | 24 | 'Syllar Sparkwalker of Blackstaff Tower' |
| l019 | Cerric Vanya'elen | comes_from | Waterdeep | stated | yes | yes | 18 | born in the Temple of the Seldarine |
| l020 | Temple of the Seldarine | in | Waterdeep | stated | yes | yes | 18 |  |
| l021 | Cerric Vanya'elen | lived_in | Castle Ward | stated | no | no | 18 | raised in a mansion there |
| l022 | Castle Ward | in | Waterdeep | inferred | no | yes | 18 |  |
| l025 | Cerric Vanya'elen | last_seen_in | Neverwinter | stated | no | yes | 27 | fled there by teleportation rune |
| l026 | Cerric Vanya'elen | last_seen_in | Icewind Dale | stated | no | yes | 27 | journeyed north into it |
| l027 | Danneth | last_seen_in | Easthaven | inferred | no | yes | 27 | the note is addressed 'Danneth, Easthaven' |
| l030 | Easthaven | in | Icewind Dale | stated | yes | yes | 27 |  |
| l035 | The wind spirit | associate | Okthandom Korran | stated | yes | yes | 36 | guides and guards him |
| l038 | Claude Ambere | comes_from | Spine of the World | stated | yes | yes | 48 |  |
| l042 | Auril the Frostmaiden | lives_in | Icewind Dale | stated | yes | yes | 57 | withdrawn there to live among mortals |
| l043 | Ten-Towns | associate | Auril the Frostmaiden | stated | no | yes | 96 | its towns sacrifice every ten-day to appease her |
| l044 | Targos | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l045 | Bryn Shander | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l046 | Easthaven | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l047 | Termalaine | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l048 | Bremen | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l049 | Good Mead | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l050 | Caer-Dineval | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l051 | Caer-Konig | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l052 | Lonelywood | in | Ten-Towns | stated | yes | yes | 96 | listed with its sacrifice (also line 7) |
| l053 | Ten-Towns | in | Icewind Dale | stated | yes | yes | 63 |  |
| l054 | Sacrifice of souls | in | Targos | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l055 | Sacrifice of souls | in | Bryn Shander | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l056 | Sacrifice of souls | in | Easthaven | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l057 | Sacrifice of souls | associate | Auril the Frostmaiden | stated | yes | yes | 96 | to appease her |
| l058 | Sacrifice of warmth | in | Termalaine | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l059 | Sacrifice of warmth | in | Bremen | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l060 | Sacrifice of warmth | in | Good Mead | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l061 | Sacrifice of warmth | associate | Auril the Frostmaiden | stated | yes | yes | 96 | to appease her |
| l062 | Sacrifice of food | in | Caer-Dineval | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l063 | Sacrifice of food | in | Caer-Konig | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l064 | Sacrifice of food | in | Lonelywood | stated | yes | yes | 7 | held there every ten-day (line 96) |
| l065 | Sacrifice of food | associate | Auril the Frostmaiden | stated | yes | yes | 96 | to appease her |
| l066 | Auril the Frostmaiden | associate | The Everlasting Rime | stated | yes | yes | 57 | casts it each night before midnight (line 4) |
| l067 | The Everlasting Rime | in | Icewind Dale | stated | yes | yes | 4 | holds the Ten-Towns in a frozen vice |
| l068 | Hlin Trollbane | works_in | Lonelywood | stated | yes | yes | 59 | operates out of it |
| l074 | Sephek Kaltro | last_seen_in | Easthaven | stated | no | yes | 102 | his last known location |
| l078 | Veron Braig | works_in | The Blue Clam | stated | yes | yes | 67 | owner and proprietor |
| l079 | The Blue Clam | in | Termalaine | stated | yes | yes | 67 |  |
| l080 | The Lucky Liar | in | Lonelywood | stated | yes | yes | 73 |  |
| l094 | Bruni Stonewake | works_in | The gemstone mine | stated | no | yes | 84 | a miner |
| l095 | The gemstone mine | in | Termalaine | inferred | no | yes | 84 |  |
| l096 | Trex | associate | The ghost | stated | no | yes | 90 | a possessed ghost emerged when he fell |
| l104 | Psi crystal | was_kept_in | The gemstone mine | inferred | yes | no | 90 | found there in the mind flayer's skull, then taken: the place is history |
| l105 | Psi crystal | associate | Mind flayer | stated | no | yes | 90 | from its petrified skull |
| l110 | Ice troll | last_seen_in | Maer Dualdon | stated | no | yes | 96 |  |
| l112 | Boy | last_seen_in | Three Flags Sailing | stated | no | yes | 102 |  |
| l113 | Three Flags Sailing | in | Targos | inferred | no | yes | 102 |  |
| l117 | Garret Velryn | last_seen_in | Kelvin's Cairn | stated | no | yes | 102 | vanished five days ago leading an expedition up it |
| l119 | Jebidiah | works_in | Targos | inferred | no | yes | 102 | a kennel master the party met there |
| l120 | Cerric Vanya'elen | comes_from | Temple of the Seldarine | stated | no | yes | 18 | born in the Temple of the Seldarine (the more exact place; l019 says the city) |
| l121 | Syllar Sparkwalker | mentor_of | Cerric Vanya'elen | stated | no | yes | 24 | served as Keeper of the Sparkwalker Spire "under Master Syllar Sparkwalker", "alongside rival apprentices Emmerich and Wit": an apprentice of his (l011 says works_for) |
| l122 | Brewner | knows (wary) | Belleros Stormgrace | stated | no | yes | 73 | the other side of the heated debate on mithril smelting (l081 is Belleros toward Brewner) |
| l123 | Okthandom Korran | associate | Icewind Dale | stated | no | yes | 36 | 'hunts across Icewind Dale searching for answers': where he roams now (#3545, step 11b) |
| l124 | Belleros Stormgrace | leads | Aurora's Wind | stated | no | yes | 99 | 'Belleros took command of the vessel and ran the ship ashore' (#3545, step 11b) |
| l125 | Okthandom Korran | enemy_of | Fizzlebomb | stated | no | yes | 99 | pushed Fizzlebomb overboard into the freezing waters, at Cerric's urging (#3545, step 11b) |
| l126 | Cerric Vanya'elen | enemy_of | Fizzlebomb | stated | no | yes | 99 | 'Cerric to convince Okthandom to push Fizzlebomb overboard' (#3545, step 11b) |

## Events

| id | what happened | who | line |
|---|---|---|---|
| e01 | Master Sparkwalker disappears, Wit comes back fatally wounded, and Cerric flees to Neverwinter with the tome | Cerric, Syllar Sparkwalker, Wit | 27 |
| e02 | Vaelrith vanishes at 105, leaving a bloodstained campsite | Okthandom, Vaelrith | 33 |
| e03 | Claude erases his own memory, is held by a circus and escapes | Claude | 48 |
| e04 | Hlin Trollbane offers the party a contract on Sephek Kaltro in The Lucky Liar | the party, Hlin Trollbane, Sephek Kaltro | 73 |
| e05 | The party fights six ice kobolds and evades a yeti on the road to Termalaine | the party | 81 |
| e06 | Rumours reach the party that an evil wizard captured in Easthaven will be burned at the stake | the party | 84 |
| e07 | The grell paralyzes Claude and destroys Glyph, then the party kills it | Claude, Cerric, the party | 90 |
| e08 | Okthandom kills Trex with an arrow, and Claude burns Penolopi to banish the ghost | Okthandom, Trex, Claude, Cerric | 90 |
| e09 | The party secures the spoils of the mine, including a psi crystal and a heart of chardalyn | the party | 90 |
| e10 | Speaker Masthew pays a bounty of 250 gold for clearing the mine | the party, Speaker Masthew | 93 |
| e11 | Claude sells the wolf figurine to Belleros for 75 gold | Claude, Belleros | 96 |
| e12 | An ice troll devours the first ship's captain and shatters his ship | ice troll, the minotaur captain | 96 |
| e13 | Cerric talks Okthandom into pushing Fizzlebomb overboard; Belleros runs the Aurora's Wind ashore | Cerric, Okthandom, Belleros, Fizzlebomb | 99 |
| e14 | Three owlbears ambush the party near Targos | the party | 99 |
| e15 | Belleros befriends Boy, who leads the party to Keegan Velryn; she asks them to find Garret | Belleros, Boy, Keegan Velryn, Garret Velryn | 102 |
| e16 | Every ten-day Targos, Bryn Shander and Easthaven sacrifice humanoid souls, spellcasters first, to appease Auril | Targos, Bryn Shander, Easthaven, Auril | 7 |
| e17 | Every ten-day Termalaine, Bremen and Good Mead forsake all warmth in a Sacrifice of Warmth | Termalaine, Bremen, Good Mead, Auril | 7 |
| e18 | Every ten-day Caer-Dineval, Caer-Konig and Lonelywood fast and surrender their food to the beasts of the tundra | Caer-Dineval, Caer-Konig, Lonelywood, Auril | 7 |
| e19 | Each night before midnight Auril casts the spell that keeps the sun from rising: the Everlasting Rime | Auril | 57 |

## Entries

### People (35)

| name | also called | note |
|---|---|---|
| Cerric Vanya'elen | Cerric, Norro Fa'ertenya, Norro, Spellweave | Player character. Norro is his old name. |
| Belleros Stormgrace | Belleros | Player character. |
| Okthandom Korran | Okthandom | Player character. |
| Claude Ambere | Claude, Griz, Major Ursa | Player character. His first name was Griz, son of Lee. |
| Roceran |  | Cerric's father, an explorer and wanderer |
| Shafina |  | Cerric's mother, a magical weaver |
| Valyra |  | Cerric's younger sister, a sorcerer |
| Kelfir Ama'Kiir |  | elven mage Valyra serves |
| Qinric |  | Roceran's brother, a war mage |
| Syllar Sparkwalker | Master Sparkwalker | Cerric's master |
| Emmerich |  | a rival apprentice |
| Wit |  | a rival apprentice, returns fatally wounded |
| Danneth |  | named in the note in the tome |
| Vaelrith |  | Okthandom's elder brother, vanished |
| Nyssara |  | Okthandom's younger sister |
| Lee |  | Claude's father |
| Hlin Trollbane |  | retired bounty hunter in Lonelywood |
| Sephek Kaltro |  | bodyguard the party is hired to catch |
| Torrga Icevein |  | dwarf merchant leading Torg's Caravan |
| Speaker Masthew |  | Town Speaker of Termalaine |
| Veron Braig |  | half-orc owner of The Blue Clam |
| Brewner |  | dwarf who argued mithril smelting with Belleros |
| Thorin |  | worker at the gemstone mine, Bruni's brother |
| Bruni Stonewake |  | miner |
| Grek |  | surrendered kobold, then killed |
| Trex |  | kobold leader, killed by Okthandom |
| Fizzlebomb |  | owner of the ship Aurora's Wind |
| Keegan Velryn |  | Garret's wife, hires the party |
| Garret Velryn |  | missing explorer |
| Jebidiah |  | kennel master |
| Cerric's paternal grandfather |  | Unnamed. Died in a storm. |
| Okthandom's child | Okthandom Korran's child (name unknown) | Unnamed. Mentioned once. |
| Brewner's daughter | Brewner's daughter (name unknown) | Unnamed. Leads Brewner home. |
| The minotaur captain | Minotaur captain of the first ship, Minotaur captain | Unnamed. Captain of the first ship. |
| The beggar woman | beggar woman | Unnamed. Gives a prophecy. |

### Creatures (8)

| name | also called | note |
|---|---|---|
| Glyph |  | Cerric's snowy owl familiar, destroyed by the grell |
| Boy |  | sled dog Belleros befriended |
| The ghost |  | Unnamed possessed ghost that emerged when Trex fell |
| Penolopi |  | pet plant the party burned |
| The wind spirit | ancient wind spirit, wind spirit, ancient spirit | Unnamed. The ancient wind spirit that guides and guards Okthandom (lore line 36); kept as a being the story never names, found by its description ('unnamed - ancient wind spirit'), never as a name. |
| Yeti |  | evaded on the road |
| Ice troll |  | ate the first ship's captain |
| Mind flayer |  | petrified skull in the mine |

### Gods (1)

| name | also called | note |
|---|---|---|
| Auril the Frostmaiden | Auril, the Frostmaiden | Minor deity of winter's wrath. |

### Groups (6)

| name | also called | note |
|---|---|---|
| The party | the heroes, our four adventurers, the company | The four player characters. |
| Lord's Alliance |  | alliance Qinric serves |
| Explorer's League |  | guild of explorers |
| Torg's Caravan |  | Torrga's merchant caravan |
| The circus |  | Unnamed travelling circus that held Claude |
| The Brotherhood |  | Unnamed. Named in the note in the tome |

### Events and rituals (4)

| name | also called | note |
|---|---|---|
| Sacrifice of souls |  | every ten-day humanoid souls are sacrificed, spellcasters first, to appease Auril |
| Sacrifice of warmth | Sacrifices of Warmth | recurring Sacrifices of Warmth: all warmth is forsaken, to appease Auril |
| Sacrifice of food |  | the residents fast and surrender their daily food to the beasts of the tundra, to appease Auril |
| The Everlasting Rime | Everlasting Rime | the magical winter Auril casts each night so the sun cannot rise |

### Places (26)

| name | also called | note |
|---|---|---|
| Icewind Dale |  |  |
| Ten-Towns |  |  |
| Targos |  |  |
| Bryn Shander |  |  |
| Easthaven | Eashaven |  |
| Termalaine |  |  |
| Bremen |  |  |
| Good Mead |  |  |
| Caer-Dineval |  |  |
| Caer-Konig |  |  |
| Lonelywood |  |  |
| Waterdeep |  |  |
| Temple of the Seldarine |  |  |
| Castle Ward |  |  |
| Sparkwalker Spire |  |  |
| Blackstaff Tower |  |  |
| Neverwinter |  |  |
| Spine of the World |  |  |
| The Lucky Liar |  |  |
| The Blue Clam |  |  |
| The gemstone mine | the mine |  |
| Maer Dualdon |  |  |
| Three Flags Sailing |  |  |
| Kelvin's Cairn |  |  |
| Eastway Road |  |  |
| The Underdark |  |  |

### Items (9)

| name | also called | note |
|---|---|---|
| Azure tome |  | Syllar's locked tome |
| Ancestral journal |  | Okthandom's family journal |
| Wolf figurine |  | inlaid with black gems, etched with Sylvan runes |
| Aurora's Wind |  | Fizzlebomb's ship |
| Wand of Conducting |  | found in the mine |
| Lantern of Greater Tracking |  | found in the mine |
| Chardalyn spear |  | found in the mine |
| Psi crystal |  | from a petrified mind flayer skull; gives telepathy |
| Heart of chardalyn |  | hidden under the bucket lift |

## A place's land, climate and ruler

Scored per layer: found, wrong (another value), missed, and extra (a row this list doesn't hold, a false note) (#4256).

| Place | Layer | Value | Line | Why |
|---|---|---|---|---|
| Icewind Dale | climate | arctic | 4 | "the North has been held in a frozen vice", and "the treacherous tundra" (line 42). Slice 2's set phrases don't cover this wording, so its reader misses it. |

No land type or ruler is stated in the document's words, so any other row is extra.

## A character's languages

A language is a property of the character, never a tie (owner, 2026-11-14; #4229). Scored as found, missed, and extra (a language row this list doesn't hold).

| Character | Language | Line | Why |
|---|---|---|---|
| Cerric Vanya'elen | Elvish | 21 | "raised fluent in Elvish", from his elven heritage (the owner added this paragraph on 2026-11-14) |
| Cerric Vanya'elen | Draconic | 21 | "earned him fluency in Draconic as well", from his studies of arcane texts |

No other character's language is stated, so any other language row is extra.

## Must not find

- a kind of creature is not an entry (#3373 rule, Supervisor 2026-11-04): no entry or tie for the white roc, ice kobolds, grell, electric eel or owlbears; one particular creature is (the mind flayer whose skull held the Psi crystal)
- any link made from dice rolls, hit points, wasp stings or spear wounds, spells (Moment to Think, Detect Magic) or marching order
- only unique items are tracked (#3373): no entry or tie for Wyvern poison, the two dog sleds and eight dogs, the scroll of Detect Magic, the scroll of Identify, the Potion of Healing or the waterproof backpacks (ordinary gear)
- the warning sign 'Kobolds only!' is a sign, not a person or a group
- 'The Brotherhood' in the note is named once and nothing is said about it: no members, no leader, and it is not the Explorer's League
- the unnamed boy who shouts the news, the unnamed beggar woman's prophecy and the unnamed ghost's own name: do not invent names for them
- 'Eashaven' is Easthaven spelled wrongly (one place, not two)
- the Ten-Towns settlement 'Dougan's Hole' is not in this document, so it must not appear
- the Sylvan runes on Claude's wolf figurine (line 48) are writing on an item, not a language Claude speaks: no language row for him

## Waits for a later slice

- Glyph travels with the party (travels_with, #3176)
- who a marching order puts ahead of whom is not a tie
