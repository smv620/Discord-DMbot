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
    text: "On My Account, each campaign is a card. Tap its name to open the campaign's page. The card says 🟢 Listening now while a session is running, and your part in it: You run it, You help run it or You play in it. It also shows the sessions played, hours played, players, NPCs, the day it started and the day it was last played. A fact DMbot doesn't know yet is left out.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your campaign has its own home",
    text: "Each campaign has its own page on the website, in four parts: Story, At the table, Rules and Settings. Settings is only for game masters. The top of every campaign page shows where you are, from My Account to the campaign to the page, and the four parts as tabs, so you can jump to another part or go back.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your story timeline",
    text: "A campaign's Story timeline lists every session in order, with the day it was played, how long it ran, its recap and the names shown to the players after it. Players see the titles of recaps that were posted, and nothing else from the recap.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your shared rules in one place",
    text: "My Account has a Shared rules page. For each campaign you run, it lists the house rules and homebrew packs you share with other tables, and the ones you use from other tables. It is for looking only. To share, stop sharing or follow a set, do it in Discord: ⚙️ Settings, then 📚 Shared rules.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Change a campaign's settings on the website",
    text: "On a campaign's Settings tab, game masters can change who can see the DM screen, how much DMbot says, and how long transcripts are kept. While DMbot saves a change the page says it is saving, and then that it is saved. If you pick a shorter time for keeping transcripts, the page shows the same warning as Discord and deletes nothing until you say yes on the page. If DMbot is away, it makes the change when it's back, as long as that is within an hour. Hand over, pause and delete are still done in Discord, and only the campaign's owner sees those steps.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Pin a connection as 100% truth",
    text: "The Story Graph has two views for game masters: Full View shows everything, secrets included, and Players View shows exactly what your players see, so you can check it before you share your screen. In Full View you can pin a connection you know is true, with the 📌 Pin as 100% truth button, and unpin it later. Only you see pins. A player can press Peek behind the DM screen only if you allow it, and you are told when they do.",
    names: [{ name: "Peek behind the DM screen", file: "dm_screen/messages.py" }],
  },
  {
    audience: "gameMaster",
    whatsNew: "See where a new name was said",
    text: "When DMbot asks you to check a name, the link 📜 Open in transcript opens that session on the website at the line where the name was said, with that line marked. Links with a moment in them work for anyone who may read the transcript.",
  },
  {
    audience: "player",
    whatsNew: "Go off the record for a while",
    text: "To take a break from being recorded, press ⏸️ Pause recording me in your 🗂️ Menu, or on the reminder DMbot sends when a session starts, and pick 5 minutes, 15 minutes or Until I resume. While you are paused DMbot records nothing of what you say, and it isn't added afterwards. Press ▶️ Resume now to come back sooner. A pause never changes your yes, and Stop recording me is still there.",
    names: [
      { name: "Pause recording me", file: "consent_dm.py" },
      { name: "Resume now", file: "consent_dm.py" },
      { name: "Stop recording me", file: "consent_dm.py" },
    ],
  },
  {
    audience: "gameMaster",
    whatsNew: "Go off the record for a while",
    text: "To give the whole table a break, press ⏸️ Pause the table on the Listening message and pick how long. Nobody is recorded until it ends or you press ▶️ Resume now. Nothing said during the break is saved, and it isn't added afterwards.",
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
