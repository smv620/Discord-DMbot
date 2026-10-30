# The owner's checks: one sitting, finished features

Last updated against `docs/GO_LIVE.md` at 2026-10-18.

These features are finished and on the server, but the website hides them until someone has watched each
one work once. You can clear most of them in one sitting on the test server. Each check is short: do the
steps, look for the sign it worked, then tell dev1.

**Names in the examples are made up. Use made-up names too.** Buttons are named as the Help page names
them. If a button is not where a check says, that is a mistake in this file: tell dev1 and WebDev will fix it.

**Before you start (once):**
1. Open your test server and type `/dmbot start`.
2. Press ＋ New campaign, or ▶ Continue last campaign if you already have one (do not make a duplicate).
3. Pick your voice channel and press ▶ Start listening. You are the game master.
4. Say yes to being recorded: in the private message DMbot sends you, press **I'm 16 or older, record me**. Without it, checks 2, 16, 17 and 18 hear nothing.

The steps are on the Help page: https://getdmbot.com/help. Keep this open on your phone and work down the
list. Checks 1 and 2 come first because they are the newest features. Check 19 waits for the next deploy.

**When you finish a check, tell dev1 in one line:** "Check N: worked" or "Check N: didn't (what you saw)".
dev1 writes it in `docs/testing-history.log`, and WebDev then switches the matching Help, News and home page
lines on. A check that didn't work is just as useful: say what you saw.

---

## Group A. Right after you start listening

### 1. (retired)
The people-and-groups channel is gone (#2875), so there is nothing to check. Numbers stay as they are.

### 2. Initiative: FightBot hears how your table runs it
**You'll do:**
1. Start a fight the way you normally do on the test server.
2. Say out loud: "we use side initiative".
3. Look at your DM screen's combat line.

**It worked if:** the line says **Initiative, as heard:** and names the side style. (There is no button for this
on purpose. You correct it by saying it differently.) If you are not sure how to start a fight on the test
server, ask dev1.

Unlocks: #1771

### 3. The DMbot channel with the buttons
**You'll do:**
1. Look for the channel **dmb-dm-bot**. Press 👤 DMbot Main Menu.
2. Press ❓ Help, then 📖 Look up a rule, and look up any rule.
3. Press ⚙️ Manage DMbot, then back out.

**It worked if:** the channel is there, only you see your buttons, the rule answer is private, and your DM
screen shows only notes, alerts and ⏹ Stop listening.

Unlocks: #1666

### 4. (retired)
The house rules channel is gone (#2875), so there is nothing to check. Numbers stay as they are.

---

## Group B. Names and the story (needs a name card)

Add one made-up person on the Names list first (type `/dmbot names`, press ➕ Add a name), for example
"Captain Rook", if you haven't. To open a name card later, press 🔍 Find a name on the Names list and pick it.
For Check 6 you also need a player character: press 🧑 Add a player's character on the Names list if you
have none.

### 5. How an NPC feels about the party (Standing)
**You'll do:** open Captain Rook's name card and press 🤝 Standing. Then press **Toward a character…** and set
a feeling.

**It worked if:** Standing shows Hostile, Unfriendly, Wary, Friendly or Devoted with the reason, and only you
can see it.

Unlocks: #1655

### 6. Recording a deed
**You'll do:** open a player character's name card, press ⚔️ Record a deed, type what happened, pick the person (Captain
Rook) and **Helped them**, and press ✅ Record it.

**It worked if:** Standing now shows the deed as a reason, for example "(session 1, helped)", and ↩ Undo that
works for a short while.

Unlocks: #1732

### 7. Story threads and promises
**You'll do:**
1. On the Names list (`/dmbot names`), press 🧵 New thread and make one, for example "Find the lost lantern".
2. Press 🤝 Add a promise and make one, with who promised and who it was promised to.

**It worked if:** both show on their cards with how far along they are, and nothing about them goes to
players.

Unlocks: #1662, #1674

### 8. Homebrew by kind
**You'll do:** press ⚙️ Manage DMbot, then 📚 Homebrew. Press a kind, then ↩ All kinds. Then press ➕ Add and pick
🧝 Species (or Subclass, or Background) as the kind, and add one made-up entry.

**It worked if:** you see one button for each kind with a count on it, and the new kinds are on the list.

Unlocks: #1663, #1665

---

## Group C. Takes a little longer

### 9. A quiet "where are we" line
**You'll do:** set the game clock under ⚙️ Manage DMbot, then Game clock. Leave listening on and don't touch the
clock for half an hour. It posts nothing when your DM screen is on Quiet, so keep it on Normal.

**It worked if:** one short line appears on your DM screen saying where the game is, and nothing is posted to
players.

Unlocks: #1738

### 10. Shared story: watching your adventure
**You'll do:**
1. Press ⚙️ Manage DMbot, then 🧭 Shared story, and choose **Watch the story**. Read the warning and
   press **I have the right**.
2. Press 📖 Give your adventure, then Give a file, and pick a short made-up text file you wrote yourself.
   Then press 📖 Read it now and Yes, read it.
3. Link one name to the adventure (open its card, press 📖 In my adventure…). Play a little.

**It worked if:** DMbot posts a line on the DM screen when your table no longer fits the adventure, and only
you see it. Use a made-up adventure, never a book you do not own.

Unlocks: #1692, #1714, #1724, #1726, #1729, #1730, #1731, #1734

---

## Group D. On the website

### 11. The support helper
**You'll do:** open your account on https://getdmbot.com/account, go to the Support form, and send one test
question.

**It worked if:** you get a thank-you email at once, then an answer from DMbot's helper, and your account page
shows who answered.

Unlocks: #1431, #1452

---

## Group E. Settings, fixes and the next deploy

Do 16, 17 and 18 before 15. Check 15 deletes a campaign.

### 12. Finding Settings
**You'll do:**
1. Type `/dmbot settings` anywhere in your test server.
2. Type `/dmbot start`, press ＋ New campaign (or ▶ Continue last campaign if one exists) and look at the step where you pick the voice channel. You do not need to press ▶ Start listening.

**It worked if:** Settings opens from the command and says only you can see it, and ⚙️ Manage DMbot is on the voice-channel step too.

Unlocks: #1619

### 13. Game masters
**You'll do:**
1. Press ⚙️ Manage DMbot, then 👑 Game masters.
2. Press ➕ Add a game master and pick a second test account of yours that is already in the server.
3. On that account, press ⚙️ Manage DMbot or type `/dmbot settings`.
4. Back on your account, press ➖ Remove a game master and take them off again. Try step 3 again.

**It worked if:** Settings opens for the second account after you add them, and does not once you take them off.

Unlocks: #1600, #1603

### 14. Splitting the party
**You'll do:**
1. Set the game clock first: press ⚙️ Manage DMbot, then Game clock, and give any day and hour. You do not need to wait for it.
2. Press Rests & time…, then ✂️ Split the party.
3. Name two groups, for example "Tower" and "Camp".
4. Press 🤝 Rejoin the party, pick a time to keep, then press Undo.

**It worked if:** each group gets its own clock after the split, the clocks come back together when you rejoin, and Undo puts the split back.

Unlocks: #1607

### 15. Deleting a whole campaign
**Do this last, on a throwaway campaign.** Make a second campaign first (`/dmbot start`, ＋ New campaign) and delete only that one. Read the name on the warning before you type it. Never do this on a campaign you play.

**You'll do:**
1. On the throwaway campaign, press ⏹ Stop listening if it is on. It cannot be deleted while it is being played. Only the game master whose plan it uses can delete it, which should be you.
2. Press ⚙️ Manage DMbot, then 🗑 Delete this campaign.
3. Read what will be lost. Press 💾 Back up first and wait for the file.
4. Press Delete it, then type the campaign's name.

**It worked if:** the warning showed before anything was deleted, the backup file arrived first, and the campaign no longer shows when you type `/dmbot start`. The transcript and chat channels may stay until a server admin removes them. That is expected.

Unlocks: #1608

### 16. Fixing a line DMbot heard wrong
**You'll do:**
1. Press ▶ Start listening and say one short sentence. Do not stop yet.
2. Wait a minute, then type `/dmbot fix`, or press ✏️ Fix a line on the DM screen.
3. Pick your sentence from the list of recent lines and type what it should say.
4. Press Undo, then stop listening.

**It worked if:** as game master the line changes at once, and Undo puts the old words back.

Unlocks: #1491, #1499

### 17. The note before tonight's session
**You'll do:**
1. Run a short session. Say a made-up name out loud, for example "Captain Rook". When DMbot asks "Did they mean…?" or lists names to confirm, confirm one. Stop listening.
2. Start listening again and look at the DM screen.

**It worked if:** a short note appears on the DM screen, for you only, with last time's recap and what you confirmed. Players do not see it.

Unlocks: #1403

### 18. A player says a secret name
**You'll do:**
1. Open the name's card (`/dmbot names`) and mark one made-up name secret, for example "Lady Vesper". The Help page's steps for secret names show how.
2. Start listening. Have a test player, who has also pressed **I'm 16 or older, record me** in their own private message, say that name out loud.
3. Press ⏹ Stop listening and look at your private messages.

**It worked if:** after you stop, DMbot asks you privately, one name at a time, whether it should stay secret or be opened up. Only you can answer.

Unlocks: #1407

### 19. What's switched on (after the next deploy)
This button is merged but not on the server yet. Do this check after dev1 deploys it.

**You'll do:**
1. Press ⚙️ Manage DMbot, then ℹ️ What's switched on.

**It worked if:** only you see the answer, and it lists what is on for your table.

Unlocks: #1831

---

## Not on this list (and why)

- **Time suggestions (#1758):** deployed, but switched off on the server until `DMBOT_TIME_SUGGESTIONS=1` is set, so
  there is nothing to check yet.
- **Secret clues (#1649):** need the story extractor, which stays off until its test passes.
- **QAbot (#1341):** needs the steps on #1341.
- **Heads-up when a fallen character speaks (#2068, #2078):** off until `DMBOT_LIVE_HEADS_UP=1`. It also needs a
  character confirmed as dead, and today only the story extractor (#1282) starts that, so there is nothing to check yet.
- **Bring in older sessions (#2105, #2106, #2107):** not built yet, so there is nothing to check yet.

The reasons are in `docs/GO_LIVE.md`.
