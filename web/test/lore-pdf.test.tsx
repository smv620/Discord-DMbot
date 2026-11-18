/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Upload lore, the PDF way (#4762), with its switch on: send, the count (no AI), the cost in words, the right-to-use box, the
// DM's yes and "Never mind". The file's words are never on the page.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/content/lore-pdf-switch", () => ({ LORE_PDF_LIVE: true }));

import { lorePage as words } from "../src/content/lore-words";
import Lore from "../src/account/Lore";
import { mockApi } from "../src/account/mock";

const PATH = "/account/lore/100.cmp1";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const pdf = (name = "adventure.pdf", size = 5): File => new File([new Uint8Array(size)], name, { type: "application/pdf" });

async function choosePdf(file = pdf()) {
  fireEvent.click(screen.getByLabelText(words.sources.pdf));
  fireEvent.change(await screen.findByLabelText(words.pdf.label), { target: { files: [file] } });
}

describe("Upload lore, a PDF", () => {
  it("offers the PDF way, sends the file, counts it and says what it will cost before anything is read", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await choosePdf();
    expect((await screen.findByText(words.pdf.counted(12, 5, 0.4)))).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("lorePdf")).slice(0, 3)).toEqual(["lorePdf:100.cmp1", "lorePdfSend:100.cmp1:5", "lorePdfCount:100.cmp1"]);
    expect(api.calls.some((c) => c.startsWith("lorePdfGo:") || c.startsWith("loreAdd:"))).toBe(false); // (nothing is read yet)
  });

  it("asks for the right to use it before the yes, then starts the read with the choices and shows the wait", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await choosePdf();
    await screen.findByText(words.pdf.counted(12, 5, 0.4));
    fireEvent.click(screen.getByRole("button", { name: words.pdf.read }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.pdf.rightsNeeded);
    expect(api.calls.some((c) => c.startsWith("lorePdfGo:"))).toBe(false);
    fireEvent.click(screen.getByLabelText(words.known.spoilers.label, { exact: false }));
    fireEvent.click(screen.getByLabelText(words.pdf.rights));
    fireEvent.click(screen.getByRole("button", { name: words.pdf.read }));
    await waitFor(() => expect(api.calls).toContain("lorePdfGo:100.cmp1:spoilers:-:rights"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(words.stages.waiting));
  });

  it("says the PDF way's own words for the right-to-use box, not the text one", async () => {
    render(<Lore api={mockApi("table")} path={PATH} />);
    fireEvent.click(screen.getByLabelText(words.sources.pdf));
    expect(screen.queryByLabelText(words.rights)).toBeNull();
    expect(screen.getByLabelText(words.pdf.rights)).toBeTruthy();
  });

  it("drops the file on Never mind", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await choosePdf();
    await screen.findByText(words.pdf.counted(12, 5, 0.4));
    fireEvent.click(screen.getByRole("button", { name: words.pdf.never }));
    await waitFor(() => expect(api.calls).toContain("lorePdfDrop:100.cmp1"));
    expect(screen.queryByRole("button", { name: words.pdf.read })).toBeNull();
  });

  it("says why a file is refused, in plain words", async () => {
    render(<Lore api={mockApi("table")} path={PATH} />);
    await choosePdf(pdf("bad.pdf"));
    expect((await screen.findByRole("alert")).textContent).toBe(words.pdf.problems["bad-file"]);
  });

  it("refuses a PDF over the cap before sending it", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    const big = pdf("big.pdf", 1);
    Object.defineProperty(big, "size", { value: 51 * 1024 * 1024 });
    await choosePdf(big);
    expect((await screen.findByRole("alert")).textContent).toBe(words.pdf.problems["bad-file"]);
    expect(api.calls.some((c) => c.startsWith("lorePdfSend:"))).toBe(false);
  });
});
