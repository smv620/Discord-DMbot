/** @jsxImportSource preact */
// A recap DMbot wrote but could not finish checking for 3 days (#2297, #2305). The DM reads the
// draft here, with the sentences DMbot would have looked at twice marked, and may open it as it is;
// it then goes on as a finished draft that they can still change and approve as usual. Nothing is
// shared by this. Shown to the DM only (the API gives the draft to no one else), and only when the
// API says it takes the approval.
import { useRef, useState } from "preact/hooks";

import { text } from "../content/recaps";
import type { AccountApi, Recap } from "./api";
import { keepFocus } from "./focus";

export default function RecapUnchecked({
  api,
  recap,
  onRecap,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(false);
  const inFlight = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const draft = recap.uncheckedDraft ?? [];
  if (!recap.working || draft.length === 0) return null;
  const marked = new Set(recap.uncheckedSentences ?? []);
  const canUse = recap.canApproveUnchecked === true && !!api.approveUnchecked;

  const use = async (): Promise<void> => {
    if (!api.approveUnchecked || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(false);
    try {
      onRecap(await api.approveUnchecked(recap.ref));
    } catch {
      setProblem(true);
      keepFocus(() => headingRef.current);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <section class="panel" aria-labelledby="recap-unchecked">
      <h3 id="recap-unchecked" tabIndex={-1} ref={headingRef}>
        {text.uncheckedHeading}
      </h3>
      <p class="muted small">{text.uncheckedLead}</p>
      <p class="recap-unchecked-text">
        {draft.map((sentence, i) =>
          marked.has(i + 1) ? (
            <span key={i}>
              <mark>
                <span class="visually-hidden">{text.uncheckedMark}: </span>
                {sentence}
              </mark>{" "}
            </span>
          ) : (
            <span key={i}>{sentence} </span>
          ),
        )}
      </p>
      {problem && (
        <p class="warn" role="alert">
          {text.uncheckedFailed}
        </p>
      )}
      {canUse && (
        <>
          <button type="button" class="button" aria-busy={busy} disabled={busy} onClick={use}>
            {busy ? text.uncheckedUsing : text.uncheckedUse}
          </button>
          <p class="muted small">{text.uncheckedAfter}</p>
        </>
      )}
    </section>
  );
}
