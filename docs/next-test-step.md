# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test G: ask the DM sidebar by voice

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Why:** you can now call DMbot out loud by starting with **"Hey DMbot"**. Check that it hears you and answers only you, privately, and that it doesn't answer when you don't say the call words.

### Steps
1. Join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening**.
2. Say clearly: **"Hey DMbot, do you need line of sight for fireball?"** Wait for the answer in your private chat with DMbot.
3. Say a normal game line without the call words, like **"The goblins run into the cave."** Nothing should come back.
4. Say a second question: **"Hey DMbot, how much damage does fireball do?"** Then ask again right away: **"Hey DMbot, what is the range of fireball?"**
5. Type `/dmbot stop`.

### It worked if
- Each "Hey DMbot" question got a short answer in your private chat that ends with **confident** or **uncertain**.
- The normal line in step 3 got no answer.
- Nothing was posted in a public channel.

**If not:** type `/dmbot stop`, then tell dev1 what you saw at the step where it went wrong.

### Tell dev1
- **Worked** or **didn't work**, and whether the quick second question (step 4) was answered or ignored.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
