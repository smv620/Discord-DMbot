/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The recap page's narration and music (#1418): three players while the audio matches the text,
// the line for every other state, and "Make the audio again". Shown only while RECAP_AUDIO_LIVE.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  httpApi,
  type AccountApi,
  type Recap,
  type RecapAudioState,
} from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { panelWords } from "../src/content/recap-panel";

// A switch the tests can flip; the real constant is false.
const live = vi.hoisted(() => ({ audio: true }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  cleanup();
  live.audio = true;
});

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;

const ready: RecapAudioState = {
  state: "ready",
  error: "",
  current: true,
  canAskAgain: true,
};

function withAudio(
  audio: RecapAudioState | undefined,
): AccountApi & { calls: string[] } {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  api.recap = async (ref) => {
    const { audio: _was, ...rest } = await read(ref);
    return (audio ? { ...rest, audio } : rest) as Recap;
  };
  return api;
}

describe("the audio part of the recap page", () => {
  it("is not there while the audio is off", async () => {
    live.audio = false;
    render(<Recaps api={withAudio(ready)} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(panelWords.heading)).toBeNull();
    expect(document.querySelector("audio")).toBeNull();
  });

  it("is not there on an API older than the audio", async () => {
    render(<Recaps api={withAudio(undefined)} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(panelWords.heading)).toBeNull();
  });

  it("shows one player for the voice and music, and the mix player under it", async () => {
    const api = withAudio(ready);
    render(<Recaps api={api} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    // One Play button and one seek bar drive the voice and music; the mix keeps its own controls.
    expect(screen.getAllByRole("button", { name: panelWords.play })).toHaveLength(1);
    expect(screen.getByRole("slider", { name: panelWords.position })).toBeTruthy();
    expect(screen.getByRole("switch", { name: panelWords.voice })).toBeTruthy();
    expect(screen.getByRole("switch", { name: panelWords.music })).toBeTruthy();
    expect(screen.getByRole("slider", { name: panelWords.voiceVolume })).toBeTruthy();
    expect(screen.getByRole("slider", { name: panelWords.musicVolume })).toBeTruthy();
    const players = [...document.querySelectorAll("audio")];
    expect(players.map((p) => p.getAttribute("aria-label"))).toEqual([
      "Voice",
      "Music",
      "Both mixed",
    ]);
    expect(players.map((p) => p.getAttribute("src"))).toEqual([
      "#demo-voice",
      "#demo-music",
      "#demo-mix",
    ]);
    expect(players.every((p) => p.getAttribute("preload") === "none")).toBe(
      true,
    );
    // Only the mix shows the browser's own controls.
    expect(players.map((p) => p.hasAttribute("controls"))).toEqual([false, false, true]);
    expect(
      api.calls.filter((c) => c.startsWith("recapAudioLinks")),
    ).toHaveLength(1);
  });

  it("the Voice switch mutes only the voice, and Music only the music", async () => {
    render(<Recaps api={withAudio(ready)} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    const [voice, music, mix] = [...document.querySelectorAll("audio")] as HTMLAudioElement[];
    fireEvent.click(screen.getByRole("switch", { name: panelWords.voice }));
    await waitFor(() => expect(voice!.muted).toBe(true));
    expect(music!.muted).toBe(false);
    expect(mix!.muted).toBe(false);
    fireEvent.click(screen.getByRole("switch", { name: panelWords.voice }));
    fireEvent.click(screen.getByRole("switch", { name: panelWords.music }));
    await waitFor(() => expect(music!.muted).toBe(true));
    expect(voice!.muted).toBe(false);
  });

  it("the volume sliders set each file's own volume", async () => {
    render(<Recaps api={withAudio(ready)} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    const [voice, music] = [...document.querySelectorAll("audio")] as HTMLAudioElement[];
    fireEvent.input(screen.getByRole("slider", { name: panelWords.musicVolume }), {
      target: { value: "0.25" },
    });
    await waitFor(() => expect(music!.volume).toBe(0.25));
    expect(voice!.volume).toBe(1);
  });

  it("Play starts the voice and music together; Pause stops both", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    render(<Recaps api={withAudio(ready)} path={path} />);
    fireEvent.click(await screen.findByRole("button", { name: panelWords.play }));
    expect(play).toHaveBeenCalledTimes(2);
    fireEvent.click(await screen.findByRole("button", { name: panelWords.pause }));
    expect(pause).toHaveBeenCalledTimes(2);
    play.mockRestore();
    pause.mockRestore();
  });

  it("seeking moves both files to the same place", async () => {
    render(<Recaps api={withAudio(ready)} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    const [voice, music] = [...document.querySelectorAll("audio")] as HTMLAudioElement[];
    Object.defineProperty(voice!, "duration", { value: 100, configurable: true });
    fireEvent.input(screen.getByRole("slider", { name: panelWords.position }), {
      target: { value: "42" },
    });
    expect(voice!.currentTime).toBe(42);
    expect(music!.currentTime).toBe(42);
  });

  it("says plainly when a file won't play", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("no"));
    render(<Recaps api={withAudio(ready)} path={path} />);
    fireEvent.click(await screen.findByRole("button", { name: panelWords.play }));
    expect(await screen.findByText(panelWords.wontPlay)).toBeTruthy();
    expect(screen.getByRole("button", { name: panelWords.play })).toBeTruthy();
    play.mockRestore();
  });

  it("shows the cues as tags in the read view, and never in the text the players get", async () => {
    const api = withAudio(ready);
    const read = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await read(ref)),
      text: "The party crossed the ford. The door opened.",
      summary: "《Tense》 The party crossed the ford. ((pause)) The door opened.",
    });
    render(<Recaps api={api} path={path} />);
    await screen.findByText(panelWords.cuesHeading);
    const mood = document.querySelector(".cue.mood");
    expect(mood?.textContent).toBe("《Tense》");
    expect(mood?.getAttribute("style")).toContain("--hue");
    expect(document.querySelector(".cue.pace")?.textContent).toBe("((pause))");
    // The posted text section has no tag and no cue characters.
    const posted = screen
      .getAllByText("The party crossed the ford. The door opened.")
      .find((p) => p.closest("section")?.querySelector("h3")?.textContent === "Recap");
    expect(posted?.textContent).not.toMatch(/[《》]|\(\(/);
    expect(posted?.closest("section")?.querySelector(".cue")).toBeNull();
  });

  it("shows no cue section when the text has no cues, or while the audio is off", async () => {
    render(<Recaps api={withAudio(ready)} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(panelWords.cuesHeading)).toBeNull();
    cleanup();
    live.audio = false;
    const api = withAudio(ready);
    const read = api.recap!.bind(api);
    api.recap = async (ref) => ({ ...(await read(ref)), summary: "《Tense》 It begins." });
    render(<Recaps api={api} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(panelWords.cuesHeading)).toBeNull();
  });

  it("asks for fresh links when a player can't load its file", async () => {
    const api = withAudio(ready);
    render(<Recaps api={api} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    fireEvent.error(document.querySelector("audio")!);
    await waitFor(() =>
      expect(
        api.calls.filter((c) => c.startsWith("recapAudioLinks")),
      ).toHaveLength(2),
    );
  });

  it("says the links ran out when they can't be read again", async () => {
    const api = withAudio(ready);
    render(<Recaps api={api} path={path} />);
    await waitFor(() =>
      expect(document.querySelectorAll("audio")).toHaveLength(3),
    );
    api.recapAudioLinks = async () => {
      throw new ApiError("network");
    };
    fireEvent.error(document.querySelector("audio")!);
    expect(await screen.findByText(panelWords.expired)).toBeTruthy();
  });

  it("after an edit says the audio is from the earlier text, with no players", async () => {
    render(
      <Recaps api={withAudio({ ...ready, current: false })} path={path} />,
    );
    expect(await screen.findByText(panelWords.stale)).toBeTruthy();
    expect(document.querySelector("audio")).toBeNull();
    expect(screen.getByRole("button", { name: panelWords.again })).toBeTruthy();
  });

  it("says the API's own sentence when the audio couldn't be made", async () => {
    render(
      <Recaps
        api={withAudio({
          state: "failed",
          error: "The voice company is busy.",
          current: false,
          canAskAgain: true,
        })}
        path={path}
      />,
    );
    expect(await screen.findByText("The voice company is busy.")).toBeTruthy();
    expect(screen.getByRole("button", { name: panelWords.again })).toBeTruthy();
  });

  it("says it is being made, with no button", async () => {
    render(
      <Recaps
        api={withAudio({
          state: "queued",
          error: "",
          current: false,
          canAskAgain: false,
        })}
        path={path}
      />,
    );
    expect(await screen.findByText(panelWords.queued)).toBeTruthy();
    expect(screen.queryByRole("button", { name: panelWords.again })).toBeNull();
  });

  it("says there is none when it was never made, with no button", async () => {
    render(
      <Recaps
        api={withAudio({
          state: "off",
          error: "",
          current: false,
          canAskAgain: false,
        })}
        path={path}
      />,
    );
    expect(await screen.findByText(panelWords.off)).toBeTruthy();
    expect(screen.queryByRole("button", { name: panelWords.again })).toBeNull();
  });

  it("Make the audio again asks once, shows it is being made, and a second ask is 'too soon'", async () => {
    const api = withAudio(ready);
    render(<Recaps api={api} path={path} />);
    fireEvent.click(
      await screen.findByRole("button", { name: panelWords.again }),
    );
    expect(await screen.findByText(panelWords.asked)).toBeTruthy();
    expect(await screen.findByText(panelWords.queued)).toBeTruthy();
    expect(api.calls).toContain(`askRecapAudioAgain:${base.ref}`);
    expect(screen.queryByRole("button", { name: panelWords.again })).toBeNull();
  });

  it("says 'too soon' in words when the API says so", async () => {
    const api = withAudio(ready);
    api.askRecapAudioAgain = async () => {
      throw new ApiError("too-soon");
    };
    render(<Recaps api={api} path={path} />);
    fireEvent.click(
      await screen.findByRole("button", { name: panelWords.again }),
    );
    expect(await screen.findByText(panelWords.tooSoon)).toBeTruthy();
  });

  it("sends one request when the button is tapped twice", async () => {
    const api = withAudio(ready);
    render(<Recaps api={api} path={path} />);
    const button = await screen.findByRole("button", {
      name: panelWords.again,
    });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(panelWords.asked);
    expect(
      api.calls.filter((c) => c.startsWith("askRecapAudioAgain")),
    ).toHaveLength(1);
  });
});

describe("the API calls", () => {
  it("reads the links with GET and asks again with POST", async () => {
    const fetcher = vi.fn(async (url: string) =>
      url.endsWith("/audio")
        ? new Response(
            JSON.stringify({ links: { voice: "https://api.example/x" } }),
            { status: 200 },
          )
        : new Response(JSON.stringify(base), { status: 200 }),
    );
    const api = httpApi(
      "https://api.example",
      fetcher as unknown as typeof fetch,
    );
    expect(await api.recapAudioLinks!(base.ref)).toEqual({
      voice: "https://api.example/x",
    });
    await api.askRecapAudioAgain!(base.ref);
    const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
    const first = calls[0]!;
    const second = calls[1]!;
    expect(first[0]).toBe(
      `https://api.example/me/recaps/${encodeURIComponent(base.ref)}/audio`,
    );
    expect(first[1].method).toBe("GET");
    expect(second[0]).toBe(
      `https://api.example/me/recaps/${encodeURIComponent(base.ref)}/audio/again`,
    );
    expect(second[1].method).toBe("POST");
    expect(
      (second[1].headers as Record<string, string>)["X-DMbot-Request"],
    ).toBe("1");
  });
});

describe("the page's security policy", () => {
  it("lets players load audio from the site itself, and the build adds the API's address", async () => {
    const { readFileSync } = await import("node:fs");
    const headers = readFileSync("public/_headers", "utf8");
    expect(headers).toContain("media-src 'self';");
    const script = readFileSync("scripts/csp-hashes.mjs", "utf8");
    expect(script).toContain("media-src 'self' ${origin};");
  });
});
