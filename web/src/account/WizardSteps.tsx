/** @jsxImportSource preact */
// The ten pages of the start-up wizard (#3956), one small component each. Each step saves what the person chose through
// `onPatch` as soon as they choose it, so a reload or a later visit picks up where they were; the shell (Wizard.tsx) owns Back,
// Next, Skip and the finish. Nothing here shows a document's words: only file names, counts and the person's own choices.
import { useEffect, useRef, useState } from "preact/hooks";

import { WIZARD_DRIVE_LIVE, WIZARD_LINKS_LIVE, WIZARD_OWN_RULESET_LIVE, WIZARD_SHEETS_LIVE } from "../content/wizard-switch";
import { SUPPLEMENTS, wizardWords as w } from "../content/wizard-words";
import PlayerPicker from "./PlayerPicker";
import { problemWords } from "./wizard-problems";
import { maxBytesFor, toBase64, type DataChoice, type WizardAnswers, type WizardApi, type WizardCharacter, type WizardDoc, type WizardDraft, type WizardMember, type WizardSlot } from "./wizard-api";

export interface StepProps {
  api: WizardApi;
  guild: string;
  draft: WizardDraft;
  /** Save a change to the answers now, keeping the step the person is on. A function gets the latest answers (so a slow
   * step never builds on an old list). */
  onPatch(patch: Partial<WizardAnswers> | ((latest: WizardAnswers) => Partial<WizardAnswers>)): Promise<void>;
  /** Replace the draft with a newer one (after a document or the rights changed). */
  onDraft(draft: WizardDraft): void;
  /** Step 9's pick (#4212): nothing is chosen until the DM picks. */
  dataChoice?: DataChoice;
  onDataChoice?(choice: "yes" | "no"): void;
  /** The text of the campaign page the DM pasted in step 1, kept in the page's own memory while the wizard is open so Back shows it
   * again (#4204); never sent to the server except to be read, and gone on a reload. */
  pasted?: string;
  onPasted?(text: string): void;
}

const INVITE = /^https:\/\/www\.dndbeyond\.com\/campaigns\/join\/\d+/;
/** The books whose rules are on by default (rules.optional.CATALOG), in step 6's codes. */
const DEFAULT_BOOKS = ["xanathar", "tasha"];
const SHEET = /^https:\/\/www\.dndbeyond\.com\/(?:profile\/[^/]+\/)?characters\/\d+/;

/** Step 1: where the campaign is, and the dndbeyond.com invite and public sheet links. */
export function WhereStep({ api, guild, draft, onPatch, pasted = "", onPasted }: StepProps) {
  const a = draft.answers;
  const [invite, setInvite] = useState(a.invite);
  const [inviteBad, setInviteBad] = useState(false);
  const [sheet, setSheet] = useState("");
  const [sheetNote, setSheetNote] = useState("");
  const [local, setLocal] = useState(pasted); // (the wizard keeps the text across steps; a lone step keeps its own)
  const paste = onPasted ? pasted : local;
  const setPaste = (text: string): void => (onPasted ? onPasted(text) : setLocal(text));
  const [pasteNote, setPasteNote] = useState("");
  const [working, setWorking] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => void (mounted.current = false), []);
  const t = w.where;

  async function saveInvite() {
    const clean = invite.trim();
    if (clean && !INVITE.test(clean)) return setInviteBad(true);
    setInviteBad(false);
    await onPatch({ invite: clean });
  }
  async function addSheet() {
    const url = sheet.trim();
    if (working) return;
    if (!SHEET.test(url)) return setSheetNote(t.sheetNotFound);
    setWorking(true);
    setSheetNote(t.sheetWaiting);
    try {
      const id = await api.sheet(guild, url);
      for (let i = 0; i < 30 && mounted.current; i++) {
        const got = await api.sheetState(guild, id);
        if (!mounted.current) return;
        if (got.state === "done") {
          const next: WizardCharacter = { full: got.name, partyName: got.name.split(" ")[0] ?? got.name, sheetUrl: url, player: "pending" };
          await onPatch((latest) => ({ characters: [...latest.characters, next].slice(0, 12) }));
          setSheet("");
          return setSheetNote(t.sheetFound(got.name));
        }
        if (got.state === "failed") return setSheetNote(got.error === "private" ? t.sheetPrivate : got.error === "slow" ? t.sheetSlow : t.sheetNotFound);
        await new Promise((r) => setTimeout(r, 1000));
      }
      if (mounted.current) setSheetNote(t.sheetSlow);
    } catch {
      if (mounted.current) setSheetNote(w.failed);
    } finally {
      if (mounted.current) setWorking(false);
    }
  }
  async function readPaste() {
    if (working) return;
    setWorking(true);
    try {
      const got = await api.pageText(guild, paste);
      if (got.characters.length === 0) return setPasteNote(t.pasteNone);
      await onPatch((latest) => ({
        pageTextUsed: true,
        ...(latest.name ? {} : { name: got.name }),
        characters: [...latest.characters, ...got.characters.map((c) => ({ full: c.full, partyName: c.partyName, sheetUrl: "", player: "pending" }))].slice(0, 12),
      }));
      setPasteNote(t.pasteFound(got.characters.length)); // (the text stays in the box, so Back shows it again)
    } catch {
      setPasteNote(w.failed);
    } finally {
      if (mounted.current) setWorking(false);
    }
  }

  const choices: [WizardAnswers["source"], string][] = [["dndbeyond", t.dndbeyond], ["file", t.file], ...(WIZARD_DRIVE_LIVE ? ([["drive", t.drive]] as [WizardAnswers["source"], string][]) : [])];
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
          {WIZARD_SHEETS_LIVE && (
            <>
          <label>
            {t.sheetLabel}
            <input type="url" value={sheet} onInput={(e) => setSheet((e.target as HTMLInputElement).value)} />
          </label>
          <p class="small">{t.sheetHint}</p>
          <button type="button" class="button secondary" disabled={working || !sheet.trim()} onClick={() => void addSheet()}>
            {t.sheetAdd}
          </button>
          {sheetNote && <p role="status">{sheetNote}</p>}
            </>
          )}
          <label>
            {t.pasteLabel}
            <textarea rows={4} value={paste} onInput={(e) => setPaste((e.target as HTMLTextAreaElement).value)} />
          </label>
          <p class="small">{t.pasteHint}</p>
          <button type="button" class="button secondary" disabled={working || !paste.trim()} onClick={() => void readPaste()}>
            {t.pasteRead}
          </button>
          {pasteNote && <p role="status">{pasteNote}</p>}
          {!paste && !pasteNote && a.pageTextUsed && <p class="small">{t.pastedEarlier(a.characters.filter((c) => !c.sheetUrl).length)}</p>}
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
  const [sheetUrl, setSheetUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const add = async () => {
    const f = full.trim();
    if (!f || adding || a.characters.length >= 12) return;
    setAdding(true);
    try {
      await onPatch((latest) => ({ characters: [...latest.characters, { full: f, partyName: party.trim() || f, sheetUrl: WIZARD_SHEETS_LIVE ? sheetUrl.trim() : "", player: "pending" }].slice(0, 12) }));
      setFull("");
      setParty("");
      setSheetUrl("");
    } finally {
      setAdding(false);
    }
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
              <button type="button" class="link" onClick={() => void onPatch((latest) => ({ characters: latest.characters.filter((_, j) => j !== i) }))}>
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
            <input type="text" maxLength={100} value={party} onInput={(e) => setParty((e.target as HTMLInputElement).value)} aria-describedby="wizard-party-hint" />
            <span class="small" id="wizard-party-hint">
              {t.partyHint}
            </span>
          </label>
          {WIZARD_SHEETS_LIVE && (
            <label>
              {t.sheetLabelBefore}
              <a href="https://www.dndbeyond.com" target="_blank" rel="noopener noreferrer">
                {t.sheetLabelLink}
              </a>
              {t.sheetLabelAfter}
              <input type="url" value={sheetUrl} onInput={(e) => setSheetUrl((e.target as HTMLInputElement).value)} />
            </label>
          )}
          <button type="button" class="button secondary" disabled={adding || !full.trim()} onClick={() => void add()}>
            {t.add}
          </button>
        </div>
      )}
    </section>
  );
}

/** Step 3: who plays each character (optional; a character with no player is "pending"). The player is picked from this server's
 * members by name (#4204); text that matches no one stays in the box and the character stays "not set yet". */
export function PlayersStep({ api, guild, draft, onPatch }: StepProps) {
  const t = w.players;
  const a = draft.answers;
  const [members, setMembers] = useState<WizardMember[] | null | "failed">(null);
  useEffect(() => {
    let live = true;
    api.members(guild).then(
      (got) => live && setMembers(got),
      () => live && setMembers("failed"),
    );
    return () => void (live = false);
  }, [api, guild]);
  const pending = a.characters.filter((c) => c.player === "pending").length;
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      {a.characters.length === 0 ? (
        <p class="muted">{t.none}</p>
      ) : members === null ? (
        <p class="muted" role="status">{t.loading}</p>
      ) : (
        <>
          {(members === "failed" || members.length === 0) && <p role="status">{t.cannotSee}</p>}
          <ul>
            {a.characters.map((c, i) => (
              <li key={`${c.full}-${i}`}>
                <PlayerPicker
                  label={c.full}
                  members={members === "failed" ? [] : members}
                  value={c.player}
                  onPick={(id) => void onPatch((latest) => ({ characters: latest.characters.map((x, j) => (j === i ? { ...x, player: id } : x)) }))}
                />
              </li>
            ))}
          </ul>
          {pending > 0 && <p class="small" role="status">{t.noPlayerYet(pending)}</p>}
        </>
      )}
    </section>
  );
}

/** Steps 4, 5, 7 and 8 (and the ruleset of 6): documents for one slot, checked only (never read for names yet). */
export function DocsStep({ api, guild, draft, onDraft, onPatch, slot, heading, lead }: StepProps & { slot: WizardSlot; heading: string; lead: string }) {
  const t = w.docs;
  const mine = draft.documents.filter((d) => d.slot === slot);
  const [link, setLink] = useState("");
  const [typed, setTyped] = useState(""); // house rules typed in, one per line (#4211)
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [ticking, setTicking] = useState(false);
  // The pictures-only count of each file, from the upload's own answer: a re-read draft does not carry it (#4030).
  const [pictures, setPictures] = useState<Record<string, number>>({});
  const input = useRef<HTMLInputElement>(null);
  const kind = slot === "ruleset" ? "ruleset" : "shared";
  const ticked = draft.rights[kind];

  async function refresh(add: Promise<WizardDoc>) {
    setBusy(true);
    setProblem("");
    try {
      const added = await add;
      if ((added.picturePages ?? 0) > 0) setPictures((was) => ({ ...was, [added.id]: added.picturePages ?? 0 }));
      const next = await api.get(guild);
      if (next) onDraft(next);
    } catch (error) {
      setProblem(problemWords(error));
    } finally {
      setBusy(false);
    }
  }
  async function addFile(file: File) {
    // The file goes as it is, as base64 in the body; the server opens it and counts its pages and words (no AI). A file over the
    // server's limit gets the "too big" line before anything is sent.
    try {
      if (file.size > maxBytesFor(file.name)) return setProblem(t.unreadable["too_big"]!);
      const data = toBase64(new Uint8Array(await file.arrayBuffer()));
      await refresh(api.docAdd(guild, slot, { name: file.name, source: "upload", data }));
    } catch (error) {
      setProblem(problemWords(error));
    }
  }
  async function tick(on: boolean) {
    if (!on || ticking) return;
    setTicking(true);
    setProblem("");
    try {
      await api.rights(guild, kind);
      const next = await api.get(guild);
      if (next) onDraft(next);
    } catch (error) {
      setProblem(problemWords(error)); // (the box stays as it was: the draft says whether it is ticked)
    } finally {
      setTicking(false);
    }
  }
  async function remove(id: string) {
    setProblem("");
    try {
      await api.docRemove(guild, id);
      const next = await api.get(guild);
      if (next) onDraft(next);
    } catch (error) {
      setProblem(problemWords(error));
    }
  }
  return (
    <section>
      <h2>{heading}</h2>
      <p>{lead}</p>
      <p class="small">{t.checkOnly}</p>
      <label class="row">
        <input type="checkbox" checked={ticked} disabled={ticked || ticking} onChange={(e) => void tick((e.target as HTMLInputElement).checked)} />{" "}
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
              {d.status === "ok" && (pictures[d.id] ?? 0) > 0 ? <span class="small">{t.picturePages(pictures[d.id] ?? 0)}</span> : null}{" "}
              <button type="button" class="link" onClick={() => void remove(d.id)}>
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
        {WIZARD_DRIVE_LIVE && draft.answers.source === "drive" && (
          <button type="button" class="button secondary" disabled={busy || !ticked} onClick={() => void refresh(api.docAdd(guild, slot, { name: "Google Drive file", source: "drive" }))}>
            {t.addDrive}
          </button>
        )}
      </div>
      {slot === "homebrew" && <HomebrewLinks {...{ draft, onPatch, ticked, busy }} />}
      {slot === "house" && (
        <div class="stack">
          <label>
            {t.typedLabel}
            <textarea rows={5} value={typed} disabled={!ticked} aria-describedby="typed-note" onInput={(e) => setTyped((e.target as HTMLTextAreaElement).value)} />
          </label>
          <span id="typed-note" class="small muted">
            {t.typedNote}
          </span>
          <button
            type="button"
            class="button secondary"
            disabled={busy || !ticked}
            onClick={() => {
              const text = typed.trim();
              if (!text) return setProblem(t.typedEmpty);
              setTyped("");
              void refresh(api.docAdd(guild, slot, { name: t.typedName, source: "upload", text }));
            }}
          >
            {t.typedAdd}
          </button>
        </div>
      )}
      {WIZARD_LINKS_LIVE && slot !== "homebrew" && (
      <div class="row">
        <label>
          {t.linkLabel}
          <input type="url" value={link} onInput={(e) => setLink((e.target as HTMLInputElement).value)} />
        </label>
        <button type="button" class="button secondary" disabled={busy || !ticked || !link.trim()} onClick={() => { const url = link.trim(); if (!/^https:\/\//.test(url)) return setProblem(t.linkBad);
            if (url.length > LINK_MAX) return setProblem(t.linkTooLong); setLink(""); void refresh(api.docAdd(guild, slot, { name: url.slice(0, 200), source: "link", link: url })); }}>
          {t.addLink}
        </button>
      </div>
      )}
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
  // Until the person changes a box, nothing is sent and the campaign keeps its default: the books whose rules are on by default
  // show ticked, so a change to one box never turns another off unseen (#4014).
  const shown = a.supplements ?? DEFAULT_BOOKS;
  // The two sets and "none". "Upload your own" (behind WIZARD_OWN_RULESET_LIVE) opens the file box under the dropdown that holds it; one
  // own set per campaign, so the other dropdown does not offer it. Core takes only the two sets as the primary today, so choosing it does
  // not change what is saved (the follow-up that turns the switch on decides that, #3953).
  const [own, setOwn] = useState<"primary" | "secondary" | null>(null);
  const label = (v: "2024" | "2014"): string => (v === "2024" ? t.current : t.legacy);
  const other = (v: "2024" | "2014"): "2024" | "2014" => (v === "2024" ? "2014" : "2024");
  const setPrimary = (v: string): void => {
    if (v === "own") return setOwn("primary");
    setOwn((o) => (o === "primary" ? null : o));
    const target = v === "2014" ? "2014" : "2024";
    // the secondary never repeats the primary: a repeat turns into the other set (or stays none)
    void onPatch({ target, ...(a.fallback === target ? { fallback: other(target) } : {}) });
  };
  const setSecondary = (v: string): void => {
    if (v === "own") return setOwn("secondary");
    setOwn((o) => (o === "secondary" ? null : o));
    void onPatch({ fallback: v === "none" ? "none" : v === "2014" ? "2014" : "2024" });
  };
  const ownBox = WIZARD_OWN_RULESET_LIVE;
  return (
    <>
      <section>
        <h2>{t.heading}</h2>
        <p>{t.lead}</p>
        <label>
          {t.primaryLabel}
          <select value={own === "primary" ? "own" : a.target} onChange={(e) => setPrimary((e.target as HTMLSelectElement).value)}>
            <option value="2024">{t.current}</option>
            <option value="2014">{t.legacy}</option>
            {ownBox && own !== "secondary" && <option value="own">{t.uploadOwn}</option>}
          </select>
        </label>
        {ownBox && own === "primary" && <DocsStep {...props} slot="ruleset" heading={t.ownLabel} lead={t.ownLead} />}
        <label>
          {t.secondaryLabel}
          <select
            value={own === "secondary" ? "own" : a.fallback}
            aria-describedby="wizard-secondary-hint"
            onChange={(e) => setSecondary((e.target as HTMLSelectElement).value)}
          >
            <option value="none">{t.none}</option>
            <option value={other(a.target)}>{label(other(a.target))}</option>
            {ownBox && own !== "primary" && <option value="own">{t.uploadOwn}</option>}
          </select>
        </label>
        <p class="small" id="wizard-secondary-hint">
          {t.secondaryHint}
        </p>
        {ownBox && own === "secondary" && <DocsStep {...props} slot="ruleset" heading={t.ownLabel} lead={t.ownLead} />}
        <p class="small">{t.houseNote}</p>
        <fieldset>
          <legend>{t.supplementsLabel}</legend>
          {SUPPLEMENTS.map((s) => (
            <label key={s.code} class="row">
              <input
                type="checkbox"
                checked={shown.includes(s.code)}
                onChange={(e) => void onPatch({ supplements: (e.target as HTMLInputElement).checked ? [...shown, s.code] : shown.filter((c) => c !== s.code) })}
              />{" "}
              {s.title}
            </label>
          ))}
        </fieldset>
        <p class="small">{t.supplementsNote}</p>
      </section>
    </>
  );
}

/** Step 9: whether this campaign's story may be used to improve DMbot. */
export function DataStep({ dataChoice = "none", onDataChoice }: StepProps) {
  const t = w.data;
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      <div role="radiogroup" aria-label={t.heading}>
        <label class="row"><input type="radio" name="wizard-data" checked={dataChoice === "yes"} onChange={() => onDataChoice?.("yes")} /> {t.yes}</label>
        <label class="row"><input type="radio" name="wizard-data" checked={dataChoice === "no"} onChange={() => onDataChoice?.("no")} /> {t.no}</label>
      </div>
      {dataChoice === "none" && <p class="small" role="status">{t.pick}</p>}
      <p class="small">{t.note}</p>
    </section>
  );
}

/** Steps 7 and 8 in the first release: nothing to add yet, only where to add it after Finish (#3953). */
export function LaterStep({ heading, later }: { heading: string; later: string }) {
  return (
    <section>
      <h2>{heading}</h2>
      <p>{later}</p>
    </section>
  );
}

/** Step 8's published homebrew pages (#4211): addresses on dndbeyond.com only, kept in the draft as a short list (the server checks the
 * address again where the page is read). Each needs the same right-to-use box as a file. */
function HomebrewLinks({ draft, onPatch, ticked, busy }: { draft: WizardDraft; onPatch: StepProps["onPatch"]; ticked: boolean; busy: boolean }) {
  const t = w.docs;
  const links = draft.answers.homebrew_links;
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState("");
  function add(): void {
    const address = typed.trim();
    let host = "";
    try {
      const url = new URL(address);
      host = url.protocol === "https:" ? url.hostname.toLowerCase() : "";
    } catch {
      host = "";
    }
    if (host !== "dndbeyond.com" && !host.endsWith(".dndbeyond.com")) return setProblem(t.homebrewLinkBad);
    if (links.length >= MAX_HOMEBREW_LINKS) return setProblem(t.homebrewLinkMany);
    setProblem("");
    setTyped("");
    if (!links.includes(address)) void onPatch((latest) => ({ homebrew_links: [...latest.homebrew_links, address] }));
  }
  return (
    <div class="stack">
      {links.length > 0 && (
        <ul>
          {links.map((l) => (
            <li key={l}>
              {l}{" "}
              <button type="button" class="link" onClick={() => void onPatch((latest) => ({ homebrew_links: latest.homebrew_links.filter((x) => x !== l) }))}>
                {t.remove}
              </button>
            </li>
          ))}
        </ul>
      )}
      <label>
        {t.homebrewLinkLabel}
        <input type="url" value={typed} disabled={!ticked} onInput={(e) => setTyped((e.target as HTMLInputElement).value)} />
      </label>
      <button type="button" class="button secondary" disabled={busy || !ticked || !typed.trim()} onClick={add}>
        {t.homebrewLinkAdd}
      </button>
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
const MAX_HOMEBREW_LINKS = 20; // the server's own limit
const LINK_MAX = 2000; // the server's limit for one address (#4206)
