/** @jsxImportSource preact */
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1, the page half of #4872): a newer version
// waiting, or an update DMbot already took, with what changed. One card per campaign, loaded only while RULE_UPDATES_LIVE. The page
// never takes an update. The Undo of a taken update is the rest of #4872 (core #4869), not here yet.
import { useEffect, useState } from "preact/hooks";

import { packUpdateWords as w } from "../content/pack-updates-words";
import type { AccountApi, PackUpdateItem, PackUpdates } from "./api";

function Pack({ item, taken }: { item: PackUpdateItem; taken: boolean }) {
  const [open, setOpen] = useState(false);
  const id = `pack-update-${encodeURIComponent(item.setId)}`;
  return (
    <li class="stack">
      <p class="small">
        <strong>{taken ? w.taken(item.name, item.to) : w.newer(item.name, item.to, item.from)}</strong>
      </p>
      {!taken && <p class="small muted">{w.takesWhen}</p>}
      <div class="row">
        <button type="button" class="button secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((was) => !was)}>
          {open ? w.hide : w.see}
        </button>
      </div>
      {open && (
        <div id={id} class="stack">
          <p class="small">{taken ? w.takenChanges : w.changes(item.words)}</p>
          <ul class="small">
            {item.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {item.more > 0 && <p class="small muted">{w.more(item.more)}</p>}
        </div>
      )}
    </li>
  );
}

export default function PackUpdatesCard({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [packs, setPacks] = useState<PackUpdates | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!api.packUpdates) return;
    let live = true;
    api.packUpdates(campaignRef).then(
      (got) => {
        if (live) setPacks(got);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef]);

  if (!api.packUpdates) return null;
  if (failed) {
    return (
      <p class="small" role="alert">
        {w.failed}
      </p>
    );
  }
  if (!packs) return null;
  // A pack with a newer version waiting shows that, not its taken update.
  const takenOnly = packs.taken.filter((t) => !packs.waiting.some((x) => x.setId === t.setId));
  if (packs.waiting.length === 0 && takenOnly.length === 0) return null;
  return (
    <section class="stack" aria-label={w.heading}>
      <h3>{w.heading}</h3>
      <ul class="stack">
        {packs.waiting.map((p) => (
          <Pack key={`w-${p.setId}`} item={p} taken={false} />
        ))}
        {takenOnly.map((p) => (
          <Pack key={`t-${p.setId}`} item={p} taken />
        ))}
      </ul>
    </section>
  );
}
