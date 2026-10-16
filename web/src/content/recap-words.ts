// The words about recaps outside the recap pages (#1384): Help, the emails list and the privacy
// page. Recaps are built but OFF on the server until dev1 has checked a real one (DMBOT_RECAPS,
// core/src/dmbot/config.py), so none of these words may show before then: Help would describe a
// feature nobody has, and the privacy page a company that gets nothing yet.
//
// HOW IT TURNS ON: when dev1 has checked a real recap and the What's new entry "Recaps for your
// table" moves out of `held` (whats-new.ts), set RECAPS_LIVE to true in the same change. A test
// ties the two together and fails until both are done.
//
// RECAP_AUDIO_LIVE is the narrated version (#1383): when a recap can have a narrator and music, and
// ElevenLabs gets its script. Turning it on also needs: the owner's go-ahead on naming ElevenLabs,
// ElevenLabs in the "Companies that help us" list and its country in the sentence under it (not
// known today, so not guessed here), and a What's new entry. It can only be true with RECAPS_LIVE.
export const RECAPS_LIVE = false;
export const RECAP_AUDIO_LIVE = false;

export const recapsHeading = "Recaps";

/** Under the game master's steps on the Help page. Each sentence rests on core/src/dmbot/recaps/
 * and bot.py (post_recaps, recap_inputs): see the pull request for the lines. */
export const recapSteps: { text: string }[] = [
  {
    text: "After a session, DMbot can write a short story-style recap of what happened, to read at the start of next time. Recaps are for paid plans.",
  },
  {
    text: "It uses only what was said at the table by people who said yes to being recorded. It never makes things up, and it never uses your secrets or your notes.",
  },
  {
    text: "DMbot emails you when a recap is ready. The email has the title and a link to your account page, never the recap itself.",
  },
  {
    text: "You read it first. You can change the words, press Try again (up to twice), or press Don't post. Nothing goes to your players until you press Post to the table.",
  },
  {
    text: "Post to the table puts the recap in your table's chat channel, dmb-chat. If a player has stopped being recorded since it was written, DMbot won't post it.",
  },
  ...(RECAP_AUDIO_LIVE
    ? [
        {
          text: "A recap can also come with a narrator's voice and music, as two audio files you play together.",
        },
      ]
    : []),
];

/** One more line in "Emails from DMbot". */
export const recapEmailStep =
  "Recap emails: when a recap is ready, or could not be written, DMbot emails you the title (or the reason) and a link. They come only to game masters, once for each recap, and have no link to stop them.";

/** Said in place of "The only others are two Try It emails, each sent once." */
export const emailsIntroTail = RECAPS_LIVE
  ? "The only others are two Try It emails, each sent once, and an email to a game master when a recap is ready."
  : "The only others are two Try It emails, each sent once.";
