// Help for the website pages and table features built in the night of 2026-10-22/23 (#2498): the
// campaign cards, a campaign's page, the story timeline, Shared rules, settings on the website, the
// graph's views, going off the record and the links to a moment in a transcript.
//
// Help must never describe something that isn't live (#1322), so each entry is tied to the What's new
// entry that announces it: while that entry is in `held`, its Help step is not shown. Delete the
// `held` line there and the step appears, with no second switch to forget. A test checks that every
// title below is a real What's new entry, so a rename can't leave a step hidden for ever.
import { entries, held } from "./whats-new";
import type { Step } from "./help";

export type Audience = "gameMaster" | "player" | "account";

export interface FeatureStep extends Step {
  /** Who reads it: the game master's steps, the players' steps, or the account section. */
  audience: Audience;
  /** The What's new entry (by title) that announces this feature. */
  whatsNew: string;
}

/** True once the entry is out of `held`: deployed, and not known to fail. */
export const isLive = (title: string): boolean => entries.some((e) => e.title === title);

export const featureSteps: FeatureStep[] = [
  {
    audience: "account",
    whatsNew: "My Account shows each campaign at a glance",
    text: "On My Account, each campaign is a card. Tap its name to open its page. 🟢 Listening now means a session is running. The card also says your part: You run it, You help run it or You play in it. It shows sessions, hours, players, NPCs and when it started and was last played. Anything DMbot doesn't know yet is left out.",
  },
  {
    audience: "account",
    whatsNew: "Receive a campaign by email",
    text: "When someone hands a campaign over to you, you get an email with a link that works for 7 days. Open it and sign in. You see which campaign it is and who offered it. Pick the server it should run on, then accept, or say no thanks. You need a paid plan with a free campaign slot, and Try It can't receive a campaign. If the link has ended, ask the game master to hand the campaign over again.",
  },
  {
    audience: "account",
    whatsNew: "Receive a campaign by email",
    text: "When someone hands a campaign over to you, you get an email with a link that works for 7 days. Open it and sign in. You see which campaign it is and who offered it. Pick the server it should run on, then accept, or say no thanks. You need a paid plan with a free campaign slot, and Try It can't receive a campaign. If the link has ended, ask the game master to hand the campaign over again.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your campaign has its own home",
    text: "Each campaign has its own page on the website, in four parts: Story, At the table, Rules and Settings. Settings is only for game masters. A trail at the top shows where you are, and four tabs let you jump to another part.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your story timeline",
    text: "A campaign's Story timeline lists every session in order, with the day it was played, how long it ran, its recap and the names the players learned that session. Players see only the titles of recaps you posted.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your shared rules in one place",
    text: "My Account has a Shared rules page. For each campaign you run, it lists the house rules and homebrew packs you share with other tables, and the ones you use from other tables. It is for looking only. To share, stop sharing or follow a set, do it in Discord: ⚙️ Settings, then 📚 Shared rules.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Change a campaign's settings on the website",
    text: "On a campaign's Settings tab, game masters can change who can see the DM screen, how much DMbot says, and how long transcripts are kept. The page says it is saving, then that it is saved. If you pick a shorter time for keeping transcripts, you see a warning first, and nothing is deleted until you say yes on the page. Changes need DMbot to be online. If it is away, your change waits for up to an hour. If DMbot is not back by then, the change is dropped and you can choose again. Hand over, pause and delete are still done in Discord, and only the campaign's owner sees them.",
  },
  {
    audience: "gameMaster",
    whatsNew: "See your story as players do",
    text: "The Story Graph has two views for game masters. Full View shows everything, secrets included. Players View shows exactly what your players see, so you can check it before you share your screen. Unless you turn peeking off, a player can press Peek behind the DM screen, and you are told when they do.",
    names: [{ name: "Peek behind the DM screen", file: "dm_screen/messages.py" }],
  },
  {
    audience: "gameMaster",
    whatsNew: "Pin a connection as 100% truth",
    text: "In Full View you can pin a connection you know is true, with the 📌 Pin as 100% truth button, and unpin it later. Pins are your call: DMbot never pins anything itself, and only game masters see pins.",
  },
  {
    audience: "gameMaster",
    whatsNew: "See where a new name was said",
    text: "When DMbot asks you to check a name, the link 📜 Open in transcript opens that session on the website at the line where the name was said, with that line marked. Anyone who can read the transcript can use the link.",
  },
  {
    audience: "player",
    whatsNew: "Go off the record for a while",
    text: "Need a break from being recorded? Press ⏸️ Pause recording me in your 🗂️ Menu, or on the reminder DMbot sends when a session starts. Pick 5 minutes, 15 minutes or Until I resume. While you are paused, nothing you say is recorded. It does not remove what you said before you paused. Press ▶️ Resume now to come back sooner. A pause ends when the session ends. Your yes stays as it was, and Stop recording me is still in the menu.",
    names: [
      { name: "Pause recording me", file: "consent_dm.py" },
      { name: "Resume now", file: "consent_dm.py" },
      { name: "Stop recording me", file: "consent_dm.py" },
    ],
  },
  {
    audience: "gameMaster",
    whatsNew: "Go off the record for a while",
    text: "To take a break from recording the whole table, press ⏸️ Pause the table on the Listening message and pick how long. While the table is paused, nothing anyone says is recorded. What was said before the pause stays recorded. The break ends when the time is up, when you press ▶️ Resume now, or when the session ends.",
    names: [
      { name: "Pause the table", file: "dm_screen/messages.py" },
      { name: "Resume now", file: "dm_screen/table_pause.py" },
    ],
  },
];

/** The steps for one audience that are live now, in order. */
export const liveFeatureSteps = (audience: Audience): Step[] =>
  featureSteps
    .filter((s) => s.audience === audience && isLive(s.whatsNew))
    .map(({ text, names, link }) => ({ text, ...(names ? { names } : {}), ...(link ? { link } : {}) }));

/** Every title above is a real What's new entry, held or live (the test uses this). */
export const knownTitle = (title: string): boolean => entries.some((e) => e.title === title) || held.some((h) => h.title === title);
