import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-09",
  title: "First names are known, so DMbot asks less",
  what: "When your story calls Tran Vosk just \"Tran\", DMbot now knows that's the same person and stops asking whether \"Tran\" is someone new. It only does this when no one else in your campaign has that name and the story really uses it on its own. It never does it for titles like Lord or Captain. You'll see it as one of their other names, and you can take it away. If you take it away, DMbot won't add it again.",
  held: "Waits for dev1 to deploy it and check it on the sample run (#3880).",
  order: 83,
};
