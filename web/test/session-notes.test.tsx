/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Session notes panel on the Transcripts page (#3027): game masters only, behind SESSION_NOTES_LIVE. The switch is
// read through a getter, so each test sets it before it renders.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SessionNotesPanel from "../src/account/SessionNotes";
import { sessionDay } from "../src/content/recaps";
import { sessionNotesWords as w } from "../src/content/session-notes-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/session-notes-switch", () => ({
  get SESSION_NOTES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
  vi.restoreAllMocks();
});

const panel = (api = mockApi("table"), campaign = "100.cmp1") => render(<SessionNotesPanel api={api} campaignRef={campaign} />);
// The row is found by its title, since its button's name changes to Hide while it is open.
const rowFor = (title: string): HTMLElement => screen.getByText(title, { selector: ".campaign-name" }).closest("li") as HTMLElement;

describe("the Session notes panel", () => {
  it("with the switch off the panel shows nothing", async () => {
    const api = mockApi("table");
    const { container } = panel(api);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.textContent).toBe("");
    expect(api.calls.some((c) => c.startsWith("sessionNotesList"))).toBe(false);
  });

  it("a game master sees one row for each session with notes, newest first, numbered or dated", async () => {
    live.on = true;
    panel();
    await screen.findByRole("heading", { name: w.heading });
    const titles = Array.from(document.querySelectorAll(".campaign-name")).map((el) => el.textContent);
    expect(titles).toEqual(["Session 2", `Session of ${sessionDay(1790000000)}`]);
    expect(within(rowFor("Session 2")).getByText(w.line(sessionDay(1791000000), 3))).toBeTruthy();
    expect(within(rowFor(`Session of ${sessionDay(1790000000)}`)).getByText(w.line(sessionDay(1790000000), 1))).toBeTruthy();
  });

  it("opening a row shows its numbered beats with Who and Where only where there are some", async () => {
    live.on = true;
    panel();
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.openFor("Session 2") }));
    await screen.findByText("The party crosses the river");
    const row = rowFor("Session 2");
    expect(row.querySelectorAll("ol > li")).toHaveLength(3);
    expect(row.textContent).toContain("Who: Cerric, Mira · Where: The Brass Ford");
    expect(row.textContent).toContain("Cerric swears an oath");
    expect(row.textContent).toContain("Who: Cerric");
    // Each check looks only at its own beat: the text between it and the next beat, or to the end of the list.
    const between = (from: string, to: string | null): string => {
      const text = row.textContent ?? "";
      const start = text.indexOf(from);
      return text.slice(start, to ? text.indexOf(to, start) : undefined);
    };
    expect(between("Cerric swears an oath", "A storm closes the ford")).not.toContain("Where:");
    expect(row.textContent).toContain("A storm closes the ford");
    expect(between("A storm closes the ford", null)).toContain("Where: The Brass Ford");
    expect(between("A storm closes the ford", null)).not.toContain("Who:");
  });

  it("Hide closes it and Open again does not ask twice", async () => {
    live.on = true;
    const api = mockApi("table");
    panel(api);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.openFor("Session 2") }));
    await screen.findByText("The party crosses the river");
    fireEvent.click(screen.getByRole("button", { name: w.hideFor("Session 2") }));
    expect(screen.queryByText("The party crosses the river")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.openFor("Session 2") }));
    await screen.findByText("The party crosses the river");
    expect(api.calls.filter((c) => c === "sessionNotes:100.cmp1:1791000000")).toHaveLength(1);
  });

  it("a player's campaign shows no panel", async () => {
    live.on = true;
    const { container } = panel(mockApi("table"), "100.cmp1.player");
    await new Promise((r) => setTimeout(r, 0));
    expect(container.textContent).toBe("");
  });

  it("a failed open says Couldn't open these notes, and Try again asks again", async () => {
    live.on = true;
    const api = mockApi("table");
    const real = api.sessionNotes!;
    let first = true;
    api.sessionNotes = async (...args) => {
      if (first) {
        first = false;
        throw new ApiError("network");
      }
      return real(...args);
    };
    panel(api);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.openFor("Session 2") }));
    await screen.findByText(w.failed);
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    await waitFor(() => expect(screen.getByText("The party crosses the river")).toBeTruthy());
    expect(screen.queryByText(w.failed)).toBeNull();
  });

  it("the panel never shows a line of the transcript", async () => {
    live.on = true;
    const api = mockApi("table");
    const view = await api.transcript!("200000000000000001.cmp1", 1, "cleaned", 0);
    expect(view.lines.length).toBeGreaterThan(0);
    panel(api, "200000000000000001.cmp1");
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.openFor("Session 2") }));
    await screen.findByText("The party crosses the river");
    for (const line of view.lines) expect(document.body.textContent).not.toContain(line);
  });
});
