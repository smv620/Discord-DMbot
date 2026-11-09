/** @jsxImportSource preact */
// The ten pages of the start-up wizard (#3956), one small component each. Each step saves what the person chose through
// `onPatch` as soon as they choose it, so a reload or a later visit picks up where they were; the shell (Wizard.tsx) owns Back,
// Next, Skip and the finish. Nothing here shows a document's words: only file names, counts and the person's own choices.
import { useRef, useState } from "preact/hooks";

import { SUPPLEMENTS, wizardWords as w } from "../content/wizard-words";
import { WizardError, type WizardAnswers, type WizardApi, type WizardCharacter, type WizardDoc, type WizardDraft, type WizardSlot } from "./wizard-api";

export interface StepProps {
  api: WizardApi;
  guild: string;
  draft: WizardDraft;
  /** Save a change to the answers now. */
  onPatch(patch: Partial<WizardAnswers>): Promise<void>;
  /** Replace the draft with a newer one (after a document or the rights changed). */
  onDraft(draft: WizardDraft): void;
}

const INVITE = /^https:\/\/www\.dndbeyond\.com\/campaigns\/join\/\d+/;
const SHEET = /^https:\/\/www\.dndbeyond\.com\/(?:profile\/[^/]+\/)?characters\/\d+/;

/** Step 1: where the campaign is, and the D&D Beyond invite and public sheet links. */
export function WhereStep({ api, guild, draft, onPatch }: StepProps) {
  const a = draft.answers;
  const [invite, setInvite] = useState(a.invite);
  const [inviteBad, setInviteBad] = useState(false);
  const [sheet, setSheet] = useState("");
  const [sheetNote, setSheetNote] = useState("");
  const [paste, setPaste] = useState("");
  const [pasteNote, setPasteNote] = useState("");
  const t = w.where;

  async function saveInvite() {
    const clean = invite.trim();
    if (clean && !INVITE.test(clean)) return setInviteBad(true);
    setInviteBad(false);
    await onPatch({ invite: clean });
  }
  async function addSheet() {
    const url = sheet.trim();
    if (!SHEET.test(url)) return setSheetNote(t.sheetNotFound);
    setSheetNote(t.sheetWaiting);
    try {
      const id = await api.sheet(guild, url);
      for (let i = 0; i < 30; i++) {
        const got = await api.sheetState(guild, id);
        if (got.state === "done") {
          const next: WizardCharacter = { full: got.name, partyName: got.name.split(" ")[0] ?? got.name, sheetUrl: url, player: "pending" };
          await onPatch({ characters: [...a.characters, next].slice(0, 12) });
          setSheet("");
          return setSheetNote(t.sheetFound(got.name));
        }
        if (got.state === "failed") return setSheetNote(got.error === "private" ? t.sheetPrivate : got.error === "slow" ? t.sheetSlow : t.sheetNotFound);
        await new Promise((r) => setTimeout(r, 1000));
      }
      setSheetNote(t.sheetSlow);
    } catch {
      setSheetNote(w.failed);
    }
  }
  async function readPaste() {
    try {
      const got = await api.pageText(guild, paste);
      if (got.characters.length === 0) return setPasteNote(t.pasteNone);
      await onPatch({
        pageTextUsed: true,
        ...(a.name ? {} : { name: got.name }),
        characters: [...a.characters, ...got.characters.map((c) => ({ full: c.full, partyName: c.partyName, sheetUrl: "", player: "pending" }))].slice(0, 12),
      });
      setPaste("");
      setPasteNote(t.pasteFound(got.characters.length));
    } catch {
      setPasteNote(w.failed);
    }
  }

  const choices: [WizardAnswers["source"], string][] = [["dndbeyond", t.dndbeyond], ["file", t.file], ["drive", t.drive]];
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      <div role="radiogroup" aria-label={t.heading}>
        {choices.map(([code, label]) => (
          <label key={code} class="row">
            <input type="radio" name="wizard-source" checked={a.source === code} onChange={() => void onPatch({ source: code })} /> {label}
          </label>
        ))}
      </div>
      {a.source === "file" && <p class="small">{t.fileLead}</p>}
      {a.source === "drive" && <p class="small">{t.driveLead}</p>}
      {a.source === "dndbeyond" && (
        <>
          <p class="small">{t.limit}</p>
          <label>
            {t.inviteLabel}
            <input type="url" value={invite} onInput={(e) => setInvite((e.target as HTMLInputElement).value)} onBlur={() => void saveInvite()} />
          </label>
          <p class="small">{t.inviteHint}</p>
          {inviteBad && <p role="alert">{t.inviteBad}</p>}
          <label>
            {t.sheetLabel}
            <input type="url" value={sheet} onInput={(e) => setSheet((e.target as HTMLInputElement).value)} />
          </label>
          <p class="small">{t.sheetHint}</p>
          <button type="button" class="button secondary" disabled={!sheet.trim()} onClick={() => void addSheet()}>
            {t.sheetAdd}
          </button>
          {sheetNote && <p role="status">{sheetNote}</p>}
          <label>
            {t.pasteLabel}
            <textarea rows={4} value={paste} onInput={(e) => setPaste((e.target as HTMLTextAreaElement).value)} />
          </label>
          <p class="small">{t.pasteHint}</p>
          <button type="button" class="button secondary" disabled={!paste.trim()} onClick={() => void readPaste()}>
            {t.pasteRead}
          </button>
          {pasteNote && <p role="status">{pasteNote}</p>}
        </>
      )}
    </section>
  );
}

/** Step 2: check the campaign's name and the characters found. */
export function ConfirmStep({ draft, onPatch }: StepProps) {
  const a = draft.answers;
  const t = w.confirm;
  const [name, setName] = useState(a.name);
  const [full, setFull] = useState("");
  const [party, setParty] = useState("");
  const add = async () => {
    const f = full.trim();
    if (!f || a.characters.length >= 12) return;
    await onPatch({ characters: [...a.characters, { full: f, partyName: party.trim() || f, sheetUrl: "", player: "pending" }] });
    setFull("");
    setParty("");
  };
  return (
    <section>
      <h2>{t.heading}</h2>
      <label>
        {t.nameLabel}
        <input type="text" maxLength={100} value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} onBlur={() => void onPatch({ name: name.trim() })} />
      </label>
      <h3>{t.charactersLabel}</h3>
      {a.characters.length === 0 ? (
        <p class="muted">{t.none}</p>
      ) : (
        <ul>
          {a.characters.map((c, i) => (
            <li key={`${c.full}-${i}`}>
              {c.full} {c.partyName !== c.full && <span class="small">({c.partyName})</span>}{" "}
              <button type="button" class="link" onClick={() => void onPatch({ characters: a.characters.filter((_, j) => j !== i) })}>
                {t.remove}
              </button>
            </li>
          ))}
        </ul>
      )}
      {a.characters.length >= 12 ? (
        <p class="small">{t.tooMany}</p>
      ) : (
        <div class="row">
          <label>
            {t.fullLabel}
            <input type="text" maxLength={100} value={full} onInput={(e) => setFull((e.target as HTMLInputElement).value)} />
          </label>
          <label>
            {t.partyLabel}
            <input type="text" maxLength={100} value={party} onInput={(e) => setParty((e.target as HTMLInputElement).value)} />
          </label>
          <button type="button" class="button secondary" disabled={!full.trim()} onClick={() => void add()}>
            {t.add}
          </button>
        </div>
      )}
    </section>
  );
}

/** Step 3: who plays each character (optional; a character with no player is "pending"). */
export function PlayersStep({ draft, onPatch }: StepProps) {
  const t = w.players;
  const a = draft.answers;
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      {a.characters.length === 0 ? (
        <p class="muted">{t.none}</p>
      ) : (
        <ul>
          {a.characters.map((c, i) => (
            <li key={`${c.full}-${i}`}>
              <label>
                {c.full}
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder={t.pending}
                  value={c.player === "pending" ? "" : c.player}
                  onChange={(e) => {
                    const v = (e.target as HTMLInputElement).value.trim();
                    void onPatch({ characters: a.characters.map((x, j) => (j === i ? { ...x, player: /^[1-9][0-9]{0,18}$/.test(v) ? v : "pending" } : x)) });
                  }}
                />
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Steps 4, 5, 7 and 8 (and the ruleset of 6): documents for one slot, checked only (never read for names yet). */
export function DocsStep({ api, guild, draft, onDraft, slot, heading, lead }: StepProps & { slot: WizardSlot; heading: string; lead: string }) {
  const t = w.docs;
  const mine = draft.documents.filter((d) => d.slot === slot);
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const kind = slot === "ruleset" ? "ruleset" : "shared";
  const ticked = draft.rights[kind];

  async function refresh(add: Promise<WizardDoc>) {
    setBusy(true);
    setProblem("");
    try {
      await add;
      const next = await api.get(guild);
      if (next) onDraft(next);
    } catch (error) {
      setProblem(error instanceof WizardError && error.kind === "too_many" ? t.full : w.failed);
    } finally {
      setBusy(false);
    }
  }
  async function addFile(file: File) {
    // A text or Markdown file is read here; the server's check reads PDF and Word from the bytes (wired with the real calls).
    const text = /\.(txt|md|markdown)$/i.test(file.name) ? await file.text() : undefined;
    await refresh(api.docAdd(guild, slot, { name: file.name, source: "upload", ...(text !== undefined ? { text } : {}) }));
  }
  async function tick(on: boolean) {
    if (!on) return;
    await api.rights(guild, kind);
    const next = await api.get(guild);
    if (next) onDraft(next);
  }
  return (
    <section>
      <h2>{heading}</h2>
      <p>{lead}</p>
      <p class="small">{t.checkOnly}</p>
      <label class="row">
        <input type="checkbox" checked={ticked} disabled={ticked} onChange={(e) => void tick((e.target as HTMLInputElement).checked)} />{" "}
        {slot === "ruleset" ? w.rules.ownRights : t.rights}
      </label>
      <p class="small">{t.rightsNote}</p>
      {mine.length === 0 ? (
        <p class="muted">{t.none}</p>
      ) : (
        <ul>
          {mine.map((d) => (
            <li key={d.id}>
              {d.name}{" "}
              {d.status === "ok" ? <span class="small">{d.source === "upload" ? t.counts(d.pages, d.words) : ""}</span> : <span role="alert">{t.unreadable[d.reason] ?? w.failed}</span>}{" "}
              <button
                type="button"
                class="link"
                onClick={() =>
                  void api.docRemove(guild, d.id).then(async () => {
                    const next = await api.get(guild);
                    if (next) onDraft(next);
                  })
                }
              >
                {t.remove}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div class="row">
        <input ref={input} type="file" hidden accept=".txt,.md,.markdown,.pdf,.docx" onChange={(e) => { const f = (e.target as HTMLInputElement).files?.[0]; if (f) void addFile(f); (e.target as HTMLInputElement).value = ""; }} />
        <button type="button" class="button secondary" disabled={busy || !ticked} onClick={() => input.current?.click()}>
          {t.add}
        </button>
        {draft.answers.source === "drive" && (
          <button type="button" class="button secondary" disabled={busy || !ticked} onClick={() => void refresh(api.docAdd(guild, slot, { name: "Google Drive file", source: "drive" }))}>
            {t.addDrive}
          </button>
        )}
      </div>
      <div class="row">
        <label>
          {t.linkLabel}
          <input type="url" value={link} onInput={(e) => setLink((e.target as HTMLInputElement).value)} />
        </label>
        <button type="button" class="button secondary" disabled={busy || !ticked || !link.trim()} onClick={() => { const url = link.trim(); setLink(""); void refresh(api.docAdd(guild, slot, { name: url, source: "link", link: url })); }}>
          {t.addLink}
        </button>
      </div>
      {!ticked && <p class="small">{t.rightsNeeded}</p>}
      {busy && <p role="status">{t.checking}</p>}
      {problem && <p role="alert">{problem}</p>}
    </section>
  );
}

/** Step 6: the rules to play with, the fallback, the seven supplements, and the person's own ruleset. */
export function RulesStep(props: StepProps) {
  const { draft, onPatch } = props;
  const a = draft.answers;
  const t = w.rules;
  const set = (target: "2024" | "2014") => void onPatch({ target, fallback: target === "2024" ? "2014" : "2024" });
  return (
    <>
      <section>
        <h2>{t.heading}</h2>
        <p>{t.lead}</p>
        <div role="radiogroup" aria-label={t.targetLabel}>
          <strong>{t.targetLabel}</strong>
          <label class="row"><input type="radio" name="wizard-target" checked={a.target === "2024"} onChange={() => set("2024")} /> {t.current}</label>
          <label class="row"><input type="radio" name="wizard-target" checked={a.target === "2014"} onChange={() => set("2014")} /> {t.legacy}</label>
        </div>
        <p class="small">
          {t.fallbackLabel}: {a.fallback === "2024" ? t.current : t.legacy}
        </p>
        <fieldset>
          <legend>{t.supplementsLabel}</legend>
          {SUPPLEMENTS.map((s) => (
            <label key={s.code} class="row">
              <input
                type="checkbox"
                checked={a.supplements.includes(s.code)}
                onChange={(e) => void onPatch({ supplements: (e.target as HTMLInputElement).checked ? [...a.supplements, s.code] : a.supplements.filter((c) => c !== s.code) })}
              />{" "}
              {s.title}
            </label>
          ))}
        </fieldset>
        <p class="small">{t.supplementsNote}</p>
      </section>
      <DocsStep {...props} slot="ruleset" heading={t.ownLabel} lead={t.ownLead} />
    </>
  );
}

/** Step 9: whether this campaign's story may be used to improve DMbot. */
export function DataStep({ draft, onPatch }: StepProps) {
  const t = w.data;
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      <div role="radiogroup" aria-label={t.heading}>
        <label class="row"><input type="radio" name="wizard-data" checked={draft.answers.dataUse} onChange={() => void onPatch({ dataUse: true })} /> {t.yes}</label>
        <label class="row"><input type="radio" name="wizard-data" checked={!draft.answers.dataUse} onChange={() => void onPatch({ dataUse: false })} /> {t.no}</label>
      </div>
      <p class="small">{t.note}</p>
    </section>
  );
}
