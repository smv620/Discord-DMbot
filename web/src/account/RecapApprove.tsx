/** @jsxImportSource preact */
// "Approve this recap" on the recap page (#2084, #2083, #2419): the DM reads the recap, may edit it,
// and approves it one of three plain ways. ✅ Approve and post text only: approved and handed to the
// bot to post, in one call. 🎙️ Approve and make audio to review: asks which (voice-over, music, both),
// makes it, and shows it to play; nothing is posted until the DM says so. 📋 Approve but don't post:
// approved and kept, with the text to copy. No audio starts by itself. The same labels are on Discord
// (#2420). The API checks the cues again and decides what is allowed; this shows what it says.
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
  type AudioUnavailable,
  type CueProblem,
  type Recap,
} from "./api";
import { CueProblems } from "./CueParts";
import { keepFocus } from "./focus";

const AUDIO_CHOICES: ApproveChoice[] = ["voice", "music", "both"];

/** The campaign's address, from the recap's: `<server>.<campaign>.<session>` without the session. */
const campaignRefOf = (recapRef: string): string =>
  recapRef.split(".").slice(0, 2).join(".");

type Kind = "post" | "keep" | ApproveChoice;

export default function RecapApprove({
  api,
  recap,
  onRecap,
  pageFocus,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
  /** Where focus goes when this part is gone (the recap was posted): the page's own heading. */
  pageFocus?: () => HTMLElement | null | undefined;
}) {
  const [picking, setPicking] = useState(false);
  // Why the narration can't be made: undefined while it is being asked, "" when it can be.
  const [why, setWhy] = useState<AudioUnavailable | "unknown" | "" | undefined>(
    undefined,
  );
  const [busy, setBusy] = useState<Kind | null>(null);
  const [done, setDone] = useState<ApproveChoice | null>(null);
  // "Keep it, don't post" pressed on a made audio: this visit's answer to the question under it.
  const [kept, setKept] = useState(false);
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [cues, setCues] = useState<CueProblem[]>([]);
  const inFlight = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const cuesRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pickRef = useRef<HTMLParagraphElement>(null);
  const audioBtnRef = useRef<HTMLButtonElement>(null);
  const on = !!recap.approval && !!api.approveRecap;
  const approval = recap.approval;
  // Shown for a recap that waits for the DM's decision, not for one already posted or set aside.
  const open = on && recap.status === "draft" && !!approval && (approval.approved || approval.canApprove);

  // What the plan and the month allow is asked once, when the buttons show: the audio button is
  // greyed with the reason, never hidden.
  useEffect(() => {
    if (!open || why !== undefined) return;
    if (!RECAP_AUDIO_LIVE) {
      setWhy("not_set_up");
      return;
    }
    if (!api.recapChoices) {
      setWhy("unknown");
      return;
    }
    let live = true;
    api.recapChoices(campaignRefOf(recap.ref)).then(
      (found) => {
        if (!live) return;
        // An API from before the reason existed says only whether the plan has it.
        setWhy(found.audioUnavailable ?? (found.canAudio ? "" : "plan"));
      },
      () => live && setWhy("unknown"),
    );
    return () => {
      live = false;
    };
  }, [api, open, why, recap.ref]);

  // The button that opened the choices is gone, so focus goes to their heading.
  useEffect(() => {
    if (picking) pickRef.current?.focus();
  }, [picking]);

  if (!open || !approval) return null;
  const { approveRecap, decideRecap } = api;

  const fail = async (error: unknown) => {
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
    // The plan or the month changed under us: stop offering the audio.
    if (kind === "not-in-plan") setWhy("plan");
    if (kind === "audio-unavailable") setWhy("allowance");
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
  };

  /** Run one request; the page shows the recap the API answers with. */
  const run = async (kind: Kind, request: () => Promise<Recap>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(kind);
    setProblem(null);
    setCues([]);
    try {
      const next = await request();
      onRecap(next);
      setPicking(false);
      keepFocus(() => statusRef.current ?? headingRef.current ?? pageFocus?.());
      return next;
    } catch (error) {
      await fail(error);
      return undefined;
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const approve = async (choice: ApproveChoice) => {
    if (!approveRecap) return;
    const next = await run(choice, () => approveRecap(recap.ref, choice));
    if (next) setDone(choice);
  };

  // ✅ One call approves and posts. An older API that doesn't know to post answers with the recap
  // still a draft: it is then approved, and the same press posts it, so the button never approves
  // and silently stops there.
  const approveAndPost = async () => {
    if (!approveRecap) return;
    await run("post", async () => {
      const next = await approveRecap(recap.ref, "text", { post: true });
      if (next.status === "draft" && next.canPost && decideRecap) {
        try {
          return await decideRecap(recap.ref, "post");
        } catch (error) {
          onRecap(next); // approved and not posted: say so, and say why the post failed
          throw error;
        }
      }
      return next;
    });
  };

  const postLater = async () => {
    if (!decideRecap) return;
    await run("post", () => decideRecap(recap.ref, "post"));
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(recap.text);
      setCopied("yes");
    } catch {
      setCopied("no");
    }
  };

  const audioMade = recap.audio?.state === "ready" && recap.audio.current;
  const audioChosen =
    approval.approved && ["voice", "music", "both"].includes(approval.choice);
  // Audio is still being made: nothing to decide yet (a failed one is the audio panel's to retry,
  // and the text can still be posted meanwhile).
  const making = audioChosen && recap.audio?.state === "queued";
  // The recap is approved and waits. After audio is made the DM first decides between posting it
  // and keeping it; once kept, or approved without audio, the recap is "approved, not posted".
  const deciding = audioChosen && audioMade && !kept;
  const settled = approval.approved && !deciding && !making;

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
            {deciding
              ? words.review
              : settled
                ? words.notPosted
                : done
                  ? words.done[done]
                  : words.was[approval.choice]}
          </p>
          {(deciding || settled) && (
            <div class="row">
              {recap.canPost && (
                <button
                  type="button"
                  class="button"
                  aria-busy={busy === "post"}
                  disabled={busy !== null}
                  onClick={postLater}
                >
                  {busy === "post" ? words.posting : words.postLater}
                </button>
              )}
              {deciding && (
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy !== null}
                  onClick={() => setKept(true)}
                >
                  {words.keepAudio}
                </button>
              )}
              {settled && (
                <button
                  type="button"
                  class="button secondary"
                  onClick={copy}
                >
                  {words.copy}
                </button>
              )}
            </div>
          )}
          {copied && (
            <p class={copied === "yes" ? "ok" : "warn"} role="status">
              {copied === "yes" ? words.copied : words.copyFailed}
            </p>
          )}
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
            <>
              <div class="row">
                <button
                  type="button"
                  class="button"
                  aria-busy={busy === "post"}
                  disabled={busy !== null}
                  onClick={approveAndPost}
                >
                  {busy === "post" ? words.busy : words.postText}
                </button>
                <button
                  type="button"
                  class="button"
                  ref={audioBtnRef}
                  aria-describedby={why ? "recap-audio-why" : undefined}
                  disabled={busy !== null || why !== ""}
                  onClick={() => {
                    setPicking(true);
                    setProblem(null);
                    setCues([]);
                  }}
                >
                  {words.makeAudio}
                </button>
                <button
                  type="button"
                  class="button secondary"
                  aria-busy={busy === "text"}
                  disabled={busy !== null}
                  onClick={() => approve("text")}
                >
                  {busy === "text" ? words.busy : words.keepIt}
                </button>
              </div>
              {why && (
                <p id="recap-audio-why" class="muted small">
                  {why === "unknown" ? words.audioUnknown : words.audioWhy[why]}
                </p>
              )}
            </>
          ) : (
            <>
              <p ref={pickRef} tabIndex={-1}>
                <strong>{words.pickHeading}</strong>
              </p>
              <div class="row">
                {AUDIO_CHOICES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    class="button"
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
                    keepFocus(() => audioBtnRef.current);
                  }}
                >
                  {words.back}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
