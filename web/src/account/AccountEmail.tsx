/** @jsxImportSource preact */
// The email section of My Account (#2349). Below the first view, so the page loads it after itself (LazyPanel, #2743).
import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { isSafeRedirect } from "./redirect";

import { text } from "../content/account";
import { ApiError, type MyEmail } from "./api";
import { ActionButton, Notice, emailConfirmToken, forgetConfirm, forgetEmailLink, recalledConfirm, useAction, useShared } from "./account-shared";

/** The email DMbot uses, for recap emails (#2349). A payment email is changed with the payment company
 * (its page is asked for only on a press); any other is changed here, but only after a link sent to
 * the new address is opened. The link works only in the same signed-in account that asked (the API
 * checks it; signed out, the page asks for a sign-in first), so a typo or a stranger's address never
 * takes over. Its code is in the address bar's `#...`, is used once and is taken out of the bar at once.
 * Only the person's own address is ever shown. */
export default function EmailSection({ hash }: { hash: string }) {
  const { api, go } = useShared();
  const { busy, notice, run, clear } = useAction();
  const [mine, setMine] = useState<MyEmail | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [address, setAddress] = useState("");
  const [sent, setSent] = useState(false);
  // The API answered with nothing waiting: the address is the one already in use, so no link was sent.
  const [nothingSent, setNothingSent] = useState(false);
  const [typeFirst, setTypeFirst] = useState(false);
  const [portalOff, setPortalOff] = useState(false);
  const box = useRef<HTMLInputElement>(null);

  const read = useCallback(async (): Promise<MyEmail | null> => {
    try {
      const now = (await api.myEmail?.()) ?? null;
      setMine(now);
      setLoadFailed(false);
      return now;
    } catch (error) {
      // An API that doesn't have the route yet (the site deploys on its own): no section, no alarm.
      if (error instanceof ApiError && error.kind === "not-found") {
        setMine(null);
        setLoadFailed(false);
        return null;
      }
      // A 401 already ends the session for the whole page (SIGNED_OUT_EVENT); this only says it did not load.
      setLoadFailed(true);
      return null;
    }
  }, [api]);
  // The emailed link, opened once: the code leaves the address bar before it is sent.
  const confirmed = useRef(false);
  const [result, setResult] = useState<string | null>(null);
  useEffect(() => {
    // From the address, or kept across the sign-in that was needed first.
    const token = emailConfirmToken(hash) ?? recalledConfirm();
    if (token && api.confirmEmail && !confirmed.current) {
      confirmed.current = true;
      void (async () => {
        let worked = false;
        let answered = false;
        await run(
          async () => {
            await api.confirmEmail?.(token);
            worked = true;
          },
          (error) => {
            // Any answer from the API spends the code (a bad link, or an address another account took
            // while it waited); only a connection that failed or a server fault keeps it to try again.
            if (error.kind === "network" || error.kind === "server") return null;
            answered = true;
            return error.kind === "bad-link" ? text.emailConfirmBad : (text.errors[error.kind] ?? null);
          },
        );
        // The code goes only once the API has answered it; a network failure leaves it to try again.
        if (worked || answered) {
          forgetConfirm();
          forgetEmailLink();
        }
        const now = await read();
        if (worked) setResult(now?.address ? text.emailConfirmed(now.address) : null);
      })();
      return;
    }
    void read();
    // `run` is stable per section; only the page and its address start this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [read, hash]);

  if (loadFailed && !mine)
    return (
      <section aria-labelledby="email-section-heading" class="panel" id="email">
        <h3 id="email-section-heading">{text.myEmailHeading}</h3>
        <p class="warn" role="alert">
          {text.emailLoadFailed}
        </p>
      </section>
    );
  if (!mine) return null;

  const payment = mine.source === "payment";
  const stripe = (): void =>
    void run(
      async () => {
        const url = await api.billingPortalUrl();
        if (!isSafeRedirect(url)) throw new ApiError("server");
        go(url);
      },
      (error) => {
        if (error.kind !== "payments-off") return null;
        setPortalOff(true);
        return "";
      },
    );
  const submit = (event: Event): void => {
    event.preventDefault();
    if (address.trim() === "") {
      setTypeFirst(true);
      box.current?.focus();
      return;
    }
    setTypeFirst(false);
    void run(
      async () => {
        const answer = await api.changeEmail?.(address.trim());
        setNothingSent(answer?.pending === null);
        setSent(true);
        setEditing(false);
        setAddress("");
        await read();
      },
      (error) => (error.kind === "email-failed" ? text.emailSendFailed : null),
    );
  };

  return (
    <section aria-labelledby="email-section-heading" class="panel" id="email">
      <h3 id="email-section-heading">{text.myEmailHeading}</h3>
      <p>{mine.address ? text.emailNow(mine.address) : text.emailNone}</p>
      {payment && <p class="small">{text.emailPayment}</p>}
      {mine.pending && (
        <p class="small" role="status">
          {text.emailPending(mine.pending, mine.address)}
        </p>
      )}
      {mine.pending && !editing && api.cancelEmailChange && (
        <div class="row">
          <ActionButton
            busy={busy}
            kind="secondary"
            onClick={() =>
              void run(async () => {
                await api.cancelEmailChange?.();
                setSent(false);
                setResult(text.emailCancelled);
                await read();
              })
            }
          >
            {text.emailCancel}
          </ActionButton>
        </div>
      )}
      {result && (
        <p class="ok" role="status">
          {result}
        </p>
      )}
      {sent && !mine.pending && !editing && (
        <p class="ok" role="status">
          {nothingSent ? text.emailAlready : text.emailSaved}
        </p>
      )}
      <Notice message={notice} />
      {payment ? (
        <>
          {portalOff ? (
            <p class="small">{text.emailPortalOff}</p>
          ) : (
            <>
              <div class="row">
                <ActionButton busy={busy} kind="secondary" onClick={stripe}>
                  {text.emailPaymentButton}
                </ActionButton>
              </div>
              <p class="small muted">{text.emailPaymentHelp}</p>
            </>
          )}
        </>
      ) : editing ? (
        <form class="stack" noValidate onSubmit={submit}>
          <label class="field">
            {text.emailNewLabel}
            <input
              ref={box}
              type="email"
              inputMode="email"
              autoCapitalize="none"
              autoComplete="email"
              aria-describedby="email-link-note"
              value={address}
              maxLength={254}
              onInput={(e) => {
                setAddress((e.target as HTMLInputElement).value);
                setTypeFirst(false);
                clear();
              }}
            />
          </label>
          <p id="email-link-note" class="small muted">
            {text.emailLinkNote}
          </p>
          {typeFirst && (
            <p class="warn" role="alert">
              {text.emailTypeFirst}
            </p>
          )}
          <div class="row">
            <button type="submit" class="button" disabled={busy}>
              {busy ? text.busy : mine.pending ? text.emailSendAgain : text.emailSend}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setEditing(false);
                setTypeFirst(false);
                clear();
              }}
            >
              {text.cancel}
            </button>
          </div>
        </form>
      ) : (
        <div class="row">
          <button
            type="button"
            class="button secondary"
            onClick={() => {
              setEditing(true);
              setSent(false);
              setTimeout(() => box.current?.focus(), 0);
            }}
          >
            {mine.pending ? text.emailSendAgain : mine.address ? text.emailChange : text.emailAdd}
          </button>
        </div>
      )}
    </section>
  );
}
