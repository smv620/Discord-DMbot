// The switch of "Remove this person" on a "Not named yet" row of the Names page (#3595, owner request). Off until migration 0364
// (the queue door's `remove` finding an unnamed entry) and the bot branch that applies it are deployed, bot first: the site can
// go live before them, so the button must not show until then (docs/GO_LIVE.md).
export const REMOVE_UNNAMED_LIVE = false;
