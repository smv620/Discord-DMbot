/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The story threads card on the Names page (#4482): the card's words and ticks, what each press sends, the rename, Skip, Undo, the lines
// (#4501), and the refusals' words.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type StoryThreadSuggestion } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import StoryThreads from "../src/account/StoryThreads";
import { storyThreadsWords as w } from "../src/content/threads-words";

afterEach(() => {
  cleanup();
});

const DM = "100.cmp1";
const QUEST = "a1".repeat(16);
const HOOK = "b2".repeat(16);
const show = (api: ReturnType<typeof mockApi>) => render(<StoryThreads api={api} refId={DM} onSignedOut={() => {}} />);

describe("the story threads card", () => {
  it("shows the card with its words, its people ticked, and the quote", async () => {
    show(mockApi("table"));
    expect(await screen.findByText(w.cardHeading)).toBeTruthy();
    expect(screen.getByText(/a quest, happening now/)).toBeTruthy();
    expect(screen.getByText("What it's about: Bring the heir home before the pass closes.")).toBeTruthy();
    expect(screen.getByText(w.involved)).toBeTruthy();
    expect((screen.getByLabelText("Keegan Velryn · quest giver") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("From Notes, 9 Nov:")).toBeTruthy();
    expect(screen.getByText("Garret needs his heir brought back by the new moon.")).toBeTruthy();
  });

  it("a hook says it is a hook, not started", async () => {
    const api = mockApi("table");
    api.storyThreads = async () => ({
      suggestions: [{ id: HOOK, kind: "hook", name: "Ask about the bell", aim: "A lead nobody has taken up yet.", quote: "", upload: "Notes", people: [], lines: [] }],
    });
    show(api);
    expect(await screen.findByText(/a hook: a lead nobody has taken up yet, not started/)).toBeTruthy();
  });

  it("Keep sends only the ticked people's places", async () => {
    const api = mockApi("table");
    show(api);
    fireEvent.click(await screen.findByLabelText("Garret Velryn · target"));
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    expect(await screen.findByText("✅ Kept: Find the heir is on your 🧵 Threads list.")).toBeTruthy();
    expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "keep", id: QUEST, on: [0], lines: [0, 1] })}`);
  });

  it("Rename sends the DM's own name", async () => {
    const api = mockApi("table");
    show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.rename }));
    fireEvent.input(screen.getByLabelText(w.renameField), { target: { value: "Find the heir, again" } });
    fireEvent.click(screen.getByRole("button", { name: w.renameDo }));
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    expect(await screen.findByText("✅ Kept: Find the heir, again is on your 🧵 Threads list.")).toBeTruthy();
    expect(api.calls.some((c) => c.includes('"name":"Find the heir, again"'))).toBe(true);
  });

  it("Not a story thread, then Undo puts it back", async () => {
    const api = mockApi("table");
    show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.notAStory }));
    expect(await screen.findByText("🚫 Turned down: Find the heir. DMbot won't suggest it again.")).toBeTruthy();
    expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "not", id: QUEST })}`);
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText("↩ Undone: Find the heir is waiting again.")).toBeTruthy();
    expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "undo", not: QUEST })}`);
  });

  it("Keep then Undo uses the batch the keep gave", async () => {
    const api = mockApi("table");
    show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.keepIt }));
    await screen.findByText("✅ Kept: Find the heir is on your 🧵 Threads list.");
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText("↩ Undone: Find the heir is waiting again.")).toBeTruthy();
    expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "undo", keep: 1 })}`);
  });

  it("a gone answer says so and shows the list again", async () => {
    const api = mockApi("table");
    api.changeStoryThreads = async () => {
      throw new ApiError("gone");
    };
    show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.keepIt }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
  });

  it("a failed press after a keep shows its error and no Undo", async () => {
    const api = mockApi("table");
    show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.keepIt }));
    await screen.findByText("✅ Kept: Find the heir is on your 🧵 Threads list.");
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
    api.changeStoryThreads = async () => {
      throw new ApiError("server");
    };
    fireEvent.click(screen.getByRole("button", { name: w.notAStory }));
    expect(await screen.findByText(w.wrong)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("a DM-only refusal shows its sentence", async () => {
    const api = mockApi("table");
    api.storyThreads = async () => {
      throw new ApiError("not-allowed");
    };
    show(api);
    expect(await screen.findByText(w.notDm)).toBeTruthy();
  });

  it("Skip shows the next one and changes nothing", async () => {
    const api = mockApi("table");
    show(api);
    await screen.findByText(w.cardHeading);
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    expect(await screen.findByText("Ask about the bell")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("changeStoryThreads"))).toBe(false);
  });

  it("an empty list shows the nothing-to-check line", async () => {
    const api = mockApi("table");
    api.storyThreads = async () => ({ suggestions: [] });
    show(api);
    expect(await screen.findByText(w.none)).toBeTruthy();
  });

  it("shows each line with its words and what it is, ticked; Untick leaves one out of the keep", async () => {
    const api = mockApi("table");
    show(api);
    expect(((await screen.findByLabelText("Find the heir · a step")) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText("The old pass · a branch"));
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    await screen.findByText("✅ Kept: Find the heir is on your 🧵 Threads list.");
    expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "keep", id: QUEST, on: [0, 1], lines: [0] })}`);
  });

  it("a suggestion with no lines shows no heading and sends lines []", async () => {
    const api = mockApi("table");
    api.storyThreads = async () => ({
      suggestions: [{ id: QUEST, kind: "quest", name: "Find the heir", aim: "x", quote: "", upload: "Notes", people: [], lines: [] } as StoryThreadSuggestion],
    });
    show(api);
    await screen.findByText(w.cardHeading);
    expect(screen.queryByText(w.whatsIn)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    await waitFor(() => expect(api.calls).toContain(`changeStoryThreads:${DM}:${JSON.stringify({ action: "keep", id: QUEST, on: [], lines: [] })}`));
  });
});
