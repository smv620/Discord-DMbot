/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The website's search bar (#3855), line 1: the 🔍 opens a box, typing shows the best page at once with its plain line, a miss
// says so, Escape closes it and returns the keyboard to the 🔍, and nothing is asked of any server.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { searchWords as w } from "../src/content/search-words";
import SearchBar from "../src/search/SearchBar";

afterEach(cleanup);
const open = () => fireEvent.click(screen.getByRole("button", { name: w.open }));
const type = (value: string) => fireEvent.input(screen.getByLabelText(w.label), { target: { value } });

describe("the search bar", () => {
  it("is closed until the 🔍 is pressed, then takes the cursor", () => {
    render(<SearchBar />);
    expect(document.getElementById("site-search-panel")?.hasAttribute("hidden")).toBe(true);
    open();
    expect(document.getElementById("site-search-panel")?.hasAttribute("hidden")).toBe(false);
    expect(document.activeElement).toBe(screen.getByLabelText(w.label));
  });
  it("shows the best page as a link with its line, as you type", () => {
    render(<SearchBar />);
    open();
    type("a summary of episodes");
    const link = screen.getByRole("link", { name: "Your recaps" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/account/recaps");
    expect(screen.getByText("The written story of each session.")).toBeTruthy();
    type("how much does it cost");
    expect((screen.getByRole("link", { name: "Prices" }) as HTMLAnchorElement).getAttribute("href")).toBe("/pricing");
  });
  it("says so when nothing fits, and shows nothing while the box is empty", () => {
    render(<SearchBar />);
    open();
    expect(screen.queryByText(w.none)).toBeNull();
    type("qzxv wplk");
    expect(screen.getByText(w.none)).toBeTruthy();
  });
  it("closes on Escape, clears the words and gives the keyboard back to the 🔍", () => {
    render(<SearchBar />);
    open();
    type("pricing");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Escape" });
    expect(document.getElementById("site-search-panel")?.hasAttribute("hidden")).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: w.open }));
    open();
    expect((screen.getByLabelText(w.label) as HTMLInputElement).value).toBe("");
  });
  it("asks no server anything", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("recaps");
    expect(fetcher).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
