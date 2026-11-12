// How a server member is named on a page that asks the DM to pick a person (#4225): the Discord display name with the username after it when
// they differ ("Eric (eric_92)"), never a piece of an id. A person DMbot knows only by id carries the placeholder "Player 1520" (or
// "Player 3 (no Discord)"): that reads as a character, so it is shown as "Unknown member (…1520)" until the bot has the name.
const PLACEHOLDER = /^Player (\d+)(?: \(no Discord\))?$/;

export function memberLabel(userId: string, name: string, username?: string | null): string {
  const holds = PLACEHOLDER.test(name.trim());
  const shown = holds ? `Unknown member (…${userId.replace(/\D/g, "").slice(-4)})` : name;
  return !holds && username && username.toLowerCase() !== name.toLowerCase() ? `${shown} (${username})` : shown;
}
