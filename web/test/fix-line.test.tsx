/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Fix this line" on a session's Cleaned transcript (#2858; core #2882). Built against the pretend API, and only while
// FIX_LINE_LIVE is on (false today), so these tests switch it on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, FixAnswer } from "../src/account/api";
import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { fixLineWords as w } from "../src/content/fix-line-words";
import { text } from "../src/content/transcripts";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/fix-line-switch", async (original) => {
  const real = await original<typeof import("../src/content/fix-line-switch")>();
  return {
    ...real,
    get FIX_LINE_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const REF = "200000000000000001.cmp1";
const CLEANED = `/account/transcripts/${REF}/2`;
const HEARD = `/account/transcripts/${REF}/2?version=heard`;

const fixButtons = () => screen.queryAllByRole("button", { name: /^Fix this line/ });

type Mock = AccountApi & { calls: string[] };
async function openCleaned(api: Mock = mockApi("table") as Mock, path = CLEANED) {
  live.on = true;
  render(<Transcripts api={api} path={path} />);
  await waitFor(() => expect(fixButtons().length).toBeGreaterThan(0));
  return api;
}

describe("switched off", () => {
  it("no button while the switch is off, even where the API lists a fixable line", async () => {
    live.on = false;
    render(<Transcripts api={mockApi("table")} path={CLEANED} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(fixButtons()).toHaveLength(0);
  });

  it("no button when the API has no fix call, even with the switch on", async () => {
    const api = mockApi("table");
    delete (api as { fixTranscriptLine?: unknown }).fixTranscriptLine;
    delete (api as { fixAnswer?: unknown }).fixAnswer;
    live.on = true;
    render(<Transcripts api={api} path={CLEANED} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(fixButtons()).toHaveLength(0);
  });
});

describe("the button", () => {
  it("no button on a line the API did not list as fixable, and none on the as-heard reading", async () => {
    await openCleaned();
    // Only the spoken line is listed; the off-topic marker and the first line get no button.
    expect(fixButtons()).toHaveLength(1);
    expect(screen.queryAllByRole("button", { name: /^Fix this line/ })[0]!.closest("li")!.textContent).toContain("Ka'zeth");
    cleanup();
    live.on = true;
    render(<Transcripts api={mockApi("table")} path={HEARD} />);
    await new Promise((r) => setTimeout(r, 80));
    expect(fixButtons()).toHaveLength(0);
  });

  it("a player sees the DM-will-decide line; a DM sees the changes-at-once line", async () => {
    await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    expect(screen.getByText(w.dmNote)).toBeTruthy();
    expect(screen.queryByText(w.playerNote)).toBeNull();
  });

  it("the box is prefilled with the words after the speaker label", async () => {
    await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    const box = screen.getByLabelText(w.label) as HTMLTextAreaElement;
    // (the cleaned reading spells the name as DMbot heard it, "Ka'zeth")
    expect(box.value).toBe("Ka'zeth draws his sword.");
  });

  it("the box is empty when the pattern is not found", () => {
    expect(w.wordsOf("[0:01:02] (Speaker 1) [14s of off-topic chat skipped]")).toBe("");
  });

  it("Send is off for an empty box and for 201 characters; the counter shows n of 200", async () => {
    await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    const box = screen.getByLabelText(w.label) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "" } });
    expect((screen.getByRole("button", { name: w.send }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(box, { target: { value: "x".repeat(201) } });
    expect((screen.getByRole("button", { name: w.send }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(w.counter(201))).toBeTruthy();
    fireEvent.input(box, { target: { value: "x".repeat(200) } });
    expect((screen.getByRole("button", { name: w.send }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(w.counter(200))).toBeTruthy();
  });
});

describe("the outcome", () => {
  it("a suggestion ends with \"Sent to your DM to check.\"", async () => {
    const api = await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    const box = screen.getByLabelText(w.label) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "Kazeth draws his blade." } });
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.sentToDm, {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("fixTranscriptLine:"))).toBe(true);
  });

  it("a rewrite ends with \"Changed.\" and the undo sentence only when undo is true", async () => {
    const api = await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    const box = screen.getByLabelText(w.label) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "Did you see the match!" } });
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(`${w.changed}${w.undoNote}`, {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("fixTranscriptLine:"))).toBe(true);
  });

  it("each refusal shows its words", async () => {
    const api = mockApi("table");
    api.fixTranscriptLine = async () => {
      throw new ApiError("not-yours");
    };
    await openCleaned(api);
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.refusals.not_yours!)).toBeTruthy();
  });

  it("each failure reason shows its words", async () => {
    const api = mockApi("table");
    const base = api.fixAnswer!.bind(api);
    api.fixAnswer = async (ref, number, answer): Promise<FixAnswer> => {
      const got = await base(ref, number, answer);
      return { ...got, state: "failed", result: null };
    };
    await openCleaned(api);
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.failures.failed!, {}, { timeout: 8000 })).toBeTruthy();
  });

  it("the wait panel shows the stage words", async () => {
    const api = await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await waitFor(() => expect(screen.getByText(w.stage.waiting)).toBeTruthy());
    expect(api.calls.some((c) => c.startsWith("fixTranscriptLine:"))).toBe(true);
  });

  it("only one fix runs at a time", async () => {
    await openCleaned();
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await waitFor(() => expect(screen.getByText(w.stage.waiting)).toBeTruthy());
    for (const b of fixButtons()) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("the line for a page loaded with \"Show more\" gets its button on the right line", async () => {
    // A second page is given by wrapping the API's transcript call (the pretend API has one page).
    const api = mockApi("table");
    const base = api.transcript!.bind(api);
    api.transcript = async (ref, number, version, offset) => {
      const first = await base(ref, number, version);
      if (offset === undefined) return { ...first, next: 1 };
      return {
        ...first,
        lines: ["[0:02:00] (Speaker 1) {Cerric}: The second page speaks."],
        fixable: [{ line: 0, speaker: "Speaker 1", startedMs: 120_000 }],
        skips: [],
        next: null,
      };
    };
    await openCleaned(api);
    fireEvent.click(screen.getByRole("button", { name: text.showMore }));
    await waitFor(() => expect(screen.getByText(/The second page speaks/)).toBeTruthy());
    // The second page's line is the fifth line of the page (after the first page's lines), and it gets the button.
    const button = await waitFor(() => {
      const b = fixButtons().find((x) => x.closest("li")?.textContent?.includes("The second page speaks"));
      expect(b).toBeTruthy();
      return b!;
    });
    expect(button).toBeTruthy();
    expect(fixButtons().filter((x) => x.closest("li")?.textContent?.includes("Ka'zeth"))).toHaveLength(1);
  });

  it("the real client sends a fix route's codes as the fix words' kinds, and a bare code from elsewhere stays generic", async () => {
    const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const original = globalThis.fetch;
    globalThis.fetch = (async () => reply(409, { error: "open" })) as unknown as typeof fetch;
    try {
      const api = httpApi("https://api.example.test");
      await expect(api.fixTranscriptLine!("100.cmp1", 2, { speaker: "Speaker 2", startedMs: 40_000 }, "x")).rejects.toMatchObject({ kind: "fix-open" });
      // The same code on a route that is not a fix route is not the fix words.
      await expect(api.chooseSheet!("100.cmp1", "e1", { action: "forget" })).rejects.toMatchObject({ kind: "server" });
    } finally {
      globalThis.fetch = original;
    }
  });

  it("a rewrite that cannot be undone ends with \"Changed.\" and no undo sentence", async () => {
    const api = mockApi("table");
    const base = api.fixAnswer!.bind(api);
    api.fixAnswer = async (ref, number, answer): Promise<FixAnswer> => ({ ...(await base(ref, number, answer)), undo: false });
    await openCleaned(api);
    fireEvent.click(fixButtons()[0]!);
    fireEvent.input(screen.getByLabelText(w.label), { target: { value: "Did you see the match!" } });
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.changed, {}, { timeout: 8000 })).toBeTruthy();
    expect(screen.queryByText(w.undoNote, { exact: false })).toBeNull();
  });

  it("a failure shows the words for the reason the answer gives", async () => {
    const api = mockApi("table");
    api.fixAnswer = async (): Promise<FixAnswer> => ({ stage: "failed", state: "failed", mode: "suggest", result: null, undo: false, reason: "line_changed" });
    await openCleaned(api);
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.failures.line_changed!, {}, { timeout: 8000 })).toBeTruthy();
  });

  it("each refusal the API can give shows its own words", async () => {
    for (const [code, kind] of [["open", "fix-open"], ["many", "fix-many"]] as const) {
      cleanup();
      const api = mockApi("table");
      api.fixTranscriptLine = async () => {
        throw new ApiError(kind);
      };
      await openCleaned(api);
      fireEvent.click(fixButtons()[0]!);
      fireEvent.click(screen.getByRole("button", { name: w.send }));
      expect(await screen.findByText(w.failures[code]!)).toBeTruthy();
    }
  });

  it("a network blip while it waits does not end the wait; the fix keeps going", async () => {
    const api = mockApi("table");
    const base = api.fixAnswer!.bind(api);
    let blips = 0;
    api.fixAnswer = async (ref, number, answer): Promise<FixAnswer> => {
      if (blips < 1) {
        blips += 1;
        throw new ApiError("network");
      }
      return base(ref, number, answer);
    };
    await openCleaned(api);
    fireEvent.click(fixButtons()[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.sentToDm, {}, { timeout: 8000 })).toBeTruthy();
    expect(screen.queryByText(w.refusals.network!)).toBeNull();
  });
});
