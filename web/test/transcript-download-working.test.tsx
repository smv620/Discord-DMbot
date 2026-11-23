/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The wait while a transcript file is built (#3347 row 8): the session file and the script button. The building line shows while
// the file is with DMbot, the slow line after a minute, and nothing when nothing runs. The script button is behind its switch, so
// that switch is on for its tests, as the theatre download tests do.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { text } from "../src/content/transcripts";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/theatre-download-switch", () => ({
  get THEATRE_DOWNLOAD_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const REF = "200000000000000001.cmp1";
const asOwner = (api: ReturnType<typeof mockApi>): AccountApi =>
  ({
    ...api,
    me: async () => {
      const me = await api.me();
      return me && { ...me, campaigns: [{ ...me.campaigns[0]!, ref: REF, role: "owner" as const }] };
    },
  }) as AccountApi;

function stub() {
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
}

describe("the building line while a transcript file is built", () => {
  it("the session file shows the building line while the file is with DMbot, and removes it when it comes back", async () => {
    stub();
    const api = mockApi("table");
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    const file = api.transcriptFile!.bind(api);
    api.transcriptFile = async (ref, number, version) => {
      await gate;
      return file(ref, number, version);
    };
    render(<Transcripts api={api} path={`/account/transcripts/${REF}/2`} />);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    expect(await screen.findByText(text.buildingFile)).toBeTruthy();
    release();
    await waitFor(() => expect(screen.queryByText(text.buildingFile)).toBeNull());
  });

  it("the script shows the building line while the script is with DMbot, and removes it when it comes back", async () => {
    stub();
    live.on = true;
    const base = mockApi("table");
    const api = asOwner(base);
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    const script = base.transcriptScript!.bind(base);
    base.transcriptScript = async (ref, number) => {
      await gate;
      return script(ref, number);
    };
    render(<Transcripts api={api} path={`/account/transcripts/${REF}/1`} />);
    fireEvent.click(await screen.findByRole("button", { name: text.scriptDownload }));
    expect(await screen.findByText(text.buildingFile)).toBeTruthy();
    release();
    await waitFor(() => expect(screen.queryByText(text.buildingFile)).toBeNull());
  });

  it("shows nothing when nothing is running, and says what to do when the session file is slow", async () => {
    stub();
    render(<Transcripts api={mockApi("table")} path={`/account/transcripts/${REF}/2`} />);
    await screen.findByRole("button", { name: text.download });
    expect(screen.queryByText(text.buildingFile)).toBeNull();
    cleanup();
    // The fake clock is set before the page is drawn, as the other slow-line tests do, since the wait reads Date.now.
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date(1_900_000_000 * 1000));
    const slow = mockApi("table");
    slow.transcriptFile = () => new Promise(() => undefined);
    render(<Transcripts api={slow} path={`/account/transcripts/${REF}/2`} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: text.download }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(59_000);
    });
    expect(screen.getByText(text.buildingFile)).toBeTruthy();
    expect(screen.queryByText(text.buildingSlow)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(text.buildingSlow)).toBeTruthy();
  });
});
