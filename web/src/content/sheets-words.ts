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
