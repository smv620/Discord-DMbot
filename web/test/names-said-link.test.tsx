/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The link from Discord's "🎤 Say it" button (#2317): `?said=<the name as written>` opens the Names page on
// that name, with its editor open and the cursor in the "how to say it" box. Anything else shows the plain
// list: no such name, two with the same words, a manager who can't edit, a long or empty value.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Names, { saidFromSearch } from "../src/account/Names";
import { namesPage as w } from "../src/content/names-words";

afterEach(cleanup);

const DM = "/account/names/100.cmp1";
const MANAGER = "/account/names/100.cmp1.names-manager";
const open = async (path: string, search: string) => {
  render(<Names api={mockApi("table")} path={path} search={search} />);
  await screen.findByRole("list", { name: w.listLabel });
};

describe("reading the link", () => {
  it.each([
    ["?said=Gorrak", "Gorrak"],
    ["?said=%20Ka%27zeth%20", "Ka'zeth"],
    ["?said=", ""],
    ["", ""],
    ["?other=1", ""],
    [`?said=${"x".repeat(81)}`, ""],
  ])("%s reads as %j", (search, expected) => {
    expect(saidFromSearch(search)).toBe(expected);
  });
});

describe("opening on the name", () => {
  it("searches for the name, opens its editor, and puts the cursor in the how-to-say-it box", async () => {
    await open(DM, "?said=Gorrak");
    const box = (await screen.findByLabelText(w.sayLabel("Gorrak"))) as HTMLInputElement;
    await waitFor(() => expect(document.activeElement).toBe(box));
    expect((screen.getByLabelText(w.searchLabel) as HTMLInputElement).value).toBe("Gorrak");
  });

  it("matches the name in any case", async () => {
    await open(DM, "?said=gorrak");
    expect(await screen.findByLabelText(w.sayLabel("Gorrak"))).toBeTruthy();
  });

  it("shows the plain list for a name that isn't there, and opens nothing", async () => {
    render(<Names api={mockApi("table")} path={DM} search="?said=Nobody%20Here" />);
    expect(await screen.findByText(w.noMatch)).toBeTruthy(); // the plain (empty) search result
    expect(screen.queryByRole("button", { name: w.saySave })).toBeNull();
  });

  it("opens nothing without the link", async () => {
    await open(DM, "");
    expect(screen.queryByRole("button", { name: w.saySave })).toBeNull();
  });

  it("opens no editor for someone who can't edit", async () => {
    await open(MANAGER, "?said=Gorrak");
    expect(screen.queryByRole("button", { name: w.saySave })).toBeNull();
  });
});
