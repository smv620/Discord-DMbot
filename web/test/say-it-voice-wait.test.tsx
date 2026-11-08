/** @jsxImportSource preact */
// @vitest-environment happy-dom
// 🎤 Say it when the bot does the listening (#3828; core #3827): a clip the bot answers late is waited for with the shared
// wait panel (where the clip is, how long), a clip stuck behind a recording table says the bot is busy and offers
// "Type it instead", and a refusal leaves the clip and the box ready.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { useState } from "preact/hooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SayIt from "../src/account/SayIt";
import { sayIt as w } from "../src/content/say-it-words";

class FakeRecorder {
  static isTypeSupported = (t: string): boolean => t === "audio/webm";
  state = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start(): void {
    this.state = "recording";
  }
  stop(): void {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob([new Uint8Array(100)]) });
    this.onstop?.();
  }
}
beforeEach(() => {
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop() {} }] })) },
  });
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:v-${(n += 1)}`);
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const NAME_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
function Box({ api, campaign }: { api: AccountApi; campaign: string }) {
  const [value, setValue] = useState("");
  return (
    <>
      <input aria-label="said as" id="f" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
      <SayIt api={api} campaignRef={campaign} nameId={NAME_ID} name="Ka'zeth" value={value} onValue={(t) => setValue(t)} fieldId="f" />
    </>
  );
}
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
async function sendClip(): Promise<void> {
  press(w.sayIt);
  await screen.findByRole("button", { name: w.stop });
  press(w.stop);
  await screen.findByRole("button", { name: w.useThis });
  press(w.useThis);
}

describe("🎤 Say it, waiting for the bot", () => {
  it("shows where the clip is while the bot works, then fills the box", async () => {
    const api = mockApi("table");
    render(<Box api={api} campaign="100.cmp1.voice-slow" />);
    await sendClip();
    expect(await screen.findByText(w.voiceWaiting)).toBeTruthy();
    expect(await screen.findByText(w.voiceWorking)).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("said as") as HTMLInputElement).value).not.toBe(""), { timeout: 4000 });
  });

  it("says the bot is busy at the table after 30 seconds, and Type it instead stops the wait", async () => {
    const api = mockApi("table");
    render(<Box api={api} campaign="100.cmp1.voice-busy" />);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await sendClip();
    expect(await screen.findByText(w.voiceWaiting)).toBeTruthy();
    expect(screen.queryByText(w.voiceBusy)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31000);
    });
    expect(screen.getByText(w.voiceBusy)).toBeTruthy();
    press(w.typeInstead);
    expect(screen.queryByText(w.voiceWaiting)).toBeNull();
    expect(document.activeElement).toBe(screen.getByLabelText("said as"));
  });

  it("a refusal says so in the existing words and leaves the clip to try again", async () => {
    const api = mockApi("table");
    api.respellFromVoice = async () => {
      throw new ApiError("voice-off");
    };
    render(<Box api={api} campaign="100.cmp1" />);
    await sendClip();
    expect((await screen.findAllByText(w.problem["voice-off"] as string)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: w.useThis })).toBeTruthy();
  });
});
