/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Remake the voice" after editing a recap's words (#2630): the page asks what making the audio again
// would do and says it in plain words (only the voice, or everything and why), shows how far the voice
// has got, and gives the editor a "Fix a name everywhere" box. All of it shows only while
// RECAP_REMAKE_LIVE is true (false today), so these tests switch it on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  httpApi,
  type AccountApi,
  type Recap,
  type RecapAudioState,
  type RecapRemake,
} from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { replaceName } from "../src/account/RecapFixName";
import { errors } from "../src/content/recaps";
import { fixNameWords as fw, remakeWords as w } from "../src/content/recap-panel";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_REMAKE_LIVE() {
      return live.on;
    },
    get RECAP_AUDIO_LIVE() {
      return true;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const draft = mockRecaps()[0]!;
const path = `/account/recaps/${draft.ref}`;

const stale: RecapAudioState = { state: "ready", error: "", current: false, canAskAgain: true };

/** The pretend API, with this recap's audio as given and the remake answer as given. */
function withAudio(
  audio: RecapAudioState,
  remake?: RecapRemake | "missing" | "fails",
): ReturnType<typeof mockApi> {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  api.recap = async (ref: string): Promise<Recap> => ({ ...(await read(ref)), audio });
  if (remake === "missing") delete (api as AccountApi).recapAudioRemake;
  else if (remake === "fails")
    api.recapAudioRemake = async () => {
      throw new ApiError("server");
    };
  else if (remake)
    api.recapAudioRemake = async (ref: string) => {
      api.calls.push(`recapAudioRemake:${ref}`);
      return remake;
    };
  return api;
}

async function open(api: ReturnType<typeof withAudio>) {
  render(<Recaps api={api} path={path} />);
  await screen.findByRole("heading", { name: "Narration and music" });
}

describe("while it is off", () => {
  it("shows the old button and no sentence", async () => {
    live.on = false;
    const api = withAudio(stale);
    await open(api);
    expect(screen.getByRole("button", { name: "Make the audio again" })).toBeTruthy();
    expect(screen.queryByText(/Only the voice is made again/)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("recapAudioRemake"))).toBe(false);
  });
});

describe("what would be made again", () => {
  it("only the voice: says how many paragraphs, and the button says so", async () => {
    live.on = true;
    const api = withAudio(stale, { how: "voice", why: "", changed: 1, total: 3 });
    await open(api);
    expect(await screen.findByText(w.voice(1, 3))).toBeTruthy();
    expect(w.voice(1, 3)).toContain("1 of 3 paragraphs");
    fireEvent.click(screen.getByRole("button", { name: w.voiceButton }));
    await waitFor(() => expect(api.calls).toContain(`askRecapAudioAgain:${draft.ref}`));
  });

  it.each([
    ["paragraphs", w.all.paragraphs],
    ["moods", w.all.moods],
    ["size", w.all.size],
    ["no_parts", w.all.no_parts],
  ] as const)("everything (%s): says why, and the button says the whole audio", async (why, words) => {
    live.on = true;
    await open(withAudio(stale, { how: "all", why, changed: 3, total: 3 }));
    expect(await screen.findByText(words as string)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.allButton })).toBeTruthy();
  });

  it("a reason the page doesn't know gets the general sentence, never the code", async () => {
    live.on = true;
    await open(
      withAudio(stale, { how: "all", why: "something_new" as never, changed: 3, total: 3 }),
    );
    expect(await screen.findByText(w.allFallback)).toBeTruthy();
    expect(screen.queryByText(/something_new/)).toBeNull();
  });

  it("nothing changed in what the narrator reads: says so", async () => {
    live.on = true;
    await open(withAudio(stale, { how: "same", why: "", changed: 0, total: 3 }));
    expect(await screen.findByText(w.same)).toBeTruthy();
  });

  it.each(["missing", "fails"] as const)(
    "an API that can't say (%s) leaves the old button and words",
    async (kind) => {
      live.on = true;
      await open(withAudio(stale, kind));
      expect(await screen.findByRole("button", { name: "Make the audio again" })).toBeTruthy();
      expect(screen.queryByText(/is made again/)).toBeNull();
    },
  );

  it("does not ask when the button isn't offered", async () => {
    live.on = true;
    const api = withAudio({ ...stale, canAskAgain: false });
    await open(api);
    expect(api.calls.some((c) => c.startsWith("recapAudioRemake"))).toBe(false);
  });
});

describe("while it is made, and after", () => {
  it("shows the stage, the paragraphs done and the clock", async () => {
    live.on = true;
    await open(
      withAudio({
        state: "queued",
        error: "",
        current: false,
        canAskAgain: false,
        progress: {
          stage: "audio_voice",
          done: 2,
          total: 3,
          unit: "paragraphs",
          startedAt: 1000,
          updatedAt: 1040,
          serverNow: 1060,
          state: "running",
        },
      }),
    );
    expect(await screen.findByText("Speaking the paragraphs")).toBeTruthy();
    expect(screen.getByText(/2 of 3 paragraphs/)).toBeTruthy();
  });

  it("a stage the page doesn't know is said in the general words", async () => {
    live.on = true;
    await open(
      withAudio({
        state: "queued",
        error: "",
        current: false,
        canAskAgain: false,
        progress: { stage: "audio_new", startedAt: 1, updatedAt: 1, serverNow: 5, state: "running" },
      }),
    );
    expect(await screen.findByText("DMbot is working on it.")).toBeTruthy();
  });

  it.each([
    ["voice", w.lastVoice],
    ["all", w.lastAll],
  ] as const)("says what the last making did (%s)", async (how, words) => {
    live.on = true;
    await open(withAudio({ state: "ready", error: "", current: true, canAskAgain: true, how }));
    expect(await screen.findByText(words)).toBeTruthy();
  });

  it("says nothing of a first making", async () => {
    live.on = true;
    await open(
      withAudio({ state: "ready", error: "", current: true, canAskAgain: false, how: "new" }),
    );
    expect(screen.queryByText(w.lastVoice)).toBeNull();
    expect(screen.queryByText(w.lastAll)).toBeNull();
  });
});

describe("Fix a name everywhere", () => {
  const edit = async (on: boolean) => {
    live.on = on;
    render(<Recaps api={mockApi("table")} path={path} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    return screen.findByLabelText(/Recap|recap/, { selector: "textarea" }).catch(() => null);
  };

  it("is not in the editor while it is off", async () => {
    await edit(false);
    expect(screen.queryByText(fw.heading)).toBeNull();
  });

  it("replaces every use in the text box and says how many, and saves nothing yet", async () => {
    await edit(true);
    const box = document.getElementById("recap-text") as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "Kethrandil came. Then kethrandil's sword. Kethrandilson left." } });
    fireEvent.input(screen.getByLabelText(fw.wrongLabel), { target: { value: "Kethrandil" } });
    fireEvent.input(screen.getByLabelText(fw.rightLabel), { target: { value: "Kethrandel" } });
    fireEvent.click(screen.getByRole("button", { name: fw.button }));
    expect(box.value).toBe("Kethrandel came. Then Kethrandel's sword. Kethrandilson left.");
    expect((await screen.findByText(/Replaced/)).textContent).toBe(fw.done("Kethrandil", "Kethrandel", 2));
  });

  it("says when the name isn't in the text and leaves the text alone", async () => {
    await edit(true);
    const box = document.getElementById("recap-text") as HTMLTextAreaElement;
    const before = box.value;
    fireEvent.input(screen.getByLabelText(fw.wrongLabel), { target: { value: "Zorbo" } });
    fireEvent.input(screen.getByLabelText(fw.rightLabel), { target: { value: "Zorba" } });
    fireEvent.click(screen.getByRole("button", { name: fw.button }));
    expect(await screen.findByText(fw.none("Zorbo"))).toBeTruthy();
    expect(box.value).toBe(before);
  });

  it("needs both spellings before it can be pressed", async () => {
    await edit(true);
    expect((screen.getByRole("button", { name: fw.button }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("replaceName matches whole names only, ignores capitals, and keeps punctuation", () => {
    expect(replaceName("Mira, mira! Miranda (Mira).", "mira", "Mara")).toEqual({
      text: "Mara, Mara! Miranda (Mara).",
      times: 3,
    });
    expect(replaceName("a.b a.b", "a.b", "x$&y")).toEqual({ text: "x$&y x$&y", times: 2 });
    expect(replaceName("nothing", "", "x")).toEqual({ text: "nothing", times: 0 });
  });
});

describe("over the wire", () => {
  const reply = (status: number, body: unknown, seen: string[] = []) =>
    httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
      seen.push(`${init.method ?? "GET"} ${url}`);
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("reads what would be remade from the audio/remake route", async () => {
    const seen: string[] = [];
    const got = await reply(200, { how: "voice", why: "", changed: 1, total: 3 }, seen).recapAudioRemake!("1.a.5");
    expect(seen).toEqual(["GET https://api.example.test/me/recaps/1.a.5/audio/remake"]);
    expect(got).toEqual({ how: "voice", why: "", changed: 1, total: 3 });
  });

  it("an edit of the opening or closing line is refused in plain words", async () => {
    const api = reply(422, { detail: "fixed_lines" });
    const error = await api.saveRecap!("1.a.5", "x", []).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("fixed-lines");
    expect(errors["fixed-lines"]).toMatch(/DMbot's and can't be changed/);
  });
});
