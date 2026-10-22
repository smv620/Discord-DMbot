/** @jsxImportSource preact */
// @vitest-environment happy-dom
// An NPC's page (#2266) against the pretend API: the member's view, a DM's extra note, someone
// outside the server, a name that isn't there, saving My notes, and downloading or deleting them.
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

const MEMBER = "/campaigns/100.cmp1/npcs/e1";
const DM = "/campaigns/100.cmp1.npc-dm/npcs/e1";
const OUTSIDE = "/campaigns/100.cmp1.npc-outside/npcs/e1";
const GONE = "/campaigns/100.cmp1/npcs/gone";
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));

function open(path = MEMBER, api = mockApi("table")) {
  render(<Npc api={api} path={path} />);
  return api;
}
const box = () => screen.findByLabelText(w.notesLabel) as Promise<HTMLTextAreaElement>;

describe("the address", () => {
  it("reads the campaign and the NPC", () => {
    expect(npcFromPath(MEMBER)).toEqual({ ref: "100.cmp1", entry: "e1" });
    expect(npcFromPath("/campaigns/100.cmp1/npcs/")).toBeNull();
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
    expect(screen.getByRole("link", { name: w.recapLink("The night on the ferry", "12 Oct") })).toBeTruthy();
    expect(screen.getByText(w.notesWho)).toBeTruthy();
    expect(screen.queryByText(w.dmNote)).toBeNull();
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
  it("lists notes on names no longer listed, with the days they are kept", async () => {
    open();
    await screen.findByText(w.unlistedHeading);
    expect(screen.getByText("Old Tam")).toBeTruthy();
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
