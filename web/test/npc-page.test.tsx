/** @jsxImportSource preact */
// @vitest-environment happy-dom
// An NPC's page (#2266) against the pretend API: the member's view, a DM's extra note, someone
// outside the server, a name that isn't there, saving My notes, and downloading or deleting them.
import { readFileSync } from "node:fs";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Npc, { npcFromPath } from "../src/account/Npc";
import { NOTE_LIMIT, npcPage as w } from "../src/content/npc-words";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const MEMBER = "/account/npc/100.cmp1/e1";
const DM = "/account/npc/100.cmp1.npc-dm/e1";
const OUTSIDE = "/account/npc/100.cmp1.npc-outside/e1";
const GONE = "/account/npc/100.cmp1/gone";
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));

function open(path = MEMBER, api = mockApi("table")) {
  render(<Npc api={api} path={path} />);
  return api;
}
const box = () => screen.findByLabelText(w.notesLabel) as Promise<HTMLTextAreaElement>;

describe("the address", () => {
  it("reads exactly the address Discord's 📖 Open builds", () => {
    const entry = "0123456789abcdef0123456789abcdef";
    expect(npcFromPath(`/account/npc/5.${entry}/${entry}`)).toEqual({ ref: `5.${entry}`, entry });
  });

  it("carries the person back here after Discord sign-in", () => {
    window.history.pushState({}, "", "/account/npc/100.cmp1/e1");
    const url = httpApi("https://api.example").signInUrl();
    expect(url).toBe("https://api.example/auth/discord/start?next=%2Faccount%2Fnpc%2F100.cmp1%2Fe1");
    window.history.pushState({}, "", "/");
  });

  it("reads the campaign and the NPC", () => {
    expect(npcFromPath(MEMBER)).toEqual({ ref: "100.cmp1", entry: "e1" });
    expect(npcFromPath("/account/npc/100.cmp1/")).toBeNull();
    expect(npcFromPath("/account/names/100.cmp1")).toBeNull();
  });
});

describe("the page", () => {
  it("shows what the table knows, the recaps that mention it and My notes", async () => {
    open();
    await screen.findByRole("heading", { name: "Mira Voss" });
    expect(screen.getByText("A person")).toBeTruthy();
    expect(screen.getByText(/the Ferrywoman/)).toBeTruthy();
    expect(screen.getByText("The Brynwater Ferrymen")).toBeTruthy();
    expect(screen.getByText(w.more(2))).toBeTruthy();
    // A player sees the recap's title and day, but no link: the recap page is for the DMs.
    expect(screen.getByText(w.recapLink("The night on the ferry", "12 Oct"))).toBeTruthy();
    expect(screen.queryByRole("link", { name: /night on the ferry/ })).toBeNull();
    expect(screen.getByText(w.notesWho)).toBeTruthy();
    expect(screen.queryByText(w.dmNote)).toBeNull();
  });

  it("links the recaps for a DM", async () => {
    open(DM);
    const link = (await screen.findByRole("link", { name: /night on the ferry/ })) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/account/recaps/100.cmp1.npc-dm.1791000000");
  });

  it("gives a DM their own note, which a member never sees", async () => {
    open(DM);
    await screen.findByRole("heading", { name: w.dmNote });
    expect(screen.getByText("Knows who burned the mill.")).toBeTruthy();
  });

  it("tells someone outside the server so, plainly", async () => {
    open(OUTSIDE);
    expect(await screen.findByText(w.notInServer)).toBeTruthy();
  });

  it("says a missing name isn't found", async () => {
    open(GONE);
    expect(await screen.findByText(w.notFound)).toBeTruthy();
  });

  it("offers another try when DMbot can't be reached", async () => {
    const api = mockApi("table");
    api.npcPage = async () => {
      throw new ApiError("network");
    };
    open(MEMBER, api);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("asks to sign in again when the session ended", async () => {
    const api = mockApi("table");
    api.npcPage = async () => {
      throw new ApiError("signed-out");
    };
    open(MEMBER, api);
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });
});

describe("My notes", () => {
  it("counts the letters and saves with the Save button", async () => {
    const api = open();
    fireEvent.input(await box(), { target: { value: "She knows the river." } });
    expect(screen.getByText(w.counter(20))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.saved);
    expect(calls(api, "saveNpcNote")).toEqual(["saveNpcNote:100.cmp1:e1:She knows the river."]);
  });

  it("saves when the box is left, and not again when nothing changed", async () => {
    const api = open();
    const field = await box();
    fireEvent.input(field, { target: { value: "Owes Harl money." } });
    fireEvent.blur(field);
    await screen.findByText(w.saved);
    fireEvent.blur(field);
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(calls(api, "saveNpcNote")).toHaveLength(1);
  });

  it("can clear a note by saving nothing", async () => {
    const api = open();
    const field = await box();
    fireEvent.input(field, { target: { value: "x" } });
    fireEvent.blur(field);
    await waitFor(() => expect(calls(api, "saveNpcNote")).toHaveLength(1));
    fireEvent.input(field, { target: { value: "" } });
    fireEvent.blur(field);
    await waitFor(() => expect(calls(api, "saveNpcNote").pop()).toBe("saveNpcNote:100.cmp1:e1:"));
  });

  it("does not send a note that is too long", async () => {
    const api = open();
    fireEvent.input(await box(), { target: { value: "a".repeat(NOTE_LIMIT + 1) } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.tooLong);
    expect(calls(api, "saveNpcNote")).toHaveLength(0);
  });

  it("says when a save did not work, and keeps the words in the box", async () => {
    const api = mockApi("table");
    api.saveNpcNote = async () => {
      throw new ApiError("network");
    };
    open(MEMBER, api);
    const field = await box();
    fireEvent.input(field, { target: { value: "keep me" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.saveFailed);
    expect(field.value).toBe("keep me");
  });

  it("asks to wait when saving is too quick", async () => {
    const api = mockApi("table");
    api.saveNpcNote = async () => {
      throw new ApiError("too-soon");
    };
    open(MEMBER, api);
    fireEvent.input(await box(), { target: { value: "again" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.tooSoon);
  });
});

describe("all my notes", () => {
  it("lists only the notes on names no longer listed, with the days they are kept", async () => {
    const api = mockApi("table");
    await api.saveNpcNote!("100.cmp1", "e1", "a listed one"); // "Mira Voss" is listed
    open(MEMBER, api);
    await screen.findByText(w.unlistedHeading);
    const heading = screen.getByText(w.unlistedHeading);
    expect(heading.parentElement!.textContent).toContain("Old Tam");
    expect(heading.parentElement!.textContent).not.toContain("Mira Voss");
    expect(screen.getByText(w.unlistedNote)).toBeTruthy();
  });

  it("downloads one plain text file", async () => {
    const api = open();
    let made: Blob | null = null;
    URL.createObjectURL = (b: Blob | MediaSource) => {
      made = b as Blob;
      return "blob:x";
    };
    URL.revokeObjectURL = () => {};
    fireEvent.click(await screen.findByRole("button", { name: w.download }));
    await waitFor(() => expect(made).not.toBeNull());
    expect(made!.type).toBe("text/plain");
    expect(await made!.text()).toContain("He owed me a favour.");
    expect(calls(api, "myNpcNotes").length).toBeGreaterThan(1);
  });

  it("deletes only after one confirmation", async () => {
    const api = open();
    fireEvent.click(await screen.findByRole("button", { name: w.deleteAsk }));
    expect(calls(api, "deleteMyNpcNotes")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: w.deleteNo }));
    expect(calls(api, "deleteMyNpcNotes")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: w.deleteAsk }));
    fireEvent.click(screen.getByRole("button", { name: w.deleteYes }));
    await screen.findByText(w.deleted(1));
    expect(calls(api, "deleteMyNpcNotes")).toEqual(["deleteMyNpcNotes:100.cmp1"]);
    expect(screen.getByText(w.noneYet)).toBeTruthy();
  });

  it("says when deleting did not work", async () => {
    const api = mockApi("table");
    api.deleteMyNpcNotes = async () => {
      throw new ApiError("network");
    };
    open(MEMBER, api);
    fireEvent.click(await screen.findByRole("button", { name: w.deleteAsk }));
    fireEvent.click(screen.getByRole("button", { name: w.deleteYes }));
    await screen.findByText(w.deleteFailed);
  });
});

describe("keeping the parts in step", () => {
  it("empties the box when all my notes are deleted", async () => {
    const api = mockApi("table");
    await api.saveNpcNote!("100.cmp1", "e1", "keep?");
    open(MEMBER, api);
    const field = await box();
    expect(field.value).toBe("keep?");
    fireEvent.click(await screen.findByRole("button", { name: w.deleteAsk }));
    fireEvent.click(screen.getByRole("button", { name: w.deleteYes }));
    await screen.findByText(w.deleted(2));
    expect((await box()).value).toBe("");
  });

  it("turns Delete on once a first note is saved", async () => {
    const api = mockApi("table");
    await api.deleteMyNpcNotes!("100.cmp1"); // nothing at all
    open(MEMBER, api);
    const del = (await screen.findByRole("button", { name: w.deleteAsk })) as HTMLButtonElement;
    await screen.findByText(w.noneYet);
    expect(del.disabled).toBe(true);
    fireEvent.input(await box(), { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect((screen.getByRole("button", { name: w.deleteAsk }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("sends words typed while a save was still out", async () => {
    const api = mockApi("table");
    const real = api.saveNpcNote!.bind(api);
    let release: () => void = () => {};
    let first = true;
    api.saveNpcNote = async (r, e, b) => {
      if (first) {
        first = false;
        await new Promise<void>((go) => (release = go));
      }
      return real(r, e, b);
    };
    open(MEMBER, api);
    const field = await box();
    fireEvent.input(field, { target: { value: "one" } });
    fireEvent.blur(field);
    fireEvent.input(field, { target: { value: "one two" } });
    fireEvent.blur(field);
    release();
    await waitFor(() => expect(calls(api, "saveNpcNote").pop()).toBe("saveNpcNote:100.cmp1:e1:one two"));
  });
});

describe("the DM note", () => {
  it("is not shown when a DM has none to show, and shows a dash when it is empty", async () => {
    const api = mockApi("table");
    const read = api.npcPage!.bind(api);
    api.npcPage = async (r, e) => ({ ...(await read(r, e)), isDm: true, dmNote: null });
    const { unmount } = render(<Npc api={api} path={MEMBER} />);
    await screen.findByRole("heading", { name: "Mira Voss" });
    expect(screen.queryByText(w.dmNote)).toBeNull();
    unmount();
    api.npcPage = async (r, e) => ({ ...(await read(r, e)), isDm: true, dmNote: "" });
    render(<Npc api={api} path={MEMBER} />);
    await screen.findByRole("heading", { name: w.dmNote });
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("is not shown to someone who is not a DM, even if the API sent one", async () => {
    const api = mockApi("table");
    const read = api.npcPage!.bind(api);
    api.npcPage = async (r, e) => ({ ...(await read(r, e)), isDm: false, dmNote: "secret" });
    open(MEMBER, api);
    await screen.findByRole("heading", { name: "Mira Voss" });
    expect(screen.queryByText("secret")).toBeNull();
  });
});

describe("try again", () => {
  it("reads the page again", async () => {
    const api = mockApi("table");
    const read = api.npcPage!.bind(api);
    let fail = true;
    api.npcPage = async (r, e) => {
      if (fail) throw new ApiError("network");
      return read(r, e);
    };
    open(MEMBER, api);
    await screen.findByRole("alert");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    await screen.findByRole("heading", { name: "Mira Voss" });
  });
});

describe("the site's rules for the page", () => {
  const read = (f: string) => readFileSync(`${process.cwd()}/public/${f}`, "utf8");
  it("serves every NPC from the one page, and never stores or indexes it", () => {
    expect(read("_redirects")).toContain("/account/npc/*  /account/npc  200");
    expect(read("_headers")).toMatch(/\/account\/npc\*\n {2}Cache-Control: no-store\n {2}X-Robots-Tag: noindex, nofollow/);
  });
});

describe("focus around the delete question", () => {
  it("goes into the question, back on Keep, and to the status after a delete", async () => {
    open();
    const opener = await screen.findByRole("button", { name: w.deleteAsk });
    fireEvent.click(opener);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: w.deleteNo })));
    fireEvent.click(screen.getByRole("button", { name: w.deleteNo }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: w.deleteAsk })));
    fireEvent.click(screen.getByRole("button", { name: w.deleteAsk }));
    fireEvent.click(await screen.findByRole("button", { name: w.deleteYes }));
    const said = await screen.findByText(w.deleted(1));
    await waitFor(() => expect(document.activeElement).toBe(said));
    // The status line can take focus although it is not a control, so it carries tabindex -1.
    expect(said.getAttribute("tabindex")).toBe("-1");
  });
});

describe("typing", () => {
  it("drops control characters as they are pasted", async () => {
    open();
    const field = await box();
    fireEvent.input(field, { target: { value: "a\u0000b\u0007c\nd" } });
    expect(field.value).toBe("abc\nd");
  });

  it("does not send words typed during a save whose save failed", async () => {
    const api = mockApi("table");
    let release: () => void = () => {};
    api.saveNpcNote = async () => {
      await new Promise<void>((go) => (release = go));
      throw new ApiError("too-soon");
    };
    const spy = vi.spyOn(api, "saveNpcNote");
    open(MEMBER, api);
    const field = await box();
    fireEvent.input(field, { target: { value: "one" } });
    fireEvent.blur(field);
    fireEvent.input(field, { target: { value: "one two" } });
    release();
    await screen.findByText(w.tooSoon);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("kinds", () => {
  it("shows a kind it does not know as Other", async () => {
    const api = mockApi("table");
    const read = api.npcPage!.bind(api);
    api.npcPage = async (r, e) => ({ ...(await read(r, e)), kind: "mystery" });
    open(MEMBER, api);
    expect(await screen.findByText("Other")).toBeTruthy();
  });
});

describe("the real API's calls", () => {
  it("sends each call to the right address", async () => {
    const seen: string[] = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${url.replace("https://api.example", "")} ${init?.body ?? ""}`.trim());
      return new Response(JSON.stringify({ notes: [], deleted: 3, ok: true }), { status: 200 });
    }) as typeof fetch;
    const api = httpApi("https://api.example", fetcher);
    await api.npcPage!("100.cmp1", "e1");
    await api.saveNpcNote!("100.cmp1", "e1", "hi");
    await api.myNpcNotes!("100.cmp1");
    expect(await api.deleteMyNpcNotes!("100.cmp1")).toBe(3);
    expect(seen).toEqual([
      "GET /me/campaigns/100.cmp1/npcs/e1",
      'PUT /me/campaigns/100.cmp1/npcs/e1/note {"body":"hi"}',
      "GET /me/npc-notes?campaign=100.cmp1",
      "DELETE /me/npc-notes?campaign=100.cmp1",
    ]);
  });

  it("reads the server-only refusal", async () => {
    const api = httpApi("https://api.example", (async () =>
      new Response(JSON.stringify({ error: "not_in_server" }), { status: 403 })) as typeof fetch);
    await expect(api.npcPage!("100.cmp1", "e1")).rejects.toMatchObject({ kind: "not-in-server" });
  });
});
