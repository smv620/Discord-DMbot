/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "See in the story graph" on an opened name of the Names page, and the graph's reading of the link it makes:
// `#centre=<id>` puts that name in the middle (its details then show, as the middle name's do).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { centreFromHash } from "../src/account/Web";
import { namesPage as w } from "../src/content/names-words";

afterEach(cleanup);

describe("reading the link", () => {
  it.each([
    ["#centre=n12", "n12"],
    ["#centre=%20n12%20", "n12"],
    ["#centre=", null],
    ["", null],
    ["#other=1", null],
    ["?centre=n12", null],
    ["#centre=a b", null],
    [`#centre=${"x".repeat(65)}`, null],
  ])("%s reads as %j", (hash, expected) => {
    expect(centreFromHash(hash)).toBe(expected);
  });
});

describe("the button on an opened name", () => {
  it("links to the graph with that name in the middle", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" hash="#said=Gorrak" />);
    const link = (await screen.findByRole("link", { name: w.seeInGraphFor("Gorrak") })) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toMatch(/^\/account\/web\/100\.cmp1#centre=[A-Za-z0-9_-]+$/);
  });

  it("is not there while the name is closed", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: w.listLabel });
    expect(screen.queryByRole("link", { name: /in the story graph/ })).toBeNull();
  });
});
