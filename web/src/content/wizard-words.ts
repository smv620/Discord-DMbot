// The words of the start-up wizard (#3956; docs/PLAN.md, "The campaign start-up wizard"; the doors are in docs/WIZARD_DESIGN.md).
// Plain words, one short page for each step. The PLAN's own sentences (Skip, the defaults) are used as written. Nothing here ever
// holds a document's words or a character sheet's: the pages show names the DM typed, file names, and counts.

export type WizardStepId = "where" | "confirm" | "players" | "lore" | "secrets" | "rules" | "house" | "homebrew" | "data" | "finish";

/** The steps in order; the number is the step the draft keeps. */
export const WIZARD_STEPS: readonly WizardStepId[] = ["where", "confirm", "players", "lore", "secrets", "rules", "house", "homebrew", "data", "finish"];

/** The seven supplements the rules step offers (PLAN, step 6). The codes are core's (wizard.py refuses any other). Choosing one only tells the rules advisor which books the table uses. */
export const SUPPLEMENTS: readonly { code: string; title: string }[] = [
  { code: "xanathar", title: "Xanathar's Guide to Everything (2017)" },
  { code: "ravnica", title: "Guildmasters' Guide to Ravnica (2018)" },
  { code: "theros", title: "Mythic Odysseys of Theros (2020)" },
  { code: "tasha", title: "Tasha's Cauldron of Everything (2020)" },
  { code: "fizban", title: "Fizban's Treasury of Dragons (2021)" },
  { code: "mordenkainen", title: "Mordenkainen Presents: Monsters of the Multiverse (2022)" },
  { code: "bigby", title: "Bigby Presents: Glory of the Giants (2023)" },
];

export const wizardWords = {
  title: "Set up a campaign",
  notYet: "This page isn't available yet.",
  loading: "Loading…",
  noServer: "Open this page from Discord: use ＋ New campaign and pick \"Set up step by step on the website\".",
  failed: "We couldn't load this. Reload the page to try again.",
  expired: "Your unfinished setup was kept for 7 days and has been removed. You can start again.",
  noRoom: "Your plan has no room for another campaign. Change your plan or remove a campaign, then come back.",
  tooFast: "Too many tries at once. Wait a moment, then try again.",
  notAllowed: "You and DMbot both need to be in this server. Open this page again from ＋ New campaign in Discord, or sign out and back in if you joined the server recently.",
  saved: "Saved. You can leave and come back: your answers are kept for 7 days.",
  saving: "Saving…",
  step: (n: number, of: number): string => `Step ${n} of ${of}`,
  back: "Back",
  next: "Next",
  skip: "Skip",
  start: "Start",
  startLead: "A few short questions, one page each. You can skip any of them.",

  // The Skip words are PLAN's, as written. Skip asks only for the name, then the defaults apply.
  skipSays: "DMbot will use the defaults. You can upload your lore and story later from DMbot's menus.",
  skipAsk: "Skip the rest of the setup?",
  skipName: "What is the campaign called?",
  skipYes: "Skip and make the campaign",
  skipRights: "A file needs its box ticked first. Go back to the page with the file, tick the box, then try again.",
  skipNo: "Keep going",

  discard: "Throw this setup away",
  discardAsk: "Throw away everything entered so far?",
  discardYes: "Yes, throw it away",
  discardNo: "No, keep it",

  // Step 1: where the campaign is.
  where: {
    heading: "Where is your campaign?",
    lead: "Tell DMbot where your campaign's notes are. Pick one, or skip it and add things later.",
    dndbeyond: "A campaign on dndbeyond.com",
    file: "A file from my computer",
    drive: "A Google Drive file or folder",
    inviteLabel: "The campaign's invite link",
    inviteHint: "The link you send players so they can join on dndbeyond.com.",
    inviteBad: "That doesn't look like a dndbeyond.com campaign invite link. Copy it from the campaign's page and paste it here.",
    // The dndbeyond.com limit, in the PLAN's plain words.
    limit: "DMbot can't open a dndbeyond.com campaign page, because it is behind your sign-in, and DMbot never asks for your password. It keeps your invite link as the campaign's \"Join on dndbeyond.com\" button. To add characters, give each character's public sheet link, or paste the text of the campaign page below.",
    sheetLabel: "A character's public sheet link",
    sheetHint: "On dndbeyond.com, open the character and make its sheet public, then copy its link.",
    sheetAdd: "Add the character",
    sheetWaiting: "Reading the sheet…",
    sheetFound: (name: string): string => `Found ${name}.`,
    sheetPrivate: "That sheet is private. On dndbeyond.com, make it public, then try again.",
    sheetNotFound: "DMbot couldn't find a sheet at that link.",
    sheetSlow: "dndbeyond.com was too slow. Try again in a minute.",
    pasteLabel: "Optional: paste the text of the campaign's page",
    pasteHint: "Open the campaign page on dndbeyond.com, select everything, copy it and paste it here. DMbot looks for the campaign's name and the characters. The text is not kept.",
    pasteRead: "Look for the characters",
    pasteNone: "I couldn't find the characters in that text. You can add them by hand.",
    pasteFound: (n: number): string => `Found ${n} ${n === 1 ? "character" : "characters"}. You will check them on the next page.`,
    fileLead: "You will add your files on the pages that follow: lore, secrets, rules, house rules and homebrew.",
    driveLead: "On the pages that follow, choose a Google Drive file or folder. DMbot can only see the files you pick.",
  },

  // Step 2: confirm the name and the characters.
  confirm: {
    heading: "Check the name and the characters",
    nameLabel: "The campaign's name",
    charactersLabel: "Player characters",
    none: "No characters yet. Add one below, or carry on and add them later.",
    fullLabel: "Full name",
    partyLabel: "Name in the party",
    add: "Add a character",
    remove: "Remove",
    tooMany: "A setup holds up to 12 characters. Add the rest later from the Names page.",
  },

  // Step 3: who plays each character.
  players: {
    heading: "Who plays each character?",
    lead: "You can skip this. Pick who plays each character, or set it later on the Names page.",
    pending: "Not set yet",
    none: "There are no characters to match yet.",
  },

  // Steps 4 and 5: lore and secrets.
  lore: {
    heading: "Lore everyone knows",
    lead: "The backdrop of your campaign: the world, its places and its people, as the whole table knows them.",
  },
  secrets: {
    heading: "Secrets not yet revealed",
    lead: "What only you know so far: hidden identities, plots, hiding places. DMbot keeps these on your DM screen and does not send them to players.",
  },
  docs: {
    checkOnly: "DMbot only checks that it can open each file and counts its pages and words. It reads nothing until you press \"Make the campaign\".",
    add: "Add a file",
    addLink: "Add a link",
    addDrive: "Choose from Google Drive",
    linkLabel: "A link",
    linkBad: "A link must start with https://.",
    remove: "Remove",
    none: "Nothing added.",
    counts: (pages: number, words: number): string =>
      `${pages > 0 ? `${pages} ${pages === 1 ? "page" : "pages"}, ` : ""}${words} ${words === 1 ? "word" : "words"}`,
    checking: "Checking the file…",
    unreadable: {
      empty: "This file is empty.",
      scanned: "This PDF looks scanned. Paste its text, or upload a text version.",
      locked: "This file is locked with a password.",
      too_big: "This file is too big. Split it into parts, or use Google Drive.",
      not_text: "DMbot can't read this kind of file. Use a text, Markdown, PDF or Word file.",
    } as Record<string, string>,
    full: "A setup holds up to 20 files. Remove one to add another.",
    // The right-to-use confirmation (CLAUDE.md, IP): required before DMbot reads shared material.
    rights: "I have the right to use what I add here.",
    rightsNote: "DMbot doesn't check this. It is recorded with your name and the date, and the files stay in this campaign only.",
    rightsNeeded: "Tick the box to say you have the right to use these files.",
  },

  // Step 6: rules.
  rules: {
    heading: "Which rules do you play?",
    lead: "Pick the rules to use first. DMbot uses the other set only when the first has no answer.",
    current: "5e current (2024)",
    legacy: "5e legacy (2014)",
    targetLabel: "Play with",
    fallbackLabel: "If it has nothing, use",
    same: "The two sets must be different.",
    supplementsLabel: "Books your table uses",
    supplementsNote: "Choosing a book only tells DMbot's rules advisor which books you use. No book's text is stored.",
    ownLabel: "Your own ruleset",
    ownLead: "To add your own rules as a file, tick the box first.",
    ownRights: "I have the right to use this ruleset.",
  },

  // Steps 7 and 8.
  house: {
    later: "You can add your house rules after you finish, on your campaign's House rules page.",
    heading: "House rules",
    lead: "Rules your table has changed. Add files, links, or one document that lists many links.",
  },
  homebrew: {
    later: "You can add your homebrew after you finish, on your campaign's Homebrew page. You can add links there too.",
    heading: "Homebrew",
    lead: "Your own items, spells, monsters, lineages, classes and more. Add files, links, or one document that lists many links.",
  },

  // Step 9: data for improving DMbot.
  data: {
    heading: "Help improve DMbot?",
    lead: "Let DMbot use this campaign's story and transcripts to improve itself? It is your choice, and only for this campaign.",
    yes: "Yes, use this campaign's story",
    no: "No, keep it to this campaign",
    note: "Players still choose for themselves whether to be recorded.",
  },

  // Step 10: finish.
  finish: {
    heading: "Ready to make the campaign",
    lead: "DMbot will make the campaign, read your lore, then your secrets, and add what it is sure of. Afterwards it lists names for you to check. You don't need to press anything else.",
    summaryName: "Name",
    summaryRules: "Rules",
    summaryFiles: (n: number): string => `${n} ${n === 1 ? "file" : "files"} added`,
    summarySkipped: "Skipped steps use the defaults.",
    button: "Make the campaign",
    retry: "Try again",
    stages: {
      campaign: "Making the campaign",
      party: "Adding the party",
      rules: "Setting the rules",
      house_homebrew: "Adding house rules and homebrew",
      data_use: "Saving your choice",
      lore: "Reading the lore",
      secrets: "Reading the secrets",
      sure: "Adding what DMbot is sure of",
      clean: "Finishing up",
    } as Record<string, string>,
    done: "Your campaign is ready.",
    open: "Open the campaign",
    // The finish run's failure words (docs/WIZARD_DESIGN.md, 4.4).
    errors: {
      plan: "Your plan has no room for another campaign.",
      "not-allowed": "You and DMbot both need to be in this server. Open this page again from ＋ New campaign in Discord, or sign out and back in if you joined the server recently.",
      gone: "The setup was thrown away before it finished. Start again.",
      "no-key": "Your campaign is ready. DMbot can't read the lore until you add an AI key in Settings. Nothing is lost.",
      failed: "Something went wrong. Nothing you entered is lost. Try again in a minute.",
      lost: "DMbot stopped partway. Try again: it carries on where it stopped.",
    } as Record<string, string>,
    quiet: "Still working. You can leave this page: the campaign will be ready when it is done.",
  },
} as const;
