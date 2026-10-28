import type { DocumentLinkProblem } from "../account/sheets-document";
// The words of "Character sheets" on a campaign's page (#2816; the character-sheets row of #2554): a player links or
// forgets their own character's sheet, and a DM reads every sheet (read only) and asks for them to be read
// again. Plain words. The bot fetches the link; the page never does. The link is shown to the player and the campaign's
// DMs only, as in Discord.
export const sheetWords = {
  heading: "Character sheets",
  lead: "Your character's sheet lets DMbot listen for its spells and items. Only you and your DM see the link.",
  loading: "Loading…",
  failedToLoad:
    "We couldn't load the character sheets. Reload the page, or try again in a minute.",
  none: "No character sheets yet. Players link theirs here or in DMbot's private message.",
  mine: (name: string): string => `${name} (yours)`,
  linkLabel: (name: string): string => `Link for ${name}'s character sheet`,
  linkPlaceholder: "https://www.dndbeyond.com/characters/…",
  linkHelp:
    "Paste your character's link from dndbeyond.com. It must look like https://www.dndbeyond.com/characters/12345.",
  link: "Link my character sheet",
  linkSaved: "Linked. DMbot reads the sheet in a moment.",
  forget: "Forget my sheet",
  forgetDone: "Done. DMbot forgot the sheet: the link and what it read.",
  typedSummary: "Type in my character",
  typedFor: (name: string): string => `Type in ${name}`,
  typedClass: "Class",
  typedClassHint: "For example: Wizard",
  typedLevel: "Level",
  typedLevelHint: "1 to 20",
  typedSpecies: "Species (optional)",
  typedSpeciesHint: "For example: Elf",
  typedNames: "Spell, feature and item names",
  typedNamesHint: "Separate with commas, up to 20. For example: Misty Step, Shield",
  typedSave: "Save what I typed",
  typedSaved:
    "Saved. DMbot now listens for the names you typed. Linking a sheet from dndbeyond.com later replaces what you typed.",
  typedReplacedLink: " This replaced your link.",
  typedReplacesLink: "You have a sheet linked. Saving what you type replaces the link.",
  typedBadLevel: "Nothing was saved: the level is a number from 1 to 20.",
  documentSummary: "Use a document link",
  documentFor: (name: string): string => `Use a document link for ${name}`,
  documentLabel: (name: string): string => `Link to your sheet for ${name}`,
  documentHint: "Anyone with the link must be able to open it. DMbot keeps no document.",
  documentRead: "Read my sheet",
  documentReplaces: "You have a sheet already. Reading a document replaces it.",
  documentSent: "Sent. DMbot is reading your document; this can take a minute.",
  documentProblems: {
    empty: "Nothing was sent: paste the link first.",
    "not-web": "Nothing was sent: the link must start with https://.",
    "too-long": "Nothing was sent: the link is longer than 400 characters.",
  } satisfies Record<DocumentLinkProblem, string>,
  typedNeedsClass: "Nothing was saved: type the class first.",
  readAgain: "Read the sheets again",
  readAgainNote:
    "Asks DMbot to read every linked sheet again. Once a minute for the campaign.",
  readAgainDone: "Asked. DMbot reads the sheets again in a moment.",
  dmOnly: "You see the sheets as the DM: read only. Players change their own.",
  onlyYou: "Only you and your DM see this link.",
  summary: (level: number | null, classes: string): string =>
    level ? `Level ${level}${classes ? `, ${classes}` : ""}` : "No level yet",
  readAt: (when: string): string => `Read ${when}`,
  notYet: "Not read yet.",
  problems: {
    "bad-document-link":
      "That isn't a web address DMbot can read. Use a public link that starts with https://.",
    "bad-link":
      "That isn't a character link from dndbeyond.com. It looks like https://www.dndbeyond.com/characters/12345.",
    "not-allowed": "Only the player of this character can change its sheet.",
    "not-found": "That character or sheet is gone. The list is up to date now.",
    "too-soon":
      "Wait a moment, then try again. A link can be made once every 20 seconds.",
    "still-waiting":
      "DMbot hasn't finished that yet. It may still happen; the list will show it. Check before trying again.",
    "read-too-soon":
      "DMbot was asked to read the sheets a moment ago. Wait a minute, then ask again.",
    "queue-full":
      "DMbot has a lot queued already. Try again in a few minutes.",
    network:
      "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
    server: "That didn't work. Try again in a minute.",
  } as Record<string, string>,
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    reading: "DMbot is reading the sheet.",
    done: "Done.",
  } as Record<string, string>,
  quiet:
    "No answer yet. It may still go through: look at the list before you try again.",
} as const;
