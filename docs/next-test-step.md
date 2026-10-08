# Next test step

**Just one small test.** When it's done, tell dev1 what happened. dev1 writes it down and puts the next test here.

---

## Test D: no false warning with a TV on

**People:** 1 (you, as the DM)
**Time:** about 3 minutes
**Why:** on Oct 8 a TV in the room set off DMbot's ⚠️ warning even though everything you said was written down. DMbot now reads the transcript before it warns. Check that a noisy room gives no false alarm.

### Before you start
- **dev1 must update DMbot first.** Check that dev1 has said the update is done.
- Turn on a TV (or music with talking) in the room, at a normal volume.
- DMbot may ask you to agree to recording again after the update. If it does, press **I consent**.
- Tell dev1 you're starting, so it can watch from its side.

### Steps
1. Join the voice channel you play in.
2. Type `/dmbot start`. Press **▶ Continue last campaign** (or pick your campaign), then **▶ Start listening**.
3. Say these three sentences clearly, with a short pause between them:
   - **"The party walks into the tavern and orders three drinks."**
   - **"The innkeeper says the road north is closed."**
   - **"Roll for initiative."**
4. Let the TV play for about one minute while you stay quiet.
5. Look at the channel whose name starts with **dmb-dm-screen**, and the one whose name starts with **dmb-transcript**.
6. Type `/dmbot stop`.

### It worked if
- **No ⚠️ warning** appears on the DM screen, **and** your three sentences are in the **dmb-transcript** channel.

**If not:** type `/dmbot stop` anyway, then tell dev1 below. Don't try again until dev1 answers. If a ⚠️ line showed, copy it for dev1.

### Tell dev1
- **Worked** or **didn't work**.
- If it didn't: what you saw (or didn't see) at the step where it went wrong.

---

*The full test plan and every result are in `docs/testing-status.log` and `docs/testing-history.log`. This page is only the next step.*
