// How the Settings tab waits for the bot's answer to a queued choice (#2484, #2753), in a file of its own so the
// choices and the lazy optional rules section share it without importing each other.

/** How long between looks at the bot's answer, then the last repeats. */
export const POLL_MS = [400, 800, 1500, 2500];
/** After this long waiting the page says it is slow; after the next, that the bot is away. */
export const SLOW_AFTER_MS = 8000;
export const OFFLINE_AFTER_MS = 30000;
/** Once the bot has been quiet that long the page looks less often, up to the hour the queue keeps a choice. */
export const LATE_POLL_MS = 30000;
export const GIVE_UP_MS = 3600000;
