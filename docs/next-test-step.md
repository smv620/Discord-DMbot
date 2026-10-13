# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test H: typed chat, a fight, and a player's sheet

**People:** 2 (you as the DM, and one player)
**Time:** about 10 minutes
**Start only after dev1 says the big update is on the server.**
**Why:** DMbot can now read what you type in the voice channel's chat, keep track of a fight's rounds, and make a player's character from their D&D Beyond sheet. This checks all three once.

**Before you start:** the player needs a D&D Beyond character set to **Public** (on D&D Beyond: the character > Manage > Character Settings > Character Privacy > Public). Have its link ready.

### Steps
1. Both of you join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening** (once only).
2. **Both of you get a new question from DMbot in a private message**, because the words changed: it now says DMbot also reads the table's chat. Each of you presses **I agree**. Until you do, DMbot doesn't record you.
3. **Typed chat:** in the voice channel's own text chat, the DM types **we take a long rest**. The game clock on the DM screen moves on, with an **Undo** note.
4. **A fight:** the DM says out loud **"Roll initiative."** The DM screen says combat started, round 1. Say **"Next round."** It says round 2. Say **"Combat's over."** It says the fight ended.
5. **The player's sheet:** the player opens DMbot's reminder message (the one with the date they agreed), presses **📜 My character sheet**, then **Link my D&D Beyond sheet**, and pastes their link. The DM screen says DMbot added that character, with an **Open character card** button.
6. Type `/dmbot stop`. Press the transcript download, and look for the long-rest line marked **(typed)**.

### It worked if
- Step 2: you both got the new question, and the bot recorded you after you said yes.
- Step 3: the clock moved after the typed line.
- Step 4: the round went 1, 2, then the fight ended.
- Step 5: the character appeared on the DM screen with the sheet's name.
- Step 6: the download shows **(typed)** next to the typed line.
- None of the DM-screen lines showed up anywhere the player can see.

**If not:** type `/dmbot stop`, then tell dev1 the step where it went wrong and what you saw.

### Tell dev1
- **Worked** or **didn't work** for each of steps 2 to 6, and anything that felt confusing.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
