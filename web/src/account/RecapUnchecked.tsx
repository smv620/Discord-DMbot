/** @jsxImportSource preact */
// A recap DMbot wrote but could not finish checking for 3 days (#2297, #2305). The DM reads the
// draft here, with the sentences DMbot would have looked at twice marked, and may open it as it is;
// it then goes on as a finished draft that they can still change and approve as usual. Nothing is
// shared by this. Shown to the DM only (the API gives the draft to no one else), and only when the
// API says it takes the approval.
import { useRef, useState } from "preact/hooks";

import { text } from "../content/recaps";
import { ApiError, type AccountApi, type Recap } from "./api";
import { keepFocus } from "./focus";

export default function RecapUnchecked({
  api,
  recap,
  onRecap,
  pageFocus,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
  /** Where focus goes once the draft is open and this part is gone: the page's own heading. */
  pageFocus?: () => HTMLElement | null | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const inFlight = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const draft = recap.uncheckedDraft ?? [];
  if (!recap.working || draft.length === 0) return null;
  const marked = new Set(recap.uncheckedSentences ?? []);
  // The fingerprint of the words on the page goes with the press, so only these words are approved.
  const canUse = recap.canApproveUnchecked === true && !!api.approveUnchecked && !!recap.textHash;

  const use = async (): Promise<void> => {
    if (!api.approveUnchecked || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      onRecap(await api.approveUnchecked(recap.ref, recap.textHash ?? ""));
      keepFocus(() => pageFocus?.());
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      if (kind === "changed" && api.recap) {
        // The words moved under the DM: show the new draft, and ask them to read it again.
        try {
          onRecap(await api.recap(recap.ref));
        } catch {
          // the message below already says to read again
        }
      }
      setProblem(kind === "changed" ? text.uncheckedChanged : kind === "not-yet" ? text.uncheckedNotYet : text.uncheckedFailed);
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
      <p>{text.uncheckedLead}</p>
      <p class="recap-unchecked-text">
        {draft.map((sentence, i) =>
          marked.has(i + 1) ? (
            <span key={i}>
              <mark>
                <strong>{text.uncheckedMark}</strong> {sentence}
              </mark>{" "}
            </span>
          ) : (
            <span key={i}>{sentence} </span>
          ),
        )}
      </p>
      {canUse ? (
        <>
          <p>{text.uncheckedAfter}</p>
          <button type="button" class="button" aria-busy={busy} disabled={busy} onClick={use}>
            {busy ? text.uncheckedUsing : text.uncheckedUse}
          </button>
        </>
      ) : (
        <p>{text.uncheckedKeepTrying}</p>
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
