/** @jsxImportSource preact */
// "Pick changes" for a followed set's newer version (#4841 C2, #4855): the DM ticks the changes to keep. Leaving any out ends the
// follow, and the words say so first. Keeping everything is the update itself, which DMbot takes at the next session start, so
// "Save my picks" waits for one change to be left out. Its own file, loaded only from the card while RULE_UPDATES_LIVE.
import { useState } from "preact/hooks";

import { ruleUpdateWords as w } from "../content/rule-updates-words";
import type { AccountApi, RuleUpdate } from "./api";
import SavingWait from "./SavingWait";

export default function RulePick({
  api,
  campaignRef,
  update,
  onPicked,
  onCancel,
  onError,
}: {
  api: AccountApi;
  campaignRef: string;
  update: RuleUpdate;
  onPicked: (got: { kept: number; left: number; take: number | null }) => void;
  onCancel: () => void;
  onError: (error: unknown) => void;
}) {
  const [ticked, setTicked] = useState<boolean[]>(() => update.lines.map(() => true));
  const [busy, setBusy] = useState(false);
  const left = ticked.filter((t) => !t).length;

  async function save() {
    // One press in flight; nothing is sent while every change is kept (that is the update itself).
    if (busy || left === 0 || !api.ruleUpdatePick) return;
    setBusy(true);
    const kept = ticked.flatMap((t, i) => (t ? [i] : []));
    try {
      onPicked(await api.ruleUpdatePick(campaignRef, update.to, kept));
    } catch (error) {
      setBusy(false);
      onError(error);
    }
  }

  return (
    <div class="stack">
      <p class="small">{w.pickIntro(update.name)}</p>
      <ul class="stack">
        {update.lines.map((line, i) => (
          <li key={i}>
            <label class="row">
              <input
                type="checkbox"
                checked={ticked[i]}
                disabled={busy}
                onChange={() => setTicked((was) => was.map((t, j) => (j === i ? !t : t)))}
              />
              <span>{line}</span>
            </label>
          </li>
        ))}
      </ul>
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
