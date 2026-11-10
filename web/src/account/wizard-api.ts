// The start-up wizard's API (#3956; the doors are in docs/WIZARD_DESIGN.md, section 3). In a file of its own, with its own
// pretend API (wizard-mock.ts), so the wizard page carries no weight on the other account pages. Every call names the server
// (`guild`, its Discord id as text); nothing here holds a document's words: only names, counts and choices come back.

export type WizardSlot = "lore" | "secrets" | "ruleset" | "house" | "homebrew";

export interface WizardCharacter {
  /** The character's full name, and the name the party uses for it. */
  full: string;
  partyName: string;
  /** The public sheet link, or "" when none was given. */
  sheetUrl: string;
  /** "pending", or the Discord id (as text) of the player. */
  player: string;
}

export interface WizardDoc {
  id: string;
  slot: WizardSlot;
  name: string;
  source: "upload" | "link" | "drive";
  pages: number;
  words: number;
  status: "ok" | "unreadable";
  /** Why a file could not be read: empty, scanned, locked, too_big or not_text. */
  reason: string;
  /** Pages that hold only pictures, from this upload's own answer (#4030). The server does not keep it: a re-read draft has none. */
  picturePages?: number;
}

export interface WizardAnswers {
  name: string;
  invite: string;
  source: "" | "dndbeyond" | "file" | "drive";
  pageTextUsed: boolean;
  characters: WizardCharacter[];
  /** The rules to play with and the fallback: "2024" or "2014". */
  target: "2024" | "2014";
  fallback: "2024" | "2014";
  /** The books the person ticked; null until they change a box, so an untouched step keeps the defaults (Xanathar's and Tasha's rules are on). */
  supplements: string[] | null;
  dataUse: boolean;
  skipped: number[];
}

export interface WizardDraft {
  /** The step the person is on, 1 to 10. */
  step: number;
  answers: WizardAnswers;
  documents: WizardDoc[];
  /** Whether the right-to-use box was ticked for shared material and for an uploaded ruleset. */
  rights: { shared: boolean; ruleset: boolean };
  /** Unix seconds, and the server's own clock now. */
  expiresAt: number;
  serverNow: number;
}

export type WizardFinishStage = "campaign" | "party" | "rules" | "house_homebrew" | "data_use" | "lore" | "secrets" | "sure" | "clean";

export interface WizardFinish {
  state: "waiting" | "working" | "done" | "failed";
  stage: WizardFinishStage | "";
  part: number;
  ofParts: number;
  /** One of the finish run's words: plan, not-allowed, gone, no-key, failed or lost; empty when none. */
  error: string;
  /** Set once the campaign exists, so the page can link to it from then on. */
  campaignRef: string;
  createdAt: number;
  movedAt: number;
  serverNow: number;
}

export interface WizardSheetRead {
  state: "waiting" | "done" | "failed";
  /** The character's name once read; the failure word otherwise (private, not_found or slow). */
  name: string;
  error: string;
}

export interface WizardPageText {
  name: string;
  characters: { full: string; partyName: string }[];
}

/** The refusals the pages word: no_room, too_fast, not_allowed, expired, bad_value, too_big, rights_needed. */
export class WizardError extends Error {
  constructor(public kind: string, public key = "") {
    super(kind);
  }
}

export interface WizardApi {
  /** The draft, or null when there is none. */
  get(guild: string): Promise<WizardDraft | null>;
  /** Make the draft (or give back the existing one). Refuses with no_room when the plan has no room. */
  start(guild: string, name: string): Promise<WizardDraft>;
  /** Merge answers into the draft and move to `step`. Refuses with bad_value (and the key) for a wrong value. */
  set(guild: string, step: number, patch: Partial<WizardAnswers>): Promise<WizardDraft>;
  skip(guild: string, step: number): Promise<WizardDraft>;
  /** Read one public sheet link: queued for the bot; `sheetState` follows it. */
  sheet(guild: string, url: string): Promise<string>;
  sheetState(guild: string, id: string): Promise<WizardSheetRead>;
  /** The optional pasted campaign page: the name and characters found by the plain reader. The text is not kept. */
  pageText(guild: string, text: string): Promise<WizardPageText>;
  /** Add one checked document: a file's bytes are checked on the server (no AI), a link or Drive file is kept as it is. */
  docAdd(guild: string, slot: WizardSlot, input: { name: string; source: "upload" | "link" | "drive"; text?: string; link?: string; data?: string }): Promise<WizardDoc>;
  docRemove(guild: string, id: string): Promise<void>;
  rights(guild: string, kind: "shared" | "ruleset"): Promise<void>;
  finish(guild: string): Promise<string>;
  finishState(guild: string): Promise<WizardFinish | null>;
  discard(guild: string): Promise<void>;
}

/** The largest file the server reads, in bytes (core name_documents.MAX_DOCUMENT_BYTES); a bigger one is refused before sending. */
export const MAX_FILE_BYTES = 10 * 1024 * 1024;

/** The largest PDF the page sends, in bytes: the website takes less than Discord (100 MB), since a file goes as base64 in one request. Must match core MAX_WEB_PDF_BYTES (core/src/dmbot/web/wizard_check.py). */
export const MAX_PDF_BYTES = 50 * 1024 * 1024;

/** The size limit for a file by its name: a PDF (any case) gets MAX_PDF_BYTES, every other type MAX_FILE_BYTES. */
export function maxBytesFor(name: string): number {
  return name.toLowerCase().endsWith(".pdf") ? MAX_PDF_BYTES : MAX_FILE_BYTES;
}

/** Bytes as base64, in pieces so a big file does not overflow the call stack. */
export function toBase64(bytes: Uint8Array): string {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

/** A new draft's answers: nothing chosen yet (the books are `null` until a box changes, so the defaults hold). */
export function emptyAnswers(): WizardAnswers {
  return { name: "", invite: "", source: "", pageTextUsed: false, characters: [], target: "2024", fallback: "2014", supplements: null, dataUse: false, skipped: [] };
}
