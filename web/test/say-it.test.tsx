/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "How to say it" (#2317): 💡 Suggest, 🎤 Say it (record, stop, play back, record again, use this), and
// 🔊 Hear it, beside a name's "said as" box. The microphone, MediaRecorder and speech voice are stand-ins.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { useState } from "preact/hooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SayIt from "../src/account/SayIt";
import { CLIP_SECONDS, sayIt as w } from "../src/content/say-it-words";

let clipBytes = 100;
let stopped = 0;
class FakeRecorder {
  static isTypeSupported(type: string): boolean {
    return type === "audio/webm";
  }
  state: "inactive" | "recording" = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start(): void {
    this.state = "recording";
  }
  stop(): void {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob([new Uint8Array(clipBytes)]) });
    this.onstop?.();
  }
}
const track = { stop: () => void (stopped += 1) };
const spoken: string[] = [];
const revoked: string[] = [];

beforeEach(() => {
  clipBytes = 100;
  stopped = 0;
  spoken.length = 0;
  revoked.length = 0;
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  vi.stubGlobal("SpeechSynthesisUtterance", class { constructor(public text: string) {} });
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: { cancel: vi.fn(), speak: (u: { text: string }) => void spoken.push(u.text) },
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track] })) },
  });
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:fake-${(n += 1)}`);
  URL.revokeObjectURL = vi.fn((url: string) => void revoked.push(url));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "speechSynthesis");
});

const REF = "100.cmp1";
const NAME_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

function Box({ api, start = "" }: { api: AccountApi; start?: string }) {
  const [value, setValue] = useState(start);
  return (
    <>
      <input aria-label="said as" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
      <SayIt api={api} campaignRef={REF} nameId={NAME_ID} name="Ka'zeth" value={value} onValue={(t) => setValue(t)} />
    </>
  );
}
const field = () => screen.getByLabelText("said as") as HTMLInputElement;
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const apiWithName = () => {
  const api = mockApi("table");
  return api;
};

describe("💡 Suggest", () => {
  it("fills the box with the API's suggestion, unsaved, and the DM can still change it", async () => {
    const api = apiWithName();
    api.suggestSaidAs = async () => "KAH-zeth";
    render(<Box api={api} />);
    press(w.suggest);
    await waitFor(() => expect(field().value).toBe("KAH-zeth"));
    expect(screen.getByText(w.suggested)).toBeTruthy();
    fireEvent.input(field(), { target: { value: "Kah-zeth" } });
    expect(field().value).toBe("Kah-zeth");
    expect(api.calls.some((c) => c.startsWith("saveSaidAs"))).toBe(false); // nothing was saved
  });

  it("says so when there is no suggestion, and what went wrong when the call fails", async () => {
    const api = apiWithName();
    api.suggestSaidAs = async () => "  ";
    render(<Box api={api} start="mine" />);
    press(w.suggest);
    expect((await screen.findByRole("alert")).textContent).toBe(w.suggestNone);
    expect(field().value).toBe("mine"); // the DM's words are left alone
    api.suggestSaidAs = async () => {
      throw new ApiError("not-allowed");
    };
    press(w.suggest);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.problem["not-allowed"]));
  });

  it("is not shown by an API older than #2316", () => {
    const api = apiWithName();
    delete api.suggestSaidAs;
    render(<Box api={api} />);
    expect(screen.queryByRole("button", { name: w.suggest })).toBeNull();
  });
});

describe("🎤 Say it", () => {
  const record = async () => {
    press(w.sayIt);
    await screen.findByRole("button", { name: w.stop });
  };

  it("asks for the microphone only on the press", async () => {
    render(<Box api={apiWithName()} />);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    await record();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("records, stops, plays back locally before anything is sent, then Use this sends it and fills the box", async () => {
    const api = apiWithName();
    render(<Box api={api} />);
    await record();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: w.stop })));
    press(w.stop);
    // Playback is a local blob; nothing has gone to the API yet.
    const audio = (await screen.findByLabelText(w.playbackLabel("Ka'zeth"))) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toMatch(/^blob:fake-/);
    expect(api.calls.some((c) => c.startsWith("respellFromVoice"))).toBe(false);
    expect(stopped).toBe(1); // the microphone is let go
    press(w.useThis);
    await waitFor(() => expect(field().value).not.toBe(""));
    expect(api.calls.some((c) => c === `respellFromVoice:${REF}:${NAME_ID}:audio/webm`)).toBe(true);
    expect(screen.getByText(w.filled)).toBeTruthy();
    // The recording is let go of once used; the box holds the unsaved respelling.
    expect(revoked.length).toBeGreaterThan(0);
    expect(api.calls.some((c) => c.startsWith("saveSaidAs"))).toBe(false);
  });

  it("Record again throws the first recording away without sending it", async () => {
    const api = apiWithName();
    render(<Box api={api} />);
    await record();
    press(w.stop);
    await screen.findByLabelText(w.playbackLabel("Ka'zeth"));
    press(w.again);
    await screen.findByRole("button", { name: w.stop });
    expect(revoked).toContain("blob:fake-1");
    expect(api.calls.some((c) => c.startsWith("respellFromVoice"))).toBe(false);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
  });

  it("stops by itself at the limit", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Box api={apiWithName()} />);
    await record();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLIP_SECONDS * 1000);
    });
    expect(await screen.findByLabelText(w.playbackLabel("Ka'zeth"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.stop })).toBeNull();
  });

  it("says to type it when the server has this switched off, and keeps the recording to try again", async () => {
    const api = apiWithName();
    api.respellFromVoice = async () => {
      throw new ApiError("voice-off");
    };
    render(<Box api={api} start="mine" />);
    await record();
    press(w.stop);
    await screen.findByLabelText(w.playbackLabel("Ka'zeth"));
    press(w.useThis);
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["voice-off"]);
    expect(field().value).toBe("mine");
    expect(screen.getByRole("button", { name: w.useThis })).toBeTruthy();
  });

  it("says to type it when the microphone is refused", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(async () => Promise.reject(new Error("denied"))) },
    });
    render(<Box api={apiWithName()} />);
    press(w.sayIt);
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["mic-refused"]);
    expect(screen.getByRole("button", { name: w.sayIt })).toBeTruthy();
  });

  it("says so for an empty recording, and sends nothing", async () => {
    clipBytes = 0;
    const api = apiWithName();
    render(<Box api={api} />);
    await record();
    press(w.stop);
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["bad-clip"]);
    expect(api.calls.some((c) => c.startsWith("respellFromVoice"))).toBe(false);
  });

  it("lets go of the microphone when the page is left mid-recording, sending nothing", async () => {
    const api = apiWithName();
    const { unmount } = render(<Box api={api} />);
    await record();
    unmount();
    expect(stopped).toBe(1);
    expect(api.calls.some((c) => c.startsWith("respellFromVoice"))).toBe(false);
  });

  it("is not shown where the browser can't record, or by an older API", () => {
    vi.stubGlobal("MediaRecorder", undefined);
    render(<Box api={apiWithName()} />);
    expect(screen.queryByRole("button", { name: w.sayIt })).toBeNull();
    cleanup();
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const api = apiWithName();
    delete api.respellFromVoice;
    render(<Box api={api} />);
    expect(screen.queryByRole("button", { name: w.sayIt })).toBeNull();
  });

  it("only one name at a time uses the microphone", async () => {
    const api = apiWithName();
    render(
      <>
        <Box api={api} />
        <Box api={api} />
      </>,
    );
    const [first, second] = screen.getAllByRole("button", { name: w.sayIt });
    fireEvent.click(first!);
    await screen.findByRole("button", { name: w.stop });
    fireEvent.click(second!);
    await act(async () => undefined);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });
});

describe("🔊 Hear it", () => {
  it("reads the box aloud with the browser's own voice", () => {
    render(<Box api={apiWithName()} start="KAH-zeth" />);
    press(w.hear);
    expect(spoken).toEqual(["KAH-zeth"]);
  });

  it("is hidden when the box is empty, and without speechSynthesis", () => {
    render(<Box api={apiWithName()} />);
    expect(screen.queryByRole("button", { name: w.hear })).toBeNull();
    cleanup();
    Reflect.deleteProperty(window, "speechSynthesis");
    render(<Box api={apiWithName()} start="KAH-zeth" />);
    expect(screen.queryByRole("button", { name: w.hear })).toBeNull();
  });
});

describe("the words", () => {
  it("say what happens to the recording", () => {
    render(<Box api={apiWithName()} />);
    expect(screen.getByText(w.how)).toBeTruthy();
    expect(w.how).toContain("deleted");
  });
});
