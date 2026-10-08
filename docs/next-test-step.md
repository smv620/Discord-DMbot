# Next test step

**Just one small test.** When it's done, tell dev1 (the server session) what happened. dev1 records it and puts the next test here.

---

## Test A: DMbot writes down what one person says

**People:** 1 (you, as the DM)
**Time:** about 3 minutes
**Why:** many updates went in today. Check the most basic thing still works before anything bigger.

### Before you start
- Be in your test Discord server, on a steady connection.
- Tell dev1 you're starting, so it can watch the logs.

### Steps
1. Join the voice channel you play in.
2. Type `/dmbot start` and pick your campaign.
3. If DMbot sends you a private message asking to record you, press **I consent**.
4. Say this sentence clearly, once: **"The party walks into the tavern and orders three drinks."**
5. Wait 10 seconds.
6. Look in the channel named **#dmb-transcript-…**
7. Type `/dmbot stop`.

### It worked if
- Within about 10 seconds, a line appears in **#dmb-transcript-…** with your name and (close to) your sentence.
- After `/dmbot stop`, DMbot says it stopped.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
