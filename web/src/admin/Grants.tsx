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

/** A grant whose end date has passed: listed until revoked, but no longer free access. */
function ended(g: GrantsView["grants"][number]): boolean {
  return g.endsAt !== null && g.endsAt * 1000 <= Date.now();
}

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
  // Two places for news, each by what it's about, so it shows where the owner is looking
  // (a long form on a phone puts the list a screen away).
  const [listNotice, setListNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [formNotice, setFormNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [adding, setAdding] = useState(false);
  // Read before re-rendering: two taps in the same moment mustn't send two requests.
  const inFlight = useRef(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [discordId, setDiscordId] = useState("");
  const [level, setLevel] = useState<GrantLevel>("guild");
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<AdminProblem | "no-id" | null>(null);
  const idBox = useRef<HTMLInputElement>(null);
  const formNews = useRef<HTMLParagraphElement>(null);
  const yesRevoke = useRef<HTMLButtonElement>(null);
  const revokeOf = useRef(new Map<string, HTMLButtonElement>());
  const lastConfirm = useRef<string | null>(null);

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

  // A refused id goes straight back to its box; any other form news gets focus, so the
  // browser scrolls to it.
  useEffect(() => {
    if (problem === "bad-id" || problem === "no-id") idBox.current?.focus();
    else if (formNotice) formNews.current?.focus();
  }, [formNotice, problem]);

  // The confirm step takes focus; Cancel gives it back to that row's Revoke.
  useEffect(() => {
    if (confirming) yesRevoke.current?.focus();
    else if (lastConfirm.current) revokeOf.current.get(lastConfirm.current)?.focus();
    lastConfirm.current = confirming;
  }, [confirming]);

  /** Runs one change; a refusal becomes plain words, the end of the session a sign-in. */
  async function act(
    work: () => Promise<string>,
    show: (news: { text: string; ok: boolean } | null) => void,
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setListNotice(null);
    setFormNotice(null);
    setProblem(null);
    try {
      show({ text: await work(), ok: true });
      await load();
    } catch (error) {
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") {
        onSignedOut();
        return;
      }
      setProblem(kind);
      show({ text: text.grantErrors[kind] ?? text.down, ok: false });
      if (kind === "no-grant") await load();
    } finally {
      inFlight.current = false;
      setBusy(false);
      setAdding(false);
      lastConfirm.current = null; // the row may be gone: don't chase its button
      setConfirming(null);
    }
  }

  function add(event: Event) {
    event.preventDefault();
    const id = discordId.trim();
    if (!id) {
      setProblem("no-id");
      setFormNotice({ text: text.grantErrors["no-id"] ?? "", ok: false });
      return;
    }
    setAdding(true);
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
    }, setFormNotice);
  }

  /** Fills the form with someone's grant, to change it. */
  function change(g: GrantsView["grants"][number]) {
    setDiscordId(g.discordId);
    setLevel(g.level);
    setEndsOn(g.endsAt === null ? "" : new Date((g.endsAt - 1) * 1000).toISOString().slice(0, 10));
    setNote(g.note);
    setFormNotice(null);
    idBox.current?.focus();
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
        {listNotice && (
          <p class={listNotice.ok ? "ok" : "warn"} role="alert">
            {listNotice.text}
          </p>
        )}
        <ul class="grants">
          {view.free.map((id) => (
            <li key={`free-${id}`} data-free={id}>
              <strong>{id}</strong>
              <span class="muted">{text.alwaysFree}</span>
            </li>
          ))}
          {/* Live grants first; ended ones stay listed (to renew or remove) after them. */}
          {[...view.grants]
            .sort((a, b) => Number(ended(a)) - Number(ended(b)))
            .map((g) => (
            <li key={g.discordId} data-grant={g.discordId}>
              <strong>{g.discordId}</strong>
              <span>
                {text.levels[g.level] ?? g.level}.{" "}
                {/* endsAt is the moment it stops: the last day is the one before. */}
                {g.endsAt === null
                  ? text.noEnd
                  : ended(g)
                    ? text.ended(day(g.endsAt - 1))
                    : text.until(day(g.endsAt - 1))}
              </span>
              {g.note && <span>{g.note}</span>}
              <span class="muted small">{text.setBy(g.grantedBy, day(g.grantedAt))}</span>
              {confirming === g.discordId ? (
                <span class="row">
                  <span role="alert">{text.confirmRevoke(g.discordId)}</span>
                  <button
                    type="button"
                    class="button"
                    ref={yesRevoke}
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        await api.revoke(csrf, g.discordId);
                        return text.revoked(g.discordId);
                      }, setListNotice)
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
                <span class="row">
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    onClick={() => change(g)}
                  >
                    {text.change}
                  </button>
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    ref={(el) => {
                      if (el) revokeOf.current.set(g.discordId, el);
                      else revokeOf.current.delete(g.discordId);
                    }}
                    onClick={() => setConfirming(g.discordId)}
                  >
                    {text.revoke}
                  </button>
                </span>
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
              aria-invalid={problem === "bad-id" || problem === "no-id"}
              aria-describedby="grant-id-hint"
              onInput={(e) => setDiscordId(e.currentTarget.value)}
            />
            <p id="grant-id-hint" class="small muted">
              {text.idHint}
            </p>
            <label for="grant-level">{text.levelLabel}</label>
            <select
              id="grant-level"
              aria-describedby="grant-level-hint"
              value={level}
              onChange={(e) => setLevel(e.currentTarget.value as GrantLevel)}
            >
              <option value="guild">{text.levels.guild}</option>
              <option value="unlimited">{text.levels.unlimited}</option>
            </select>
            <p id="grant-level-hint" class="small muted">
              {text.levelHint}
            </p>
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
          {formNotice && (
            <p class={formNotice.ok ? "ok" : "warn"} role="alert" tabIndex={-1} ref={formNews}>
              {formNotice.text}
            </p>
          )}
          <button type="submit" class="button" disabled={busy}>
            {adding ? text.adding : text.add}
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
