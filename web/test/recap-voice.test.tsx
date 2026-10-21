/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Speak instead" under "Tell DMbot what to fix" (#2114; core #2113 part 2). The recording is turned
// into words by the API and deleted there; this page only records after a press, sends the clip on
// Stop, and puts the words in the box to check before Send. It shows only while RECAP_VOICE_LIVE is
// true (false today), so these tests switch it on. The microphone and MediaRecorder are stand-ins.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
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
  vi.useRealTimers();
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
    expect(screen.getByText(`${w.hint} ${w.privacy}`)).toBeTruthy();
    expect(w.privacy).toContain("We keep only the words");
    expect(w.hint).toBe("Up to a minute.");
  });

  it("puts the listening-time cost behind a tappable ⓘ beside the microphone button", async () => {
    await open();
    const info = screen.getByRole("button", { name: w.listeningButton });
    expect(info.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(w.listening)).toBeNull();
    fireEvent.click(info);
    expect(info.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(w.listening)).toBeTruthy();
    expect(info.getAttribute("aria-describedby")).toBe("recap-voice-tip");
    fireEvent.click(info);
    expect(screen.queryByText(w.listening)).toBeNull();
  });

  it("opens the ⓘ on a mouse hover but not on a touch (where the tap does it)", async () => {
    await open();
    const info = screen.getByRole("button", { name: w.listeningButton });
    fireEvent.pointerEnter(info, { pointerType: "touch" });
    expect(screen.queryByText(w.listening)).toBeNull();
    fireEvent.pointerEnter(info, { pointerType: "mouse" });
    expect(screen.getByText(w.listening)).toBeTruthy();
    fireEvent.pointerLeave(info, { pointerType: "mouse" });
    expect(screen.queryByText(w.listening)).toBeNull();
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

  it("keeps words typed while the note is being turned into words", async () => {
    await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    // The words are typed after the clip went, before the answer comes back.
    fireEvent.input(screen.getByLabelText(fix.label), { target: { value: "Typed meanwhile." } });
    await screen.findByText(w.heard, {}, { timeout: 6000 });
    expect((screen.getByLabelText(fix.label) as HTMLTextAreaElement).value).toBe(
      "Typed meanwhile. Change the innkeeper's name to Brannoch.",
    );
  }, 10000);

  it("closes the ⓘ with Escape", async () => {
    await open();
    const info = screen.getByRole("button", { name: w.listeningButton });
    fireEvent.click(info);
    expect(screen.getByText(w.listening)).toBeTruthy();
    fireEvent.keyDown(info, { key: "Escape" });
    expect(screen.queryByText(w.listening)).toBeNull();
  });

  it("takes the cut-off note away when the DM types", async () => {
    const api = mockApi("table");
    api.voiceResult = async () => ({ state: "done" as const, text: "x".repeat(1200) });
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    await screen.findByText(w.cutOff, {}, { timeout: 6000 });
    fireEvent.input(screen.getByLabelText(fix.label), { target: { value: "Short." } });
    expect(screen.queryByText(w.cutOff)).toBeNull();
  }, 10000);

  it("moves focus to Stop when recording starts, and keeps the timer out of the live region", async () => {
    await open();
    speak();
    const button = await screen.findByRole("button", { name: w.stopLabel });
    await waitFor(() => expect(document.activeElement).toBe(button));
    expect(screen.getByText(w.recordingNow).getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText(/^0:00 of 1:00$/).getAttribute("aria-hidden")).toBe("true");
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

  it.each([
    ["bad_clip", "bad-clip"],
    ["too_long", "too_long"],
    ["no_words", "no_words"],
    ["transcribe_failed", "transcribe_failed"],
    ["no_consent", "no-consent"],
    ["not_allowed", "not-allowed"],
    ["not_editable", "not-editable"],
    ["too_soon", "too-soon"],
  ])("a failed run with %s shows its own words, not 'something went wrong'", async (code, key) => {
    const api = mockApi("table");
    api.voiceResult = async () => ({ state: "failed" as const, error: code });
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    const alert = await screen.findByRole("alert", {}, { timeout: 6000 });
    expect(alert.textContent).toBe(w.problem[key]);
    expect(alert.textContent).not.toBe(w.problem.server);
  }, 10000);

  it("an unknown code gets the plain 'something went wrong'", async () => {
    const api = mockApi("table");
    api.voiceResult = async () => ({ state: "failed" as const, error: "constructor" });
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    expect((await screen.findByRole("alert", {}, { timeout: 6000 })).textContent).toBe(w.problem.server);
  }, 10000);

  it("stops by itself after a minute, says so, and sends the clip", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    await vi.advanceTimersByTimeAsync(59_000);
    await waitFor(() => expect(api.calls).toContain(`sendVoice:${draft.ref}:audio/webm:100`));
    expect(screen.getByText(new RegExp(w.atLimit.slice(0, 20)))).toBeTruthy();
    vi.useRealTimers();
  });

  it("says '15 seconds left' once, at 45 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    await vi.advanceTimersByTimeAsync(44_000);
    expect(screen.queryByText(new RegExp(w.warn))).toBeNull();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(screen.getByText(new RegExp(w.warn))).toBeTruthy();
    vi.useRealTimers();
  });

  it("Cancel throws the recording away and sends nothing", async () => {
    const api = await open();
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    fireEvent.click(screen.getByRole("button", { name: w.cancelLabel }));
    expect(await screen.findByText(w.cancelled)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("sendVoice"))).toBe(false);
    expect(stopped).toBe(1);
    expect(screen.getByRole("button", { name: w.speak })).toBeTruthy();
  });

  it("says when heard words are longer than the box and the end was cut", async () => {
    const api = mockApi("table");
    api.voiceResult = async () => ({ state: "done" as const, text: "x".repeat(1200) });
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    expect(await screen.findByText(w.cutOff, {}, { timeout: 6000 })).toBeTruthy();
    expect((screen.getByLabelText(fix.label) as HTMLTextAreaElement).value.length).toBe(1000);
  }, 10000);

  it("shows a no-consent refusal at send time (403) in words, and asks for 32 kbit/s", async () => {
    const seen: unknown[] = [];
    vi.stubGlobal("MediaRecorder", class extends FakeRecorder {
      constructor(_s: unknown, options: unknown) {
        super();
        seen.push(options);
      }
    });
    const api = mockApi("table");
    api.sendVoice = async () => {
      throw new ApiError("no-consent");
    };
    await open(api);
    speak();
    await screen.findByRole("button", { name: w.stopLabel });
    stop();
    expect((await screen.findByRole("alert")).textContent).toBe(w.problem["no-consent"]);
    expect(w.problem["no-consent"]).toContain("Agree to recording in Discord first");
    expect(seen).toEqual([{ mimeType: "audio/webm", audioBitsPerSecond: 32000 }]);
  });

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

describe("over the wire", () => {
  const reply = (status: number, body: unknown, seen: { url: string; init: RequestInit }[] = []) =>
    httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("sends the clip as it is, with its own type, and reads the words", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const clip = new Blob([new Uint8Array(5)], { type: "audio/webm" });
    const sent = await reply(202, { requestId: "v1" }, seen).sendVoice!("1.a.5", clip);
    expect(sent.requestId).toBe("v1");
    expect(seen[0]?.url).toBe("https://api.example.test/me/recaps/1.a.5/voice");
    expect(seen[0]?.init.body).toBe(clip);
    expect((seen[0]?.init.headers as Record<string, string>)["Content-Type"]).toBe("audio/webm");
    const got = await reply(200, { state: "done", text: "Hello" }, seen).voiceResult!("1.a.5", "v1");
    expect(got).toEqual({ state: "done", text: "Hello" });
    expect(seen[1]?.url).toBe("https://api.example.test/me/recaps/1.a.5/voice/v1");
  });

  it.each([
    [413, { error: "too_big" }, "too-big"],
    [415, { error: "bad_type" }, "bad-type"],
    [429, { error: "too_soon" }, "too-soon"],
    [403, { error: "no_consent" }, "no-consent"],
    [422, { error: "bad_clip" }, "bad-clip"],
  ])("reads a %i answer as the %s problem", async (status, body, kind) => {
    const clip = new Blob([new Uint8Array(5)], { type: "audio/webm" });
    await expect(reply(status, body).sendVoice!("r", clip)).rejects.toMatchObject({ kind });
  });
});
