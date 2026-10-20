/** @jsxImportSource preact */
// "Approve the text" on the recap page (#2084, #2083): the DM reads the recap, may edit it, approves
// the text, and only then picks what DMbot makes from it. No audio starts by itself. The same
// choices are on Discord (✅ Approve the text, then four buttons). The API checks the cues again
// and decides what is allowed; this shows what it says. It shows only while RECAP_AUDIO_LIVE is
// true, and only for a recap the API says has an approval to give or give back.
import { useEffect, useRef, useState } from "preact/hooks";

import {
  approveErrors,
  approveRefreshed,
  approveWords as words,
} from "../content/recap-approve";
import { errors } from "../content/recaps";
import { RECAP_AUDIO_LIVE } from "../content/recap-words";
import {
  ApiError,
  type AccountApi,
  type ApproveChoice,
  type CueProblem,
  type Recap,
} from "./api";
import { CueProblems } from "./CueParts";
import { keepFocus } from "./focus";

const ORDER: ApproveChoice[] = ["voice", "music", "both", "text"];

/** The campaign's address, from the recap's: `<server>.<campaign>.<session>` without the session. */
const campaignRefOf = (recapRef: string): string =>
  recapRef.split(".").slice(0, 2).join(".");

export default function RecapApprove({
  api,
  recap,
  onRecap,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [canAudio, setCanAudio] = useState<boolean | null>(null);
  const [planUnknown, setPlanUnknown] = useState(false);
  const [busy, setBusy] = useState<ApproveChoice | null>(null);
  const [done, setDone] = useState<ApproveChoice | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [cues, setCues] = useState<CueProblem[]>([]);
  const inFlight = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const cuesRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pickRef = useRef<HTMLParagraphElement>(null);
  const approveBtnRef = useRef<HTMLButtonElement>(null);
  const on = RECAP_AUDIO_LIVE && !!recap.approval && !!api.approveRecap;

  // What the plan includes is asked only when the DM starts to pick.
  useEffect(() => {
    if (!on || !picking || canAudio !== null) return;
    if (!api.recapChoices) {
      setCanAudio(false);
      setPlanUnknown(true);
      return;
    }
    let live = true;
    api.recapChoices(campaignRefOf(recap.ref)).then(
      (found) => live && setCanAudio(found.canAudio),
      () => {
        if (!live) return;
        setCanAudio(false); // not known: offer only the choice that makes nothing
        setPlanUnknown(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, on, picking, canAudio, recap.ref]);

  // The button that opened the choices is gone, so focus goes to their heading.
  useEffect(() => {
    if (picking) pickRef.current?.focus();
  }, [picking]);

  if (!on || !recap.approval) return null;
  const { approval } = recap;
  const { approveRecap } = api;
  // Nothing to show before the recap is ready, or once it is decided without an approval.
  if (!approval.approved && !approval.canApprove) return null;

  const approve = async (choice: ApproveChoice) => {
    if (!approveRecap || inFlight.current) return;
    inFlight.current = true;
    setBusy(choice);
    setProblem(null);
    setCues([]);
    try {
      onRecap(await approveRecap(recap.ref, choice));
      setDone(choice);
      setPicking(false);
      keepFocus(() => statusRef.current);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      const found = error instanceof ApiError ? error.cueProblems : [];
      setCues(found);
      // A listed refusal is carried by the list's own heading; saying it twice is noise.
      setProblem(
        kind === "bad-cues"
          ? found.length > 0
            ? null
            : words.cuesUnlisted
          : (approveErrors[kind] ?? errors.other),
      );
      // The plan changed under us: stop offering audio.
      if (kind === "not-in-plan") setCanAudio(false);
      // The page may be out of date (decided in Discord, say): read it again.
      if (kind === "not-allowed" && api.recap) {
        try {
          onRecap(await api.recap(recap.ref));
          setPicking(false);
          setProblem(approveRefreshed);
        } catch {
          // The message already says to reload.
        }
      }
      keepFocus(() => cuesRef.current ?? headingRef.current);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  return (
    <section class="panel" aria-labelledby="recap-approve">
      <h3 id="recap-approve" tabIndex={-1} ref={headingRef}>
        {words.heading}
      </h3>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      {approval.approved && approval.choice ? (
        <>
          <p ref={statusRef} tabIndex={-1}>
            {done ? words.done[done] : words.was[approval.choice]}
          </p>
          <p class="muted small">{words.editEnds}</p>
        </>
      ) : (
        <>
          <p class="muted small">{words.lead}</p>
          {cues.length > 0 && (
            <CueProblems
              ref={cuesRef}
              problems={cues}
              help={false}
              action="approve"
            />
          )}
          {!picking ? (
            <button
              type="button"
              class="button"
              ref={approveBtnRef}
              onClick={() => {
                setPicking(true);
                setProblem(null);
                setCues([]);
              }}
            >
              {words.approve}
            </button>
          ) : (
            <>
              <p ref={pickRef} tabIndex={-1}>
                <strong>{words.pickHeading}</strong>
              </p>
              {canAudio === null ? (
                <p class="muted small">{words.checking}</p>
              ) : (
                <>
                  {!canAudio && (
                    <p class="muted small">
                      {planUnknown ? words.planUnknown : words.textOnlyNote}
                    </p>
                  )}
                  <div class="row">
                    {ORDER.filter((c) => canAudio || c === "text").map((c) => (
                      <button
                        key={c}
                        type="button"
                        class={c === "text" ? "button secondary" : "button"}
                        aria-busy={busy === c}
                        disabled={busy !== null}
                        onClick={() => approve(c)}
                      >
                        {busy === c ? words.busy : words.choices[c]}
                      </button>
                    ))}
                    <button
                      type="button"
                      class="button secondary"
                      disabled={busy !== null}
                      onClick={() => {
                        setPicking(false);
                        keepFocus(() => approveBtnRef.current);
                      }}
                    >
                      {words.back}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
