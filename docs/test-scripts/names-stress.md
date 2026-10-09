# DMbot test script: look-alike names

A short story full of names that sound alike. It tests whether DMbot keeps look-alike names
apart, and which new names it suggests after a session (#534). **One person reads every
line**, alone. It takes three to four minutes.

**How to read**
- Record it on your phone or computer, not through DMbot. Use a quiet room: no music or TV.
  Keep the phone the same distance from your mouth the whole time.
- Save it as `names-stress` (any audio type: .m4a, .mp3 or .wav) and send it to the web
  session, the same way you sent the bake-off recording.
- Read only the numbered sentences. Don't read the numbers or the **bold** headings.
- Before you start, say every name in the guide once out loud.
- Say each name the way the guide shows, so the two names in a pair sound different. Keep
  your normal speed.
- **Pause about 2 seconds after every line** (count "one thousand, two thousand").
- Stumbled? Pause, then say the whole line again. Don't skip lines. Afterwards, write down
  which lines you said twice, and send that note with the file.

**Pronunciation guide** (capitals = stressed part)

| Name | Say it | Look-alike | Say it |
|---|---|---|---|
| Mara | MAR-ah | Marin | MAR-in |
| Kael | KAYL | Kaelen | KAY-len |
| Orrin | OR-in | Orrick | OR-ick |
| Tamsin | TAM-zin | Tamsa | TAM-sah |
| Gorrak | GOR-ak | Gorran | GOR-an |
| Ilvaris | il-VAR-is | Ilvara | il-VAR-ah |
| Saelith | SAY-lith | Saelin | SAY-lin |
| Nyxara | nix-AR-ah | Nyxa | NIX-ah |
| Cerric | SER-ik | Cedric | SED-rik |
| Hrothgar | ROTH-gar | Rothgard | ROTH-gard |
| Dravenmoor | DRAY-ven-moor | Ravenmoor | RAY-ven-moor |
| Zephyrine | ZEF-ih-reen | Zephyra | ZEF-ih-rah |
| Vhalzimar | VAL-zih-mar | Valzaren | VAL-zah-ren |
| Ysolde | ih-SOLD | Isolda | ih-SOL-dah |
| Oskar Vane | OS-kar VAYN | | |
| Quillon | KWIL-on | | |

Hale, Wren, Knight and Reed are said like the everyday words.

**Watch these:** Rothgard ends with a clear **d**, and Hrothgar doesn't. Dravenmoor starts
with a clear **D**. Tamsin has a **z** sound, and Tamsa an **s**. Ysolde has two parts
(ih-SOLD), and Isolda three (ih-SOL-dah).

---

## Lines

**Two known names that sound alike** (DMbot knows both)

1. Tonight the party meets Mara at the old mill.
2. Mara has a twin brother, and his name is Marin.
3. You can tell Marin from Mara by the scar on his chin.
4. The ranger Kael waits outside with his cousin Kaelen.
5. Kaelen is younger, and Kael never lets him forget it.
6. Captain Orrin keeps the gate, and his deputy is Orrick.
7. Ask Orrick about the toll, because Orrin is asleep.
8. The bard Tamsin sings, and his sister Tamsa plays the drum.
9. When Tamsa stops playing, Tamsin stops too.
10. The orc chief Gorrak has a son called Gorran.
11. One day Gorran wants the throne, but Gorrak won't give it up.
12. The scholar Ilvaris writes to his daughter Ilvara every week.
13. This week Ilvara writes back, and Ilvaris reads it twice.
14. A stranger named Saelith arrives with her guide Saelin.
15. Her guide Saelin carries the map, and Saelith carries the gold.
16. The priestess Nyxara has a pet fox called Nyxa.
17. The fox Nyxa sleeps by the fire while Nyxara prays.

**A name that sounds like an everyday word** (the word must stay a word)

18. The merchant Oskar Vane is rich, and he is vain about his rings.
19. Everyone says Vane is too vain to lose.
20. Captain Hale rides into town through the hail.
21. The hail stops just as Hale reaches the inn.
22. A wren sings on the roof where Wren the scout is hiding.
23. The scout Wren laughs, because the wren sounds just like her.
24. The old soldier Knight keeps watch all night.
25. Every night, Knight tells stories of the war.
26. Brother Reed asks if anyone can read the letter.
27. "I can read it," says Reed, "but only by the fire."
28. The halfling Quillon puts his quill on the table.
29. With the quill on the desk, Quillon starts to write.

**A known name and a new one that sounds like it** (DMbot knows only the first)

30. On the road, Cerric meets a traveller called Cedric.
31. Nobody trusts Cedric yet, not even Cerric.
32. The blacksmith Hrothgar has a rival named Rothgard.
33. Every market day, Rothgard sells cheaper shields than Hrothgar.
34. The road from Dravenmoor leads to a village called Ravenmoor.
35. The people of Ravenmoor have never heard of Dravenmoor.
36. The sorcerer Zephyrine has an apprentice called Zephyra.
37. When Zephyra casts a spell, Zephyrine watches closely.
38. The wizard Vhalzimar sends his servant Valzaren into town.
39. The guards let Valzaren pass, because they fear Vhalzimar.
40. The healer Ysolde has a student called Isolda.
41. Today Isolda heals the wounded, and Ysolde rests.
42. Let's stop there for tonight.

---

## Names

**Before the live test** (not needed for recording):
1. Make a **new campaign** just for this test. Don't play or add names in it first: names
   it already has change what DMbot asks.
2. Download the names list:
   https://github.com/smv620/Discord-DMbot/raw/development/docs/test-scripts/names-stress-names.txt
3. Type `/dmbot names`, choose that campaign, press **📥 Add many**, and add the file. All
   38 names should go in, with no questions.
4. If DMbot does ask whether two names are the same, press **Different**. **Never press
   Same**: it joins two people into one and spoils the test.

The list has every name above except the six new ones, plus the rest of the bake-off
cast, so DMbot has look-alikes to confuse them with.

**New names** (not on the list): Cedric, Rothgard, Ravenmoor, Zephyra, Valzaren, Isolda.

| Name | Also OK |
|---|---|
| Kaelen | Kaylen |
| Orrick | Orrik |
| Gorran | Goran |
| Saelin | Saylin |
| Nyxa | Nixa |
| Oskar Vane, Vane | Oscar Vane |
| Rothgard | Rothgarde |
| Ravenmoor | Raven Moor |
| Zephyra | Zefira |
| Valzaren | Valzarin |

Other names count only as spelled above. **Isolde is not OK for Isolda**: DMbot knows
Ysolde, and Isolde sounds a lot like it. Since #573, DMbot leaves Isolde as heard when
Ysolde is in the same line, and otherwise changes it only with a note and Undo on the DM
screen, never silently.

## Scoring key

Use the transcript after DMbot's name fixing. A name DMbot fixed shows on the DM screen
with an Undo button, so a **wrong fix** (a right name changed into a wrong one by DMbot) can
be told apart from a name the speech-to-text got wrong (no point, but not a wrong fix).

- **A capital letter means the name, a small letter means the word:** "Wren" is the scout,
  "wren" the bird. "knight" in small letters is the word, not Knight.
- **A line said twice** is scored once, from the last time it was said.
- **Two known names (lines 1–17), 8 pairs, 33 names said:** each name written as said is
  one point. A name turned into its look-alike ("Marin" written as "Mara") gets no point;
  if DMbot did it, it's a wrong fix.
- **Name or word (lines 18–29), 6 pairs:** each name written as the name is one point (12),
  and each word left as the word is one point (12: vain, hail, wren, night, read, quill
  on). Oskar Vane counts once, as Vane. DMbot never turns a small-letter word into a name,
  so any mistake here is the speech-to-text's: this part scores the speech-to-text only.
- **Known and new (lines 30–41), 6 pairs, 24 names said:** each name written as said is
  one point. A new name turned into the known one ("Cedric" written as "Cerric") is the
  worst wrong fix: DMbot must never merge a new name into a known one by itself.
  "Rothgar" (Rothgard without its d) is the likeliest, then Isolde.
- **After the session:** each of the six new names DMbot suggests is one point, under any
  OK spelling above. If DMbot suggests Cedric but asks whether it's the same as Cerric,
  it still counts: it asks, it doesn't decide. A new name the transcript never got right
  can't be suggested: note it as "misheard", not as a miss. An OK spelling of a known name
  suggested as new (Kaylen, Nixa…) is noted as "known, suggested again". Anything else
  suggested is a **false name**.
- **Line 42:** no names. Any name here is a false name.

Record: names right __/33, __/12, __/24 · words kept __/12 · wrong fixes __ · new names
suggested __/6 (misheard __) · known suggested again __ · false names __
