/** @jsxImportSource preact */
// The admin page's free access panel (#773): who has it, add or change someone, revoke
// (after a confirm step), and the recent changes. The API checks every rule again.
import { useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/admin";
import {
  type AdminApi,
  AdminApiError,
  type AdminProblem,
  type GrantLevel,
  type GrantsView,
} from "./api";

interface Props {
  api: AdminApi;
  csrf: string;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
}

const NOTE_MAX = 200;

/** A date as "Oct 8, 2026", in UTC (the API stores whole UTC days). */
function day(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export default function Grants({ api, csrf, onSignedOut }: Props) {
  const [view, setView] = useState<GrantsView | "loading" | "down">("loading");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [discordId, setDiscordId] = useState("");
  const [level, setLevel] = useState<GrantLevel>("guild");
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<AdminProblem | null>(null);
  const idBox = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setView(await api.grants());
    } catch (error) {
      if (error instanceof AdminApiError && error.kind === "signed-out") onSignedOut();
      else setView("down");
    }
  }

  useEffect(() => {
    void load();
  }, [api]);

  // A refused id goes straight back to its box.
  useEffect(() => {
    if (problem === "bad-id") idBox.current?.focus();
  }, [problem]);

  /** Runs one change; a refusal becomes plain words, the end of the session a sign-in. */
  async function act(work: () => Promise<string>) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    setProblem(null);
    try {
      setNotice({ text: await work(), ok: true });
      await load();
    } catch (error) {
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") {
        onSignedOut();
        return;
      }
      setProblem(kind);
      setNotice({ text: text.grantErrors[kind] ?? text.down, ok: false });
      if (kind === "no-grant") await load();
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  function add(event: Event) {
    event.preventDefault();
    const id = discordId.trim();
    void act(async () => {
      const action = await api.give(csrf, {
        discordId: id,
        level,
        endsOn: endsOn || null,
        note,
      });
      setDiscordId("");
      setEndsOn("");
      setNote("");
      return action === "change" ? text.changed(id) : text.added(id);
    });
  }

  if (view === "loading") return <p role="status">{text.listLoading}</p>;
  if (view === "down") {
    return (
      <div class="stack">
        <p class="warn" role="alert">
          {text.down}
        </p>
        <button type="button" class="button" onClick={() => void load()}>
          {text.tryAgain}
        </button>
      </div>
    );
  }

  return (
    <div class="stack">
      <section class="stack" aria-labelledby="free-heading">
        <h2 id="free-heading">{text.freeHeading}</h2>
        <p>{text.freeLead}</p>
        {notice && (
          <p class={notice.ok ? "ok" : "warn"} role="alert">
            {notice.text}
          </p>
        )}
        <ul class="grants">
          {view.free.map((id) => (
            <li key={`free-${id}`} data-free={id}>
              <strong>{id}</strong>
              <span class="muted">{text.alwaysFree}</span>
            </li>
          ))}
          {view.grants.map((g) => (
            <li key={g.discordId} data-grant={g.discordId}>
              <strong>{g.discordId}</strong>
              <span>
                {text.levels[g.level] ?? g.level}.{" "}
                {/* endsAt is the moment it stops: the last day is the one before. */}
                {g.endsAt === null ? text.noEnd : text.until(day(g.endsAt - 1))}
              </span>
              {g.note && <span>{g.note}</span>}
              <span class="muted small">{text.addedBy(g.grantedBy, day(g.grantedAt))}</span>
              {confirming === g.discordId ? (
                <span class="row">
                  <span>{text.confirmRevoke(g.discordId)}</span>
                  <button
                    type="button"
                    class="button"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        await api.revoke(csrf, g.discordId);
                        return text.revoked(g.discordId);
                      })
                    }
                  >
                    {text.yesRevoke}
                  </button>
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    onClick={() => setConfirming(null)}
                  >
                    {text.cancel}
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  onClick={() => setConfirming(g.discordId)}
                >
                  {text.revoke}
                </button>
              )}
            </li>
          ))}
        </ul>
        {view.grants.length === 0 && <p class="muted">{text.nobody}</p>}
      </section>

      <section aria-labelledby="add-heading">
        <h2 id="add-heading">{text.addHeading}</h2>
        <p>{text.addLead}</p>
        <form onSubmit={add} class="stack" noValidate>
          <fieldset disabled={busy}>
            <label for="grant-id">{text.idLabel}</label>
            <input
              id="grant-id"
              ref={idBox}
              type="text"
              inputMode="numeric"
              autocomplete="off"
              required
              value={discordId}
              aria-invalid={problem === "bad-id"}
              aria-describedby="grant-id-hint"
              onInput={(e) => setDiscordId(e.currentTarget.value)}
            />
            <p id="grant-id-hint" class="small muted">
              {text.idHint}
            </p>
            <label for="grant-level">{text.levelLabel}</label>
            <select
              id="grant-level"
              value={level}
              onChange={(e) => setLevel(e.currentTarget.value as GrantLevel)}
            >
              <option value="guild">{text.levels.guild}</option>
              <option value="unlimited">{text.levels.unlimited}</option>
            </select>
            <label for="grant-end">{text.endLabel}</label>
            <input
              id="grant-end"
              type="date"
              value={endsOn}
              aria-invalid={problem === "bad-date" || problem === "past-date"}
              aria-describedby="grant-end-hint"
              onInput={(e) => setEndsOn(e.currentTarget.value)}
            />
            <p id="grant-end-hint" class="small muted">
              {text.endHint}
            </p>
            <label for="grant-note">{text.noteLabel}</label>
            <input
              id="grant-note"
              type="text"
              maxLength={NOTE_MAX}
              value={note}
              aria-invalid={problem === "long-note"}
              aria-describedby="grant-note-hint"
              onInput={(e) => setNote(e.currentTarget.value)}
            />
            <p id="grant-note-hint" class="small muted">
              {text.noteHint}
            </p>
          </fieldset>
          <button type="submit" class="button" disabled={busy}>
            {busy ? text.adding : text.add}
          </button>
        </form>
      </section>

      <section aria-labelledby="history-heading">
        <h2 id="history-heading">{text.historyHeading}</h2>
        {view.log.length === 0 ? (
          <p class="muted">{text.noHistory}</p>
        ) : (
          <ul class="history">
            {view.log.map((e, i) => (
              <li key={`${e.at}-${i}`}>{text.logLine(day(e.at), e.by, e.action, e.discordId)}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
