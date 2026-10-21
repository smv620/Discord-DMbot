/** @jsxImportSource preact */
// "Names in this campaign" on the recap page (#1451): the campaign's public names, each with a box
// for how the narrator should say it, and one Save for each name. Folded until opened, DM only (the
// recap page already is), and nothing at all while recaps or narration are off.
import { useRef, useState } from "preact/hooks";

import { nameWords as words, SAID_AS_MAX } from "../content/recap-names";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type CampaignName } from "./api";
import { sayIt as sayWords } from "../content/say-it-words";
import { keepFocus } from "./focus";
import SayIt from "./SayIt";

type Load =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; names: CampaignName[] };

type Row = { kind: "saving" | "saved" | "cleared" | "error"; message: string };

/** The campaign's address, from the recap's: `<server>.<campaign>.<session>` without the session. */
export function campaignRefOf(recapRef: string): string {
  return recapRef.split(".").slice(0, 2).join(".");
}

// The API lists at most this many names (core web/names.py, the database door).
const LIST_LIMIT = 200;

const letters = (text: string): number => [...text].length;

export default function RecapNames({
  api,
  recapRef,
}: {
  api: AccountApi;
  recapRef: string;
}) {
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Record<string, Row>>({});
  // One request per name at a time: a second tap before the page redraws sends nothing.
  const saving = useRef(new Set<string>());
  const ref = campaignRefOf(recapRef);

  if (!RECAPS_LIVE || !RECAP_AUDIO_LIVE || !api.campaignNames || !api.saveSaidAs)
    return null;
  const { campaignNames, saveSaidAs } = api;

  const open = (event: Event) => {
    if (!(event.currentTarget as HTMLDetailsElement).open || load.kind !== "idle")
      return;
    setLoad({ kind: "loading" });
    campaignNames(ref).then(
      (names) => {
        setDrafts(Object.fromEntries(names.map((n) => [n.id, n.saidAs ?? ""])));
        setLoad({ kind: "ready", names });
      },
      () => setLoad({ kind: "error" }),
    );
  };

  const say = (id: string, row: Row | null) =>
    setRows((r) => {
      const next = { ...r };
      if (row) next[id] = row;
      else delete next[id];
      return next;
    });

  const save = (name: CampaignName) => {
    if (saving.current.has(name.id)) return;
    const draft = (drafts[name.id] ?? "").trim().replace(/\s+/g, " ");
    if (letters(draft) > SAID_AS_MAX) {
      say(name.id, { kind: "error", message: words.tooLong });
      return;
    }
    saving.current.add(name.id);
    say(name.id, { kind: "saving", message: words.saving });
    // The button is disabled while saving, which drops its focus: put it back when done.
    const back = () => document.getElementById(`save-said-as-${name.id}`);
    saveSaidAs(ref, name.id, draft === "" ? null : draft).then(
      () => {
        saving.current.delete(name.id);
        keepFocus(back);
        setDrafts((d) => ({ ...d, [name.id]: draft }));
        say(name.id, {
          kind: draft === "" ? "cleared" : "saved",
          message: draft === "" ? words.cleared : words.saved,
        });
      },
      (error: unknown) => {
        saving.current.delete(name.id);
        keepFocus(back);
        const kind = error instanceof ApiError ? error.kind : "server";
        const message =
          kind === "signed-out"
            ? words.signedOut
            : kind === "not-found"
              ? words.gone
              : kind === "bad-text"
                ? words.odd
                : words.failed;
        say(name.id, { kind: "error", message });
      },
    );
  };

  return (
    <details class="panel names" onToggle={open}>
      <summary>{words.heading}</summary>
      <p>{words.lead}</p>
      <p class="small muted">{words.secret}</p>
      {/* Said once for the whole list, not under every name (it pushed Save away at 360 px). */}
      <p class="small muted">{sayWords.how}</p>
      {load.kind === "loading" && (
        <p class="muted" role="status">
          {words.loading}
        </p>
      )}
      {load.kind === "error" && (
        <p class="warn" role="alert">
          {words.loadFailed}
        </p>
      )}
      {load.kind === "ready" &&
        (load.names.length === 0 ? (
          <p class="muted" role="status">
            {words.none}
          </p>
        ) : (
          <ul class="campaigns">
            {load.names.map((name) => {
              const row = rows[name.id];
              const inputId = `said-as-${name.id}`;
              return (
                <li key={name.id}>
                  <span class="campaign-name">{name.text}</span>
                  <label for={inputId}>{words.label(name.text)}</label>
                  <input
                    id={inputId}
                    type="text"
                    maxLength={SAID_AS_MAX}
                    value={drafts[name.id] ?? ""}
                    onInput={(e) => {
                      setDrafts((d) => ({
                        ...d,
                        [name.id]: (e.currentTarget as HTMLInputElement).value,
                      }));
                      // "Saved." must not stay on screen over words that are not saved yet.
                      if (rows[name.id]?.kind !== "saving") say(name.id, null);
                    }}
                    disabled={row?.kind === "saving"}
                  />
                  <SayIt
                    api={api}
                    campaignRef={ref}
                    nameId={name.id}
                    name={name.text}
                    fieldId={inputId}
                    value={drafts[name.id] ?? ""}
                    disabled={row?.kind === "saving"}
                    onValue={(text) => {
                      // Into the box, unsaved: "Saved." must not stay over words that are not saved.
                      setDrafts((d) => ({ ...d, [name.id]: text }));
                      say(name.id, null);
                    }}
                  />
                  <button
                    type="button"
                    id={`save-said-as-${name.id}`}
                    class="button secondary"
                    onClick={() => save(name)}
                    disabled={row?.kind === "saving"}
                  >
                    {row?.kind === "saving" ? words.saving : words.save}
                  </button>
                  {row && row.kind !== "saving" && (
                    <p
                      class={row.kind === "error" ? "warn" : "small muted"}
                      role={row.kind === "error" ? "alert" : "status"}
                    >
                      {row.message}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        ))}
      {load.kind === "ready" && load.names.length >= LIST_LIMIT && (
        <p class="small muted">{words.more}</p>
      )}
    </details>
  );
}
