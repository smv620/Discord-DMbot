/** @jsxImportSource preact */
// @vitest-environment happy-dom
// 🔊 Hear it in the recap narrator's voice (#3807, behind HEAR_NARRATOR_LIVE): the words in the box are sent to the bot, the
// clip that comes back is played, the wait shows DMbot working, the words going to the voice service are said once, and when
// narration is off the browser's own voice reads it and the page says so. With the switch off nothing here is asked.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { useState } from "preact/hooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SayIt from "../src/account/SayIt";
import { sayIt as w } from "../src/content/say-it-words";
import { savingWait } from "../src/content/work-progress";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/hear-narrator-switch", () => ({
  get HEAR_NARRATOR_LIVE() {
    return live.on;
  },
}));

const spoken: string[] = [];
const played: string[] = [];
beforeEach(() => {
  spoken.length = 0;
  played.length = 0;
  live.on = true;
  vi.stubGlobal("SpeechSynthesisUtterance", class { constructor(public text: string) {} });
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: { cancel: vi.fn(), speak: (u: { text: string }) => void spoken.push(u.text) },
  });
  vi.stubGlobal(
    "Audio",
    class {
      onended: (() => void) | null = null;
      constructor(public src: string) {}
      pause(): void {}
      async play(): Promise<void> {
        played.push(this.src);
      }
    },
  );
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:clip-${(n += 1)}`);
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "speechSynthesis");
});

const NAME_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
function Box({ api, campaign = "100.cmp1", start = "KAH-zeth" }: { api: AccountApi; campaign?: string; start?: string }) {
  const [value, setValue] = useState(start);
  return <SayIt api={api} campaignRef={campaign} nameId={NAME_ID} name="Ka'zeth" value={value} onValue={(t) => setValue(t)} />;
}
const press = () => fireEvent.click(screen.getByRole("button", { name: w.hear }));

describe("🔊 Hear it in the narrator's voice", () => {
  it("sends the words in the box, plays the clip that comes back, and does not use the browser's voice", async () => {
    const api = mockApi("table");
    render(<Box api={api} />);
    press();
    await waitFor(() => expect(played).toEqual(["blob:clip-1"]));
    expect(api.calls).toContain(`hearSaidAs:100.cmp1:${NAME_ID}:KAH-zeth`);
    expect(spoken).toEqual([]);
  });

  it("says once, near the button, that the words go to DMbot's voice service", () => {
    render(<Box api={mockApi("table")} />);
    expect(screen.getAllByText(w.hearPrivacy)).toHaveLength(1);
  });

  it("shows DMbot working while the clip is made, then clears it", async () => {
    const api = mockApi("table");
    let finish: (b: Blob) => void = () => {};
    api.hearSaidAs = () => new Promise<Blob>((go) => (finish = go));
    render(<Box api={api} />);
    press();
    expect(await screen.findByText(w.hearStage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    finish(new Blob([new Uint8Array(44)], { type: "audio/wav" }));
    await waitFor(() => expect(screen.queryByText(w.hearStage)).toBeNull());
    expect(played).toHaveLength(1);
  });

  it("says the wait has stalled after a minute", async () => {
    const api = mockApi("table");
    api.hearSaidAs = () => new Promise<Blob>(() => {});
    render(<Box api={api} />);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], shouldAdvanceTime: true });
    press();
    await vi.advanceTimersByTimeAsync(70_000);
    expect(screen.getByText(w.hearQuiet)).toBeTruthy();
    expect(savingWait.quiet).not.toBe(w.hearQuiet);
  });

  it("falls back to the browser's voice when narration is off, and says so", async () => {
    render(<Box api={mockApi("table")} campaign="100.cmp1.hear-off" />);
    press();
    await waitFor(() => expect(spoken).toEqual(["KAH-zeth"]));
    expect(screen.getAllByText(w.browserVoice).length).toBeGreaterThan(0);
    expect(played).toEqual([]);
    // Pressed again, it goes straight to the browser's voice, without asking the bot again.
    const api = mockApi("table");
    cleanup();
    render(<Box api={api} campaign="100.cmp1.hear-off" />);
    press();
    await waitFor(() => expect(spoken.length).toBe(2));
    press();
    await waitFor(() => expect(spoken.length).toBe(3));
    expect(api.calls.filter((c) => c.startsWith("hearSaidAs"))).toHaveLength(1);
  });

  it("says plainly when it is pressed too often, and plays nothing", async () => {
    render(<Box api={mockApi("table")} campaign="100.cmp1.hear-soon" />);
    press();
    expect((await screen.findAllByText(w.problem["too-soon"] as string)).length).toBeGreaterThan(0);
    expect(played).toEqual([]);
    expect(spoken).toEqual([]);
  });

  it("says plainly when a refusal has no browser voice to fall back on", async () => {
    Reflect.deleteProperty(window, "speechSynthesis");
    vi.stubGlobal("SpeechSynthesisUtterance", undefined);
    render(<Box api={mockApi("table")} campaign="100.cmp1.hear-off" />);
    press();
    expect((await screen.findAllByText(w.problem["voice-off"] as string)).length).toBeGreaterThan(0);
  });

  it("with the switch off reads it with the browser's voice and asks the bot for nothing", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<Box api={api} />);
    expect(screen.queryByText(w.hearPrivacy)).toBeNull();
    press();
    expect(spoken).toEqual(["KAH-zeth"]);
    expect(api.calls.some((c) => c.startsWith("hearSaidAs"))).toBe(false);
  });

  it("does not offer Hear it for an empty box", () => {
    render(<Box api={mockApi("table")} start="" />);
    expect(screen.queryByRole("button", { name: w.hear })).toBeNull();
    expect(screen.queryByText(w.hearPrivacy)).toBeNull();
  });

  it("a name that has left the campaign says so", async () => {
    const api = mockApi("table");
    api.hearSaidAs = async () => {
      throw new ApiError("not-found");
    };
    render(<Box api={api} />);
    press();
    expect((await screen.findAllByText(w.problem["not-found"] as string)).length).toBeGreaterThan(0);
  });
  it("asks once when pressed twice in the same tick", async () => {
    const api = mockApi("table");
    render(<Box api={api} />);
    const b = screen.getByRole("button", { name: w.hear });
    fireEvent.click(b);
    fireEvent.click(b);
    await waitFor(() => expect(played).toHaveLength(1));
    expect(api.calls.filter((c) => c.startsWith("hearSaidAs"))).toHaveLength(1);
  });

  it("says so when the browser refuses to play a clip that was made", async () => {
    vi.stubGlobal(
      "Audio",
      class {
        onended = null;
        pause(): void {}
        async play(): Promise<void> {
          throw new Error("NotAllowedError");
        }
      },
    );
    render(<Box api={mockApi("table")} />);
    press();
    expect((await screen.findAllByText(w.problem["play-refused"] as string)).length).toBeGreaterThan(0);
  });
});
