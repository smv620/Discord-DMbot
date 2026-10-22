/** @jsxImportSource preact */
// Which email a recap's email goes to, and how to change it (#2335). Shown only where the page says
// an email is coming. The payment company's page is asked for only when the DM presses the button,
// never ahead of time, and the browser goes only where isSafeRedirect allows.
import { useState } from "preact/hooks";

import { emailWords as words } from "../content/recap-email";
import { type AccountApi, ApiError, type RecapEmail as Email } from "./api";
import { isSafeRedirect } from "./redirect";

const defaultGo = (url: string): void => {
  window.location.assign(url);
};

export default function RecapEmailLine({
  api,
  email,
  go = defaultGo,
}: {
  api: AccountApi;
  email: Email | undefined;
  go?: ((url: string) => void) | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [paymentsOff, setPaymentsOff] = useState(false);
  if (!email) return null; // an API older than #2334 doesn't say; the page then names no address

  const stripe = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const url = await api.billingPortalUrl();
      if (!isSafeRedirect(url)) throw new ApiError("server");
      go(url);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "payments-off") setPaymentsOff(true);
      else setProblem(kind === "signed-out" ? words.signedOut : words.failed);
    } finally {
      setBusy(false);
    }
  };

  if (!email.on)
    return (
      <p class="small">
        {words.off}{" "}
        <a
          href="#recap-email-switch"
          onClick={(e) => {
            // Land on the switch itself, so the keyboard and a screen reader do too.
            const box = document.getElementById("recap-email-switch");
            if (!box) return;
            e.preventDefault();
            box.scrollIntoView?.({ block: "center" });
            box.focus();
          }}
        >
          {words.turnOn}
        </a>
      </p>
    );
  if (email.source === "none" || !email.address)
    return (
      <p class="small">
        {words.none} <a href="/account">{words.add}</a>
      </p>
    );
  if (email.source === "signin")
    return (
      <p class="small">
        {words.to(email.address)} {words.signin} <a href="/account">{words.change}</a>
      </p>
    );
  return (
    <div class="stack small">
      <p>
        {words.to(email.address)} {words.payment}
      </p>
      {paymentsOff ? (
        <p>
          {words.paymentsOff} <a href="/account">{words.accountPage}</a>
        </p>
      ) : (
        <>
          <div class="row">
            <button type="button" class="button secondary" onClick={() => void stripe()} disabled={busy}>
              {busy ? words.opening : words.changePayment}
            </button>
          </div>
          {problem && (
            <p class="warn" role="alert">
              {problem}
            </p>
          )}
          <p class="muted">{words.paymentHelp}</p>
        </>
      )}
    </div>
  );
}
