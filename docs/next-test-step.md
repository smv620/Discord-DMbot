# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test I: the new consent question

**People:** 1 (you, as the DM)
**Time:** about 5 minutes
**Why:** the big update is on the server. DMbot's words about recording changed, because it now also reads the table's chat. So everyone who agreed before is asked again, and nobody is recorded until they say yes. This checks that.

### Steps
1. Join your voice channel. Type `/dmbot start`, press **▶ Continue last campaign**, then **▶ Start listening** (once only).
2. DMbot sends you a **private message** with a new question. Read it: it should say DMbot also reads the table's chat. **Don't press anything yet.**
3. Say out loud: **"This sentence is before I agreed."**
4. In the private message, press **I'm 16 or older, record me**. DMbot confirms, and the message has a **⚙️ Menu** button.
5. Say out loud: **"This sentence is after I agreed."**
6. Leave the voice channel and join it again. DMbot sends a short private reminder with the date you agreed and a **⚙️ Menu** button.
7. Type `/dmbot stop`. Press the transcript download.

### It worked if
- Step 2: you were asked again, even though you agreed before, and the words mention the table's chat.
- Step 4: DMbot confirmed it, with a **⚙️ Menu** button.
- Step 6: the reminder shows the date and **⚙️ Menu**.
- Step 7: the transcript has the "after" sentence and **not** the "before" one.

**If not:** type `/dmbot stop`, then tell dev1 the step where it went wrong and what you saw.

### Tell dev1
- **Worked** or **didn't work** for each of steps 2, 4, 6 and 7, and anything that felt confusing.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
