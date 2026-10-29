# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test M: the backstory upload again (long document)

**People:** you (the DM), alone, on the test server
**Time:** about 10 minutes
**Why:** the last long upload reported "no new names", but the reading had been cut off, not finished. The fix reads the whole text in two passes, and a second run picks up where the first stopped. This checks a long document gives back its names and connections.

### Steps
1. In the test server, open the names menu (the names panel, then 📜 From my story, or 📜 Upload campaign lore if you see it).
2. Paste the link to the same long document you used last time.
3. Press **🤖 Find names**.
4. Wait for DMbot's reply in the DM screen. Don't press anything else while it works.
5. If the reply says a part couldn't be read, press **🤖 Find names** again. It picks up the rest.

### It worked if
- Step 4: the reply lists names and connections from the document (it does not say "no new names" for a long text).
- Step 5, if needed: a second press adds what the first one missed, and the reply says so.

**If not:** tell dev1 the step where it went wrong, and what the reply said (copy the message, but leave out any private names).

### Tell dev1
- **Worked** or **didn't work** for each step 1 to 5.
- How many names and connections the reply gave, and whether a part was left unread (a count is enough; no names or words).
- Anything that felt confusing.

---

*Test L (a real session with a recap) is paused until the recap fix is checked. Test K was done on 2026-10-18 (the owner, one person). Test I passed on 2026-10-17. The full record is in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
