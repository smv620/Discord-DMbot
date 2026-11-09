/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Script and Session notes beside Cleaned and As heard on a session's Transcripts page (#3898, owner request 2026-11-09): four
// views for a game master, two for a player; the script and the notes are read on the page and saved; a session with none
// says so; a refused or failed read shows the failure line with a retry.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { sessionNotesWords as notes } from "../src/content/session-notes-words";
import { text } from "../src/content/transcripts";

vi.mock("../src/content/theatre-download-switch", () => ({ THEATRE_DOWNLOAD_LIVE: true }));
vi.mock("../src/content/session-notes-switch", () => ({ SESSION_NOTES_LIVE: true }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const REF = "200000000000000001.cmp1";
const asRole = (api: ReturnType<typeof mockApi>, role: "owner" | "player"): AccountApi =>
  Object.assign(
    {
      ...api,
      me: async () => {
        const me = await api.me();
        return me && { ...me, campaigns: [{ ...me.campaigns[0]!, ref: REF, role }] };
      },
    },
    { calls: api.calls },
  ) as AccountApi;
const open = async (api: AccountApi, number: number) => {
  render(<Transcripts api={api} path={`/account/transcripts/${REF}/${number}`} />);
  await screen.findByRole("button", { name: text.versions.cleaned });
};
const tab = (name: string) => screen.getByRole("button", { name });
const tabNames = () => Array.from(document.querySelectorAll('[role="group"] button[aria-pressed]')).map((b) => b.textContent);

describe("the views", () => {
  it("shows a game master four, in order", async () => {
    await open(asRole(mockApi("table"), "owner"), 1);
    await waitFor(() => expect(tabNames()).toEqual([text.versions.cleaned, text.versions.heard, text.tabs.script, text.tabs.notes]));
  });
  it("shows a player two, and never asks for the script or the notes", async () => {
    const base = mockApi("table");
    const api = asRole(base, "player");
    await open(api, 1);
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
    expect(tabNames()).toEqual([text.versions.cleaned, text.versions.heard]);
    expect(base.calls.some((c: string) => c.startsWith("transcriptScript") || c.startsWith("sessionNotes"))).toBe(false);
  });
});

describe("the save buttons' status lines", () => {
  it("draw no empty warning box beside Save as a text file or Save the script", async () => {
    await open(asRole(mockApi("table"), "owner"), 1);
    await screen.findByRole("button", { name: text.download });
    for (const status of Array.from(document.querySelectorAll('main [role="status"]'))) {
      if ((status.textContent ?? "").trim() === "") expect(status.classList.contains("warn"), "an empty status is not drawn as a warning").toBe(false);
    }
  });
});

describe("the Script view", () => {
  it("reads the script on the page, with Save, in the transcript's own list", async () => {
    await open(asRole(mockApi("table"), "owner"), 1);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.script));
    fireEvent.click(tab(text.tabs.script));
    expect(await screen.findByText(/Mira \[Belleros's character\] draws her sword/)).toBeTruthy();
    expect(document.querySelector("ul.transcript-lines")).not.toBeNull();
    expect(await screen.findByRole("button", { name: text.scriptDownload })).toBeTruthy();
  });
  it("says so when no script has been made", async () => {
    await open(asRole(mockApi("table"), "owner"), 2);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.script));
    fireEvent.click(tab(text.tabs.script));
    expect(await screen.findByText(text.scriptNone)).toBeTruthy();
  });
  it("shows the failure line and retries when the read fails", async () => {
    const base = asRole(mockApi("table"), "owner");
    let fails = 1;
    const api = { ...base, transcriptScript: async (r: string, n: number) => (fails-- > 0 ? Promise.reject(new ApiError("server")) : base.transcriptScript!(r, n)) } as AccountApi;
    await open(api, 1);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.script));
    fireEvent.click(tab(text.tabs.script));
    fireEvent.click(await screen.findByRole("button", { name: text.tryAgain }));
    expect(await screen.findByText(/draws her sword/)).toBeTruthy();
  });
});

describe("the Session notes view", () => {
  it("reads the beats on the page and saves them as a file", async () => {
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    await open(asRole(mockApi("table"), "owner"), 2);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.notes));
    fireEvent.click(tab(text.tabs.notes));
    expect(await screen.findByText("The party crosses the river")).toBeTruthy();
    expect(screen.getByText(notes.who("Cerric, Mira"), { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.saveNotes }));
    await waitFor(() => expect(saved).toEqual(["session-2-notes.txt"]));
  });
  it("says so when the session has no notes", async () => {
    await open(asRole(mockApi("table"), "owner"), 1);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.notes));
    fireEvent.click(tab(text.tabs.notes));
    expect(await screen.findByText(text.notesNone)).toBeTruthy();
  });
  it("shows the failure line with a retry when the read fails", async () => {
    const base = asRole(mockApi("table"), "owner");
    const api = { ...base, sessionNotesList: async () => Promise.reject(new ApiError("server")) } as AccountApi;
    await open(api, 2);
    await waitFor(() => expect(tabNames()).toContain(text.tabs.notes));
    fireEvent.click(tab(text.tabs.notes));
    expect(await screen.findByText(notes.failed, { exact: false })).toBeTruthy();
    expect(screen.getByRole("button", { name: notes.tryAgain })).toBeTruthy();
  });
});
