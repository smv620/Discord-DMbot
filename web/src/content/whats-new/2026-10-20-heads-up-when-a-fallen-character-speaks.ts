import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Heads-up when a fallen character speaks",
  what: "If someone speaks as a character you confirmed has died, DMbot points it out on your DM screen, and nothing changes unless you press a button. It waits a few minutes between heads-ups, stays silent on Quiet, and players see it only if your DM screen lets them.",
  held: "It needs the story extractor, which stays off until its prompt passes the measurement (#1282); held until it has been seen working on the test server (#2068, #2078), and DMBOT_LIVE_HEADS_UP stays off until dev1's live check.",
  order: 15,
};
