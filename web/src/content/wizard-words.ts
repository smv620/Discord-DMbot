// The words of the start-up wizard (#3956; docs/PLAN.md, "The campaign start-up wizard"; the doors are in docs/WIZARD_DESIGN.md).
// Plain words, one short page for each step. The PLAN's own sentences (Skip, the defaults) are used as written. Nothing here ever
// holds a document's words or a character sheet's: the pages show names the DM typed, file names, and counts.

export type WizardStepId = "where" | "confirm" | "players" | "lore" | "secrets" | "rules" | "house" | "homebrew" | "data" | "finish";

/** The steps in order; the number is the step the draft keeps. */
export const WIZARD_STEPS: readonly WizardStepId[] = ["where", "confirm", "players", "lore", "secrets", "rules", "house", "homebrew", "data", "finish"];

/** The seven supplements the rules step offers (PLAN, step 6). Choosing one only tells the rules advisor which books the table uses. */
export const SUPPLEMENTS: readonly { code: string; title: string }[] = [
  { code: "xgte", title: "Xanathar's Guide to Everything (2017)" },
  { code: "ggtr", title: "Guildmasters' Guide to Ravnica (2018)" },
  { code: "motm", title: "Mythic Odysseys of Theros (2020)" },
  { code: "tcoe", title: "Tasha's Cauldron of Everything (2020)" },
  { code: "ftod", title: "Fizban's Treasury of Dragons (2021)" },
  { code: "mpmm", title: "Mordenkainen Presents: Monsters of the Multiverse (2022)" },
  { code: "bgg", title: "Bigby Presents: Glory of the Giants (2023)" },
];

export const wizardWords = {
  title: "Set up a campaign",
  notYet: "This page isn't available yet.",
  loading: "Loading…",
  failed: "We couldn't load this. Reload the page to try again.",
  expired: "Your unfinished setup was kept for 7 days and is gone now. You can start again.",
  noRoom: "Your plan has no room for another campaign. Change your plan or remove a campaign, then come back.",
  tooFast: "That was a bit quick. Wait a moment and try again.",
  notAllowed: "Only a game master of this server can set up a campaign here.",
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
  skipNo: "Keep going",

  discard: "Throw this setup away",
  discardAsk: "Throw away everything entered so far?",
  discardYes: "Yes, throw it away",
  discardNo: "No, keep it",

  // Step 1: where the campaign is.
  where: {
    heading: "Where is your campaign?",
    lead: "Tell DMbot where your campaign's notes are. Pick one, or skip it and add things later.",
    dndbeyond: "A D&D Beyond campaign",
    file: "A file from my computer",
    drive: "A Google Drive file or folder",
    inviteLabel: "The campaign's invite link",
    inviteHint: "The link you send players so they can join on D&D Beyond.",
    inviteBad: "That doesn't look like a D&D Beyond campaign invite link. Copy it from the campaign's page and paste it here.",
    // The D&D Beyond limit, in the PLAN's plain words.
    limit: "D&D Beyond keeps a campaign's page behind your sign-in, and DMbot never asks for your D&D Beyond password. So DMbot keeps the invite link as the campaign's \"Join on D&D Beyond\" button, and asks you for what it can read: each character's public sheet link.",
    sheetLabel: "A character's public sheet link",
    sheetHint: "On D&D Beyond, open the character and make its sheet public, then copy its link.",
    sheetAdd: "Add the character",
    sheetWaiting: "Reading the sheet…",
    sheetFound: (name: string): string => `Found ${name}.`,
    sheetPrivate: "That sheet is private. On D&D Beyond, make it public, then try again.",
    sheetNotFound: "DMbot couldn't find a sheet at that link.",
    sheetSlow: "D&D Beyond was too slow. Try again in a minute.",
    pasteLabel: "Optional: paste the text of the campaign's page",
    pasteHint: "Open the campaign page on D&D Beyond, select everything, copy it and paste it here. DMbot looks for the campaign's name and the characters. The text is not kept.",
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
    lead: "You can leave this empty. A character with no player is still a player character, and its player can be set later in Questions for you or on the Names page.",
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
    lead: "What only you know yet: hidden identities, plots and hiding places. DMbot keeps these as spoilers and never tells the players.",
  },
  docs: {
    checkOnly: "DMbot only checks that it can open each file and counts its pages and words. It doesn't read it for names until you finish.",
    add: "Add a file",
    addLink: "Add a link",
    addDrive: "Choose from Google Drive",
    linkLabel: "A link",
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
    full: "A setup holds up to 20 files.",
    // The right-to-use confirmation (CLAUDE.md, IP): required before DMbot reads shared material.
    rights: "I have the right to use what I add here.",
    rightsNote: "DMbot doesn't check this. Who confirmed and when is recorded with the campaign.",
    rightsNeeded: "Tick the box to say you have the right to use these files.",
  },

  // Step 6: rules.
  rules: {
    heading: "Which rules do you play?",
    lead: "Pick the rules to use first. The other set is used only where the first has nothing.",
    current: "D&D 5e current (2024)",
    legacy: "D&D 5e legacy (2014)",
    targetLabel: "Play with",
    fallbackLabel: "If it has nothing, use",
    same: "The two sets must be different.",
    supplementsLabel: "Books your table uses",
    supplementsNote: "Choosing a book only tells DMbot's rules advisor which books you use. No book's text is stored.",
    ownLabel: "Your own ruleset",
    ownLead: "You can add your own ruleset as a file. Tick the box first.",
    ownRights: "I have the right to use this ruleset.",
  },

  // Steps 7 and 8.
  house: {
    heading: "House rules",
    lead: "Rules your table has changed. Add files, links, or one document that lists many links.",
  },
  homebrew: {
    heading: "Homebrew",
    lead: "Your own items, spells, monsters, lineages, classes and more. Add files, links, or one document that lists many links.",
  },

  // Step 9: data for improving DMbot.
  data: {
    heading: "Help improve DMbot?",
    lead: "You can let DMbot learn from this campaign's story to get better. It is your choice for this campaign.",
    yes: "Yes, use this campaign's story",
    no: "No, keep it to this campaign",
    note: "Everyone at your table still says yes to being recorded themselves, as always.",
  },

  // Step 10: finish.
  finish: {
    heading: "Ready to make the campaign",
    lead: "DMbot will make the campaign, read the lore everyone knows, then the secrets, and add what it is sure of. Then it goes on to the names for you to check by itself. There is nothing more to press.",
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
      clean: "Tidying up",
    } as Record<string, string>,
    done: "Your campaign is ready.",
    open: "Open the campaign",
    // The finish run's failure words (docs/WIZARD_DESIGN.md, 4.4).
    errors: {
      plan: "Your plan has no room for another campaign.",
      "not-allowed": "Only a game master of this server can set up a campaign here.",
      gone: "The setup was thrown away before it finished. Start again.",
      "no-key": "Your campaign is ready. The lore is waiting for an AI key. Add one in Settings; nothing is lost.",
      failed: "Something went wrong. Nothing you entered is lost. Try again in a minute.",
      lost: "DMbot stopped partway. Try again: it carries on where it stopped.",
    } as Record<string, string>,
    quiet: "Still working. You can leave this page: the campaign will be ready when it is done.",
  },
} as const;
