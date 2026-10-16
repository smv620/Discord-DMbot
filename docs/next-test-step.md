# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test J: keeping and deleting transcripts

**People:** 1 (you, as the DM)
**Time:** about 5 minutes. No voice needed, and nothing is deleted.
**Why:** this update adds a way to choose how long DMbot keeps a campaign's transcripts, and a button that deletes yours. We only need to see that the screens open, that your choice is saved, and that the warning is clear.

### Steps
1. In your server, type `/dmbot start` and press **▶ Continue last campaign** (don't press Start listening).
2. Open **⚙️ Settings**, then **📜 Transcripts**. You should see a list of choices for how long to keep transcripts.
3. Pick one choice (any one). DMbot should say it saved it. Open **📜 Transcripts** again: your choice should still be picked. Then pick the choice that keeps them as before, so nothing is lost.
4. Open the **⚙️ Menu** (the button DMbot's private reminders carry, or the one in Settings). Press **🗑 Delete my transcripts**. Read the warning. It should be one short message with a button to confirm and a way to cancel. **Press cancel. Do not confirm.**
5. Open **⚙️ Settings**, then **📚 Shared rules**, then **📦 Homebrew packs**. It should open (an empty list is fine).

### It worked if
- Step 2: the list of choices opens.
- Step 3: the choice is saved and still picked when you reopen it.
- Step 4: the warning is short and clear, and cancelling leaves everything as it was.
- Step 5: Homebrew packs opens.

**If not:** tell dev1 the step where it went wrong and what you saw (a screenshot of the message is fine, with no private names in it).

### Tell dev1
- **Worked** or **didn't work** for each of steps 2 to 5, and anything that felt confusing.

---

*Test I (the new consent question; 1 person, 5 minutes) is still waiting and goes next. The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
