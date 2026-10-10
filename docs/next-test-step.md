# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test E: try the DM sidebar at the table

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Why:** the DM sidebar lets you ask DMbot a quick question in the middle of the game and get a short answer on your DM screen. DMbot checked 19 sample questions and the Supervisor says it may go on. Now see it work for real.

### Before you start
- **dev1 must say the sidebar is on first.** It is a setting that is off until you or dev1 switch it on. If dev1 hasn't said so, tell dev1.
- Join the voice channel you play in.

### Steps
1. Type `/dmbot start`. Press **▶ Continue last campaign**, then **▶ Start listening**.
2. Ask one question by voice, clearly, for example: **"DMbot, do you need line of sight for fireball?"**
3. Ask one question in the **dmb-dm-screen** channel by typing it, for example: **"how much damage does fireball do"**.
4. Look at **dmb-dm-screen**: each answer should be short, say where it comes from (like "SRD 5.2.1 p. 131"), and say "sure" or "not sure".
5. Type `/dmbot stop`, then open the **raw** transcript: your two questions and DMbot's answers should show as lines tagged **[DM Sidebar]** and **DMbot**.

### It worked if
- Both questions got a short answer on the DM screen **only** (nothing in the public channels), and the raw transcript has the **[DM Sidebar]** lines.

**If not:** type `/dmbot stop`, then tell dev1. If an answer was wrong **and** said "sure", copy it for dev1.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
