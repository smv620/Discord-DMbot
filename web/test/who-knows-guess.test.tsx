/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Who knows this?" marks a character DMbot only guessed knows the connection (#3940, page half of #4970): the state line
// and the picker say so, and while a guess is ticked the save says it keeps the guesses left ticked as the DM's own. A
// connection with no guess looks as it did before. The DM's view only: the API sends `guess` to the DM alone.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, WebLink, WebLinkChange, WebViewer } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { WhoKnows } from "../src/account/WebLinePanel";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const REF = "100.cmp1";
const noAct = async () => null;
function link(knownBy: NonNullable<WebLink["knownBy"]>): WebLink {
  return { id: "l1", words: "trusts", secret: true, direction: "a-b", who: "some", knownBy } as WebLink;
}
const guessed = link([
  { id: "pc-cerric", name: "Cerric", guess: "sure" },
  { id: "pc-mira", name: "Mira", guess: "likely" },
]);
function render_(l: WebLink, api: AccountApi = mockApi("table")) {
  render(<WhoKnows api={api} refId={REF} link={l} act={noAct} />);
}

describe("a guessed holder", () => {
  it("the state line marks a guess as sure or likely", () => {
    render_(guessed);
    expect(screen.getByText(/Cerric \(DMbot's guess, sure\)/)).toBeTruthy();
    expect(screen.getByText(/Mira \(DMbot's guess, likely\)/)).toBeTruthy();
  });

  it("the picker shows the mark on a guessed holder and none on the others", async () => {
    render_(guessed);
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /^Cerric/ })).toBeTruthy());
    const cerric = screen.getByRole("checkbox", { name: /^Cerric/ }).closest("label")!;
    expect(cerric.textContent).toContain(w.guessMark("sure"));
    const mira = screen.getByRole("checkbox", { name: /^Mira/ }).closest("label")!;
    expect(mira.textContent).toContain(w.guessMark("likely"));
    const guard = screen.getByRole("checkbox", { name: "Guard 1" }).closest("label")!;
    expect(guard.textContent).not.toContain("DMbot's guess");
  });

  it("the keep-as-yours line shows while a guess is ticked and hides when the last one is unticked", async () => {
    render_(guessed);
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /^Cerric/ })).toBeTruthy());
    expect(screen.getByText(w.whoKeepsGuesses)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /^Cerric/ }));
    expect(screen.getByText(w.whoKeepsGuesses)).toBeTruthy(); // Mira's guess is still ticked
    fireEvent.click(screen.getByRole("checkbox", { name: /^Mira/ }));
    expect(screen.queryByText(w.whoKeepsGuesses)).toBeNull();
  });
});

describe("a connection with no guess", () => {
  it("looks exactly as before: no mark, and no keep-as-yours line", async () => {
    render_(link([{ id: "pc-cerric", name: "Cerric" }]));
    expect(screen.getByText(/Only Cerric know it\./)).toBeTruthy();
    expect(screen.queryByText(/DMbot's guess/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /^Cerric/ })).toBeTruthy());
    expect(screen.queryByText(/DMbot's guess/)).toBeNull();
    expect(screen.queryByText(w.whoKeepsGuesses)).toBeNull();
  });
});

describe("the shown list sent with a save (#4968 ruling, core #4976)", () => {
  const viewer = (id: string, name = id): WebViewer => ({ id, name, kind: "npc", group: "other" });
  function picker(holders: NonNullable<WebLink["knownBy"]>, pages: (q: string) => WebViewer[], who: WebLink["who"] = "some") {
    const api = mockApi("table");
    api.webViewers = async (_ref, q) => {
      const viewers = pages(q);
      return { viewers, total: viewers.length };
    };
    const sent: WebLinkChange[] = [];
    const act = async (change: WebLinkChange) => {
      sent.push(change);
      return null;
    };
    render(<WhoKnows api={api} refId={REF} link={{ ...link(holders), who }} act={act} />);
    return { sent };
  }

  it("save sends every id the picker showed across searches, once each, at most 50", async () => {
    let calls = 0;
    const { sent } = picker([{ id: "pc-cerric", name: "Cerric" }], () => {
      calls++;
      if (calls === 1) return [viewer("pc-cerric", "Cerric"), viewer("pc-mira", "Mira")];
      return [viewer("npc-tavin", "Tavin"), viewer("pc-mira", "Mira")];
    });
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /^Mira/ })).toBeTruthy());
    fireEvent.input(screen.getByRole("searchbox"), { target: { value: "Tav" } });
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /^Tavin/ })).toBeTruthy());
    fireEvent.click(screen.getByRole("checkbox", { name: /^Tavin/ }));
    fireEvent.click(screen.getByRole("button", { name: w.whoSave }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const change = sent[0] as Extract<WebLinkChange, { action: "who" }>;
    expect(change.shown?.sort()).toEqual(["npc-tavin", "pc-cerric", "pc-mira"]);
    expect(change.shown).toHaveLength(3);
  });

  it("the shown list holds the 50 most recently shown when more were shown", async () => {
    let calls = 0;
    const { sent } = picker([{ id: "pc-cerric", name: "Cerric" }], () => {
      calls++;
      const from = calls === 1 ? 1 : 31;
      return Array.from({ length: 30 }, (_, i) => viewer(`v${from + i}`));
    });
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "v1" })).toBeTruthy());
    fireEvent.input(screen.getByRole("searchbox"), { target: { value: "x" } });
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "v60" })).toBeTruthy());
    fireEvent.click(screen.getByRole("checkbox", { name: "v60" }));
    fireEvent.click(screen.getByRole("button", { name: w.whoSave }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const change = sent[0] as Extract<WebLinkChange, { action: "who" }>;
    expect(change.shown).toHaveLength(50);
    expect(change.shown).toContain("v60");
    expect(change.shown).not.toContain("v1");
  });

  it("a guessed holder never shown isn't in shown", async () => {
    const { sent } = picker([{ id: "npc-ghost", name: "Ghost", guess: "likely" }, { id: "pc-cerric", name: "Cerric" }], () => [viewer("pc-cerric", "Cerric")]);
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    // Ghost is ticked but never drawn; Cerric is drawn and unticked, so the save goes ahead without Cerric.
    fireEvent.click(await screen.findByRole("checkbox", { name: /^Cerric/ }));
    fireEvent.click(screen.getByRole("button", { name: w.whoSave }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const change = sent[0] as Extract<WebLinkChange, { action: "who" }>;
    expect(change.characters).toEqual(["npc-ghost"]);
    expect(change.shown).toEqual(["pc-cerric"]);
    expect(change.shown).not.toContain("npc-ghost");
  });

  it("save sends no shown when the list showed nobody", async () => {
    // The connection is "dm" today, so saving the ticked holder is a change, and the list has nobody to show.
    const { sent } = picker([{ id: "pc-cerric", name: "Cerric" }], () => [], "dm");
    fireEvent.click(screen.getByRole("button", { name: w.whoSome }));
    await waitFor(() => expect((screen.getByRole("button", { name: w.whoSave }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.whoSave }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect("shown" in sent[0]!).toBe(false);
  });
});
