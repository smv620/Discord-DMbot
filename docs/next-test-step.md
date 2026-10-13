# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test F again: the game clock and a timer, after the fix

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Start only after dev1 says the timer fix is on the server** (it needs a restart that dev1 does).
**Why:** last time the "has likely ended" line kept the old length after you pressed **Still going**, and nothing said you had extended it. That is fixed. Check it.

### Steps
1. Join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening**. (Press **Start listening** once only.)
2. Open **⚙️ Settings**, press **Game clock**, set a day and a time. On the clock message press **Start a timer**: write **Bless**, a name, and **1 minute**. Submit.
3. Press **+10 min**. The DM screen says "⏳ Bless on (the name) has likely ended" with **Ended** and **Still going +10 min**.
4. Press **Still going +10 min**. The DM screen should now say that you extended it.
5. Press **+10 min** twice more. The ended line should show the **total** time (for example 11 minutes or "1 minute, extended 10 minutes"), not just "1 minute". Press **Ended**.
6. Type `/dmbot stop`.

### It worked if
- After **Still going**, the DM screen says you extended it.
- The next "has likely ended" line shows the longer total.
- All of it shows only on the DM screen.

**If not:** type `/dmbot stop`, then tell dev1 what you saw at the step where it went wrong.

### Tell dev1
- **Worked** or **didn't work**, and anything that felt confusing.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
