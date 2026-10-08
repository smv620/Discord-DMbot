# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test C: Yes, stop ends the session

**People:** 1 (you, as the DM)
**Time:** about 2 minutes
**Why:** Test B showed **Cancel** keeps DMbot listening. This checks the other answer: **Yes, stop** really ends the session.

### Before you start
- Phone or computer, either is fine.
- Tell dev1 you're starting, so it can watch from its side.

### Steps
1. Join the voice channel you play in.
2. Type `/dmbot start`. Press **▶ Continue last campaign** (or pick your campaign), then **▶ Start listening**.
3. Open the channel whose name starts with **dmb-dm-screen**. Find DMbot's message that says it's listening.
4. Press **⏹ Stop listening**. A question appears that only you can see: "Stop listening and end the session for …?"
5. Press **Yes, stop**.

### It worked if
- DMbot shows **"Stopping…"**, then the session ends: DMbot leaves the voice channel and the transcript channel shows **Session ended**.

**If not:** type `/dmbot stop` anyway, then tell dev1 below. Don't try again until dev1 answers.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
