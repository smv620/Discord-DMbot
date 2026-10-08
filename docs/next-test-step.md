# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test B: the Stop listening button asks before it stops

**People:** 1 (you, as the DM)
**Time:** about 2 minutes
**Why:** the ⏹ **Stop listening** button now asks "are you sure?" first, so a slip of the thumb can't end the game. Check that **Cancel** keeps DMbot listening.

### Before you start
- Phone or computer, either is fine.
- Tell dev1 you're starting, so it can watch from its side.

### Steps
1. Join the voice channel you play in.
2. Type `/dmbot start`. Press **▶ Continue last campaign** (or pick your campaign), then **▶ Start listening**.
3. Open the channel whose name starts with **dmb-dm-screen**. Find DMbot's message that says it's listening.
4. Press **⏹ Stop listening**. A question appears that only you can see: "Stop listening and end the session for …?"
5. Press **Cancel**.
6. Type `/dmbot stop`.

### It worked if
- After **Cancel**, DMbot says **"Still listening."** and doesn't end the session.

**If not:** type `/dmbot stop` anyway, then tell dev1 below. Don't try again until dev1 answers.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
