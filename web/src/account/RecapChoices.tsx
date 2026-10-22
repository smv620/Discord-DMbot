/** @jsxImportSource preact */
// "Your recap choices" on the recap page (#1468): the DM's three switches, the same as the DM
// screen's 📜 Recaps panel, in the panel's own words. A switch saves when it is pressed and says so
// in one line (the panel's "Saved." lines). The narration switch shows only while narration is on
// (RECAP_AUDIO_LIVE) and the API says the plan has it. Nothing at all before RECAPS_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { choiceWords as words } from "../content/recap-choices";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type RecapChoice, type RecapChoices, type RecapEmail } from "./api";
import RecapEmailLine from "./RecapEmail";

/** The campaign's address, from the recap's: `<server>.<campaign>.<session>` without the session. */
const campaignRefOf = (recapRef: string): string => recapRef.split(".").slice(0, 2).join(".");

const ORDER: RecapChoice[] = ["text", "audio", "email"];

export default function RecapChoicesPart({
  api,
  recapRef,
  email,
  working = false,
  onEmail,
}: {
  api: AccountApi;
  recapRef: string;
  /** Where this recap's email goes (#2335), so the switch never promises one with no address. */
  email?: RecapEmail | undefined;
  /** The recap is being made: the line above already names the address, so this panel does not (#2335). */
  working?: boolean;
  /** Tells the page whether the email switch is on, so the line while a recap is made follows it. */
  onEmail?: ((on: boolean) => void) | undefined;
}) {
  const [choices, setChoices] = useState<RecapChoices | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);
  // One change at a time: a second tap before the page redraws sends nothing (the switch is not
  // disabled, which would drop its focus).
  const saving = useRef(false);
  const ref = campaignRefOf(recapRef);
  const on = RECAPS_LIVE && !!api.recapChoices && !!api.setRecapChoice;

  useEffect(() => {
    if (!on || !api.recapChoices) return;
    let live = true;
    api.recapChoices(ref).then(
      (found) => live && setChoices(found),
      () => live && setProblem(words.loadFailed),
    );
    return () => {
      live = false;
    };
  }, [api, ref, on]);

  const emailOn = choices?.email;
  useEffect(() => {
    if (emailOn !== undefined) onEmail?.(emailOn);
    // `onEmail` is only a setter; the switch is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emailOn]);

  if (!on) return null;
  const { setRecapChoice } = api;

  const change = (which: RecapChoice, to: boolean) => {
    if (saving.current || !setRecapChoice) return;
    saving.current = true;
    setNote({ ok: true, message: words.saving });
    setRecapChoice(ref, which, to).then(
      (next) => {
        saving.current = false;
        setChoices(next);
        setNote({ ok: true, message: words.saved[which][to ? "on" : "off"] });
      },
      (error: unknown) => {
        saving.current = false;
        const kind = error instanceof ApiError ? error.kind : "server";
        setNote({
          ok: false,
          message:
            kind === "not-in-plan"
              ? words.notInPlan
              : kind === "signed-out"
                ? words.signedOut
                : kind === "not-found"
                  ? words.gone
                  : words.failed,
        });
      },
    );
  };

  const shown = ORDER.filter((w) => w !== "audio" || (RECAP_AUDIO_LIVE && choices?.canAudio));
  return (
    <section class="panel" aria-labelledby="recap-choices-heading">
      <h3 id="recap-choices-heading">{words.heading}</h3>
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {!problem && !choices && (
        <p class="muted" role="status">
          {words.loading}
        </p>
      )}
      {choices && (
        <>
          <ul class="switches">
            {shown.map((which) => (
              <li key={which}>
                <label>
                  <input
                    type="checkbox"
                    // The "Turn emails on" link under a working recap lands here (#2335).
                    id={which === "email" ? "recap-email-switch" : undefined}
                    checked={choices[which]}
                    onChange={(e) => change(which, (e.currentTarget as HTMLInputElement).checked)}
                  />{" "}
                  {words.labels[which]}
                  {which === "audio" && choices.audioHow ? ` (${choices.audioHow})` : ""}
                </label>
              </li>
            ))}
          </ul>
          {!choices.email && <p class="small muted">{words.emailOff}</p>}
          {/* With the switch on, the same line as under a working recap: which address, or that there is none.
              `on` follows the switch just changed here, not the page's first read. */}
          {choices.email && !working && <RecapEmailLine api={api} email={email ? { ...email, on: true } : undefined} />}
          {/* Always on the page, only its words change: a screen reader announces a live region that
              was already there, not one added together with its text. */}
          <p class="small" role="status">
            {note?.ok ? note.message : ""}
          </p>
          {note && !note.ok && (
            <p class="warn" role="alert">
              {note.message}
            </p>
          )}
          <p class="small muted">{words.sameOnDiscord}</p>
        </>
      )}
    </section>
  );
}
