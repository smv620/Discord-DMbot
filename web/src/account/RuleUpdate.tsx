/** @jsxImportSource preact */
// "See changes" under a followed house-rules set (#4841 C1, the page half of #4845): a newer version of the set is waiting for
// the campaign, with what would change. Its own file, loaded only from the House rules page while RULE_UPDATES_LIVE. Nothing is
// drawn when no update waits for this set, and nothing is changed here: DMbot takes the update at the next session start.
import { useEffect, useState } from "preact/hooks";

import { ruleUpdateWords as w } from "../content/rule-updates-words";
import type { AccountApi, RuleUpdate as Update } from "./api";

export default function RuleUpdate({ api, campaignRef, name }: { api: AccountApi; campaignRef: string; name: string }) {
  const [update, setUpdate] = useState<Update | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!api.ruleUpdate) return;
    let live = true;
    api.ruleUpdate(campaignRef).then(
      (got) => {
        if (!live) return;
        setUpdate(got.update);
        setLoaded(true);
      },
      () => {
        if (!live) return;
        setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef]);

  if (!api.ruleUpdate) return null;
  // A failed read is said, never silent (it is the same for every set on the page).
  if (failed) return <p class="small" role="alert">{w.failed}</p>;
  if (!loaded || !update || update.name !== name) return null;

  const id = `rule-update-${encodeURIComponent(`${campaignRef}-${name}`)}`;
  return (
    <div class="stack">
      <p class="small">{w.newer(update.name, update.to, update.from)}</p>
      <p class="small muted">{w.takesWhen}</p>
      <button type="button" class="button secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((was) => !was)}>
        {open ? w.hideChanges : w.seeChanges}
      </button>
      {open && (
        <div id={id} class="stack">
          <p class="small">{w.changes(update.words)}</p>
          <ul class="small">
            {update.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {update.more > 0 && <p class="small muted">{w.more(update.more)}</p>}
        </div>
      )}
    </div>
  );
}
