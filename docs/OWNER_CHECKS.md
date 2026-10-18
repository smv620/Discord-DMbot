# The owner's checks: one sitting, finished features

Last updated against `docs/GO_LIVE.md` at 2026-10-18.

These features are finished and on the server, but the website hides them until someone has watched each
one work once. You can clear most of them in one sitting on the test server. Each check is short: do the
steps, look for the sign it worked, then tell dev1.

**Names in the examples are made up. Use made-up names too.** Buttons are named as the Help page names
them. If a button is not where a check says, that is a mistake in this file: tell dev1 and WebDev will fix it.

**Before you start (once):** open your test server, type `/dmbot start`, press ＋ New campaign (or ▶ Continue last campaign), pick your
voice channel and press ▶ Start listening. You are the game master. The steps are on the Help page:
https://getdmbot.com/help. You must have said yes to being recorded on the test server (the private message DMbot sent you), or checks 2, 16, 17 and 18 hear nothing. Keep this open on your phone and work down the list. Checks 1 and 2 come first
because they unlock the latest deploy.

**When you finish a check, tell dev1 in one line:** "Check N: worked" or "Check N: didn't (what you saw)".
dev1 writes it in `docs/testing-history.log`, and WebDev then switches the matching Help, News and home page
lines on. A check that didn't work is just as useful: say what you saw.

---

## Group A. Right after you start listening

### 1. The people-and-groups channel
**You'll do:**
1. Look in your server's channel list for one called **dmb-npcs** (it has a short name after it).
2. Type `/dmbot names` and pick it from the list that pops up. That is the Names list. Press ➕ Add a name and add one made-up person, for example "Captain Rook". Confirm them.
3. Look at dmb-npcs again.

**It worked if:** the channel is there, Captain Rook is listed with the other names and groups you gave them,
and you cannot type in the channel.

Unlocks: #1761

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
1. Look for the channel **dmb-dm-bot**. Press 👤 My buttons.
2. Press 📖 Look up a rule and look up any rule.
3. Press ⚙️ Settings, then back out.

**It worked if:** the channel is there, only you see your buttons, the rule answer is private, and your DM
screen shows only notes, alerts and ⏹ Stop listening.

Unlocks: #1666

### 4. The house rules channel
**You'll do:**
1. Press ⚙️ Settings and add one made-up house rule, for example "Rope takes a full turn to tie".
2. Look for the channel **dmb-rules**.

**It worked if:** the channel is there, your rule appears as its own message, and you cannot type in the
channel.

Unlocks: #1752

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
**You'll do:** open a player character's name card, press ⚔️ Record a deed, type what happened, pick Captain
Rook and **Helped**, and press ✅ Record it.

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
**You'll do:** press ⚙️ Settings, then 📚 Homebrew. Press a kind, then ↩ All kinds. Then press ➕ Add and pick
🧝 Species (or Subclass, or Background) as the kind, and add one made-up entry.

**It worked if:** you see one button for each kind with a count on it, and the new kinds are on the list.

Unlocks: #1663, #1665

---

## Group C. Takes a little longer

### 9. A quiet "where are we" line
**You'll do:** set the game clock under ⚙️ Settings, then Game clock. Leave listening on and don't touch the
clock for half an hour. It posts nothing when your DM screen is on Quiet, so keep it on Normal.

**It worked if:** one short line appears on your DM screen saying where the game is, and nothing is posted to
players.

Unlocks: #1738

### 10. Shared story: watching your adventure
**You'll do:**
1. Press ⚙️ Settings, then 🧭 Shared story, and choose **Watch the story**. Read the warning and press "I have the
   right".
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

### 12. Finding Settings
**You'll do:**
1. Type `/dmbot settings` anywhere in your test server.
2. Then type `/dmbot start`, press ＋ New campaign (or ▶ Continue last campaign) and look at the step where you pick the voice channel.

**It worked if:** Settings opens for you alone from the command, and ⚙️ Settings is on the voice-channel step too.

Unlocks: #1619

### 13. Game masters
**You'll do:**
1. Press ⚙️ Settings, then 👑 Game masters.
2. Press ➕ Add a game master and pick a second made-up test account, or a friend you trust.
3. Press ➖ Remove a game master and take them off again.

**It worked if:** the person you added can open the DM screen's Settings straight away, and cannot once you take them off.

Unlocks: #1600, #1603

### 14. Splitting the party
**You'll do:**
1. On the clock message, press Rests & time…, then ✂️ Split the party.
2. Name two groups, for example "Tower" and "Camp".
3. Press 🤝 Rejoin the party, then Undo.

**It worked if:** each group gets its own clock after the split, the clocks come back together when you press Rejoin the party, and Undo puts the split back.

Unlocks: #1607

### 15. Deleting a whole campaign
**Use a throwaway campaign you do not mind losing.**

**You'll do:**
1. Press ⚙️ Settings and press Delete this campaign.
2. Read the warning. Press 💾 Back up first and let it finish.
3. Type the campaign's name when it asks.

**It worked if:** the warning shows before anything is deleted, the backup arrives first, and the campaign and its channels are gone after you type the name.

Unlocks: #1608

### 16. Fixing a line DMbot heard wrong
**You'll do:**
1. Say a short sentence while listening, then stop.
2. Type `/dmbot fix`, or press ✏️ Fix a line on the DM screen, pick the line, and type what it should say.
3. Press Undo while the session is still running.

**It worked if:** as game master the line changes at once, and Undo puts the old words back.

Unlocks: #1491, #1499

### 17. The note before tonight's session
**You'll do:**
1. Run a short session on the test server and confirm at least one name when DMbot asks, then stop.
2. Start listening again the next day, or after a pause, and look at the DM screen.

**It worked if:** a short note appears on the DM screen, for you only, with last time's recap and what you confirmed. Players do not see it.

Unlocks: #1403

### 18. A player says a secret name
**You'll do:**
1. Mark one made-up name secret, for example "Lady Vesper".
2. Have a test player say that name out loud while recording.
3. After the session, look at your private messages.

**It worked if:** DMbot asks you privately, one name at a time, whether it should stay secret or be opened up. Only you can answer.

Unlocks: #1407

### 19. What's switched on (after the next deploy)
This button is merged but not on the server yet. Do this check after dev1 deploys it.

**You'll do:**
1. Press ⚙️ Settings, then ℹ️ What's switched on.

**It worked if:** only you see the answer, and it lists what is on for your table.

Unlocks: #1831

---

## Not on this list (and why)

- **Time suggestions (#1758):** deployed, but switched off on the server until `DMBOT_TIME_SUGGESTIONS=1` is set, so
  there is nothing to check yet.
- **Secret clues (#1649):** need the story extractor, which stays off until its test passes.
- **Recaps and narrated recaps (#1382, #1385):** need `DMBOT_RECAPS=1`, and narration needs your ElevenLabs go-ahead.
- **QAbot (#1341):** needs the steps on #1341.

The reasons are in `docs/GO_LIVE.md`.
