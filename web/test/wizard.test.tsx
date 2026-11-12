/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The start-up wizard's pages (#3956, behind WIZARD_LIVE): start, one page for each step saved as it is chosen, Back and Next,
// Skip with PLAN's words (only the name is asked), checked documents with the right-to-use box first, the rules step, the finish
// wait with its stages, and the failures' words. Run against the wizard's pretend API (wizard-mock.ts).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Wizard from "../src/account/Wizard";
import { maxBytesFor, MAX_FILE_BYTES, MAX_PDF_BYTES, WizardError } from "../src/account/wizard-api";
import { problemWords } from "../src/account/wizard-problems";
import { mockWizard } from "../src/account/wizard-mock";
import { SUPPLEMENTS, wizardWords as w } from "../src/content/wizard-words";

// The first release leaves four things out behind small flags (#3953); each test turns on what it needs.
const flags = vi.hoisted(() => ({ sheets: false, house: false, own: false, drive: false, links: false }));
vi.mock("../src/content/wizard-switch", () => ({
  WIZARD_LIVE: true,
  get WIZARD_SHEETS_LIVE() {
    return flags.sheets;
  },
  get WIZARD_HOUSE_FILES_LIVE() {
    return flags.house;
  },
  get WIZARD_OWN_RULESET_LIVE() {
    return flags.own;
  },
  get WIZARD_DRIVE_LIVE() {
    return flags.drive;
  },
  get WIZARD_LINKS_LIVE() {
    return flags.links;
  },
}));

afterEach(() => {
  window.sessionStorage.clear();
  flags.sheets = flags.house = flags.own = flags.drive = flags.links = false;
  cleanup();
  vi.useRealTimers();
});

const G = "300000000000000001";
const start = async (api = mockWizard(), guild = G) => {
  render(<Wizard api={api} guild={guild} />);
  fireEvent.click(await screen.findByRole("button", { name: w.start }));
  await screen.findByText(w.step(1, 10));
  return api;
};
const next = async (to: number) => {
  fireEvent.click(screen.getByRole("button", { name: w.next }));
  await screen.findByText(w.step(to, 10));
};

describe("the wizard", () => {
  it("offers a start, then shows step 1 of 10", async () => {
    await start();
    expect(screen.getByRole("heading", { name: w.where.heading })).toBeTruthy();
  });

  it("a plan with no room says so at once, before any step", async () => {
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.full`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.noRoom);
  });

  it("picks up where the person was after a reload", async () => {
    const api = await start();
    await next(2);
    cleanup();
    render(<Wizard api={api} guild={G} />);
    expect(await screen.findByText(w.step(2, 10))).toBeTruthy();
  });

  it("Back goes to the page before, and is off on the first", async () => {
    await start();
    expect((screen.getByRole("button", { name: w.back }) as HTMLButtonElement).disabled).toBe(true);
    await next(2);
    fireEvent.click(screen.getByRole("button", { name: w.back }));
    await screen.findByText(w.step(1, 10));
  });

  it("step 1 says what D&D Beyond keeps behind a sign-in, and takes only a D&D Beyond invite link", async () => {
    const api = await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    expect(await screen.findByText(w.where.limit)).toBeTruthy();
    const box = screen.getByLabelText(w.where.inviteLabel) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "https://example.com/not-it" } });
    fireEvent.blur(box);
    expect((await screen.findByRole("alert")).textContent).toBe(w.where.inviteBad);
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/campaigns/join/1234567" } });
    fireEvent.blur(box);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("set:") && c.endsWith(":invite"))).toBe(true));
  });

  it("a public sheet link adds the character; a private one says to make it public", async () => {
    flags.sheets = true;
    await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    const box = await screen.findByLabelText(w.where.sheetLabel);
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/characters/1111111" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.sheetAdd }));
    expect(await screen.findByText(w.where.sheetFound("Brannoc Stonebelt"), undefined, { timeout: 3000 })).toBeTruthy();
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/characters/2222222/private" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.sheetAdd }));
    expect(await screen.findByText(w.where.sheetPrivate, undefined, { timeout: 3000 })).toBeTruthy();
  });

  it("pasted campaign text that holds no characters says to add them by hand", async () => {
    await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    fireEvent.input(await screen.findByLabelText(w.where.pasteLabel), { target: { value: "nothing useful" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.pasteRead }));
    expect(await screen.findByText(w.where.pasteNone)).toBeTruthy();
  });

  it("step 9 opens with neither option picked, Next waits for a pick and says why (#4212)", async () => {
    await start();
    for (let to = 2; to <= 9; to++) await next(to);
    const yes = screen.getByLabelText(w.data.yes) as HTMLInputElement;
    const no = screen.getByLabelText(w.data.no) as HTMLInputElement;
    expect([yes.checked, no.checked]).toEqual([false, false]);
    expect((screen.getByRole("button", { name: w.next }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(w.data.pick)).toBeTruthy();
    // yes comes first, then no (the order stays)
    expect(yes.compareDocumentPosition(no) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a pick of Yes saves yes, a pick of No saves no, and Next goes on (#4212)", async () => {
    const api = await start();
    for (let to = 2; to <= 9; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.data.yes));
    await waitFor(async () => expect((await api.get(G))?.answers.dataUse).toBe(true));
    expect(screen.queryByText(w.data.pick)).toBeNull();
    fireEvent.click(screen.getByLabelText(w.data.no));
    await waitFor(async () => expect((await api.get(G))?.answers.dataUse).toBe(false));
    expect((screen.getByRole("button", { name: w.next }) as HTMLButtonElement).disabled).toBe(false);
    await next(10);
    expect(screen.getByText(w.finish.summaryData("no"))).toBeTruthy();
  });

  it("Back and a reload show the earlier pick, and a No stays a No (#4212)", async () => {
    const api = await start();
    for (let to = 2; to <= 9; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.data.no));
    await waitFor(async () => expect((await api.get(G))?.answers.dataUse).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.back }));
    await screen.findByText(w.step(8, 10));
    await next(9);
    expect((screen.getByLabelText(w.data.no) as HTMLInputElement).checked).toBe(true);
    cleanup(); // (a reload: this tab still has its pick)
    render(<Wizard api={api} guild={G} />);
    await screen.findByText(w.step(9, 10));
    expect((screen.getByLabelText(w.data.no) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText(w.data.yes) as HTMLInputElement).checked).toBe(false);
  });

  it("with no pick the finish page would say not chosen (off) (#4212)", () => {
    expect(w.finish.summaryData("none")).toBe("Help improve DMbot: not chosen (off)");
  });

  it("keeps the pasted page text when the DM goes Next and then Back (#4204)", async () => {
    await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    fireEvent.input(await screen.findByLabelText(w.where.pasteLabel), { target: { value: "Brannoc Stonebelt\nSera Dunmoor" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.pasteRead }));
    await screen.findByText(w.where.pasteFound(2));
    await next(2);
    fireEvent.click(screen.getByRole("button", { name: w.back }));
    await screen.findByText(w.step(1, 10));
    expect((screen.getByLabelText(w.where.pasteLabel) as HTMLTextAreaElement).value).toBe("Brannoc Stonebelt\nSera Dunmoor");
  });

  it("after a reload the box is empty and one line says the text was used, with the count (#4204)", async () => {
    const api = await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    fireEvent.input(await screen.findByLabelText(w.where.pasteLabel), { target: { value: "Brannoc Stonebelt\nSera Dunmoor" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.pasteRead }));
    await screen.findByText(w.where.pasteFound(2));
    cleanup(); // (a reload: the page's memory is gone, the server's draft is not)
    render(<Wizard api={api} guild={G} />);
    await screen.findByText(w.step(1, 10)); // (the draft is there, so it opens on its step)
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    expect((await screen.findByLabelText(w.where.pasteLabel) as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByText(w.where.pastedEarlier(2))).toBeTruthy();
  });

  it("step 2 says the fields are about the character (#4204)", async () => {
    await start();
    await next(2);
    expect(screen.getByLabelText(new RegExp(w.confirm.fullLabel))).toBeTruthy();
    expect(screen.getByLabelText(new RegExp(w.confirm.partyLabel))).toBeTruthy();
    expect(screen.getByText(w.confirm.partyHint)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.confirm.add })).toBeTruthy();
    expect(w.confirm.fullLabel).toBe("Character's full name");
    expect(w.confirm.add).toBe("Add this character to the campaign");
    expect(screen.queryByLabelText(w.confirm.sheetLabel)).toBeNull(); // (the sheet field waits for its switch)
  });

  it("the sheet link sits in the character's row once its switch is on, and is kept with the character (#4204)", async () => {
    flags.sheets = true;
    const api = await start();
    await next(2);
    fireEvent.input(screen.getByLabelText(new RegExp(w.confirm.fullLabel)), { target: { value: "Tavin Ashgrove" } });
    fireEvent.input(screen.getByLabelText(w.confirm.sheetLabel), { target: { value: "https://www.dndbeyond.com/characters/3333333" } });
    fireEvent.click(screen.getByRole("button", { name: w.confirm.add }));
    await screen.findByText(/Tavin Ashgrove/);
    expect((await api.get(G))?.answers.characters[0]?.sheetUrl).toBe("https://www.dndbeyond.com/characters/3333333");
  });

  it("\"dndbeyond.com\" in the sheet label links to its site in a new tab, and the label still names the box (#4204)", async () => {
    flags.sheets = true;
    await start();
    await next(2);
    const box = screen.getByLabelText(w.confirm.sheetLabel) as HTMLInputElement;
    const link = box.closest("label")!.querySelector("a")!;
    expect(link.textContent).toBe("dndbeyond.com");
    expect(link.getAttribute("href")).toBe("https://www.dndbeyond.com");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("Skip uses PLAN's words, asks only for the name, then makes the campaign with the defaults", async () => {
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    expect(await screen.findByText(w.skipSays)).toBeTruthy();
    const yes = screen.getByRole("button", { name: w.skipYes }) as HTMLButtonElement;
    expect(yes.disabled).toBe(true); // (no name yet)
    fireEvent.input(screen.getByLabelText(w.skipName), { target: { value: "The Frostmaiden" } });
    fireEvent.click(yes);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("finish:"))).toBe(true));
    expect(api.calls.some((c) => c.startsWith("skip:"))).toBe(true);
  });

  it("a document needs the right-to-use box first, then is checked and counted", async () => {
    const api = await start();
    await next(2);
    await next(3);
    await next(4);
    const add = screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(screen.getByText(w.docs.rightsNeeded)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const file = new File(["the harbour town of Harrowgate and its bells"], "lore.txt", { type: "text/plain" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    expect(await screen.findByText("lore.txt")).toBeTruthy();
    expect(await screen.findByText(w.docs.counts(0, 8))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("docAdd:") && c.endsWith(":lore"))).toBe(true);
  });

  it("a file with pictures-only pages says how many, singular and plural, and an ordinary file says nothing", async () => {
    await start();
    await next(2);
    await next(3);
    await next(4);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const pictures = new File(["the harbour town of Harrowgate and its bells"], "lore-pictures.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [pictures] } });
    expect(await screen.findByText(w.docs.picturePages(3))).toBeTruthy();
    const one = new File(["the harbour town of Harrowgate and its bells"], "lore-onepicture.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [one] } });
    expect(await screen.findByText(w.docs.picturePages(1))).toBeTruthy();
    const plain = new File(["the harbour town of Harrowgate and its bells"], "lore-plain.txt", { type: "text/plain" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [plain] } });
    expect(await screen.findByText("lore-plain.txt")).toBeTruthy();
    expect(screen.getAllByText(/pictures only/)).toHaveLength(2);
  });

  it("the pretend server keeps the count off the stored draft, but answers it once", async () => {
    const api = await start();
    const doc = await api.docAdd(G, "lore", { name: "lore-pictures.pdf", source: "upload", text: "the harbour town and its bells" });
    expect(doc.picturePages).toBe(3);
    const again = await api.get(G);
    expect(again?.documents.find((d) => d.id === doc.id)?.picturePages).toBeUndefined();
  });

  it("a file that cannot be read shows no pictures-only note", async () => {
    await start();
    await next(2);
    await next(3);
    await next(4);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const scanned = new File(["   "], "scan-pictures.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [scanned] } });
    expect(await screen.findByText("scan-pictures.pdf")).toBeTruthy();
    expect(screen.queryByText(/pictures only/)).toBeNull();
  });

  it("a file over the server's limit is refused with the way out before anything is sent", async () => {
    const api = await start();
    for (let to = 2; to <= 4; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const big = new File(["x"], "huge.txt", { type: "text/plain" });
    Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [big] } });
    expect((await screen.findByRole("alert")).textContent).toBe(w.docs.unreadable["too_big"]);
    expect(api.calls.some((c) => c.startsWith("docAdd:"))).toBe(false);
  });

  it("maxBytesFor gives 50 MB for a PDF in any case and 10 MB for other names", () => {
    expect(maxBytesFor("adventure.pdf")).toBe(50 * 1024 * 1024);
    expect(maxBytesFor("ADVENTURE.PDF")).toBe(MAX_PDF_BYTES);
    expect(maxBytesFor("notes.docx")).toBe(MAX_FILE_BYTES);
    expect(maxBytesFor("lore.txt")).toBe(10 * 1024 * 1024);
    expect(maxBytesFor("pdf.docx")).toBe(MAX_FILE_BYTES);
  });

  it("a 20 MB pdf is not refused by the page and a 20 MB docx is", async () => {
    const api = await start();
    for (let to = 2; to <= 4; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(["the harbour town of Harrowgate"], "adventure.pdf", { type: "application/pdf" });
    Object.defineProperty(pdf, "size", { value: 20 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [pdf] } });
    expect(await screen.findByText("adventure.pdf")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("docAdd:"))).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
    const docx = new File(["x"], "adventure.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    Object.defineProperty(docx, "size", { value: 20 * 1024 * 1024 });
    const before = api.calls.filter((c) => c.startsWith("docAdd:")).length;
    fireEvent.change(input, { target: { files: [docx] } });
    expect((await screen.findByRole("alert")).textContent).toBe(w.docs.unreadable["too_big"]);
    expect(api.calls.filter((c) => c.startsWith("docAdd:")).length).toBe(before);
    const huge = new File(["x"], "bigger.pdf", { type: "application/pdf" });
    Object.defineProperty(huge, "size", { value: 51 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [huge] } });
    expect((await screen.findByRole("alert")).textContent).toBe(w.docs.unreadable["too_big"]);
    expect(api.calls.filter((c) => c.startsWith("docAdd:")).length).toBe(before);
  });

  it("the too-big words name the real limits and no Google Drive", () => {
    const words = w.docs.unreadable["too_big"]!;
    expect(words).toBe("This file is too big. A PDF can be up to 50 MB and other files up to 10 MB. For a bigger PDF, add it in Discord.");
    expect(words).toContain("50 MB");
    expect(words).toContain("10 MB");
    expect(words).toContain("Discord");
    expect(words).not.toContain("Drive");
  });

  it("doc_too_big shows its own text words, not the file-size ones", () => {
    const text = problemWords(new WizardError("doc_too_big"));
    expect(text).toBe(w.docs.unreadable["doc_too_big"]);
    expect(text).not.toBe(w.docs.unreadable["too_big"]);
    expect(text).not.toContain("MB");
  });

  it("a link is not offered in the first release", async () => {
    await start();
    for (let to = 2; to <= 4; to++) await next(to);
    expect(screen.queryByLabelText(w.docs.linkLabel)).toBeNull();
  });

  async function onRules() {
    const api = await start();
    for (let to = 2; to <= 6; to++) await next(to);
    return api;
  }
  const primary = () => screen.getByLabelText(w.rules.primaryLabel) as HTMLSelectElement;
  const secondary = () => screen.getByLabelText(w.rules.secondaryLabel) as HTMLSelectElement;
  const optionsOf = (select: HTMLSelectElement) => [...select.options].map((o) => o.textContent);

  it("the rules step has a required primary and an optional secondary dropdown, with the defaults (#4210)", async () => {
    await onRules();
    expect(primary().value).toBe("2024");
    expect(secondary().value).toBe("2014");
    expect(optionsOf(primary())).toEqual([w.rules.current, w.rules.legacy]);
    expect(screen.getByText(w.rules.secondaryHint)).toBeTruthy();
    expect(screen.getByText(w.rules.houseNote)).toBeTruthy(); // (house rules come in the next steps)
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThanOrEqual(7); // (the books stay below)
  });

  it("the secondary never offers the primary's choice, and follows when the primary changes (#4210)", async () => {
    await onRules();
    expect(optionsOf(secondary())).toEqual([w.rules.none, w.rules.legacy]);
    fireEvent.change(primary(), { target: { value: "2014" } });
    await waitFor(() => expect(optionsOf(secondary())).toEqual([w.rules.none, w.rules.current]));
    expect(secondary().value).toBe("2024"); // (the repeat turned into the other set)
  });

  it("None saves no fallback (#4210)", async () => {
    const api = await onRules();
    fireEvent.change(secondary(), { target: { value: "none" } });
    await waitFor(async () => expect((await api.get(G))?.answers.fallback).toBe("none"));
    expect(secondary().value).toBe("none");
    fireEvent.change(primary(), { target: { value: "2014" } });
    await waitFor(async () => expect((await api.get(G))?.answers.target).toBe("2014"));
    expect((await api.get(G))?.answers.fallback).toBe("none"); // (no clash, so none stays)
  });

  it("with the switch off there is no upload option (#4210)", async () => {
    await onRules();
    expect(screen.queryByText(w.rules.uploadOwn)).toBeNull();
    expect(screen.queryByText(w.rules.ownLabel)).toBeNull();
  });

  it("with the switch on, Upload your own opens the box under its dropdown, and only one dropdown can hold it (#4210)", async () => {
    flags.own = true;
    await onRules();
    expect(optionsOf(primary())).toEqual([w.rules.current, w.rules.legacy, w.rules.uploadOwn]);
    expect(optionsOf(secondary())).toEqual([w.rules.none, w.rules.legacy, w.rules.uploadOwn]);
    fireEvent.change(secondary(), { target: { value: "own" } });
    expect(await screen.findByRole("heading", { name: w.rules.ownLabel })).toBeTruthy();
    expect(optionsOf(primary())).not.toContain(w.rules.uploadOwn); // (one own set for the campaign)
    fireEvent.change(secondary(), { target: { value: "none" } });
    await waitFor(() => expect(screen.queryByRole("heading", { name: w.rules.ownLabel })).toBeNull());
    fireEvent.change(primary(), { target: { value: "own" } });
    expect(await screen.findByRole("heading", { name: w.rules.ownLabel })).toBeTruthy();
    expect(optionsOf(secondary())).not.toContain(w.rules.uploadOwn);
  });

  it("the last page makes the campaign and shows the stages, then links to it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "The Frostmaiden" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("finish:"))).toBe(true));
    for (let i = 0; i < 30 && !screen.queryByText(w.finish.done); i++) {
      await vi.advanceTimersByTimeAsync(2100);
    }
    expect(screen.getByText(w.finish.done)).toBeTruthy();
    // (the campaign's page reads its ref from the path, not from the hash: #4216)
    expect(screen.getByRole("link", { name: w.finish.open }).getAttribute("href")).toBe(`/account/campaigns/${G}.cmp-new`);
  });

  it("a finish that fails says what happened and offers another try", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.fail`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    fireEvent.click(await screen.findByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    for (let i = 0; i < 10 && !screen.queryByText(w.finish.retry); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getAllByText(w.finish.errors["failed"]!).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: w.finish.retry })).toBeTruthy();
  });

  describe("the finish page's motion and how the DM is told (#4213)", () => {
    async function running() {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const api = await start();
      fireEvent.click(screen.getByRole("button", { name: w.skip }));
      fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "The Frostmaiden" } });
      fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
      await waitFor(() => expect(api.calls.some((c) => c.startsWith("finish:"))).toBe(true));
      return api;
    }

    it("says from the start how the DM is told, and what to do if private messages are closed", async () => {
      await running();
      expect(await screen.findByText(w.finish.told)).toBeTruthy();
      expect(screen.getByText(w.finish.toldClosed)).toBeTruthy();
      expect(w.finish.told).toContain("private message in Discord");
      expect(w.finish.told).toContain("No email is sent.");
    });

    it("the clock moves every half second", async () => {
      await running();
      await screen.findByText(w.finish.told);
      const before = screen.getByText(/^Running for/).textContent;
      await vi.advanceTimersByTimeAsync(3000);
      expect(screen.getByText(/^Running for/).textContent).not.toBe(before);
      expect(screen.getByText(/Last progress/)).toBeTruthy();
    });

    it("looks far less often while the tab is hidden", async () => {
      const api = await running();
      await screen.findByText(w.finish.told);
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      try {
        await vi.advanceTimersByTimeAsync(2000); // let a look in flight settle
        const seen = api.calls.filter((c) => c.startsWith("finishState")).length;
        await vi.advanceTimersByTimeAsync(6000);
        expect(api.calls.filter((c) => c.startsWith("finishState")).length - seen).toBeLessThanOrEqual(1);
      } finally {
        Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      }
    });

    it("a person who asked for less motion gets words, not a moving bar", async () => {
      const real = window.matchMedia;
      window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as typeof window.matchMedia;
      try {
        await running();
        await screen.findByText(w.finish.told);
        expect(document.querySelector("progress[aria-label='Working…']")).toBeNull();
        expect(screen.getAllByText(w.finish.working).length).toBeGreaterThan(0);
      } finally {
        window.matchMedia = real;
      }
    });
  });

  it("throwing the setup away asks once, then starts fresh", async () => {
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.discard }));
    expect(await screen.findByText(w.discardAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.discardYes }));
    await screen.findByRole("button", { name: w.start });
    expect(api.calls.some((c) => c.startsWith("discard:"))).toBe(true);
  });

  it("pressing Next twice moves one page, not two", async () => {
    const api = await start();
    const button = screen.getByRole("button", { name: w.next });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(w.step(2, 10));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(w.step(3, 10))).toBeNull();
    expect(api.calls.filter((c) => c.startsWith("set:")).length).toBe(1);
  });

  it("a rights box that could not be saved says so and stays unticked", async () => {
    const api = await start();
    for (let to = 2; to <= 4; to++) await next(to);
    api.rights = async () => Promise.reject(new Error("down"));
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect((screen.getByLabelText(w.docs.rights) as HTMLInputElement).checked).toBe(false);
  });

  it("a link must be https", async () => {
    flags.links = true;
    const api = await start();
    for (let to = 2; to <= 4; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.input(screen.getByLabelText(w.docs.linkLabel), { target: { value: "http://example.com/x" } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.addLink }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.docs.linkBad);
    expect(api.calls.some((c) => c.startsWith("docAdd:"))).toBe(false);
  });

  it("a link longer than the server keeps is refused at the step, and homebrew has its own dndbeyond.com box instead (#4206)", async () => {
    flags.links = true;
    flags.house = true;
    const api = await start();
    for (let to = 2; to <= 4; to++) await next(to);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.input(screen.getByLabelText(w.docs.linkLabel), { target: { value: `https://example.com/${"a".repeat(2000)}` } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.addLink }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.docs.linkTooLong);
    expect(api.calls.some((c) => c.startsWith("docAdd:"))).toBe(false);
    for (let to = 5; to <= 8; to++) await next(to);
    expect(screen.queryByLabelText(w.docs.linkLabel)).toBeNull(); // (step 8: the dndbeyond.com box only)
    expect(screen.getByLabelText(w.docs.homebrewLinkLabel)).toBeTruthy();
  });

  it("one failed look at the finish is tried again, and the wait goes on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockWizard();
    const real = api.finishState.bind(api);
    let looks = 0;
    api.finishState = async (g) => {
      looks += 1;
      if (looks === 2) throw new Error("blip"); // (the first look is the page's, the second is the first poll)
      return real(g);
    };
    render(<Wizard api={api} guild={G} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    fireEvent.click(await screen.findByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    for (let i = 0; i < 30 && !screen.queryByText(w.finish.done); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getByText(w.finish.done)).toBeTruthy();
  });

  it("a campaign made but the lore waiting for a key links to the campaign and offers no retry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.nokey`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    fireEvent.click(await screen.findByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    for (let i = 0; i < 20 && !screen.queryByRole("link", { name: w.finish.open }); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getAllByText(w.finish.errors["no-key"]!).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: w.finish.retry })).toBeNull();
    expect(screen.getByRole("link", { name: w.finish.open }).getAttribute("href")).toBe(`/account/campaigns/${encodeURIComponent(`${G}.nokey.cmp-new`)}`);
    expect(screen.getByRole("link", { name: w.finish.open }).getAttribute("href")).not.toContain("#");
  });

  it("offers exactly the supplement codes core accepts, and the pretend API refuses any other", async () => {
    // (core's wizard.py list: a code not on it is refused at the door)
    expect(SUPPLEMENTS.map((s) => s.code).sort()).toEqual(["bigby", "fizban", "mordenkainen", "ravnica", "tasha", "theros", "xanathar"]);
    const api = mockWizard();
    await api.start(G, "");
    await expect(api.set(G, 6, { supplements: ["tasha", "nope"] })).rejects.toMatchObject({ kind: "bad_value", key: "supplements" });
    await expect(api.set(G, 6, { supplements: ["tasha", "bigby"] })).resolves.toBeTruthy();
  });

  it("the first release hides the sheet-link field and the Google Drive choice", async () => {
    await start();
    expect(screen.queryByLabelText(w.where.drive)).toBeNull();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    await screen.findByText(w.where.limit);
    expect(screen.queryByLabelText(w.where.sheetLabel)).toBeNull();
    expect(screen.getByLabelText(w.where.pasteLabel)).toBeTruthy();
  });

  it("the Google Drive choice and the sheet-link field come back with their flags", async () => {
    flags.drive = flags.sheets = true;
    await start();
    expect(screen.getByLabelText(w.where.drive)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    expect(await screen.findByLabelText(w.where.sheetLabel)).toBeTruthy();
  });

  it("steps 7 and 8 offer no upload or link, and say where to add them after Finish", async () => {
    await start();
    for (let to = 2; to <= 7; to++) await next(to);
    expect(screen.getByText(w.house.later)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.docs.add })).toBeNull();
    expect(screen.queryByLabelText(w.docs.linkLabel)).toBeNull();
    await next(8);
    expect(screen.getByText(w.homebrew.later)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.docs.add })).toBeNull();
    await next(9); // (Next and Skip still work)
  });

  it("steps 7 and 8 take files again with their flag", async () => {
    flags.house = true;
    await start();
    for (let to = 2; to <= 7; to++) await next(to);
    expect(screen.getByRole("button", { name: w.docs.add })).toBeTruthy();
  });

  it("step 7 takes house rules typed in, one document, after the right-to-use box is ticked (#4211)", async () => {
    flags.house = true;
    const api = await start();
    for (let to = 2; to <= 7; to++) await next(to);
    const box = screen.getByLabelText(w.docs.typedLabel) as HTMLTextAreaElement;
    const add = screen.getByRole("button", { name: w.docs.typedAdd }) as HTMLButtonElement;
    expect([box.disabled, add.disabled]).toEqual([true, true]); // (nothing is added before the right-to-use box)
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByLabelText(w.docs.typedLabel) as HTMLTextAreaElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.docs.typedAdd }));
    expect(await screen.findByText(w.docs.typedEmpty)).toBeTruthy(); // (an empty box adds nothing)
    expect(api.calls.some((c) => c.startsWith("docAdd:"))).toBe(false);
    fireEvent.input(screen.getByLabelText(w.docs.typedLabel), { target: { value: "1. Flanking gives advantage.\n2. Potions take a bonus action." } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.typedAdd }));
    expect(await screen.findByText(w.docs.typedName)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("docAdd:")).length).toBe(1);
    expect((screen.getByLabelText(w.docs.typedLabel) as HTMLTextAreaElement).value).toBe(""); // (the box empties once it is added)
  });

  it("step 8 takes dndbeyond.com links only, after the right-to-use box, and keeps them in the draft (#4211)", async () => {
    flags.house = true;
    const api = await start();
    for (let to = 2; to <= 8; to++) await next(to);
    const box = () => screen.getByLabelText(w.docs.homebrewLinkLabel) as HTMLInputElement;
    expect(box().disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect(box().disabled).toBe(false));
    fireEvent.input(box(), { target: { value: "https://example.com/homebrew/1" } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.homebrewLinkAdd }));
    expect(await screen.findByText(w.docs.homebrewLinkBad)).toBeTruthy();
    fireEvent.input(box(), { target: { value: "http://www.dndbeyond.com/homebrew/1" } }); // (not https)
    fireEvent.click(screen.getByRole("button", { name: w.docs.homebrewLinkAdd }));
    expect(await screen.findByText(w.docs.homebrewLinkBad)).toBeTruthy();
    const good = "https://www.dndbeyond.com/homebrew/creations/1234-a-test";
    fireEvent.input(box(), { target: { value: good } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.homebrewLinkAdd }));
    expect(await screen.findByText(good)).toBeTruthy();
    await waitFor(async () => expect((await api.get(G))?.answers.homebrew_links).toEqual([good]));
    fireEvent.click(screen.getByRole("button", { name: w.docs.remove }));
    await waitFor(async () => expect((await api.get(G))?.answers.homebrew_links).toEqual([]));
  });

  it("the finish page says, in plain counts, what could not be used when the campaign was made anyway (#4211)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.notes`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    fireEvent.click(await screen.findByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    for (let i = 0; i < 30 && !screen.queryByText(w.finish.done); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getByText(w.finish.notes["house_unreadable"]!(2))).toBeTruthy();
    expect(screen.getByText(w.finish.notes["homebrew_link_failed"]!(1))).toBeTruthy();
    expect(screen.queryByText(/homebrew_/)).toBeNull(); // (words and counts, never the raw note)
  });

  it("the typed box is only on the house rules step, not homebrew (#4211)", async () => {
    flags.house = true;
    await start();
    for (let to = 2; to <= 8; to++) await next(to);
    expect(screen.queryByLabelText(w.docs.typedLabel)).toBeNull();
  });

  it("step 6 hides the person's own ruleset upload in the first release", async () => {
    await start();
    for (let to = 2; to <= 6; to++) await next(to);
    expect(screen.queryByLabelText(w.rules.ownRights)).toBeNull();
    expect(screen.getAllByRole("checkbox").length).toBe(7);
  });

  it("step 6 shows the two books that are on by default ticked and sends nothing until a box changes", async () => {
    const api = await start();
    for (let to = 2; to <= 6; to++) await next(to);
    const box = (title: string) => screen.getByLabelText(new RegExp(title)) as HTMLInputElement;
    expect(box("Xanathar").checked).toBe(true);
    expect(box("Tasha").checked).toBe(true);
    expect(box("Fizban").checked).toBe(false);
    expect(api.calls.some((c) => c.endsWith(":supplements"))).toBe(false);
    fireEvent.click(box("Xanathar"));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith(":supplements"))).toBe(true));
    await waitFor(() => expect(box("Xanathar").checked).toBe(false));
    expect(box("Tasha").checked).toBe(true); // (turning one off did not turn the other off)
  });

  it("step 6 lists the books in alphabetical order by title", async () => {
    await start();
    for (let to = 2; to <= 6; to++) await next(to);
    const titles = SUPPLEMENTS.map((s) => s.title);
    const sorted = [
      "Bigby Presents: Glory of the Giants (2023)",
      "Fizban's Treasury of Dragons (2021)",
      "Guildmasters' Guide to Ravnica (2018)",
      "Mordenkainen Presents: Monsters of the Multiverse (2022)",
      "Mythic Odysseys of Theros (2020)",
      "Tasha's Cauldron of Everything (2020)",
      "Xanathar's Guide to Everything (2017)",
    ];
    expect(titles).toEqual([...titles].sort((a, b) => a.localeCompare(b))); // the data is in order, so a future book lands in place
    // What the person sees: each book's checkbox label, top to bottom, against the literal sorted list.
    const onScreen = [...document.querySelectorAll("label")]
      .map((label) => label.textContent ?? "")
      .map((text) => sorted.find((title) => text.includes(title)))
      .filter((title): title is string => title !== undefined);
    expect(onScreen).toEqual(sorted);
  });

  it("a saved answer for the books is shown as saved", async () => {
    const api = await start();
    await api.set(G, 1, { supplements: ["fizban"] });
    cleanup();
    render(<Wizard api={api} guild={G} />);
    await screen.findByText(w.step(1, 10));
    for (let to = 2; to <= 6; to++) await next(to);
    expect((screen.getByLabelText(/Fizban/) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText(/Tasha/) as HTMLInputElement).checked).toBe(false);
  });
});
