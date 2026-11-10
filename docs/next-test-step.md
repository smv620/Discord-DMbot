# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test N: a PDF of a real adventure (20 to 50 MB)

**People:** you (the DM), alone, on the test server
**Time:** about 5 minutes
**Why:** the bot now reads PDFs of up to 100 MB, and a long adventure book is often 20 to 50 MB. This checks that Discord takes such a file and DMbot reads it, and that a text from the book gives names back.

### Steps
1. In the test server, open the names menu (the names panel, then 📜 From my story, or 📜 Upload campaign lore if you see it).
2. Upload a PDF of an adventure you own, between 20 and 50 MB. Use a file you have the right to use; DMbot does not check that.
3. Confirm the right to use it when DMbot asks.
4. Wait for DMbot's reply in the DM screen. It shows what stage it is at and how far it has got. Don't press anything else while it works.

### It worked if
- Step 2: Discord accepts the file, with no "file too large" message.
- Step 4: the reply finishes and gives a count of names and connections (not "no new names" for a whole adventure), and the progress keeps moving until it does.

**If not:** tell dev1 the step where it went wrong, and the exact words of any error (copy the message, but leave out any private names).

### Tell dev1
- **Worked** or **didn't work** for each step 1 to 4.
- The file's size in MB, and how many pages it has (a number is enough; no title or words from the book).
- How many names and connections the reply gave, and how long it took.
- Anything that felt confusing.

---

*The deploy 15 checks for the DM Bot channel (recap notices and the two-character transcript names) come after this one. Test M (the long backstory upload) was done before deploy 15. The full record is in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
