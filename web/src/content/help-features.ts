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
    whatsNew: "Delete a campaign from the website",
    text: "Open the campaign's page from My Account and scroll to the bottom. Press Delete this campaign. Only the person whose plan the campaign uses sees it. It says what goes and that it can't be undone. To keep a copy, download a backup first. Then type the campaign's name and press Delete it. You can't delete while a session is running. A campaign on a server DMbot can't reach any more still shows in your list with its own Delete button, and you can back it up or delete it there; its Discord channels stay until a server admin removes them.",
  },
  {
    audience: "account",
    whatsNew: "Receive a campaign by email",
    text: "When someone hands a campaign over to you, you get an email with a link that works for 7 days. Open it and sign in. You see which campaign it is and who offered it. Pick the server it should run on (if you play on a server where DMbot already runs this campaign, your Discord account simply becomes its DM there), then take the campaign, or say no thanks. If you take it, it moves to your account, its play time comes out of your hours, and the DM who offered it stays on as a co-DM. You need a paid plan with a free campaign slot, and Try It can't receive a campaign. If the link has ended, ask the DM to hand the campaign over again.",
  },
  {
    audience: "account",
    whatsNew: "Use more than one Discord account",
    text: "On My Account, Your Discord accounts shows every Discord account that belongs to your DMbot account. To add one, sign in with it; you never type a number. Your plan and campaigns then work from any of them. You can remove one whenever you like, except the one you're using now and your last. Each Discord account decides for itself whether it is recorded. A Discord account that runs its own campaigns or has its own plan can't be added until those are handed over.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Your campaign has its own home",
    text: "Each campaign has its own page on the website, in four parts: Story, At the table, Rules and Settings. Settings is only for game masters. A trail at the top shows where you are, and four tabs let you jump to another part.",
  },
  {
    audience: "gameMaster",
    whatsNew: "The website can show what is waiting for you, and answer \"Which Mara?\"",
    text: "Under the count line, Waiting for you lists what DMbot needs your answer on. Facts heard at the table: Yes, that's true, True, but keep it secret, or Not right. Connections found in a backstory are grouped by the upload they came from: Keep or Skip each one, or Keep all, which keeps the ready ones shown, up to 50 at a time. Skip is the same as No on Discord. A connection with a new name in it waits until you answer that name in Check new names. Same name, which one? has a button for each match and Neither. DMbot never decides these. Players never see this list.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Fix a word, remake only the voice",
    text: "Heard a word said wrong in a narrated recap? On the recap page press Edit, change the words and save. Before you approve the new text, a line beside the audio choice says what the new audio will be. After a small change: only the paragraphs you changed are spoken again, and the music stays. After a bigger change, or if the old parts weren't kept: the voice and the music are both made again, and the line says why. Choose your audio when you approve and it starts. On a recap that is already posted, the same line sits under the player, with a Remake the voice or Remake voice and music button. While it works you see the step, how many paragraphs are done, and when it last moved. The first and last lines of a recap are fixed, so they can't be edited. DMbot never changes your text on its own. In the editor, Fix a name everywhere replaces every use of a spelling in the box, apart from those two lines. Nothing is kept until you press Save.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Make a recap for a past session",
    text: "On a campaign's recaps page, Recaps for past sessions lists every session with its recap. Choose Make the recap for a session that has none, or Make it again for one that failed, was held back or that you want fresh. If the recap is already approved, DMbot asks once before replacing it. To add a recap someone else wrote, choose Add a recap someone wrote, paste it, pick a text file or give a Google Doc link, and say who wrote it. The first time, you say you have the right to use it; DMbot doesn't check. While a recap is made, the page shows which part it is on and how long it has run. Nothing goes to your players until you post it.",
  },
  {
    audience: "gameMaster",
    whatsNew: "After the session: things to check",
    text: "Under a session's recap, After the session: things to check lists what DMbot noticed. First, things it holds that may not fit together, with both sides and where each came from; if the words behind a line are gone, it says a line from that session is no longer available. Then what the party has wrong, in three groups: disguised on purpose, misunderstood, and simply false. Each one has four answers: That's intended (kept, and not raised again), Fix the DM's view (opens the Story Graph), Not a problem, and Remind me next session. Undo takes an answer back. Only the DM sees this list, never a player. DMbot only lists; you decide.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Names fixed this session, with Undo",
    text: "Below a session's recap text, Names fixed this session shows the names DMbot fixed in the cleaned transcript. Each name says how many times it was fixed and how it was written before. Press Undo to put a name back as it was heard. DMbot then leaves that spelling alone. Only you, the DM, see this list.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Who was in each scene",
    text: "Under a recap, Who was in each scene shows the characters DMbot guesses were in each scene. If it is wrong, press Change on that scene, tick the right characters and press Save list. Your list is final: DMbot never changes it. Do it before you approve the recap. Only you, the DM, see this.",
  },
  {
    audience: "gameMaster",
    whatsNew: "What people believe, on the Story Graph",
    text: "On a campaign's Story Graph, What people believe lists theories, accusations and reveals DMbot heard. Press Keep as a belief to note what that person thinks, or Drop it to skip one. Undo is there right after you keep one. A belief is never treated as a fact. If a new name is in it, answer Check new names first. Only you, the DM, see this.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Every name on the Story Graph",
    text: "At the top of the Story Graph, a line counts every name and every connection in the campaign, not only the ones drawn. Not connected yet lists the names that have no connection, by kind. Tap one to check: it says it has no connections yet. If the names form more than one group, Other groups of linked names has a button for each. Tap one to put that group in the middle of the map. Players see only what they are allowed to see.",
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
    text: "On a campaign's Settings tab, game masters can change who can see the DM screen, how much DMbot says, and how long transcripts are kept. The page says it is saving, then that it is saved. If you pick a shorter time for keeping transcripts, you see a warning first, and nothing is deleted until you say yes on the page. Changes need DMbot to be online. If it is away, your change waits for up to an hour. If DMbot is not back by then, the change is dropped and you can choose again. Hand over and pause are still done in Discord, and only the campaign's owner sees them.",
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
    audience: "gameMaster",
    whatsNew: "Check a recap's new names online",
    text: "Under a recap on the website, Names to check lists the new names DMbot was not sure about. For each one, say what it is, that it is a name you already have, how it is spelled, or that it is not a name. You can also explain it in a few words. It does the same as the new names list in Discord, so answering in one place answers both. DMbot never decides what a name is.",
  },
  {
    audience: "gameMaster",
    whatsNew: "Plot check on a recap",
    text: "Before you approve a recap, its page can show a Plot check: things that don't seem to match your earlier sessions, with the recap's sentence and the earlier note beside it. For each one you can fix the recap (that adds the words to the box under Tell DMbot what to fix), talk it through (DMbot may suggest a note, and it is kept only if you say Save), say it is not a problem, or leave it as it is. Approve opens when every question has an answer. DMbot only asks. You decide, and only you see these questions.",
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
