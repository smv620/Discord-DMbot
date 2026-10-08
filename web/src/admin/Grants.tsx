/** @jsxImportSource preact */
// The admin page's free access panel (#773): who has it (by name where we know it), give
// or change someone, revoke (after a confirm step), and the recent changes. The API
// checks every rule again.
import { useEffect, useRef, useState } from "preact/hooks";

import { grantError, NOTE_MAX, text, who } from "../content/admin";
import {
  type AdminApi,
  AdminApiError,
  type AdminGrant,
  type AdminProblem,
  type GrantLevel,
  type GrantsView,
} from "./api";

interface Props {
  api: AdminApi;
  csrf: string;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
  /** The admin page was switched off while open: the page shows that. */
  onOff: () => void;
}

interface News {
  text: string;
  ok: boolean;
}

/** A grant whose end date has passed: listed until revoked, but no longer free access. */
function ended(g: AdminGrant): boolean {
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

/** "2026-12-31": the last day a grant works, for the date box. */
function lastDay(endsAt: number): string {
  return new Date((endsAt - 1) * 1000).toISOString().slice(0, 10);
}

/** Tomorrow in UTC, the earliest end date the API accepts ("after today"). */
function tomorrow(): string {
  return new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
}

export default function Grants({ api, csrf, onSignedOut, onOff }: Props) {
  const [view, setView] = useState<GrantsView | "loading" | "down">("loading");
  const [busy, setBusy] = useState(false);
  // Two places for news, each by what it's about, so it shows where the owner is looking
  // (a long form on a phone puts the list a screen away).
  const [listNews, setListNews] = useState<News | null>(null);
  const [formNews, setFormNews] = useState<News | null>(null);
  const [saving, setSaving] = useState(false);
  // Read before re-rendering: two taps in the same moment mustn't send two requests.
  const inFlight = useRef(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  // Whoever the form is changing (their id box is then read-only), or null when adding.
  const [editing, setEditing] = useState<{ id: string; who: string } | null>(null);
  const [discordId, setDiscordId] = useState("");
  const [level, setLevel] = useState<GrantLevel>("guild");
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<AdminProblem | "no-id" | null>(null);
  const idBox = useRef<HTMLInputElement>(null);
  const levelBox = useRef<HTMLInputElement>(null);
  const formNewsBox = useRef<HTMLParagraphElement>(null);
  const listNewsBox = useRef<HTMLParagraphElement>(null);
  const yesRevoke = useRef<HTMLButtonElement>(null);
  const revokeOf = useRef(new Map<string, HTMLButtonElement>());
  const lastConfirm = useRef<string | null>(null);

  async function load() {
    try {
      setView(await api.grants());
    } catch (error) {
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") onSignedOut();
      else if (kind === "off") onOff();
      else setView("down");
    }
  }

  useEffect(() => {
    void load();
  }, [api]);

  // A refused id goes straight back to its box; other form news takes focus, so a phone
  // scrolls to it.
  useEffect(() => {
    if (problem === "bad-id" || problem === "no-id") idBox.current?.focus();
    else if (formNews) formNewsBox.current?.focus();
  }, [formNews, problem]);

  // A "Done." about the list takes focus too (the list may be a screen away).
  useEffect(() => {
    if (listNews) listNewsBox.current?.focus();
  }, [listNews]);

  // The confirm step takes focus; Cancel gives it back to that row's Revoke.
  useEffect(() => {
    if (confirming) yesRevoke.current?.focus();
    else if (lastConfirm.current) revokeOf.current.get(lastConfirm.current)?.focus();
    lastConfirm.current = confirming;
  }, [confirming]);

  /** Runs one change; a refusal becomes plain words, the end of the session a sign-in. */
  async function act(work: () => Promise<string>, show: (news: News | null) => void) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setListNews(null);
    setFormNews(null);
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
      if (kind === "off") {
        onOff();
        return;
      }
      setProblem(kind);
      show({ text: grantError(kind) ?? text.down, ok: false });
      if (kind === "no-grant") await load();
    } finally {
      inFlight.current = false;
      setBusy(false);
      setSaving(false);
      lastConfirm.current = null; // the row may be gone: don't chase its button
      setConfirming(null);
    }
  }

  function clearForm() {
    setEditing(null);
    setDiscordId("");
    setLevel("guild");
    setEndsOn("");
    setNote("");
    setFormNews(null);
    setProblem(null);
  }

  function save(event: Event) {
    event.preventDefault();
    const id = discordId.trim();
    if (!id) {
      setProblem("no-id");
      setFormNews({ text: grantError("no-id") ?? "", ok: false });
      return;
    }
    setSaving(true);
    void act(async () => {
      const done = await api.give(csrf, { discordId: id, level, endsOn: endsOn || null, note });
      const name = who(done.name, id);
      clearForm();
      return done.action === "change" ? text.changed(name) : text.added(name);
    }, setFormNews);
  }

  /** Fills the form with someone's grant, to change it. */
  function change(g: AdminGrant) {
    setEditing({ id: g.discordId, who: who(g.name, g.discordId) });
    setDiscordId(g.discordId);
    setLevel(g.level);
    setEndsOn(g.endsAt === null ? "" : lastDay(g.endsAt));
    setNote(g.note);
    setFormNews(null);
    setProblem(null);
    // Straight to the first thing that can change.
    setTimeout(() => levelBox.current?.focus(), 0);
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

  const person = (name: string | null, id: string) => (
    <>
      <strong>{name ?? id}</strong>
      {name && <span class="muted small">{id}</span>}
    </>
  );

  return (
    <div class="stack">
      <section class="stack" aria-labelledby="free-heading">
        <h2 id="free-heading">{text.freeHeading}</h2>
        <p>{text.freeLead}</p>
        {listNews && (
          <p
            class={listNews.ok ? "ok" : "warn"}
            role="alert"
            tabIndex={-1}
            ref={listNewsBox}
          >
            {listNews.text}
          </p>
        )}
        <ul class="grants">
          {view.free.map((p) => (
            <li key={`free-${p.discordId}`} data-free={p.discordId}>
              {person(p.name, p.discordId)}
              <span class="muted">{text.alwaysFree}</span>
            </li>
          ))}
          {/* Live grants first; ended ones stay listed (to renew or remove) after them. */}
          {[...view.grants]
            .sort((a, b) => Number(ended(a)) - Number(ended(b)))
            .map((g) => (
              <li key={g.discordId} data-grant={g.discordId}>
                {person(g.name, g.discordId)}
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
                    <span role="alert">{text.confirmRevoke(who(g.name, g.discordId))}</span>
                    <button
                      type="button"
                      class="button"
                      ref={yesRevoke}
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await api.revoke(csrf, g.discordId);
                          return text.revoked(who(g.name, g.discordId));
                        }, setListNews)
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
        <h2 id="add-heading">{editing ? text.changeHeading(editing.who) : text.addHeading}</h2>
        {!editing && <p>{text.addLead}</p>}
        <form onSubmit={save} class="stack" noValidate>
          <fieldset disabled={busy}>
            <label for="grant-id">{text.idLabel}</label>
            <input
              id="grant-id"
              ref={idBox}
              type="text"
              inputMode="numeric"
              autocomplete="off"
              required
              readOnly={editing !== null}
              value={discordId}
              aria-invalid={problem === "bad-id" || problem === "no-id"}
              aria-describedby="grant-id-hint"
              onInput={(e) => setDiscordId(e.currentTarget.value)}
            />
            {!editing && (
              <p id="grant-id-hint" class="small muted">
                {text.idHint}
              </p>
            )}
          </fieldset>
          <fieldset disabled={busy} aria-describedby="grant-level-hint">
            <legend>{text.levelLabel}</legend>
            {(["guild", "unlimited"] as const).map((value) => (
              <label key={value} class="choice">
                <input
                  type="radio"
                  name="grant-level"
                  value={value}
                  ref={(el) => {
                    if (value === "guild") levelBox.current = el;
                  }}
                  checked={level === value}
                  onChange={() => setLevel(value)}
                />{" "}
                {text.levels[value]}
              </label>
            ))}
            <p id="grant-level-hint" class="small muted">
              {text.levelHint}
            </p>
          </fieldset>
          <fieldset disabled={busy}>
            <label for="grant-end">{text.endLabel}</label>
            <input
              id="grant-end"
              type="date"
              min={tomorrow()}
              value={endsOn}
              aria-invalid={problem === "bad-date" || problem === "past-date"}
              aria-describedby="grant-end-hint"
              onInput={(e) => setEndsOn(e.currentTarget.value)}
            />
            <p id="grant-end-hint" class="small muted">
              {text.endHint}
            </p>
            {endsOn && (
              <button type="button" class="button secondary" onClick={() => setEndsOn("")}>
                {text.noEndButton}
              </button>
            )}
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
              {text.noteHint(NOTE_MAX)}
            </p>
          </fieldset>
          {formNews && (
            <p
              class={formNews.ok ? "ok" : "warn"}
              role="alert"
              tabIndex={-1}
              ref={formNewsBox}
            >
              {formNews.text}
            </p>
          )}
          <div class="row">
            <button type="submit" class="button" disabled={busy}>
              {saving ? text.adding : editing ? text.save : text.add}
            </button>
            {editing && (
              <button type="button" class="button secondary" disabled={busy} onClick={clearForm}>
                {text.startOver}
              </button>
            )}
          </div>
        </form>
      </section>

      <section aria-labelledby="history-heading">
        <h2 id="history-heading">{text.historyHeading}</h2>
        {view.log.length === 0 ? (
          <p class="muted">{text.noHistory}</p>
        ) : (
          <ul class="history">
            {view.log.map((e, i) => (
              <li key={`${e.at}-${i}`}>
                {text.logLine(day(e.at), e.by, e.action, who(e.name, e.discordId))}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
