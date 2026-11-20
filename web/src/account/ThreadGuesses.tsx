/** @jsxImportSource preact */
// The table's guesses about a story thread on the quest sheet (#4665): read only, the lines as the API gives them, in Discord's
// words, with the markdown asterisks taken out (the page shows plain text). Nothing at all when there are no lines or the read
// is refused: no heading, no "none yet" text. No button and no words of right or wrong.
import { useEffect, useState } from "preact/hooks";

import { threadGuessWords as w } from "../content/thread-guesses-words";
import type { AccountApi, ThreadGuesses } from "./api";

export default function ThreadGuesses({ api, refId, thread }: { api: AccountApi; refId: string; thread: string }) {
  const [data, setData] = useState<ThreadGuesses | null>(null);

  useEffect(() => {
    let live = true;
    if (!api.threadGuesses) return;
    api
      .threadGuesses(refId, thread)
      .then((got) => live && setData(got))
      .catch(() => live && setData(null)); // a refused or failed read shows nothing at all
    return () => {
      live = false;
    };
  }, [api, refId, thread]);

  if (!data || data.lines.length === 0) return null;
  return (
    <section class="stack" aria-labelledby={`guesses-${thread}`}>
      <h3 id={`guesses-${thread}`}>{w.heading}</h3>
      {data.lines.map((line, i) => (
        <p key={i}>{line.replace(/\*\*/g, "")}</p>
      ))}
      <p class="small muted">{w.note}</p>
    </section>
  );
}
