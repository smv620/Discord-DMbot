/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Speak instead" under "Tell DMbot what to fix" (#2114; core #2113 part 2). The recording is turned
// into words by the API and deleted there; this page only records after a press, sends the clip on
// Stop, and puts the words in the box to check before Send. It shows only while RECAP_VOICE_LIVE is
// true (false today), so these tests switch it on. The microphone and MediaRecorder are stand-ins.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { fixWords as fix } from "../src/content/recap-fix";
import { voiceWords as w } from "../src/content/recap-voice";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_FIX_LIVE() {
      return true;
    },
    get RECAP_VOICE_LIVE() {
      return live.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

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

beforeEach(() => {
  clipBytes = 100;
  stopped = 0;
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track] })) },
  });
});
afterEach(() => {
  cleanup();
  live.on = false;
  vi.unstubAllGlobals();
});

const draft = mockRecaps()[0]!;
async function open(api = mockApi("table")) {
  live.on = true;
  render(<Recaps api={api} path={`/account/recaps/${draft.ref}`} />);
  await screen.findByRole("heading", { name: fix.heading });
  return api;
}
const speak = () => fireEvent.click(screen.getByRole("button", { name: w.speak }));
const stop = () => fireEvent.click(screen.getByRole("button", { name: w.stopLabel }));

describe("the switch", () => {
  it("can only be on together with the typed box", async () => {
    const real = await vi.importActual<typeof import("../src/content/recap-words")>(
      "../src/content/recap-words",
    );
    expect(!real.RECAP_VOICE_LIVE || real.RECAP_FIX_LIVE).toBe(true);
  });

  it("is not on the page while it is off", async () => {
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${draft.ref}`} />);
    await screen.findByRole("heading", { name: fix.heading });
    expect(screen.queryByRole("button", { name: w.speak })).toBeNull();
  });
});

describe("speaking a note", () => {
  it("asks for the microphone only on the press, then puts the words in the box without sending them", async () => {
    const api = await open();
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    speak();
    await screen.findByText(w.recordingNow);
    stop();
    await screen.findByText(w.heard, {}, { timeout: 6000 });
    expect((screen.getByLabelText(fix.label) as HTMLTextAreaElement).value).toBe(
      "Change the innkeeper's name to Brannoch.",
    );
    expect(api.calls).toContain(`sendVoice:${draft.ref}:audio/webm:100`);
    // The words are only in the box: the DM presses Send.
    expect(api.calls.some((c) => c.startsWith("sendFix"))).toBe(false);
    // The microphone is let go as soon as the recording stops.
    expect(stopped).toBe(1);
  }, 10000);

  it("says in plain sight that the recording is deleted, that only the words are kept, and what it costs", async () => {
    await open();
    expect(screen.getByText(w.privacy)).toBeTruthy();
    expect(w.privacy).toContain("listening time");
    expect(w.privacy).toContain("We keep only the words");
  });

  it("keeps words already typed and adds the heard words after them", async () => {
    await open();
    fireEvent.input(screen.getByLabelText(fix.label), { target: { value: "Also shorten part two." } });
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    await screen.findByText(w.heard, {}, { timeout: 6000 });
    expect((screen.getByLabelText(fix.label) as HTMLTextAreaElement).value).toBe(
      "Also shorten part two. Change the innkeeper's name to Brannoch.",
    );
  }, 10000);

  it("moves focus to Stop when recording starts, and keeps the timer out of the live region", async () => {
    await open();
    speak();
    const button = await screen.findByRole("button", { name: w.stopLabel });
    await waitFor(() => expect(document.activeElement).toBe(button));
    expect(screen.getByText(w.recordingNow).getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText(/^0:00 of 2:00$/).getAttribute("aria-hidden")).toBe("true");
  });

  it("says what to do when the browser won't let it use the microphone", async () => {
    await open();
    (navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("no"), { name: "NotAllowedError" }),
    );
    speak();
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem.denied);
    // Typing still works.
    expect(screen.getByLabelText(fix.label)).toBeTruthy();
  });

  it("says when there is no microphone", async () => {
    await open();
    (navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("none"), { name: "NotFoundError" }),
    );
    speak();
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["no-mic"]);
  });

  it("says to type instead where the browser can't record webm or ogg", async () => {
    vi.stubGlobal("MediaRecorder", class { static isTypeSupported = () => false; });
    await open();
    expect(screen.getByText(w.unsupported)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.speak })).toBeNull();
  });

  it.each([
    [7, "no-consent"],
    [13, "no_words"],
  ])("a clip of %i bytes ends in words, not a code", async (bytes, key) => {
    clipBytes = bytes;
    await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    const alert = await screen.findByRole("alert", {}, { timeout: 6000 });
    expect(alert.textContent).toBe(w.problem[key]);
    expect(screen.queryByText(/no_consent|no_words/)).toBeNull();
  }, 10000);

  it("shows a refusal from the API in words", async () => {
    const api = mockApi("table");
    api.sendVoice = async () => {
      throw new ApiError("too-soon");
    };
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["too-soon"]);
  });

  it("lets go of the microphone and sends nothing when the page is left", async () => {
    const api = await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    cleanup();
    expect(stopped).toBe(1);
    expect(api.calls.some((c) => c.startsWith("sendVoice"))).toBe(false);
  });
});
