# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test L: a real session with a recap

**People:** you (the DM) and at least one player who has said yes to being recorded
**Time:** about 35 minutes of play, then a few minutes on the recap page
**Why:** the recap is now on for real sessions. Nothing is posted or spoken until you approve it. This checks the whole path once: the recap waits, you check the text, and only then the voice and music are made.

### Steps
1. Start a normal session as usual (`/dmbot start`, then Start listening). Play at least 35 minutes.
2. During the game, say one secret name aloud, one of your secrets, so the bot hears it.
3. Stop listening. The DM screen should show **📜 Writing a recap…**. No audio should start by itself.
4. Open the recap page (the link in your DM screen). Check that:
   - a bad cue such as 《tensed》 is refused with a "did you mean" hint;
   - you can approve the text, then press **Both** to make the voice-over and the music.
5. When you're asked whether the secret name should be kept secret, press **No, keep it secret**. It should not ask a second time in that session.

### It worked if
- Step 3: the DM screen says "Writing a recap…" and no audio starts before you approve.
- Step 4: the bad cue is refused, and Both makes the voice-over and music.
- Step 5: the secret question appears once.

**If not:** tell dev1 the step where it went wrong and what you saw (a screenshot is fine, with no private names in it).

### Tell dev1
- **Worked** or **didn't work** for each step 1 to 5, and anything that felt confusing.

---

*Test K was done on 2026-10-18 (the owner, one person). Test I passed on 2026-10-17. The full record is in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
