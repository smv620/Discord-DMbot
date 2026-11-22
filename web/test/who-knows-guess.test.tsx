/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Who knows this?" marks a character DMbot only guessed knows the connection (#3940, page half of #4970): the state line
// and the picker say so, and while a guess is ticked the save says it keeps the guesses left ticked as the DM's own. A
// connection with no guess looks as it did before. The DM's view only: the API sends `guess` to the DM alone.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, WebLink } from "../src/account/api";
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
