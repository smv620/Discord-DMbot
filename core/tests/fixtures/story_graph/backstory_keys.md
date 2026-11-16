# Backstory answer keys (sample campaign)

What the graph should hold after each player character's secret backstory is read (#4523, stage 1a), and who may know each row. The documents are linked in the [README](README.md), not copied; these keys give names, kinds, ties and knowers only. **Knows** is who knows the row before anything public names it: a name (the speaker unless another is given), `party`, `everyone` or `dm` (nobody, not even the speaker). **Lore** is the sample lore's row that later makes it the party's (`lore_key.json` `reveals`); a row with none stays its knowers' secret.

## Cerric Vanya'elen

**Entries:** Cerric Vanya'elen; Roceran; Shafina; Valyra; Kelfir Ama'Kiir; Cerric's paternal grandfather; Qinric; Syllar Sparkwalker; Emmerich Ravenstride; Wit; Danneth; Khelben Arunsun (good find); The Brotherhood; Lord's Alliance; Council of the Blackstaff; The Harpers (good find); Lathander (good find); Waterdeep; Temple of the Seldarine; Castle Ward; Sparkwalker Spire; Blackstaff Tower; Spires of the Morning; The Sapphire House (good find); The Font of Knowledge (good find); Sword Street (good find); Calamastyr Lane (good find); Neverwinter; Easthaven; Ten-Towns; Icewind Dale; Azure tome; Marble bust of Khelben (good find)

| id | from | tie | to | tier | must find | current | line | knows | lore |
|---|---|---|---|---|---|---|---|---|---|
| c001 | Roceran | parent_of | Cerric Vanya'elen | stated | yes | yes | 1 | Cerric | l001 |
| c002 | Shafina | parent_of | Cerric Vanya'elen | stated | yes | yes | 1 | Cerric | l002 |
| c003 | Roceran | parent_of | Valyra | stated | yes | yes | 1 | Cerric | l003 |
| c004 | Shafina | parent_of | Valyra | inferred | yes | yes | 1 | Cerric | l004 |
| c005 | Cerric Vanya'elen | sibling | Valyra | stated | yes | yes | 1 | Cerric | l005 |
| c006 | Cerric's paternal grandfather | grandparent_of | Cerric Vanya'elen | stated | yes | yes | 16 | Cerric | l006 |
| c007 | Qinric | sibling | Roceran | stated | yes | yes | 16 | Cerric | l007 |
| c008 | Qinric | uncle_of | Cerric Vanya'elen | stated | yes | yes | 16 | Cerric | l008 |
| c009 | Qinric | member | Lord's Alliance | stated | yes | yes | 16 | Cerric | l009 |
| c010 | Valyra | apprentice_of | Kelfir Ama'Kiir | stated | yes | yes | 13 | Cerric | l010 |
| c011 | Cerric Vanya'elen | works_for | Syllar Sparkwalker | stated | yes | no | 16 | Cerric | l011 |
| c012 | Cerric Vanya'elen | leads | Sparkwalker Spire | stated | no | no | 19 | Cerric | l012 |
| c013 | Cerric Vanya'elen | works_in | Sparkwalker Spire | stated | yes | no | 19 | Cerric | l013 |
| c014 | Syllar Sparkwalker | member | Council of the Blackstaff | stated | yes | yes | 16 | Cerric |  |
| c015 | Syllar Sparkwalker | mentor_of | Emmerich Ravenstride | stated | yes | yes | 25 | Cerric | l015 |
| c016 | Syllar Sparkwalker | mentor_of | Wit | stated | yes | yes | 25 | Cerric | l016 |
| c017 | Emmerich Ravenstride | rival_of | Cerric Vanya'elen | stated | yes | yes | 28 | Cerric, Emmerich | l017 |
| c018 | Wit | knows (respect) | Cerric Vanya'elen | stated | yes | yes | 28 | Cerric, Wit |  |
| c019 | Cerric Vanya'elen | comes_from | Waterdeep | stated | yes | yes | 1 | Cerric | l019 |
| c020 | Temple of the Seldarine | in | Waterdeep | stated | yes | yes | 1 | everyone | l020 |
| c021 | Cerric Vanya'elen | lived_in | Castle Ward | stated | yes | no | 4 | Cerric | l021 |
| c022 | Castle Ward | in | Waterdeep | inferred | yes | yes | 4 | everyone | l022 |
| c023 | Cerric Vanya'elen | carries | Azure tome | stated | yes | yes | 37 | Cerric | l023 |
| c024 | Syllar Sparkwalker | owns | Azure tome | stated | yes | yes | 37 | Cerric | l024 |
| c025 | Cerric Vanya'elen | last_seen_in | Neverwinter | stated | yes | yes | 52 | Cerric | l025 |
| c026 | Cerric Vanya'elen | last_seen_in | Spires of the Morning | stated | no | no | 43 | Cerric |  |
| c027 | Cerric Vanya'elen | last_seen_in | Blackstaff Tower | stated | no | no | 39 | Cerric |  |
| c028 | Danneth | last_seen_in | Easthaven | inferred | yes | yes | 59 | Cerric | l027 |
| c029 | Syllar Sparkwalker | knows | Danneth | inferred | yes | yes | 59 | Cerric | l028 |
| c030 | Easthaven | in | Ten-Towns | stated | yes | yes | 70 | everyone | l046 |
| c031 | Ten-Towns | in | Icewind Dale | stated | yes | yes | 70 | everyone | l053 |
| c032 | Spires of the Morning | associate | Lathander | stated | no | yes | 40 | everyone |  |
| c033 | Syllar Sparkwalker | works_in | Blackstaff Tower | inferred | yes | yes | 37 | Cerric | l014 |
| c034 | Wit | last_seen_in | Sparkwalker Spire | stated | no | no | 34 | Cerric, Wit |  |
| c035 | Khelben Arunsun | leads | The Harpers | stated | no | yes | 37 | everyone |  |
| c036 | Cerric Vanya'elen | knows | The Brotherhood | inferred | no | yes | 64 | Cerric |  |

**Events:**

- ce01 (line 13): Valyra leaves for her apprenticeship and the siblings stop meeting. Knows: Cerric, Valyra. Lore: none
- ce02 (line 16): The grandfather dies in a storm; Qinric visits and Cerric becomes the spire's steward. Knows: Cerric. Lore: l006
- ce03 (line 31): Master Sparkwalker disappears; Emmerich, then Wit, leave to find him. Knows: Cerric. Lore: e01
- ce04 (line 34): Wit returns fatally wounded and tells Cerric to save the tome. Knows: Cerric, Wit. Lore: e01
- ce05 (line 37): Cerric breaks the lock, takes the tome and escapes by the rune as someone enters. Knows: Cerric. Lore: e01
- ce06 (line 52): In Neverwinter he takes the name Cerric Vanya'elen and unlocks the tome. Knows: Cerric. Lore: e01

**Threads:**

- bc01 quest: What happened to Master Sparkwalker (taker Cerric, same as lq01). Lore: lq01
- bc02 mystery: Who are the Brotherhood, and who came for the tome (taker Cerric). Lore: none
- bc03 mystery: What became of Emmerich (taker Cerric). Lore: none

**Languages:** Elvish, Draconic

**Must not find:** the teleport command word is not an entry; Azuth appears only in an exclamation: no entry needed (a good find if kept as a deity, not a miss)

## Okthandom Korran

**Entries:** Okthandom Korran; Vaelrith Korran; Nyssara Korran; Okthandom's child; Okthandom's father (good find); Lady Martha; The apparition; Ancestral journal; Family talisman (good find); Icewind Dale

| id | from | tie | to | tier | must find | current | line | knows | lore |
|---|---|---|---|---|---|---|---|---|---|
| o001 | Okthandom Korran | sibling | Vaelrith Korran | stated | yes | yes | 10 | Okthandom, Nyssara | l031 |
| o002 | Okthandom Korran | sibling | Nyssara Korran | stated | yes | yes | 13 | Okthandom, Nyssara | l032 |
| o003 | Vaelrith Korran | sibling | Nyssara Korran | stated | yes | yes | 13 | Okthandom, Nyssara | l033 |
| o004 | Okthandom Korran | parent_of | Okthandom's child | stated | yes | yes | 16 | Okthandom | l034 |
| o005 | Okthandom's father | parent_of | Okthandom Korran | stated | no | yes | 7 | Okthandom |  |
| o006 | Okthandom Korran | owns | Ancestral journal | inferred | yes | yes | 7 | Okthandom | l036 |
| o007 | The apparition | associate | Okthandom Korran | stated | yes | yes | 4 | Okthandom |  |
| o008 | The apparition | associate | Nyssara Korran | inferred | yes | yes | 13 | Okthandom, Nyssara |  |
| o009 | Lady Martha | enemy_of | Okthandom Korran | inferred | no | yes | 2 | Okthandom |  |
| o010 | Okthandom Korran | associate | Icewind Dale | stated | yes | yes | 16 | Okthandom | l123 |
| o011 | Vaelrith Korran | owns | Ancestral journal | inferred | no | no | 10 | Okthandom |  |

**Events:**

- oe01 (line 10): Vaelrith vanishes at 105, leaving a bloodied campsite and a shattered talisman. Knows: Okthandom, Nyssara. Lore: e02
- oe02 (line 13): Nyssara confesses the whispers have started for her. Knows: Okthandom, Nyssara. Lore: e02

**Threads:**

- bo01 quest: What is haunting the Korran bloodline, and how to end it (taker Okthandom, same as lq03). Lore: lq03

**Must not find:** the kinds of thing it might be (spirit, ghost, devil, demon, trickster god) are not entries

## Claude Ambere

**Entries:** Claude Ambere; The circus; The ringmaster (good find); The party

| id | from | tie | to | tier | must find | current | line | knows | lore |
|---|---|---|---|---|---|---|---|---|---|
| a001 | Claude Ambere | held_captive_by | The circus | stated | yes | no | 28 | Claude | l040 |
| a002 | The ringmaster | leads | The circus | inferred | no | yes | 28 | Claude |  |
| a003 | The ringmaster | enemy_of | Claude Ambere | stated | no | no | 28 | Claude |  |
| a004 | Claude Ambere | member | The party | stated | yes | yes | 37 | party | l086 |

**Events:**

- ae01 (line 25): Claude erased his own memories with magic to forget something terrible. Knows: dm. Lore: e03
- ae02 (line 28): The circus captures him and trains him by cruelty. Knows: Claude. Lore: e03
- ae03 (line 31): The ringmaster dies in his cage and Claude escapes. Knows: Claude. Lore: e03

**Threads:**

- ba01 mystery: What Claude made himself forget (taker Claude). Lore: none

**Must not find:** Ursine (bear-folk) is his kind, not an entry; his four guesses are not events

## Belleros Stormgrace

**Entries:** Belleros Stormgrace; Explorer's League; Wardens of the Wilds; Clan Battlehammer; Bruenor Battlehammer; Gandalug Battlehammer; Shimmergloom; Thrain Coppervein; Belleros's parents (good find); Mithral Hall; Kelvin's Cairn; Dwarven Valley; Ten-Towns; Icewind Dale; Brass compass; Grand Map

| id | from | tie | to | tier | must find | current | line | knows | lore |
|---|---|---|---|---|---|---|---|---|---|
| b001 | Belleros Stormgrace | member | Explorer's League | stated | yes | yes | 23 | Belleros | l037 |
| b002 | Belleros Stormgrace | member | Wardens of the Wilds | stated | yes | yes | 64 | Belleros |  |
| b003 | Bruenor Battlehammer | member | Clan Battlehammer | stated | yes | yes | 43 | everyone |  |
| b004 | Bruenor Battlehammer | leads | Kelvin's Cairn | stated | yes | yes | 43 | everyone |  |
| b005 | Kelvin's Cairn | in | Dwarven Valley | stated | yes | yes | 43 | everyone |  |
| b006 | Belleros Stormgrace | member | Clan Battlehammer | inferred | no | yes | 46 | Belleros |  |
| b007 | Gandalug Battlehammer | enemy_of | Belleros Stormgrace | inferred | yes | yes | 69 | Belleros |  |
| b008 | Gandalug Battlehammer | leads | Mithral Hall | stated | yes | no | 69 | everyone |  |
| b009 | Shimmergloom | enemy_of | Belleros Stormgrace | stated | yes | yes | 89 | Belleros |  |
| b010 | Shimmergloom | associate | Mithral Hall | stated | yes | yes | 69 | everyone |  |
| b011 | Belleros Stormgrace | comes_from | Mithral Hall | stated | yes | yes | 74 | Belleros |  |
| b012 | Thrain Coppervein | mentor_of | Belleros Stormgrace | stated | yes | yes | 83 | Belleros |  |
| b013 | Thrain Coppervein | owns | Brass compass | stated | yes | no | 114 | Belleros |  |
| b014 | Belleros Stormgrace | carries | Brass compass | stated | yes | yes | 114 | Belleros |  |
| b015 | Belleros Stormgrace | owns | Grand Map | stated | yes | yes | 120 | Belleros |  |
| b016 | Belleros Stormgrace | last_seen_in | Ten-Towns | stated | yes | yes | 91 | Belleros |  |
| b017 | Ten-Towns | in | Icewind Dale | stated | yes | yes | 91 | everyone | l053 |
| b018 | Belleros's parents | parent_of | Belleros Stormgrace | stated | no | yes | 80 | Belleros |  |

**Events:**

- be01 (line 32): He swears the Explorer's Oath. Knows: Belleros. Lore: none
- be02 (line 64): After a narrow escape from a ruin he trains as a ranger of the Ward. Knows: Belleros. Lore: none
- be03 (line 89): Shimmergloom destroys Mithral Hall and scatters his people. Knows: everyone. Lore: none

**Threads:**

- bb01 quest: Find a way to kill Shimmergloom (taker Belleros). Lore: none
- bb02 mystery: Belleros's shadow moves wrongly (taker Belleros). Lore: none
- bb03 hook: Map Icewind Dale for the League (taker Belleros). Lore: none

**Must not find:** the oath's words and the background feature are not entries or ties
