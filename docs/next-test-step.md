# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test K: the new DM Bot channel

**People:** 1 (you, as the DM)
**Time:** about 5 minutes. No voice needed, and nothing is deleted or changed.
**Why:** settings and options used to crowd the DM screen. They now live in a channel of their own, so the DM screen keeps only what you need during the game. This checks that the new channel is there, that it works, and that the DM screen is tidier.

### Steps
1. In your server, look for a channel named like `dmb-dm-bot-` followed by a short version of your campaign's name. **If you can't find it:** type `/dmbot start`, press **▶ Continue last campaign** (don't press Start listening), then look again. Tell me if you had to do this.
2. Open it. There should be **one** message from DMbot with buttons on it, and nobody else can post there. As the DM you should see **⚙️ Settings** and also your own things such as a ⚙️ Menu, **📖 Look up a rule** and a **❓ Help** button. (The exact buttons may differ a little; tell me what you see.)
3. Press **⚙️ Settings**. The answer should be a **private** message, one only you can see.
4. In Settings, open **Homebrew**. Each kind (spells, monsters, items and so on) should show with a **count** next to it. Your 3 entries should be counted.
5. Go back to the channel and press **❓ Help**. It should answer privately too.
6. Open your **#dm-screen** channel (`dmb-dm-screen-…`). It should **not** have a Settings card or option buttons any more, only a one-line note saying settings and options are in the DM Bot channel.

### It worked if
- Step 2: the channel exists with one message of buttons, and you can see the Settings button.
- Step 3 and 5: the answers are private.
- Step 4: Homebrew shows each kind with a count.
- Step 6: the DM screen has no Settings card, just the pointer.

**If not:** tell me the step where it went wrong and what you saw (a screenshot is fine, with no private names in it).

### Tell me
- **Worked** or **didn't work** for each of steps 1 to 6, and anything that felt confusing.
- This test can't check what **a player** sees in the channel (they should get fewer buttons than you). That needs a second person, so it comes later.

---

*Test I (the new consent question) passed on 2026-10-17. The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
