/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A sign-in that ends after the page has opened (#2301): the real API client tells the "who is signed
// in" line, which then offers the one Sign in button. /me says signed in; the page's own call is
// answered 401. (The importer shows only while IMPORT_LIVE is on, so this file switches it on.)
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return { ...real, IMPORT_LIVE: true };
});

import { httpApi, SIGNED_OUT_EVENT, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import Names from "../src/account/Names";
import SignedInLine from "../src/account/SignedInLine";
import { importPage as importWords } from "../src/content/import-words";
import { namesPage as namesWords } from "../src/content/names-words";
import { signedInLine as w } from "../src/content/signed-in-words";

afterEach(cleanup);

const me = {
  user: { id: "100", name: "Mira" },
  plan: null,
  campaigns: [{ id: "cmp1", importsOn: true }],
  servers: [],
  offers: { incoming: [], outgoing: [] },
};
const reply = (status: number, body: unknown = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const realApi = (): AccountApi =>
  httpApi("https://api.example", (async (input: RequestInfo | URL) =>
    String(input).endsWith("/me") ? reply(200, me) : reply(401, { error: "signed_out" })) as typeof fetch);

describe("a sign-in that ends after the page opened", () => {
  it("Names: the line turns to 'not signed in', with exactly one sign-in link", async () => {
    const api = realApi();
    render(
      <>
        <SignedInLine api={api} />
        <Names api={api} path="/account/names/100.cmp1" />
      </>,
    );
    expect(await screen.findByText(namesWords.signedOut)).toBeTruthy();
    await screen.findByText(w.notSignedIn, { exact: false });
    expect(screen.getAllByRole("link", { name: w.signIn })).toHaveLength(1);
    expect(screen.queryByText(w.as, { exact: false })).toBeNull();
  });

  it("Import: a refused Preview does the same", async () => {
    const api = realApi();
    render(
      <>
        <SignedInLine api={api} />
        <Import api={api} path="/account/import/100.cmp1" />
      </>,
    );
    const input = (await screen.findByLabelText(importWords.pickLabel, { selector: "input" })) as HTMLInputElement;
    Object.defineProperty(input, "files", {
      value: [new File(["Cerric: hello"], "one.txt", { type: "text/plain" })],
      configurable: true,
    });
    fireEvent.change(input);
    await screen.findByText(importWords.chosen(1));
    fireEvent.click(screen.getByRole("button", { name: importWords.preview }));
    await screen.findByText(importWords.signedOut);
    await screen.findByText(w.notSignedIn, { exact: false });
    expect(screen.getAllByRole("link", { name: w.signIn })).toHaveLength(1);
  });

  it("the line follows the event (without the listener it would keep saying 'Signed in as')", async () => {
    render(<SignedInLine api={mockApi("table")} />);
    await screen.findByText(w.as, { exact: false });
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    expect(await screen.findByText(w.notSignedIn, { exact: false })).toBeTruthy();
  });
});
