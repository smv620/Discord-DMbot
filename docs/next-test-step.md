# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test E: try the DM sidebar at the table

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Why:** the DM sidebar lets you ask DMbot a quick question in the middle of the game and get a short answer in your private chat with it. DMbot checked 19 sample questions and the Supervisor says it may go on. Now see it work for real.

### Before you start
- The sidebar is on (dev1 checked). It only answers **you, the DM**, while a game is running.
- **Where to ask matters.** DMbot answers in its **private chat with you** (open it from DMbot's name in the member list, **Message**), not in the **dmb-dm-screen** channel. Questions typed or sent as voice notes in the DM screen channel are ignored.

### Steps
1. Join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening**.
2. **Typed:** open your private chat with DMbot and type **"do you need line of sight for fireball?"**. Wait up to 30 seconds.
3. **Voice note:** in the same private chat, hold the microphone button and send a voice note: **"how much damage does fireball do?"**
4. **At the table, out loud** (in the voice channel, as the DM): say **"Hold on, I need to find if you need line of sight for fireball."** Use those words: DMbot only reacts to "hold on, I need to find / look up / check ...". Wait up to 30 seconds. The answer comes to your private chat.
5. Check each answer: short, names a source (like "SRD 5.2.1 p. 131") and says "sure" or "not sure".
6. Type `/dmbot stop`, then open the **raw** transcript: your questions and DMbot's answers show as lines tagged **[DM Sidebar]** and **DMbot**.

### It worked if
- Each question got a short answer in your **private chat** with DMbot (nothing in any public channel), and the raw transcript has the **[DM Sidebar]** lines.

**If not:** type `/dmbot stop`, then tell dev1. If an answer was wrong **and** said "sure", copy it for dev1.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
