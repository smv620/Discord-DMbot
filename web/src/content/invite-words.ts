// The words of the campaign invite panel on a campaign's page (#2985). DMbot can't add anyone to a campaign on dndbeyond.com
// (there is no official way), so the DM pastes their own campaign's invite link and players get a button. The shape check
// is the same as Discord's form (core campaigns/invite.py); the bot checks again and its plain words are shown if it refuses.
export const INVITE_PATTERN = /^https:\/\/(?:www\.)?dndbeyond\.com\/campaigns\/join\/[A-Za-z0-9]{4,64}\/?$/;

export const invitePanel = {
  heading: "Campaign invite from dndbeyond.com",
  intro: "DMbot can't add anyone to your campaign on dndbeyond.com. It can pass on your invite. Anyone in your Discord server will see a Join button where DMbot talks about the game.",
  none: "No invite link yet.",
  now: (link: string): string => `Current link: ${link}`,
  label: "Your campaign invite link",
  help: "On dndbeyond.com, open your campaign and copy its invite link. Paste only the link. It starts with https://www.dndbeyond.com/campaigns/join/",
  add: "Add the link",
  save: "Save the link",
  change: "Change the link",
  remove: "Remove the link",
  cancel: "Cancel",
  notAnInvite: "That isn't a campaign invite link from dndbeyond.com. Open your campaign there, copy its invite link, and paste only the link. It starts with https://www.dndbeyond.com/campaigns/join/",
  nothingYet: "Paste your invite link first.",
  waiting: { set: "Saving your invite link. DMbot will do it in a moment.", remove: "Removing your invite link. DMbot will do it in a moment." },
  working: { set: "Saving your invite link now.", remove: "Removing your invite link now." },
  done: { set: "Saved. Players now see a Join button.", remove: "Removed. The Join button is gone. Add the link again any time." },
  failed: "Nothing was changed.",
  failedNext: "Try again in a minute.",
  stillNow: (link: string): string => (link ? `The link is still ${link}` : "There is still no invite link."),
  timedOut: "DMbot hasn't answered yet. It may still do it. Check back in a minute; the current link above will change when it has.",
  problems: {
    "not-allowed": "Only a DM of this campaign can do this. Ask your DM.",
    "bad-value": "That isn't a campaign invite link from dndbeyond.com. Paste only the link from your campaign there.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "DMbot can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong and nothing was changed. Try again, or ask for help if it keeps happening.",
};
