// The switch of "Not named yet" on the Names page (#2803): the people the story described and never named, listed first
// for the campaign's game master, with "Give them a name". Switched off until the bot applies the name, join and undo
// steps the website queues (CloudDev's half) and the website server is deployed with the matching change (core
// migration 0266, web/all_names.py); the bot must go first. Flip it with the What's new entry "Give a name to people the
// story never named" (docs/GO_LIVE.md).
export const NOT_NAMED_LIVE = false;
