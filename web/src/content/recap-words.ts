// The words about recaps outside the recap pages (#1384): Help, the emails list and the privacy
// page. Recaps are built but OFF on the server until dev1 has checked a real one (DMBOT_RECAPS,
// core/src/dmbot/config.py), so none of these words may show before then: Help would describe a
// feature nobody has, and the privacy page a company that gets nothing yet.
//
// HOW IT TURNS ON: when dev1 has checked a real recap and the What's new entry "Recaps for your
// table" moves out of `held` (whats-new.ts), set RECAPS_LIVE to true in the same change. A test
// ties the two together and fails until both are done. It also shows My Account's "Read your
// session recaps" link and the recap list's lead and email promise (#1401).
//
// RECAP_AUDIO_LIVE is the narrated version (#1383): when a recap can have a narrator and music, and
// ElevenLabs gets its script. Turning it on also needs: the owner's go-ahead on naming ElevenLabs,
// ElevenLabs in the "Companies that help us" list and its country in the sentence under it (not
// known today, so not guessed here), and a What's new entry. It can only be true with RECAPS_LIVE.
//
// RECAP_NAMES_LIVE is the names-to-check part of a recap (#2027, #2030, #2031): held until dev1
// deploys #2030, because until then no recap has the "Facts to be confirmed" list or the private
// message. It moves with the What's new entry "New names from your recap" and can only be true with
// RECAPS_LIVE.
export const RECAPS_LIVE = true;
export const RECAP_AUDIO_LIVE = false;
export const RECAP_NAMES_LIVE = false;
// RECAP_FIX_LIVE is "Tell DMbot what to fix" on the recap page (#2113, #2114): a typed note, sorted by
// the bot and polled by the page, then a rewrite with the points the DM kept. It needs the web API's
// new routes deployed, so it stays off until dev1 has deployed #2113 and seen it work. It moves with
// the What's new entry "Tell DMbot what to fix" and can only be true with RECAPS_LIVE.
export const RECAP_FIX_LIVE = false;
// RECAP_VOICE_LIVE is "Speak instead" under that box (#2113 part 2, #2114): the DM records a short note,
// DMbot turns it into words and deletes the recording. It needs the voice routes deployed and the
// microphone header (public/_headers) live, and dev1 to have seen a note spoken and heard back. It moves
// with the What's new entry "Speak a note to DMbot" and can only be true with RECAP_FIX_LIVE.
export const RECAP_VOICE_LIVE = false;
// RECAP_SHARE_LIVE is the shared recap link (#2118, #2119): a game master shares a posted recap by a
// link anyone can open without signing in (owner decision, 2026-10-20). It moves with the What's new
// entry for the link, and can only be true with RECAPS_LIVE. Until then the privacy page says nothing
// of it, so it never describes a link nobody can make.
export const RECAP_SHARE_LIVE = false;

export const recapsHeading = "Recaps";

// How many recaps a campaign can have in a calendar month (UTC), and how many of those can be
// narrated (#1428, #1449). These MIRROR the server's defaults, DMBOT_RECAPS_PER_MONTH and
// DMBOT_RECAP_AUDIO_PER_MONTH (.env.example): change them together, or the site promises a number
// the bot does not keep. Free access is not limited.
export const RECAPS_PER_MONTH = 10;
export const RECAP_AUDIO_PER_MONTH = 8;

/** The one sentence about the monthly allowance, for the plans FAQ and Help. Empty while recaps
 * are off (it would promise a feature nobody has); the narrated number only while narration is. */
export const allowanceSentence = (recapsLive: boolean, audioLive: boolean): string =>
  !recapsLive
    ? ""
    : audioLive
      ? `Each campaign gets up to ${RECAPS_PER_MONTH} recaps a month. Up to ${RECAP_AUDIO_PER_MONTH} of those can be narrated, with a voice and music.`
      : `Each campaign gets up to ${RECAPS_PER_MONTH} recaps a month.`;

/** The Help step for the names a recap asks you to check (#2031). A function so a test can read both
 * wordings while the switch is off. The narration is named only while narration is on: nothing here
 * may promise it before. */
export const recapNamesStep = (
  audioLive: boolean,
): { text: string; names: { name: string; file: string }[] } => ({
  text: `When a recap uses a name DMbot doesn't know yet, DMbot tells you on the DM screen and in a private message; press 📝 Check new names to look at them. Nothing is added to your game until you say so. Your copy of the recap ends with Facts to be confirmed, a list of those names; it is never posted to the table${
    audioLive ? ", and the narration never reads it" : ""
  }. DMbot keeps a new name only if someone said it at the table, and fixes the spelling when it misheard a name.`,
  names: [{ name: "📝 Check new names", file: "ui/names.py" }],
});

/** Under the game master's steps on the Help page. Each sentence rests on core/src/dmbot/recaps/
 * and bot.py (post_recaps, recap_inputs): see the pull request for the lines. */
export const recapSteps: { text: string; names?: { name: string; file: string }[] }[] = [
  {
    text: "After a session, DMbot can write a short story-style recap of what happened, to read at the start of next time. Recaps are for paid plans.",
  },
  {
    text: "A recap's length follows your session: about 200 words for a one-hour game, about 400 for three hours. A game of six hours or more gets about 700 words, the longest.",
  },
  {
    text: "It is written only from what was said by people who said yes to being recorded, and DMbot checks the sentences against what was said, has it written again once if one doesn't match, and cuts any that still don't. If too little is left, there is no recap. It also takes out compass directions. It leaves out any name you marked secret. Anything said out loud at the table can still be in it, so read it before you post.",
  },
  ...(RECAP_NAMES_LIVE ? [recapNamesStep(RECAP_AUDIO_LIVE)] : []),
  {
    text: "DMbot tells you on the DM screen when a recap is ready. It also emails you, unless you turn that off, and the email says why if a recap couldn't be written; with the email off, the DM screen tells you it couldn't. The email has the title (or the reason), and a link when there is something to do, never the recap itself.",
  },
  {
    text: "On the DM screen, press Read it to read the recap there. Press Post to the table to send it to your players; DMbot asks once first. Press Not this one to skip it; you can still find it on your recap page. To change the words first, use the link in the message.",
    names: [
      { name: "Read it", file: "ui/recap_dm.py" },
      { name: "Post to the table", file: "ui/recap_dm.py" },
      { name: "Not this one", file: "ui/recap_dm.py" },
    ],
  },
  {
    text: `Press Recaps on ⚙️ Settings to stop the recaps, to stop only the email (the DM screen still tells you when a recap is ready)${
      RECAP_AUDIO_LIVE ? ", or, on plans with narration, to stop the narration" : ""
    }. You can turn them back on there. Your plan decides what you can have.`,
    names: [{ name: "Recaps", file: "dm_screen/recap_panel.py" }],
  },
  {
    text: "You read it first. You can change the words. Try again writes a new one and throws away your changes; you can do that up to twice. Or press Don't post, which is the same as Not this one on the DM screen. Nothing goes to your players until you press Post to the table.",
  },
  {
    text: "Post to the table puts the recap in your table's chat channel, dmb-chat. It can also list hit points, items and conditions. DMbot checks again first: if a player has stopped being recorded since it was written, or it names something you keep secret, it won't post and tells you why. It can take about a minute.",
  },
  ...(RECAP_FIX_LIVE
    ? [
        {
          text: "To change a recap without editing it yourself, open it on the website, type what you'd like changed under Tell DMbot what to fix, and press Send. DMbot sorts your note into points. Remove any you don't want and keep the style notes you do; a style note you keep is saved for this campaign's later recaps too. DMbot keeps the newest 10. You can remove them in ⚙️ Settings → 📜 Recaps → ✍️ Recap style. Then choose Rewrite with these. That uses one Try again, and you approve the new text again. DMbot only sorts what you wrote and rewrites with what you kept; it never adds story, and you decide what is kept.",
        },
      ]
    : []),
  ...(RECAP_VOICE_LIVE
    ? [
        {
          text: "Under that box you can press Speak instead and say your note (up to a minute). Your browser asks to use the microphone the first time. DMbot turns what you said into words in the box and deletes the recording as soon as the words are ready. Check the words, change them if you like, then press Send. A note you speak counts toward your listening time, and DMbot only listens to people who have agreed to be recorded.",
        },
      ]
    : []),
  ...(RECAP_SHARE_LIVE
    ? [
        {
          text: "To share a posted recap, open it on the website and choose Make a link. Anyone who has the link can read the recap without signing in, even people outside your Discord server. Only a posted recap can be shared, never a draft.",
        },
        {
          text: "Treat the link like a password: anyone you give it to can pass it on. Choose Turn the link off, or Make a new link, whenever you like. The old link then stops opening the recap, but that can't take back what someone already read or saved.",
        },
      ]
    : []),
  ...(RECAP_AUDIO_LIVE
    ? [
        {
          text: "A recap can also come with a narrator's voice and music, as two audio files you play together.",
        },
        {
          text: "DMbot makes no audio by itself. Read the recap, then press Approve the text, on the DM screen or on your recap page, and pick voice-over, background music, both, or text only (no audio). You only see what your plan includes. If you change the text later, you approve it again. If a mood or pause mark in the text is wrong, DMbot shows what to fix, and nothing is approved until you fix it.",
        },
      ]
    : []),
  {
    // These steps show only while recaps are on (Help hides the section otherwise).
    text: `${allowanceSentence(true, RECAP_AUDIO_LIVE)} If you use them all, DMbot tells you on the DM screen, and the next recap comes on the 1st of next month.`,
  },
];

/** One more line in "Emails from DMbot". */
export const recapEmailStep =
  "Recap emails: DMbot emails you when a recap is ready, or when it couldn't write one. Each has the title (or the reason), and a link when there is something to do. It goes to one game master of the campaign, once for each recap. They have no link to stop them.";

/** Said in place of "The only others are two Try It emails, each sent once." */
export const emailsIntroTail = RECAPS_LIVE
  ? "The only others are two Try It emails, each sent once, and an email to a game master when a recap is ready or could not be written."
  : "The only others are two Try It emails, each sent once.";
