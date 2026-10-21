/** @jsxImportSource preact */
// @vitest-environment happy-dom
// With narration switched on, a held recap still shows no audio panel (#2210): it has no text to
// narrate, and a normal recap with the same audio state does show it. RECAP_AUDIO_LIVE is false
// today, so this test turns it on the way recap-approve.test.tsx does.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { panelWords } from "../src/content/recap-panel";
import { heldWords } from "../src/content/recaps";

const live = vi.hoisted(() => ({ audio: false }));
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
  live.audio = false;
});

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;
const audio = { state: "ready" as const, error: "", current: true, canAskAgain: true };

function withExtra(extra: Partial<Recap>): AccountApi {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  api.recap = async (ref) => ({ ...(await read(ref)), audio, ...extra });
  return api;
}

describe("the audio panel", () => {
  it("shows for a normal recap when narration is on (so the check below means something)", async () => {
    live.audio = true;
    render(<Recaps api={withExtra({})} path={path} />);
    expect(await screen.findByRole("heading", { name: panelWords.heading })).toBeTruthy();
  });

  it("is absent while the recap is held, with narration on", async () => {
    live.audio = true;
    render(<Recaps api={withExtra({ waitingForNames: true, namesToAnswer: 2 })} path={path} />);
    await screen.findByText(heldWords.line(2));
    expect(screen.queryByRole("heading", { name: panelWords.heading })).toBeNull();
    expect(screen.queryByRole("button", { name: /again/i })).toBeNull();
  });
});
