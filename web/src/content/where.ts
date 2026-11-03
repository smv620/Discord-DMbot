// The "Where to find everything" page (the Help button's 🧭 link): the whole way around DMbot as
// one tree, in Discord and on the website, with the paths only a game master (DM) can take marked.
//
// It is a map, not a manual: each place says where it is and what it is for, in a few words. The
// step-by-step is the Help page. Nothing here is a copy that can drift:
// - the website's campaign pages come from `sectionPages` (the same list the campaign tabs use),
//   and "DM only" is whatever a DM sees that a player does not;
// - every Discord button and command is checked against the bot's own code by web/test/where.test.ts
//   (a name that is not in core/src/dmbot fails the test);
// - a place whose switch is off is left out, so the page never points at something nobody can reach.
import { campaignPage as words } from "./campaign-page";
import { DM_BOT_CHANNEL_LIVE } from "./help";
import { RECAPS_LIVE } from "./recap-words";
import { CAMPAIGN_DELETE_LIVE } from "./campaign-delete";
import { sectionPages } from "../account/campaignSections";

export interface Place {
  /** As shown (a button or command exactly as the bot shows it; a page by its name on the site). */
  label: string;
  /** What it is for, in a few plain words. */
  note?: string;
  /** Only the campaign's game masters can use it. Its children are DM only too. */
  dmOnly?: boolean;
  /** A short extra on who: "the campaign's owner", "server managers too". */
  who?: string;
  /** A page on this website. */
  href?: string;
  /** "button" and "command" are checked against the bot's code; a "group" is only a heading. */
  kind?: "button" | "command" | "group";
  children?: Place[];
}

export const whereHeading = "Where to find everything";
export const whereLead =
  "How to get to anything in DMbot, in Discord or on the website. Places marked DM only are for the game masters of a campaign; players don't see them.";
export const whereLegend = "DM only means only a campaign's game masters can use it.";
export const discordHeading = "In Discord";
export const websiteHeading = "On the website";
export const dmOnlyBadge = "DM only";

const group = (label: string, children: Place[], extra: Partial<Place> = {}): Place => ({ label, kind: "group", children, ...extra });
const button = (label: string, note?: string, extra: Partial<Place> = {}): Place => ({ label, kind: "button", ...(note ? { note } : {}), ...extra });
const command = (label: string, note: string, extra: Partial<Place> = {}): Place => ({ label, kind: "command", note, ...extra });

/** Every slash command, in the order Help lists them. A test keeps this and the bot's Help list the same. */
export const commands: Place[] = [
  command("/dmbot help", "What DMbot does, and where to find things."),
  command("/dmbot rule", "Look up a spell, condition, creature or rule. Only you see the answer."),
  command("/transcript", "Download what was said in a session."),
  command("/dmbot fix", "Fix a line DMbot heard wrong. A DM can fix any line."),
  command("/dmbot backup", "Download a copy of a campaign."),
  command("/dmbot delete-my-lines", "Delete the lines DMbot wrote down from you."),
  command("/consent give", "Let DMbot record your voice."),
  command("/consent revoke", "Stop DMbot recording your voice."),
  command("/dmbot migrate", "Move your plan to a new Discord account."),
  command("/dmbot houserules", "See the house rules. The DM adds, edits and removes them."),
  command("/dmbot start", "Pick a campaign and voice channel, then start listening.", { dmOnly: true }),
  command("/dmbot stop", "Stop listening.", { dmOnly: true }),
  command("/dmbot settings", "Open ⚙️ Manage DMbot.", { dmOnly: true }),
  command("/dmbot names", "The names DMbot listens for.", { dmOnly: true }),
  command("/dmbot optionalrules", "Turn optional rules on or off.", { dmOnly: true }),
  command("/dmbot homebrew", "Your own spells, monsters and items.", { dmOnly: true }),
  command("/dmbot recap", "Make a session's recap, or make it again.", { dmOnly: true }),
  command("/dmbot recap-upload", "Add a recap someone at your table wrote.", { dmOnly: true }),
  command("/dmbot import", "Bring in older sessions, on the website.", { dmOnly: true }),
  command("/dmbot restore", "Bring a campaign back from a copy.", { dmOnly: true }),
];

const manageDmbot: Place[] = [
  button("Quiet", "How much DMbot says. Quiet or Normal.", { who: "server managers too" }),
  button("Who can see the DM screen", "Only the DM, players can peek, or everyone in the server.", { who: "server managers too" }),
  button("Turn rules cards on", "Show a rules card when a spell, condition or creature is named."),
  button("Game clock", "Start or show the game clock on the DM screen."),
  button("Table chat", "Turn table chat on or off, and pick its channel."),
  button("Stop when quiet", "How long a silent table is listened to before DMbot stops."),
  button("What's switched on", "The features this DMbot has turned on."),
  group("Campaign management", [
    button("Hand over", "Give the campaign to someone else.", { who: "the campaign's owner" }),
    button("Pause this campaign", "Pause or unpause.", { who: "the campaign's owner" }),
    button("Transcript retention", "How long transcripts are kept."),
    button("Game masters", "Add or remove a game master."),
    button("Delete this campaign", "Delete it for good.", { who: "the plan holder" }),
  ]),
  button("Shared story", "Let DMbot follow your adventure."),
  button("House-rules file", "Connect a file of house rules."),
  button("Campaign invite link", "A link players use to join your campaign's character tool."),
  button("Recap style", "Show DMbot how your recaps should sound."),
  button("Shared rules", "Share your house rules, or follow another table's."),
  button("Homebrew", "Your campaign's own spells, monsters and items."),
  button("Upload campaign lore", "Give DMbot your notes; it finds names and connections."),
];

const privateMenu: Place[] = [
  button("My character sheet", "Link or type in your character."),
  button("Pause recording me", "Stop being recorded for a while."),
  button("Stop recording me", "Stop being recorded."),
  button("Delete my transcripts", "Delete the lines DMbot wrote down from you."),
];

/** The Discord side. Where the buttons start depends on whether the DMbot channel is live yet. */
export function discordTree(): Place[] {
  const help: Place = button("Help", "What DMbot does.", {
    children: [button("Look up a rule", "A spell, condition, creature or rule."), button("Transcripts", "Opens the Transcripts page on the website."), button("Where to find everything", "Opens this page.")],
  });
  const where: Place[] = DM_BOT_CHANNEL_LIVE
    ? [
        group("The dmb-dm-bot channel", [
          button("DMbot Main Menu", "Press it for the buttons for your part at the table. Only you see the answer.", {
            children: [
              button("My data", "Your recording, transcripts and character sheet.", { children: privateMenu }),
              button("Manage DMbot", "What DMbot does in this server.", { dmOnly: true, who: "server managers can open it too", children: manageDmbot }),
              button("Manage campaign", "Opens your campaign on the website.", { dmOnly: true }),
              help,
            ],
          }),
        ]),
      ]
    : [
        group("Your DM screen", [button("Manage DMbot", "What DMbot does in this server. You can also type /dmbot settings.", { dmOnly: true, who: "server managers can open it too", children: manageDmbot })]),
      ];
  return [
    group("Type a command", commands, { note: "In any channel of your server." }),
    ...where,
    group("DMbot's private message to you", [button("Menu", "Your recording, transcripts and character sheet.", { children: privateMenu })], {
      note: "DMbot sends it when you join the voice channel.",
    }),
    group("During a session", [
      button("Pause the table", "Pause for 5 minutes, 15 minutes or until you resume.", { dmOnly: true }),
      button("Stop listening", "Stop DMbot listening.", { dmOnly: true, who: "server managers too" }),
      button("Peek behind the DM screen", "See the DM screen, if your DM lets players peek. DMbot warns about spoilers first."),
    ]),
  ];
}

/** The pages of a campaign, from the same list the campaign tabs use. A page is "DM only" when a DM
 * is given it and a player is not. */
function campaignPages(): Place[] {
  const ref = "{server}.{campaign}"; // shown as a path, never followed
  const dm = sectionPages(ref, { isDm: true, importsOn: false });
  const player = sectionPages(ref, { isDm: false, importsOn: false });
  const part = (key: "story" | "add" | "rules", label: string): Place | null => {
    const playerKeys = new Set(player[key].map((p) => p.key));
    const pages = dm[key].map((p): Place => ({ label: p.label, note: p.note, href: p.href.replace(encodeURIComponent(ref), ref), ...(playerKeys.has(p.key) ? {} : { dmOnly: true }) }));
    if (pages.length === 0) return null;
    return { label, kind: "group", children: pages, ...(player[key].length === 0 ? { dmOnly: true } : {}) };
  };
  const parts = [part("story", words.groups.story), part("add", words.groups.add), part("rules", words.groups.rules)].filter((p): p is Place => p !== null);
  return [
    { label: words.groups.overview, note: "The campaign at a glance.", href: "/account/campaigns/{server}.{campaign}" },
    ...parts,
    {
      label: words.groups.settings,
      note: "Who can see the DM screen, how much DMbot says, and how long transcripts are kept.",
      dmOnly: true,
    },
    ...(CAMPAIGN_DELETE_LIVE ? [{ label: "Delete this campaign", note: "Delete it for good.", dmOnly: true, who: "the plan holder" }] : []),
  ];
}

/** The website side. */
export function websiteTree(): Place[] {
  return [
    group(
      "Before you sign in",
      [
        { label: "Help", note: "Step by step, in Discord.", href: "/help" },
        { label: "How it works", href: "/how-it-works" },
        { label: "For players", note: "What DMbot does at your table.", href: "/players" },
        { label: "Prices", href: "/pricing" },
        { label: "What's new", href: "/whats-new" },
      ],
      { note: "Open to everyone." },
    ),
    group(
      "My Account",
      [
        { label: "My plan and billing", note: "Your plan, and how to change it." },
        {
          label: "My campaigns",
          note: "Each campaign has its own page.",
          children: campaignPages(),
        },
        { label: "Transcripts", note: "Read and download what was said. Everyone in the server can read these.", href: "/account/transcripts" },
        ...(RECAPS_LIVE ? [{ label: "Read your session recaps", note: "Every recap of the campaigns you run.", href: "/account/recaps", dmOnly: true }] : []),
        { label: "Download my data", note: "A copy of what DMbot holds about you." },
        { label: "Delete my account and data", note: "Remove your account." },
      ],
      { note: "Sign in with Discord.", href: "/account" },
    ),
  ];
}
