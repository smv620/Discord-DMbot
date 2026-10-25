/** @jsxImportSource preact */
// The hand-over accept page (#2534; core #2532; docs/PLAN.md, "Hand-over by email"). The emailed link
// is `/handover#<token>` (in the hash, so no server log holds it). Signed in, the person sees which
// campaign, who offered it and whether they can take it, picks the server it runs on, and accepts or
// says no. The API decides everything (a paid plan with a free slot, a server of theirs with DMbot in
// it, an offer still open); this page only says what it is told. A link that has ended says so and
// changes nothing. The token goes in a request body, never in an address, and is kept across the
// sign-in in sessionStorage because the hash does not survive it.
import { useEffect, useRef, useState } from "preact/hooks";

import { HANDOVER_LIVE, handoverWords as words } from "../content/handover-words";
import { campaignHref } from "./campaignLinks";
import { ApiError, type AccountApi, type HandoverLook, type HandoverServer } from "./api";
import { keepFocus } from "./focus";

const KEY = "dmbot:handover";

/** The token in the address's hash, or one kept across the sign-in; null if neither looks right. */
export function handoverToken(hash: string): string | null {
  const fromHash = hash.replace(/^#/, "");
  if (/^[\w-]{1,200}$/.test(fromHash)) return fromHash;
  try {
    const kept = sessionStorage.getItem(KEY);
    return kept && /^[\w-]{1,200}$/.test(kept) ? kept : null;
  } catch {
    return null;
  }
}

export function keepHandoverToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // Storage may be blocked; the link in the email still works.
  }
}

function forgetHandoverToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing to forget
  }
}

type View =
  | { kind: "loading" }
  | { kind: "no-link" }
  | { kind: "signed-out" }
  | { kind: "down" }
  | { kind: "ended" }
  | { kind: "open"; look: Extract<HandoverLook, { status: "open" }> }
  | { kind: "accepted"; ref: string }
  // Accepted onto another server: the bot moves the campaign, then it shows up under the person's campaigns.
  | { kind: "moving"; name: string; before: string[]; slow: boolean }
  | { kind: "declined" };

/** The server to start on: where it runs now if the person can use it, else any other they can; never one the
 * campaign can't move to (`why`), which only leads to a refusal. */
function pickServer(servers: HandoverServer[]): string | null {
  const usable = servers.filter((s) => s.botIn && !s.why);
  return (usable.find((s) => s.current) ?? usable[0])?.id ?? null;
}

export default function Handover({ api, hash = "" }: { api: AccountApi; hash?: string }) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [server, setServer] = useState<string | null>(null);
  const [busy, setBusy] = useState<"accept" | "decline" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const token = useRef<string | null>(null);
  const headRef = useRef<HTMLHeadingElement>(null);
  const inFlight = useRef(false);
  const polls = useRef(0); // looks made while a campaign moves
  const [tryNo, setTry] = useState(0); // "Try again" after a failed look

  useEffect(() => {
    if (!HANDOVER_LIVE) return;
    const found = handoverToken(hash);
    token.current = found;
    if (!found) {
      setView({ kind: "no-link" });
      return;
    }
    keepHandoverToken(found); // so a sign-in on the way does not lose it
    // Take the code out of the address bar, so it is not left in the history or shared by a screenshot.
    // The page keeps it in memory and in sessionStorage; the email link still works.
    if (typeof window !== "undefined" && window.location.hash !== "") {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    void (async () => {
      try {
        const me = await api.me();
        if (!me) {
          setView({ kind: "signed-out" });
          return;
        }
        const look = await api.handoverLook!(found);
        if (look.status === "ended") {
          forgetHandoverToken();
          setView({ kind: "ended" });
          return;
        }
        // Your own campaign: nothing to accept, so the kept code is forgotten (My Account stops waiting).
        if (look.why === "owner") forgetHandoverToken();
        setServer(pickServer(look.servers));
        setView({ kind: "open", look });
      } catch (error) {
        setView(error instanceof ApiError && error.kind === "signed-out" ? { kind: "signed-out" } : { kind: "down" });
      }
    })();
    // Runs again only for a new API or "Try again": `hash` is read once (the code leaves the address bar), and
    // the other values it uses are refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, tryNo]);

  // While the bot moves the campaign, look every 3 seconds (up to 3 minutes) for it among the person's own.
  const moving = view.kind === "moving" && !view.slow;
  const [tickMove, setTickMove] = useState(0);
  const misses = useRef(0); // looks in a row that failed: each waits twice as long (3 s, 6, 12, up to 30)
  useEffect(() => {
    if (!moving || view.kind !== "moving") return;
    const wait = Math.min(3000 * 2 ** misses.current, 30000);
    const timer = setTimeout(async () => {
      polls.current += wait;
      try {
        // The offer first: while the bot moves it, `look` says ended; if the move failed the offer is open
        // again with the reason, and the person goes back to it (the code is kept in memory for this).
        const look = await api.handoverLook!(token.current ?? "");
        if (look.status === "open") {
          keepHandoverToken(token.current ?? "");
          setServer(pickServer(look.servers));
          setView({ kind: "open", look });
          keepFocus(() => headRef.current);
          return;
        }
        const found = ((await api.me())?.campaigns ?? []).find((c) => c.role === "owner" && c.name === view.name && !view.before.includes(c.ref ?? c.id));
        misses.current = 0;
        if (found?.ref) {
          setView({ kind: "accepted", ref: found.ref });
          keepFocus(() => headRef.current);
          return;
        }
      } catch {
        misses.current += 1; // a dropped look is tried again, a little later
      }
      if (polls.current >= 180000) setView({ ...view, slow: true });
      else setTickMove((n) => n + 1);
    }, wait);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moving, tickMove]);

  if (!HANDOVER_LIVE || !api.handoverLook || !api.handoverAccept || !api.handoverDecline) {
    return <p role="status">{words.notOpen}</p>;
  }

  const fail = (error: unknown) => {
    const kind = error instanceof ApiError ? error.kind : "server";
    if (kind === "signed-out" || kind === "sign-in-again") {
      setView({ kind: "signed-out" }); // the session ended meanwhile: sign in again, the code is kept
      keepFocus(() => headRef.current);
      return;
    }
    if (kind === "ended") {
      forgetHandoverToken();
      setView({ kind: "ended" });
      keepFocus(() => headRef.current);
      return;
    }
    setProblem(words.problem[kind] ?? words.problem["server"] ?? "");
  };

  const accept = async () => {
    if (inFlight.current || !token.current || !server) return;
    inFlight.current = true;
    setBusy("accept");
    setProblem(null);
    try {
      // The campaigns the person has now, to tell the moved one from them when it appears.
      const before = await api.me().then((m) => (m?.campaigns ?? []).map((c) => c.ref ?? c.id), () => [] as string[]);
      const done = await api.handoverAccept!(token.current, server);
      forgetHandoverToken();
      if ("moving" in done) {
        polls.current = 0;
        misses.current = 0;
        setView({ kind: "moving", name: view.kind === "open" ? view.look.campaign.name : "", before, slow: false });
      } else {
        setView({ kind: "accepted", ref: done.campaignRef });
      }
      keepFocus(() => headRef.current);
    } catch (error) {
      fail(error);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const decline = async () => {
    if (inFlight.current || !token.current) return;
    inFlight.current = true;
    setBusy("decline");
    setProblem(null);
    try {
      await api.handoverDecline!(token.current);
      forgetHandoverToken();
      setView({ kind: "declined" });
      keepFocus(() => headRef.current);
    } catch (error) {
      fail(error);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  switch (view.kind) {
    case "loading":
      return <p role="status">{words.loading}</p>;
    case "no-link":
      return <p role="status">{words.noLink}</p>;
    case "signed-out":
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p>{words.signInLead}</p>
          <p>
            <a class="button" href={api.signInUrl()}>
              {words.signInDiscord}
            </a>
          </p>
          <p>
            <a href="/account">{words.signInEmail}</a>
          </p>
          <p class="muted small">{words.signInNote}</p>
        </section>
      );
    case "down":
      return (
        <div class="stack">
          <p class="warn" role="alert">
            {words.loadFailed}
          </p>
          <p>
            <button type="button" class="button secondary" onClick={() => setTry((n) => n + 1)}>
              {words.tryAgain}
            </button>
          </p>
        </div>
      );
    case "ended":
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p role="status">{words.ended}</p>
        </section>
      );
    case "accepted":
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p role="status">{words.accepted}</p>
          <p>
            <a class="button" href={campaignHref(view.ref)}>
              {words.openCampaign}
            </a>
          </p>
        </section>
      );
    case "moving":
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p role="status">{view.slow ? words.movingSlow : words.moving}</p>
          {view.slow && (
            <p>
              <a class="button" href="/account">
                {words.myAccount}
              </a>
            </p>
          )}
        </section>
      );
    case "declined":
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p role="status">{words.declined}</p>
        </section>
      );
    case "open": {
      const { look } = view;
      const reason = look.why ? (words.why[look.why] ?? words.cantNow) : words.cantNow;
      const anyServer = look.servers.some((s) => s.botIn && !s.why);
      const onlyGreyed = !anyServer && look.servers.some((s) => s.botIn && s.why);
      return (
        <section class="panel stack" aria-labelledby="handover-heading">
          <h2 id="handover-heading" tabIndex={-1} ref={headRef}>
            {words.heading}
          </h2>
          <p>
            <strong>{words.offer(look.campaign.name, look.from.name)}</strong>
          </p>
          <p>{words.runsOn(look.server.name)}</p>
          {!look.canAccept ? (
            <>
              <p role="status">{reason}</p>
              {(look.why === "try_it" || look.why === "no_plan" || look.why === "no_slot") && (
                <p>
                  <a class="button" href="/pricing">
                    {words.seePlans}
                  </a>
                </p>
              )}
              {look.why !== "owner" && <p class="muted small">{words.linkKept}</p>}
              {look.why === "no_slot" && (
                <p>
                  <a href="/account">{words.myAccount}</a>
                </p>
              )}
              {look.why !== "owner" && (
                <p>
                  <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void decline()}>
                    {busy === "decline" ? words.declining : words.decline}
                  </button>
                </p>
              )}
            </>
          ) : (
            <>
              <fieldset class="stack">
                <legend>
                  <strong>{words.whereHeading}</strong>
                </legend>
                <p class="muted small">{words.whereLead}</p>
                {look.servers.map((s) => (
                  <div key={s.id} class="choice">
                    <label>
                      <input
                        type="radio"
                        name="handover-server"
                        value={s.id}
                        checked={server === s.id}
                        disabled={!s.botIn || !!s.why}
                        onChange={() => setServer(s.id)}
                      />{" "}
                      {s.name} {s.current && <span class="muted small">{words.current}</span>}
                      {!s.botIn && !s.why && <span class="muted small"> {words.needsBot}</span>}
                      {s.why && <span class="muted small"> {words.serverWhy[s.why] ?? words.cantMoveThere}</span>}
                    </label>
                    {!s.botIn && !s.why && (
                      <>
                        {" "}
                        <a href={api.installUrl(s.id)}>{words.addBotFor(s.name)}</a>
                      </>
                    )}
                  </div>
                ))}
                {!anyServer && !onlyGreyed && <p>{words.noServers}</p>}
                {onlyGreyed && <p role="status">{words.onlyHere}</p>}
                {look.servers.some((s) => !s.botIn) && <p class="muted small">{words.comeBack}</p>}
              </fieldset>
              <p>{words.acceptNote}</p>
              {look.moveFailed && (
                <p class="note" role="status">
                  {look.moveFailed}
                </p>
              )}
              {problem && (
                <p class="note bad" role="alert">
                  {problem}
                </p>
              )}
              {anyServer && !server && <p class="muted small">{words.pickFirst}</p>}
              <div class="row">
                {!onlyGreyed && (
                  <button
                    type="button"
                    class="button"
                    disabled={busy !== null || !server || !anyServer}
                    aria-busy={busy === "accept"}
                    onClick={() => void accept()}
                  >
                    {busy === "accept" ? words.accepting : words.accept}
                  </button>
                )}
                <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void decline()}>
                  {busy === "decline" ? words.declining : words.decline}
                </button>
              </div>
            </>
          )}
          {!look.canAccept && problem && (
            <p class="note bad" role="alert">
              {problem}
            </p>
          )}
        </section>
      );
    }
  }
}
