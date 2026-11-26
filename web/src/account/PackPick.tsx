/** @jsxImportSource preact */
// "Pick changes" for a followed pack's newer version (#4841 packs P3, the page half of #4873): the DM ticks the changes to keep.
// Leaving any out ends the follow, and the words say so first. Keeping everything is the update itself, which DMbot takes at the
// next session start, so "Save my picks" waits for one change to be left out. Its own file, loaded only from the pack card.
import { useRef, useState } from "preact/hooks";

import { packUpdateWords as w } from "../content/pack-updates-words";
import type { AccountApi, PackUpdateItem } from "./api";
import SavingWait from "./SavingWait";

export default function PackPick({
  api,
  campaignRef,
  item,
  id,
  onStart,
  onPicked,
  onCancel,
  onError,
}: {
  api: AccountApi;
  campaignRef: string;
  item: PackUpdateItem;
  id: string;
  onStart: () => void;
  onPicked: (got: { kept: number; left: number; skipped: number; take: number | null }) => void;
  onCancel: () => void;
  onError: (error: unknown) => void;
}) {
  const [ticked, setTicked] = useState<boolean[]>(() => item.lines.map(() => true));
  const [busy, setBusy] = useState(false);
  const left = ticked.filter((t) => !t).length;
  // Focus moves into the picker when it opens: once, when its first change is put on the page (#4873).
  const focused = useRef(false);

  async function save() {
    // One press in flight; nothing is sent while every change is kept (that is the update itself).
    if (busy || left === 0 || !api.packUpdatePick) return;
    onStart();
    setBusy(true);
    const kept = ticked.flatMap((t, i) => (t ? [i] : []));
    try {
      onPicked(await api.packUpdatePick(campaignRef, item.setId, item.to, kept));
    } catch (error) {
      setBusy(false);
      onError(error);
    }
  }

  return (
    <div class="stack" id={id}>
      <p class="small">{w.pickIntro(item.name)}</p>
      <fieldset class="stack">
        <legend>{w.pickLegend}</legend>
        <ul class="stack">
          {item.lines.map((line, i) => (
            <li key={i}>
              <label class="row">
                <input
                  ref={(el) => {
                    if (el && i === 0 && !focused.current) {
                      focused.current = true;
                      el.focus();
                    }
                  }}
                  type="checkbox"
                  checked={ticked[i] ?? false}
                  disabled={busy}
                  onChange={() => setTicked((was) => was.map((t, j) => (j === i ? !t : t)))}
                />
                <span>{line}</span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
      <div class="row">
        <button type="button" class="button" disabled={busy || left === 0} onClick={() => void save()}>
          {w.save}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onCancel}>
          {w.neverMind}
        </button>
      </div>
      <SavingWait active={busy} />
    </div>
  );
}
