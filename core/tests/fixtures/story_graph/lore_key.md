# Lore answer key (sample campaign)

What the graph should hold after the sample lore document is read: [Icewind Dale Background & Premise](https://docs.google.com/document/d/1zbt7g3r6uFfiSYr3fl_gyaxARhwJPizauI0jmiEa2aw/edit?usp=sharing) (about 2,200 words). The player characters and many other names are invented for testing; some towns, gods and non-player characters come from a published adventure (names only, never its text). The players agreed to its use as a public example. The document is linked in the [README](README.md), not copied here. **The line numbers** are lines of the document's plain-text export (blank lines count), so you can find each source.

**Test players** (made-up usernames):

| Player character | Username |
|---|---|
| Cerric Vanya'elen | `user_ist` |
| Belleros Stormgrace | `user_jac` |
| Okthandom Korran | `user_eri` |
| Claude Ambere | `user_owe` |

**Tiers:** `stated` the words are in the text · `inferred` only implied, offered as a suggestion and never extracted · `by_rule` made by a rule, not read. **Must find: no** means a good find if present, not a miss if absent. An `inferred` tie marked must-find is scored on whether DMbot *offers* it to the DM. **Current: no** is history: it was true and is not now.

**Totals:** 90 entries, 105 connections (65 must-find), 15 events.

## Connections

### Family (16)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l001 | Roceran | parent_of | Cerric Vanya'elen | stated | yes | yes | 18 |  |
| l002 | Shafina | parent_of | Cerric Vanya'elen | stated | yes | yes | 18 |  |
| l003 | Roceran | parent_of | Valyra | inferred | no | yes | 18 | 'his younger sister, Valyra': the same parents |
| l004 | Shafina | parent_of | Valyra | inferred | no | yes | 18 |  |
| l005 | Cerric Vanya'elen | sibling | Valyra | stated | yes | yes | 18 | younger sister |
| l006 | Cerric's paternal grandfather | grandparent_of | Cerric Vanya'elen | stated | yes | yes | 18 | died in a severe storm |
| l007 | Qinric | sibling | Roceran | stated | yes | yes | 18 | 'Roceran's brother Qinric'; the owner asked for this tie directly |
| l008 | Qinric | uncle_of | Cerric Vanya'elen | inferred | yes | yes | 18 | never said as 'uncle': Roceran's brother |
| l030 | Okthandom Korran | sibling | Vaelrith | stated | yes | yes | 27 | elder brother, vanished at 105 |
| l031 | Okthandom Korran | sibling | Nyssara | stated | yes | yes | 27 | younger sister; a player character's family must be kept |
| l032 | Vaelrith | sibling | Nyssara | inferred | no | yes | 27 |  |
| l033 | Okthandom Korran | parent_of | Okthandom's child | stated | no | yes | 30 | 'his child', named once in passing |
| l038 | Lee | parent_of | Claude Ambere | stated | yes | yes | 42 | 'Griz, son of Lee'; the owner flagged this as missing |
| l068 | Brewner | parent_of | Brewner's daughter | stated | no | yes | 67 |  |
| l079 | Bruni Stonewake | sibling | Thorin | stated | yes | yes | 78 | 'Thorin, his miner brother Bruni' |
| l101 | Garret Velryn | spouse | Keegan Velryn | stated | yes | yes | 96 | 'whose husband' |

### Teaching and work (18)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l009 | Qinric | member | Lord's Alliance | stated | yes | yes | 18 | a war mage evoker in it |
| l010 | Valyra | apprentice_of | Kelfir Ama'Kiir | stated | yes | yes | 18 |  |
| l011 | Cerric Vanya'elen | works_for | Syllar Sparkwalker | stated | yes | yes | 18 | Keeper of the Sparkwalker Spire under him |
| l014 | Syllar Sparkwalker | mentor_of | Emmerich | inferred | no | yes | 18 | Cerric's fellow apprentices |
| l015 | Syllar Sparkwalker | mentor_of | Wit | inferred | no | yes | 18 |  |
| l036 | Belleros Stormgrace | member | Explorer's League | stated | yes | yes | 36 | 'a scout associated with the Explorer's League' |
| l055 | The party | works_for | Hlin Trollbane | stated | yes | yes | 53 | 100 gold to capture or eliminate Kaltro (line 67) |
| l057 | Sephek Kaltro | works_for | Torrga Icevein | stated | yes | yes | 57 | her bodyguard (also line 55) |
| l058 | Torrga Icevein | leads | Torg's Caravan | stated | yes | yes | 57 | under her own heading on line 56 |
| l059 | Sephek Kaltro | member | Torg's Caravan | inferred | no | yes | 55 | accompanies it |
| l061 | Speaker Masthew | leads | Termalaine | stated | yes | yes | 59 |  |
| l062 | The party | works_for | Speaker Masthew | stated | yes | yes | 78 | 50 gold, then 250 for the mine (line 87) |
| l069 | Cerric Vanya'elen | member | The party | stated | yes | yes | 67 | 'our four adventurers' |
| l070 | Belleros Stormgrace | member | The party | stated | yes | yes | 67 | 'our four adventurers' |
| l071 | Okthandom Korran | member | The party | stated | yes | yes | 67 | 'our four adventurers' |
| l072 | Claude Ambere | member | The party | stated | yes | yes | 67 | 'our four adventurers' |
| l102 | Garret Velryn | member | Explorer's League | stated | yes | yes | 96 | 'a noted member' |
| l104 | The party | works_for | Keegan Velryn | stated | yes | yes | 96 | 50 gold and a route map to find Garret |

### Feelings and opposition (16)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l016 | Emmerich | rival_of | Cerric Vanya'elen | stated | yes | yes | 18 | 'rival apprentices Emmerich and Wit'; the owner flagged backstory rivals as missing |
| l017 | Wit | rival_of | Cerric Vanya'elen | stated | no | yes | 18 |  |
| l027 | Syllar Sparkwalker | knows | Danneth | inferred | no | yes | 21 | the note is signed S |
| l039 | Claude Ambere | held_captive_by | The circus | stated | yes | no | 42 | before he escaped |
| l056 | Hlin Trollbane | enemy_of | Sephek Kaltro | inferred | no | yes | 53 | she hires against him; she is not his servant (#3256) |
| l067 | Belleros Stormgrace | knows (wary) | Brewner | stated | no | yes | 67 | a heated debate |
| l073 | Cerric Vanya'elen | knows | Belleros Stormgrace | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l074 | Cerric Vanya'elen | knows | Okthandom Korran | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l075 | Cerric Vanya'elen | knows | Claude Ambere | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l076 | Belleros Stormgrace | knows | Okthandom Korran | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l077 | Belleros Stormgrace | knows | Claude Ambere | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l078 | Okthandom Korran | knows | Claude Ambere | by_rule | yes | yes | 67 | player characters who play together know each other (#3036) |
| l084 | Okthandom Korran | enemy_of | Trex | stated | no | yes | 84 | killed him with an arrow |
| l094 | Ice troll | enemy_of | The minotaur captain | stated | no | yes | 90 | devoured him and shattered his ship |
| l096 | Electric eel | enemy_of | Fizzlebomb | stated | no | yes | 93 | pulled him under |
| l097 | Belleros Stormgrace | knows (friendly) | Boy | stated | no | yes | 96 | befriended him |

### Things (15)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l022 | Cerric Vanya'elen | carries | Azure tome | stated | yes | yes | 21 | grabbed his master's locked tome |
| l023 | Syllar Sparkwalker | owns | Azure tome | stated | yes | yes | 21 | 'his master's' |
| l028 | Cerric Vanya'elen | owns | Glyph | stated | yes | yes | 21 | snowy owl familiar |
| l035 | Okthandom Korran | owns | Ancestral journal | inferred | no | yes | 27 | found the curse in it |
| l040 | Claude Ambere | carries | Wolf figurine | stated | yes | no | 42 | carried it until he sold it to Belleros (line 90): history |
| l063 | Veron Braig | owns | The Blue Clam | stated | yes | yes | 61 |  |
| l083 | Trex | carries | Penolopi | stated | no | yes | 84 | the plant was in his backpack |
| l085 | Cerric Vanya'elen | carries | Wand of Conducting | stated | no | yes | 84 | Okthandom found it and gave it to him |
| l086 | The party | owns | Lantern of Greater Tracking | stated | no | yes | 84 | found in the mine |
| l087 | The party | owns | Chardalyn spear | stated | no | yes | 84 | spoils of the mine |
| l088 | The party | owns | Heart of chardalyn | stated | no | yes | 84 | hidden under the bucket lift |
| l089 | Cerric Vanya'elen | carries | Psi crystal | inferred | yes | yes | 87 | studied it; the party took it from the skull |
| l092 | Belleros Stormgrace | owns | Wolf figurine | stated | yes | yes | 90 | bought it from Claude for 75 gold |
| l093 | Fizzlebomb | owns | Aurora's Wind | stated | yes | yes | 90 | 'Fizzlebomb's vessel'; the owner flagged this as missing |
| l100 | Garret Velryn | owns | Boy | inferred | yes | yes | 96 | Boy led the party to Keegan's home: the owner says he is Garret's dog. The text never says it; offer it to the DM, never extract it |

### Places and other ties (40)

| id | from | tie | to | tier | must find | current | line | note |
|---|---|---|---|---|---|---|---|---|
| l012 | Cerric Vanya'elen | works_in | Sparkwalker Spire | stated | yes | yes | 18 | Keeper of it |
| l013 | Syllar Sparkwalker | works_in | Blackstaff Tower | inferred | no | yes | 18 | 'Syllar Sparkwalker of Blackstaff Tower' |
| l018 | Cerric Vanya'elen | comes_from | Waterdeep | stated | yes | yes | 18 | born in the Temple of the Seldarine |
| l019 | Temple of the Seldarine | in | Waterdeep | stated | yes | yes | 18 |  |
| l020 | Cerric Vanya'elen | lived_in | Castle Ward | stated | no | no | 18 | raised in a mansion there |
| l021 | Castle Ward | in | Waterdeep | inferred | no | yes | 18 |  |
| l024 | Cerric Vanya'elen | last_seen_in | Neverwinter | stated | no | yes | 21 | fled there by teleportation rune |
| l025 | Cerric Vanya'elen | last_seen_in | Icewind Dale | stated | no | yes | 21 | journeyed north into it |
| l026 | Danneth | last_seen_in | Easthaven | inferred | no | yes | 21 | the note is addressed 'Danneth, Easthaven' |
| l029 | Easthaven | in | Icewind Dale | stated | yes | yes | 21 |  |
| l034 | The wind spirit | associate | Okthandom Korran | stated | yes | yes | 30 | guides and guards him |
| l037 | Claude Ambere | comes_from | Spine of the World | stated | yes | yes | 42 |  |
| l041 | Auril the Frostmaiden | lives_in | Icewind Dale | stated | yes | yes | 51 | withdrawn there to live among mortals |
| l042 | Auril the Frostmaiden | associate | White roc | stated | no | yes | 51 | rides it |
| l043 | Ten-Towns | associate | Auril the Frostmaiden | stated | no | yes | 90 | its towns sacrifice every ten-day to appease her |
| l044 | Targos | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l045 | Bryn Shander | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l046 | Easthaven | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l047 | Termalaine | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l048 | Bremen | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l049 | Good Mead | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l050 | Caer-Dineval | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l051 | Caer-Konig | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l052 | Lonelywood | in | Ten-Towns | stated | yes | yes | 90 | listed with its sacrifice (also line 7) |
| l053 | Ten-Towns | in | Icewind Dale | stated | yes | yes | 57 |  |
| l054 | Hlin Trollbane | works_in | Lonelywood | stated | yes | yes | 53 | operates out of it |
| l060 | Sephek Kaltro | last_seen_in | Easthaven | stated | no | yes | 96 | his last known location |
| l064 | Veron Braig | works_in | The Blue Clam | stated | yes | yes | 61 | owner and proprietor |
| l065 | The Blue Clam | in | Termalaine | stated | yes | yes | 61 |  |
| l066 | The Lucky Liar | in | Lonelywood | stated | yes | yes | 67 |  |
| l080 | Bruni Stonewake | works_in | The gemstone mine | stated | no | yes | 78 | a miner |
| l081 | The gemstone mine | in | Termalaine | inferred | no | yes | 78 |  |
| l082 | Trex | associate | The ghost | stated | no | yes | 84 | a possessed ghost emerged when he fell |
| l090 | Psi crystal | was_kept_in | The gemstone mine | inferred | yes | no | 84 | found there in the mind flayer's skull, then taken: the place is history |
| l091 | Psi crystal | associate | Mind flayer | stated | no | yes | 84 | from its petrified skull |
| l095 | Ice troll | last_seen_in | Maer Dualdon | stated | no | yes | 90 |  |
| l098 | Boy | last_seen_in | Three Flags Sailing | stated | no | yes | 96 |  |
| l099 | Three Flags Sailing | in | Targos | inferred | no | yes | 96 |  |
| l103 | Garret Velryn | last_seen_in | Kelvin's Cairn | stated | no | yes | 96 | vanished five days ago leading an expedition up it |
| l105 | Jebidiah | works_in | Targos | inferred | no | yes | 96 | a kennel master the party met there |

## Events

| id | what happened | who | line |
|---|---|---|---|
| e01 | Master Sparkwalker disappears, Wit comes back fatally wounded, and Cerric flees to Neverwinter with the tome | Cerric, Syllar Sparkwalker, Wit | 21 |
| e02 | Vaelrith vanishes at 105, leaving a bloodstained campsite | Okthandom, Vaelrith | 27 |
| e03 | Claude erases his own memory, is held by a circus and escapes | Claude | 42 |
| e04 | Hlin Trollbane offers the party a contract on Sephek Kaltro in The Lucky Liar | the party, Hlin Trollbane, Sephek Kaltro | 67 |
| e05 | The party fights six ice kobolds and evades a yeti on the road to Termalaine | the party | 75 |
| e06 | Rumours reach the party that an evil wizard captured in Easthaven will be burned at the stake | the party | 78 |
| e07 | The grell paralyzes Claude and destroys Glyph, then the party kills it | Claude, Cerric, the party | 84 |
| e08 | Okthandom kills Trex with an arrow, and Claude burns Penolopi to banish the ghost | Okthandom, Trex, Claude, Cerric | 84 |
| e09 | The party secures the spoils of the mine, including a psi crystal and a heart of chardalyn | the party | 84 |
| e10 | Speaker Masthew pays a bounty of 250 gold for clearing the mine | the party, Speaker Masthew | 87 |
| e11 | Claude sells the wolf figurine to Belleros for 75 gold | Claude, Belleros | 90 |
| e12 | An ice troll devours the first ship's captain and shatters his ship | ice troll, the minotaur captain | 90 |
| e13 | Cerric talks Okthandom into pushing Fizzlebomb overboard; Belleros runs the Aurora's Wind ashore | Cerric, Okthandom, Belleros, Fizzlebomb | 93 |
| e14 | Three owlbears ambush the party near Targos | the party | 93 |
| e15 | Belleros befriends Boy, who leads the party to Keegan Velryn; she asks them to find Garret | Belleros, Boy, Keegan Velryn, Garret Velryn | 96 |

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
| Okthandom's child |  | Unnamed. Mentioned once. |
| Brewner's daughter |  | Unnamed. Leads Brewner home. |
| The minotaur captain |  | Unnamed. Captain of the first ship. |
| The beggar woman |  | Unnamed. Gives a prophecy. |

### Creatures (13)

| name | also called | note |
|---|---|---|
| Glyph |  | Cerric's snowy owl familiar, destroyed by the grell |
| Boy |  | sled dog Belleros befriended |
| The ghost |  | Unnamed possessed ghost that emerged when Trex fell |
| Penolopi |  | pet plant the party burned |
| The wind spirit |  | Unnamed ancient spirit that guides and guards Okthandom |
| White roc |  | Auril's mount |
| Ice kobolds |  | six fought on the road |
| Yeti |  | evaded on the road |
| Grell |  | killed the familiar |
| Ice troll |  | ate the first ship's captain |
| Electric eel |  | pulled Fizzlebomb under |
| Owlbears |  | three ambushed the party near Targos |
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

## Must not find

- any link made from dice rolls, hit points, wasp stings or spear wounds, spells (Moment to Think, Detect Magic) or marching order
- only unique items are tracked (#3373): no entry or tie for Wyvern poison, the two dog sleds and eight dogs, the scroll of Detect Magic, the scroll of Identify, the Potion of Healing or the waterproof backpacks (ordinary gear)
- the warning sign 'Kobolds only!' is a sign, not a person or a group
- 'The Brotherhood' in the note is named once and nothing is said about it: no members, no leader, and it is not the Explorer's League
- the unnamed boy who shouts the news, the unnamed beggar woman's prophecy and the unnamed ghost's own name: do not invent names for them
- 'Eashaven' is Easthaven spelled wrongly (one place, not two)
- the Ten-Towns settlement 'Dougan's Hole' is not in this document, so it must not appear

## Waits for a later slice

- Glyph travels with the party (travels_with, #3176)
- who a marching order puts ahead of whom is not a tie
