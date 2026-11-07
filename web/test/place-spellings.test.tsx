/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Is this the same place?" on the Names page (#3533, website half of #3037), against the pretend API. Shown only while
// PLACE_SPELLING_LIVE is true (false today), so these tests switch it on. DM only.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as w } from "../src/content/names-words";
import { placeSpellingWords as p } from "../src/content/place-spelling-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/place-spelling-switch", () => ({
  get PLACE_SPELLING_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const open = async (path = "/account/names/100.cmp1", api = mockApi("table")) => {
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: w.listLabel });
  return api;
};
const asked = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("answerPlaceSpelling"));

describe("one place written two ways", () => {
  it("shows the line and both buttons to a DM, with the nothing-changes note", async () => {
    await open();
    await screen.findByText(p.heading);
    const line = screen.getAllByText((_, el) => el?.tagName === "P" && el.textContent === "Salt Marsh looks like another way of writing Saltmarsh.");
    expect(line.length).toBe(1);
    expect(screen.getAllByText(p.lead).length).toBe(2);
    expect(screen.getByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") }).textContent).toBe("Yes, the same place");
    expect(screen.getByRole("button", { name: p.noFor("Salt Marsh", "Saltmarsh") }).textContent).toBe("No, two places");
  });

  it("Yes makes it another name of the place and says so", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") }));
    await screen.findByText(p.saidYes("Salt Marsh", "Saltmarsh"));
    expect(asked(api)).toEqual(["answerPlaceSpelling:100.cmp1:sp1:true"]);
    expect(screen.queryByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") })).toBeNull();
  });

  it("No keeps two places, says it will not ask again, and leaves the other question", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: p.noFor("Salt Marsh", "Saltmarsh") }));
    await screen.findByText(p.saidNo("Salt Marsh", "Saltmarsh"));
    expect(asked(api)).toEqual(["answerPlaceSpelling:100.cmp1:sp1:false"]);
    expect(screen.getByRole("button", { name: p.yesFor("Grey Watch", "Greywatch") })).toBeTruthy();
  });

  it("says plainly when the answer did not save, and keeps the question", async () => {
    await open("/account/names/100.cmp1.spelling-fails");
    fireEvent.click(await screen.findByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") }));
    await screen.findByText(p.failed);
    expect(screen.getByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") })).toBeTruthy();
  });

  it("asks once: a page redraw after an answer does not read the questions again, and a double press answers once", async () => {
    const api = await open();
    const yes = await screen.findByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await screen.findByText(p.saidYes("Salt Marsh", "Saltmarsh"));
    await new Promise((r) => setTimeout(r, 50));
    expect(asked(api)).toEqual(["answerPlaceSpelling:100.cmp1:sp1:true"]);
    expect(api.calls.filter((c) => c.startsWith("placeSpellings")).length).toBe(1);
    expect(screen.queryByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") })).toBeNull();
  });

  it("drops a question that was already answered somewhere else", async () => {
    const api = await open();
    const yes = await screen.findByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") });
    await api.answerPlaceSpelling!("100.cmp1", { id: "sp1", placeId: "n24", place: "Saltmarsh", spelling: "Salt Marsh" }, false); // (answered in Discord meanwhile)
    fireEvent.click(yes);
    await waitFor(() => expect(screen.queryByRole("button", { name: p.yesFor("Salt Marsh", "Saltmarsh") })).toBeNull());
    expect(screen.queryByText(p.failed)).toBeNull();
  });

  it("says so when the questions can't be read", async () => {
    const api = mockApi("table");
    api.placeSpellings = async () => {
      throw new Error("down");
    };
    await open("/account/names/100.cmp1", api);
    await screen.findByText(p.loadFailed);
  });

  it("is not there for someone who can't change names, and not while the switch is off", async () => {
    const api0 = await open("/account/names/100.cmp1.names-manager");
    expect(screen.queryByText(p.heading)).toBeNull();
    expect(api0.calls.some((c) => c.startsWith("placeSpellings"))).toBe(false);
    cleanup();
    live.on = false;
    const api = await open();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(p.heading)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("placeSpellings"))).toBe(false);
  });

  it("has no accessibility violations", async () => {
    await open();
    await screen.findByText(p.heading);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
