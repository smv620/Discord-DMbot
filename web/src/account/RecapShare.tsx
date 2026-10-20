/** @jsxImportSource preact */
// Sharing a posted recap by a link (#2119; core #2118): the DM sees the link with Copy, can turn it
// off, or make a new one (the old one stops). Only a posted recap can have one; the API decides, and
// this shows what it says. The API gives the link's path (`/r/<token>`) and the page adds its own
// address. Shown only while RECAP_SHARE_LIVE is true and the API sent a `share` for the recap.
import { useRef, useState } from "preact/hooks";

import { shareWords as words } from "../content/recap-share";
import { RECAP_SHARE_LIVE } from "../content/recap-words";
import {
  ApiError,
  type AccountApi,
  type Recap,
  type RecapShare,
  type ShareAction,
} from "./api";

export default function RecapShareControls({
  api,
  recap,
  onShare,
}: {
  api: AccountApi;
  recap: Recap;
  onShare: (next: RecapShare) => void;
}) {
  const [busy, setBusy] = useState<ShareAction | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inFlight = useRef(false);
  const field = useRef<HTMLInputElement>(null);

  if (!RECAP_SHARE_LIVE || !recap.share || !api.shareRecap) return null;
  // A draft or skipped recap has no link to make; say what to do instead of a dead button.
  if (recap.status !== "posted")
    return (
      <section class="panel" aria-labelledby="recap-share">
        <h3 id="recap-share">{words.heading}</h3>
        <p class="muted small">{words.notPosted}</p>
      </section>
    );

  const { share } = recap;
  const link =
    share.on && share.path ? `${window.location.origin}${share.path}` : null;

  const act = async (action: ShareAction): Promise<void> => {
    if (!api.shareRecap || inFlight.current) return;
    inFlight.current = true;
    setBusy(action);
    setProblem(null);
    setNote(null);
    setCopied(false);
    try {
      onShare(await api.shareRecap(recap.ref, action));
      setNote(
        action === "off"
          ? words.doneOff
          : action === "new"
            ? words.doneFresh
            : words.doneOn,
      );
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(words.problem[kind] ?? words.problem.server ?? "");
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const copy = async (): Promise<void> => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      // No clipboard (an older browser, or not allowed): select the link so it can be copied by hand.
      field.current?.select();
      setProblem(words.copyFailed);
    }
  };

  return (
    <section class="panel" aria-labelledby="recap-share">
      <h3 id="recap-share">{words.heading}</h3>
      <p class="muted small">
        {link ? words.onLead(words.audio) : words.offLead}
      </p>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      <p class="small" role="status" aria-live="polite">
        {note ?? ""}
        {copied ? ` ${words.copied}.` : ""}
      </p>
      {link ? (
        <>
          <label class="field">
            {words.linkLabel}
            <input
              ref={field}
              type="text"
              readOnly
              value={link}
              onFocus={(e) => (e.target as HTMLInputElement).select()}
            />
          </label>
          <div class="row">
            <button type="button" class="button" onClick={() => void copy()}>
              {words.copy}
            </button>
            <button
              type="button"
              class="button secondary"
              disabled={busy !== null}
              onClick={() => void act("off")}
            >
              {busy === "off" ? words.working : words.off}
            </button>
            <button
              type="button"
              class="button secondary"
              disabled={busy !== null}
              onClick={() => void act("new")}
            >
              {busy === "new" ? words.working : words.fresh}
            </button>
          </div>
          <p class="muted small">{words.freshHint}</p>
        </>
      ) : (
        <button
          type="button"
          class="button"
          disabled={busy !== null}
          onClick={() => void act("on")}
        >
          {busy === "on" ? words.working : words.make}
        </button>
      )}
    </section>
  );
}
