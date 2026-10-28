/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Not named yet" on the Names page (#2803): the people the story described and never named, listed first for a DM,
// "Give them a name", the question when DMbot already knows that name, and Undo. Shown only while NOT_NAMED_LIVE
// (false today): before then the page asks for nothing new and shows none of it.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as names } from "../src/content/names-words";
import { notNamedWords as w } from "../src/content/not-named-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/not-named-switch", () => ({
  get NOT_NAMED_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
});

const slow = { timeout: 8000 };
let count = 0;
/** A fresh pretend campaign each time: the pretend API remembers what was named, per page. */
async function open(ending = ".not-named", path?: string) {
  live.on = true;
  const api = mockApi("table");
  render(<Names api={api} path={path ?? `/account/names/100.nn${(count += 1)}${ending}`} />);
  await screen.findByRole("list", { name: names.listLabel });
  return api;
}
const rowOf = (text: string): HTMLElement =>
  screen.getAllByRole("listitem").find((li) => li.classList.contains("name-row") && li.textContent?.includes(text)) as HTMLElement;
const calls = (api: ReturnType<typeof mockApi>, start: string) => api.calls.filter((c) => c.startsWith(start));
const openEditor = (who: string) => fireEvent.click(within(rowOf(who)).getByRole("button", { name: who }));
const give = (name: string) => {
  fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: name } });
  fireEvent.click(screen.getByRole("button", { name: w.give }));
};

describe("while it is switched off", () => {
  it("shows no unnamed person and offers no way to name one", async () => {
    live.on = false;
    render(<Names api={mockApi("table")} path="/account/names/100.off.not-named" />);
    await screen.findByRole("list", { name: names.listLabel });
    expect(screen.queryByText("Ken's sister")).toBeNull();
    expect(screen.queryByLabelText(new RegExp(w.group))).toBeNull();
  });
});

describe("the list", () => {
  it("shows the group and the people in it, first, for a DM", async () => {
    await open();
    expect(rowOf("Ken's sister")).toBeTruthy();
    expect(screen.getByLabelText(new RegExp(`^${w.group}`))).toBeTruthy();
    expect(within(rowOf("Ken's sister")).getByText(w.group)).toBeTruthy();
  });

  it("has no tick box on an unnamed person: they can't be merged", async () => {
    await open();
    expect(within(rowOf("Ken's sister")).queryByRole("checkbox")).toBeNull();
  });

  it("shows nothing of it to someone who isn't a DM", async () => {
    await open(".not-named", "/account/names/100.mgr.not-named.names-manager");
    expect(screen.queryByText("Ken's sister")).toBeNull();
  });

  it("is not in a campaign that has none", async () => {
    await open("");
    expect(screen.queryByText("Ken's sister")).toBeNull();
  });
});

describe("giving them a name", () => {
  it("renames them, says so, and offers Undo", async () => {
    const api = await open();
    openEditor("Ken's sister");
    give("Mara Quill");
    expect(await screen.findByText(w.named("Ken's sister", "Mara Quill"), {}, slow)).toBeTruthy();
    expect(calls(api, "nameThem")[0]).toContain('"action":"name"');
    await waitFor(() => expect(screen.queryByText("Ken's sister")).toBeNull());
    expect(rowOf("Mara Quill")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("asks for a name before sending anything", async () => {
    const api = await open();
    openEditor("Ken's sister");
    fireEvent.click(screen.getByRole("button", { name: w.give }));
    expect(screen.getByText(w.nameFirst)).toBeTruthy();
    expect(calls(api, "nameThem")).toHaveLength(0);
  });

  it("sends one request when the button is pressed twice", async () => {
    const api = await open();
    openEditor("Ken's sister");
    fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: "Mara Quill" } });
    const button = screen.getByRole("button", { name: w.give });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(w.named("Ken's sister", "Mara Quill"), {}, slow);
    expect(calls(api, "nameThem")).toHaveLength(1);
  });

  it("shows what DMbot is doing while it waits", async () => {
    await open();
    openEditor("Ken's sister");
    give("Mara Quill");
    expect(await screen.findByText(w.stage["waiting"] as string)).toBeTruthy();
    await screen.findByText(w.named("Ken's sister", "Mara Quill"), {}, slow);
  });

  it("says so when two people could be the one meant", async () => {
    await open(".ambiguous.not-named");
    openEditor("Ken's sister");
    give("Mara Quill");
    expect(await screen.findByText(w.problems["ambiguous"] as string, {}, slow)).toBeTruthy();
    expect(rowOf("Ken's sister")).toBeTruthy();
  });

  it("says so when DMbot hasn't finished in time, and does not claim nothing happened", async () => {
    await open(".timed-out.not-named");
    openEditor("Ken's sister");
    give("Mara Quill");
    expect(await screen.findByText(w.problems["still-waiting"] as string, {}, slow)).toBeTruthy();
  });
});

describe("a name DMbot already knows", () => {
  it("asks whether they are the same person before changing anything", async () => {
    const api = await open();
    openEditor("Ken's sister");
    give("Mira Voss");
    expect(await screen.findByText(w.known("Ken's sister", "Mira Voss"), {}, slow)).toBeTruthy();
    expect(calls(api, "nameThem")).toHaveLength(1);
    expect(rowOf("Ken's sister")).toBeTruthy();
  });

  it("joins them into the known name on Yes", async () => {
    const api = await open();
    openEditor("Ken's sister");
    give("Mira Voss");
    fireEvent.click(await screen.findByRole("button", { name: w.sameYes }, slow));
    expect(await screen.findByText(w.joined("Ken's sister", "Mira Voss"), {}, slow)).toBeTruthy();
    expect(calls(api, "nameThem")[1]).toContain('"action":"name-join"');
    await waitFor(() => expect(screen.queryByText("Ken's sister")).toBeNull());
  });

  it("goes back to the name box on No, and sends nothing more", async () => {
    const api = await open();
    openEditor("Ken's sister");
    give("Mira Voss");
    fireEvent.click(await screen.findByRole("button", { name: w.sameNo }, slow));
    expect(screen.getByLabelText(w.nameLabel)).toBeTruthy();
    expect(calls(api, "nameThem")).toHaveLength(1);
  });
});

describe("Undo", () => {
  it("puts a new name back", async () => {
    const api = await open();
    openEditor("Ken's sister");
    give("Mara Quill");
    fireEvent.click(await screen.findByRole("button", { name: w.undo }, slow));
    expect(await screen.findByText(w.undone, {}, slow)).toBeTruthy();
    expect(calls(api, "nameThem")[1]).toContain('"action":"name-undo"');
    await waitFor(() => expect(rowOf("Ken's sister")).toBeTruthy());
    expect(screen.queryByText("Mara Quill")).toBeNull();
  });

  it("puts a join back", async () => {
    await open();
    openEditor("Ken's sister");
    give("Mira Voss");
    fireEvent.click(await screen.findByRole("button", { name: w.sameYes }, slow));
    fireEvent.click(await screen.findByRole("button", { name: w.undoJoin }, slow));
    expect(await screen.findByText(w.undone, {}, slow)).toBeTruthy();
    await waitFor(() => expect(rowOf("Ken's sister")).toBeTruthy());
  });
});

describe("accessibility", () => {
  it("has no violations with the editor open", async () => {
    await open();
    openEditor("Ken's sister");
    const found = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(found.violations).toEqual([]);
  });
});
