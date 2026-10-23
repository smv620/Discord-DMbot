import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-16",
  title: "When a player says a secret name",
  what: "If a player says a name you've marked secret, it ends up in the transcript, which anyone in your server can read. After the session DMbot asks you privately, one name at a time (up to five), whether it should stay secret from players or be opened up. Only you can answer, and DMbot never decides for you.",
  held: "Held until it has been seen working on the test server (#1407).",
  order: 5,
};
