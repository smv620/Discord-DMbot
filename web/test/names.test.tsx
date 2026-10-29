/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Names page (#2295) against the pretend API: search as you type, the filter by kind with its
// counts, sorting, paging, what each action sends, and the views for someone who may not change
// names (a server manager who isn't a DM) or may not see the list at all.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names, { namesRefFromPath } from "../src/account/Names";
import { NAME_LIMIT, namesPage as w } from "../src/content/names-words";

afterEach(cleanup);

const DM = "/account/names/100.cmp1";
const MANAGER = "/account/names/100.cmp1.names-manager";
const NOBODY = "/account/names/100.cmp1.names-none";

function open(path = DM, api = mockApi("table")) {
  render(<Names api={api} path={path} />);
  return api;
}
const rows = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("name-row"));
const rowOf = (name: string) => rows().find((li) => li.textContent?.includes(name)) as HTMLElement;
const ready = async () => screen.findByRole("list", { name: w.listLabel });
// The page shows 50 names unless asked; most of these tests are about a page of 20, as before.
const twenty = async () => {
  fireEvent.change(screen.getByLabelText(w.perPageLabel), { target: { value: "20" } });
  await waitFor(() => expect(rows()).toHaveLength(20));
};
const types = () => screen.getByRole("group", { name: w.kindLabel });
const tick = (label: string) => fireEvent.click(within(types()).getByLabelText(label));
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));

describe("the address", () => {
  it("reads the campaign out of the path", () => {
    expect(namesRefFromPath("/account/names/100.cmp1")).toBe("100.cmp1");
    expect(namesRefFromPath("/account/names/")).toBeNull();
    expect(namesRefFromPath("/account/import/100.cmp1")).toBeNull();
  });
});

describe("finding names", () => {
  it("lists the first fifty, with the count and a tick box with a count for each type", async () => {
    const api = open();
    await ready();
    const total = Number(/^(\d+) names$/.exec(screen.getByText(/^\d+ names$/).textContent ?? "")?.[1]);
    expect(rows()).toHaveLength(Math.min(50, total));
    expect(calls(api, "allNames").shift()).toContain("size=50");
    expect(within(types()).getByLabelText("NPCs (16)")).toBeTruthy();
    expect(within(types()).getByLabelText("Places (9)")).toBeTruthy();
    const clear = within(types()).getByRole("button", { name: /Show all types/ });
    expect(clear.getAttribute("aria-disabled")).toBe("true"); // (not `disabled`: focus must stay)
  });

  it("offers 20, 50, 200 and 500 a page, 50 to begin with, and starts over at page 1", async () => {
    const api = open();
    await ready();
    const select = screen.getByLabelText(w.perPageLabel) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["20", "50", "200", "500"]);
    expect(select.value).toBe("50");
    await twenty();
    expect(calls(api, "allNames").pop()).toContain("page=1:size=20");
    fireEvent.change(select, { target: { value: "200" } });
    await waitFor(() => expect(calls(api, "allNames").pop()).toContain("size=200"));
  });

  it("searches as you type, in names and in other names, and says when nothing matches", async () => {
    const api = open();
    await ready();
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "voss" } });
    await waitFor(() => expect(calls(api, "allNames:100.cmp1:q=voss").length).toBeGreaterThan(0));
    await waitFor(() => expect(rows().every((r) => /voss/i.test(r.textContent ?? ""))).toBe(true));
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "zzzz" } });
    expect(await screen.findByText(w.noMatch)).toBeTruthy();
    expect(screen.getByText("0 names")).toBeTruthy();
  });

  it("filters by kind and starts over at page 1", async () => {
    const api = open();
    await ready();
    tick("Places (9)");
    await waitFor(() => expect(rows()).toHaveLength(9));
    expect(calls(api, "allNames:100.cmp1:q=:kind=place").pop()).toContain("page=1");
    expect(rows().every((r) => /Places/.test(r.textContent ?? ""))).toBe(true);
  });

  it("takes several types at once, and one button shows every type again", async () => {
    const api = open();
    await ready();
    tick("Places (9)");
    await waitFor(() => expect(rows()).toHaveLength(9));
    tick("NPCs (16)");
    await waitFor(() => expect(rows()).toHaveLength(25));
    expect(calls(api, "allNames").pop()).toContain("kind=place,npc");
    tick("Places (9)"); // un-ticked again
    await waitFor(() => expect(rows()).toHaveLength(16));
    fireEvent.click(within(types()).getByRole("button", { name: /Show all types/ }));
    await waitFor(() => expect(rows().length).toBeGreaterThan(25));
    expect(calls(api, "allNames").pop()).toContain("kind=:");
  });

  it("sorts by name or newest first", async () => {
    open();
    await ready();
    const first = () => rows()[0]?.querySelector(".campaign-name")?.textContent;
    const byName = first();
    fireEvent.change(screen.getByLabelText(w.sortLabel), { target: { value: "newest" } });
    await waitFor(() => expect(first()).not.toBe(byName));
    expect(first()).toMatch(/Alaric/); // the newest name added to the pretend campaign
  });

  it("moves between pages with the page bar, which says which page each button goes to", async () => {
    const api = open();
    await ready();
    await twenty();
    const bar = screen.getByRole("navigation", { name: w.pagesNav });
    fireEvent.click(within(bar).getByRole("button", { name: "Next, page 2" }));
    await waitFor(() => expect(calls(api, "allNames").some((c) => c.includes("page=2"))).toBe(true));
    await waitFor(() => expect(rows().length).toBeLessThan(20));
    expect(within(bar).getByRole("button", { name: "Previous, page 1" })).toBeTruthy();
  });

  it("starts over at page 1 when the page size changes while on page 2", async () => {
    const api = open();
    await ready();
    await twenty();
    const bar = screen.getByRole("navigation", { name: w.pagesNav });
    fireEvent.click(within(bar).getByRole("button", { name: "Next, page 2" }));
    await waitFor(() => expect(calls(api, "allNames").pop()).toContain("page=2"));
    fireEvent.change(screen.getByLabelText(w.perPageLabel), { target: { value: "50" } });
    await waitFor(() => expect(calls(api, "allNames").pop()).toContain("page=1:size=50"));
  });

  it("goes back to the first page, and says so, when the page asked for is gone", async () => {
    const api = open();
    await ready();
    await twenty();
    const real = api.allNames!.bind(api);
    let first = true;
    api.allNames = async (ref, q) => {
      if (q.page === 2 && first) {
        first = false;
        throw new ApiError("bad-page");
      }
      return real(ref, q);
    };
    const bar = screen.getByRole("navigation", { name: w.pagesNav });
    fireEvent.click(within(bar).getByRole("button", { name: "Next, page 2" }));
    expect(await screen.findByText(w.pageGone)).toBeTruthy();
    expect(rows()).toHaveLength(20);
  });

  it("shows a name's kind, its other names, a secret mark, and the NPC page's link for an NPC", async () => {
    open();
    await ready();
    const aldric = rowOf("Captain Brask");
    expect(within(aldric).getByText("NPCs")).toBeTruthy();
    expect(within(aldric).getByText(/Also called: Captain, Captain Brask the Elder/)).toBeTruthy();
    const link = within(aldric).getByRole("link", { name: w.npcLinkFor("Captain Brask") });
    expect(link.getAttribute("href")).toBe("/account/npc/100.cmp1/n4");
    expect(screen.getAllByText(w.secretWords).length).toBeGreaterThan(0);
    // A place has no NPC page.
    tick("Places (9)");
    await waitFor(() => expect(rows()).toHaveLength(9));
    expect(screen.queryByRole("link", { name: /NPC page/ })).toBeNull();
  });
});

describe("what a DM can do", () => {
  const edit = async (name: string) => {
    const row = rowOf(name);
    fireEvent.click(within(row).getByRole("button", { name: name }));
    return within(row);
  };

  it("says the changes can be undone in Discord, and has a name to press on every row", async () => {
    open();
    await ready();
    await twenty();
    expect(screen.getByText(new RegExp(w.undoNote.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeTruthy();
    expect(screen.queryByText(w.readOnly)).toBeNull();
    expect(document.querySelectorAll("button.name-open")).toHaveLength(20);
    expect(screen.getByText(new RegExp(w.pressName))).toBeTruthy(); // the one cue that a name can be pressed
    expect(document.querySelector("button.name-open")?.getAttribute("aria-describedby")).toBe("names-press-hint");
    expect(document.getElementById("names-press-hint")?.textContent).toBe(w.pressName);
  });

  it("fixes a name of exactly the limit", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    const exactly = "G".repeat(NAME_LIMIT);
    fireEvent.input(row.getByLabelText(w.spellLabel), { target: { value: exactly } });
    fireEvent.click(row.getByRole("button", { name: w.saveSpelling }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe(`changeName:100.cmp1:n7:{"action":"spell","name":"${exactly}"}`));
    expect(row.getByLabelText<HTMLInputElement>(w.spellLabel).maxLength).toBe(NAME_LIMIT);
  });

  it("says a name the API finds too long is too long, with the limit", async () => {
    const api = open();
    api.changeName = async () => {
      throw new ApiError("ticket-too-long");
    };
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.spellLabel), { target: { value: "Gorrak the Tall" } });
    fireEvent.click(row.getByRole("button", { name: w.saveSpelling }));
    expect(await row.findByText(`That name is too long. Use ${NAME_LIMIT} letters or fewer.`)).toBeTruthy();
  });

  it("fixes the spelling", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.spellLabel), { target: { value: "Gorrak Stonefist" } });
    fireEvent.click(row.getByRole("button", { name: w.saveSpelling }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"spell","name":"Gorrak Stonefist"}'));
    expect(await screen.findByText(w.saved)).toBeTruthy();
    expect(await screen.findByText("Gorrak Stonefist")).toBeTruthy();
  });

  it("says a name another name already has is taken, and keeps the editor open", async () => {
    open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.spellLabel), { target: { value: "Mira Voss" } });
    fireEvent.click(row.getByRole("button", { name: w.saveSpelling }));
    expect(await row.findByText(w.errors["name-exists"] ?? "")).toBeTruthy();
    expect(row.getByLabelText(w.spellLabel)).toBeTruthy();
  });

  it("changes the kind", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.change(row.getByLabelText(w.kindChange), { target: { value: "group" } });
    fireEvent.click(row.getByRole("button", { name: w.saveKind }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"kind","kind":"group"}'));
    await waitFor(() => expect(within(rowOf("Gorrak")).getByText("Groups")).toBeTruthy());
  });

  it("adds and removes another name", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.addOtherLabel), { target: { value: "the Stone Fist" } });
    fireEvent.click(row.getByRole("button", { name: w.addOther }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"add-other","other":"the Stone Fist"}'));
    const added = await screen.findByRole("button", { name: w.removeOther("the Stone Fist") });
    fireEvent.click(added);
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"remove-other","other":"the Stone Fist"}'));
  });

  it("marks a name secret and unmarks it", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.click(row.getByLabelText(w.hideCheck));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"hide","hidden":true}'));
    await waitFor(() => expect((within(rowOf("Gorrak")).getByLabelText(w.hideCheck) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(within(rowOf("Gorrak")).getByLabelText(w.hideCheck));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"hide","hidden":false}'));
  });

  it("shows the hide switch from `hidden` only, not from a secret flag", async () => {
    const api = mockApi("table");
    const read = api.allNames!.bind(api);
    api.allNames = async (r, q) => {
      const page = await read(r, q);
      // A secret main name, with no `hidden` at all: the switch is the entry's, so it stays unticked.
      return { ...page, names: page.names.map(({ hidden: _hidden, ...n }) => ({ ...n, secret: true })) };
    };
    open(DM, api);
    await ready();
    const row = await edit("Gorrak");
    expect((row.getByLabelText(w.hideCheck) as HTMLInputElement).checked).toBe(false);
  });

  it("says a change may still happen when the bot never answers, and reads the list again", async () => {
    const api = mockApi("table");
    api.changeName = async () => {
      throw new ApiError("still-waiting");
    };
    open(DM, api);
    await ready();
    const before = calls(api, "allNames").length;
    const row = await edit("Gorrak");
    fireEvent.click(row.getByLabelText(w.hideCheck));
    expect(await screen.findByText(w.errors["still-waiting"]!)).toBeTruthy();
    await waitFor(() => expect(calls(api, "allNames").length).toBeGreaterThan(before));
  });

  it("shows Waiting for DMbot… while a change is out, and clears it after", async () => {
    const api = mockApi("table");
    let finish: () => void = () => {};
    api.changeName = () => new Promise((go) => (finish = () => go({ removed: true })));
    open(DM, api);
    await ready();
    const row = await edit("Gorrak");
    fireEvent.click(row.getByLabelText(w.hideCheck));
    const line = await screen.findByText(w.waiting);
    expect(line.getAttribute("role")).toBe("status");
    finish();
    await waitFor(() => expect(screen.queryByText(w.waiting)).toBeNull());
  });

  it("says a change may still happen, never that nothing was changed", async () => {
    expect(w.errors["still-waiting"]).toContain("may still happen");
    expect(w.errors["still-waiting"]).not.toContain("nothing was changed");
    expect(w.hideCheck).toBe("🙈 Hide from players");
  });

  it("gives a refused target its own words, not 'try again'", async () => {
    const api = mockApi("table");
    api.changeName = async () => {
      throw new ApiError("bad-target");
    };
    open(DM, api);
    await ready();
    const row = await edit("Gorrak");
    fireEvent.click(row.getByLabelText(w.hideCheck));
    expect(await screen.findByText(w.errors["bad-target"]!)).toBeTruthy();
    expect(w.errors["bad-target"]).not.toMatch(/try again/i);
  });

  it("merges into a name picked from a search, only after a confirmation", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.mergeSearch), { target: { value: "Dunmar" } });
    const pick = await row.findByRole("radio", { name: /Dunmar/ });
    fireEvent.click(pick);
    fireEvent.click(row.getByRole("button", { name: w.mergeButton }));
    expect(calls(api, "changeName")).toHaveLength(0); // asked, not done
    expect(row.getByText(w.mergeAsk("Gorrak", "Dunmar"))).toBeTruthy();
    fireEvent.click(row.getByRole("button", { name: w.mergeYes }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"merge","into":"n10"}'));
    expect(await screen.findByText(w.merged("Gorrak", "Dunmar"))).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Gorrak")).toBeNull());
  });

  it("leaves a name out of its own merge search", async () => {
    open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.mergeSearch), { target: { value: "Gorrak" } });
    expect(await row.findByText(w.mergeNone)).toBeTruthy();
  });

  it("removes a name only after one confirmation, and says so", async () => {
    const api = open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.click(row.getByRole("button", { name: w.removeButton }));
    expect(calls(api, "changeName")).toHaveLength(0);
    fireEvent.click(row.getByRole("button", { name: w.keep }));
    expect(calls(api, "changeName")).toHaveLength(0);
    fireEvent.click(row.getByRole("button", { name: w.removeButton }));
    fireEvent.click(row.getByRole("button", { name: w.removeYes }));
    await waitFor(() => expect(calls(api, "changeName").pop()).toBe('changeName:100.cmp1:n7:{"action":"remove","confirm":true}'));
    expect(await screen.findByText(w.removed("Gorrak"))).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Gorrak" })).toBeNull());
  });

  it("reads the list again and says so when the name is already gone", async () => {
    const api = open();
    await ready();
    api.changeName = async () => {
      throw new ApiError("not-found");
    };
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.spellLabel), { target: { value: "Gorrak B" } });
    fireEvent.click(row.getByRole("button", { name: w.saveSpelling }));
    expect(await screen.findByText(w.errors["not-found"] ?? "")).toBeTruthy();
    expect(screen.queryByLabelText(w.spellLabel)).toBeNull();
    // The editor is gone, so the keyboard goes to the news line rather than to the top of the page.
    await waitFor(() => {
      const now = document.activeElement as HTMLElement | null;
      expect(now?.getAttribute("role")).toBe("status");
      expect(now?.getAttribute("tabindex")).toBe("-1");
    });
  });
});

describe("someone who can't change names", () => {
  it("a server manager sees no secret names, no Change buttons, and is told why", async () => {
    open(MANAGER);
    await ready();
    expect(screen.getByText(w.readOnly)).toBeTruthy();
    expect(document.querySelectorAll("button.name-open")).toHaveLength(0);
    expect(screen.queryByText(new RegExp(w.pressName))).toBeNull();
    expect(screen.queryByText(w.secretWords)).toBeNull();
    expect(screen.queryByText(w.secretMark)).toBeNull();
    expect(screen.queryByText(new RegExp(w.pressName))).toBeNull();
    // The counts leave the secret names out too.
    const total = Number(/^(\d+) names$/.exec(screen.getByText(/^\d+ names$/).textContent ?? "")?.[1]);
    expect(total).toBeLessThan(50);
  });

  it("anyone else gets one plain line, and no list", async () => {
    open(NOBODY);
    expect(await screen.findByText(w.notYours)).toBeTruthy();
    expect(screen.queryByRole("list", { name: w.listLabel })).toBeNull();
    expect(screen.queryByLabelText(w.searchLabel)).toBeNull();
  });

  it("a signed-out person is asked to sign in", async () => {
    open(DM, mockApi("signed-out"));
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
    // The page's own panel only says it; the one Sign in button is the shared line's (#2301).
    expect(screen.queryByRole("link", { name: w.signIn })).toBeNull();
  });

  it("an API that can't be reached says so and offers another try", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    let fail = true;
    api.allNames = async (ref, q) => {
      if (fail) throw new ApiError("network");
      return real(ref, q);
    };
    open(DM, api);
    expect(await screen.findByText(w.down)).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    await ready();
  });

  it("says it can't show the list for an API without the Names routes", () => {
    const api = mockApi("table");
    delete (api as { allNames?: unknown }).allNames;
    const { container } = render(<Names api={api} path={DM} />);
    expect(container.textContent).toBe(w.notYours);
  });

  it("treats a broken address as a campaign that isn't there", () => {
    expect(namesRefFromPath("/account/names/%E0%A4%A")).toBeNull();
  });
});

describe("only the newest read counts", () => {
  it("never lets a slow old search overwrite a newer one", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    const slow = vi.fn(async (ref: string, q: Parameters<typeof real>[1]) => {
      const answer = await real(ref, q);
      if (q.q === "aldric") await new Promise((r) => setTimeout(r, 600));
      return answer;
    });
    api.allNames = slow;
    open(DM, api);
    await ready();
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "aldric" } });
    await waitFor(() => expect(slow.mock.calls.some((c) => c[1].q === "aldric")).toBe(true));
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "gorrak" } });
    await waitFor(() => expect(rows()).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 800));
    expect(rows()).toHaveLength(1);
    expect(rows()[0]?.textContent).toContain("Gorrak");
  });
});

describe("follow-ups from the first review (#2299)", () => {
  const edit = async (name: string) => {
    const row = rowOf(name);
    fireEvent.click(within(row).getByRole("button", { name: name }));
    return within(row);
  };

  it("the name limit is 100, the same as core's NAME_MAX", async () => {
    expect(NAME_LIMIT).toBe(100);
    const api = mockApi("table");
    await expect(api.changeName!("100.cmp1", "n7", { action: "spell", name: "x".repeat(100) })).resolves.toBeTruthy();
    await expect(api.changeName!("100.cmp1", "n7", { action: "spell", name: "y".repeat(101) })).rejects.toMatchObject({
      kind: "ticket-too-long",
    });
    expect(w.errors["ticket-too-long"]).toBe("That name is too long. Use 100 letters or fewer.");
  });

  it("never shows a secret name to someone who can't edit, even if the API sent one", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    api.allNames = async (ref, query) => {
      const page = await real(ref, query);
      return {
        ...page,
        canEdit: false,
        names: [...page.names, { id: "zz", name: "Hidden Sorcerer", kind: "npc", others: [], secret: true, npcId: null }],
      };
    };
    open(DM, api);
    await ready();
    expect(screen.queryByText("Hidden Sorcerer")).toBeNull();
    expect(screen.queryByText(w.secretMark)).toBeNull();
  });

  it("moves the keyboard onto the question when Remove opens it, and back onto Remove on 'Keep it'", async () => {
    open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.click(row.getByRole("button", { name: w.removeButton }));
    await waitFor(() => expect(document.activeElement).toBe(row.getByRole("button", { name: w.removeYes })));
    fireEvent.click(row.getByRole("button", { name: w.keep }));
    await waitFor(() => expect(document.activeElement).toBe(row.getByRole("button", { name: w.removeButton })));
  });

  it("does the same for the merge question", async () => {
    open();
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.mergeSearch), { target: { value: "Dunmar" } });
    fireEvent.click(await row.findByRole("radio", { name: /Dunmar/ }));
    fireEvent.click(row.getByRole("button", { name: w.mergeButton }));
    await waitFor(() => expect(document.activeElement).toBe(row.getByRole("button", { name: w.mergeYes })));
    fireEvent.click(row.getByRole("button", { name: w.mergeKeep }));
    await waitFor(() => expect(document.activeElement).toBe(row.getByRole("button", { name: w.mergeButton })));
  });

  it("typing in the merge search keeps the cursor there when it closes the question", async () => {
    open();
    await ready();
    const row = await edit("Gorrak");
    const box = row.getByLabelText(w.mergeSearch) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "Dunmar" } });
    fireEvent.click(await row.findByRole("radio", { name: /Dunmar/ }));
    fireEvent.click(row.getByRole("button", { name: w.mergeButton }));
    box.focus();
    fireEvent.input(box, { target: { value: "Dunmar a" } });
    await waitFor(() => expect(row.queryByRole("button", { name: w.mergeYes })).toBeNull());
    expect(document.activeElement).toBe(box);
  });

  it("typing in the merge search keeps the cursor there when it closes the Remove question too", async () => {
    // Fails if "No" and "typing" are mixed up: only "Keep it" may hand the keyboard back to a button.
    open();
    await ready();
    const row = await edit("Gorrak");
    const box = row.getByLabelText(w.mergeSearch) as HTMLInputElement;
    fireEvent.click(row.getByRole("button", { name: w.removeButton }));
    await row.findByRole("button", { name: w.removeYes });
    box.focus();
    fireEvent.input(box, { target: { value: "Dun" } });
    await waitFor(() => expect(row.queryByRole("button", { name: w.removeYes })).toBeNull());
    expect(document.activeElement).toBe(box);
  });

  it("closes an open editor when its row leaves the list, and it stays closed when the row returns", async () => {
    open();
    await ready();
    await edit("Gorrak");
    expect(screen.getByRole("button", { name: "Gorrak" }).getAttribute("aria-expanded")).toBe("true");
    const search = screen.getByLabelText(w.searchLabel);
    fireEvent.input(search, { target: { value: "Dunmar" } });
    await waitFor(() => expect(screen.queryByText("Gorrak")).toBeNull());
    fireEvent.input(search, { target: { value: "" } });
    await waitFor(() => expect(screen.getByText("Gorrak")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Gorrak" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("says so when the merge search fails, rather than 'no other name matches'", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    api.allNames = async (ref, query) => {
      if (query.q === "Dunmar") throw new ApiError("network");
      return real(ref, query);
    };
    open(DM, api);
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.mergeSearch), { target: { value: "Dunmar" } });
    expect(await row.findByText(w.mergeSearchDown)).toBeTruthy();
    expect(row.queryByText(w.mergeNone)).toBeNull();
  });

  it("tells the DM when the merge search has more matches than it shows", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    api.allNames = async (ref, query) => {
      const page = await real(ref, query);
      return query.q === "a" ? { ...page, pages: 3 } : page;
    };
    open(DM, api);
    await ready();
    const row = await edit("Gorrak");
    fireEvent.input(row.getByLabelText(w.mergeSearch), { target: { value: "a" } });
    expect(await row.findByText(w.mergeMore)).toBeTruthy();
  });
});
