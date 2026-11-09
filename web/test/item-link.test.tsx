/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Open on the website" in a private message links with `?item=<id>` (#3663): the shared reading of the link, and the pages that
// open on the item or say it has been answered.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { itemFromSearch, pairFromItem } from "../src/account/itemLink";
import { itemWords } from "../src/content/item-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(() => {
  cleanup();
  window.history.pushState({}, "", "/");
});

describe("reading the link", () => {
  it.each([
    ["?item=c1", "c1"],
    ["?item=%20abc.def%20", "abc.def"],
    ["?other=1&item=n12", "n12"],
    ["?item=", null],
    ["", null],
    ["#item=c1", null],
    ["?item=a b", null],
    ["?item=<script>", null],
    [`?item=${"x".repeat(81)}`, null],
  ])("%s reads as %j", (search, expected) => {
    expect(itemFromSearch(search)).toBe(expected);
  });

  it("reads a line from two entry ids joined by a dot, and nothing else", () => {
    const a = "a".repeat(32);
    const b = "b".repeat(32);
    expect(pairFromItem(`${a}.${b}`)).toEqual([a, b]);
    expect(pairFromItem(a)).toBeNull();
    expect(pairFromItem(`${a}.${b}x`)).toBeNull();
  });
});

describe("the waiting part", () => {
  const open = async (search: string) => {
    window.history.pushState({}, "", `/${search}`);
    render(<WebWaiting api={mockApi("table")} campaignRef="100.cmp1" />);
    await screen.findByRole("heading", { name: w.heading });
  };

  it("opens on the claim the link names: scrolled to and focused", async () => {
    await open("?item=c2");
    await waitFor(() => expect(document.activeElement?.id).toBe("item-c2"));
    expect(screen.queryByText(itemWords.answered)).toBeNull();
  });

  it("says so when that one has been answered, and shows the rest as usual", async () => {
    await open("?item=gone-already");
    expect(await screen.findByText(itemWords.answered)).toBeTruthy();
    expect(screen.getByText("Mira Voss knows Far Hall")).toBeTruthy();
  });

  it("says nothing without an item, and ignores a pair of names (that is a line)", async () => {
    await open(`?item=${"a".repeat(32)}.${"b".repeat(32)}`);
    expect(screen.queryByText(itemWords.answered)).toBeNull();
  });
});
