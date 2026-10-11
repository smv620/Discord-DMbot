# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test F: the game clock and a timer

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Why:** DMbot now keeps your game's clock and can time a spell like Bless. Check that you can start a timer and that DMbot tells you, only on your DM screen, when it has likely ended. It never ends one by itself.

### Steps
1. Join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening**.
2. Open **⚙️ Settings** and press **Game clock**. Set a day and a time. A clock message appears in **dmb-dm-screen** with buttons.
3. On the clock message press **Start a timer**. In the form write **Bless**, who it is on (a name), and **1 minute** for how long. Press submit.
4. The clock message now lists the timer. Press **+10 min**.
5. The DM screen should say "⏳ Bless on (the name) has likely ended" with two buttons: **Ended** and **Still going +10 min**. Press **Still going +10 min**, then press **+10 min** twice more and see it say so again. Press **Ended** this time.
6. Type `/dmbot stop`.

### It worked if
- The clock shows the time you set and moves with the buttons.
- The "has likely ended" line shows **only** on the DM screen (never in the transcript or a public channel) and only after you pressed a button that passed the time.

**If not:** type `/dmbot stop`, then tell dev1 what you saw at the step where it went wrong.

### Tell dev1
- **Worked** or **didn't work**, and anything that felt confusing.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
