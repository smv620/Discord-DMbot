/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Names in this campaign" on the recap page (#1451): folded, loaded when opened, one Save for each
// name, the plain-word states, DM only (the page already is), and nothing at all while recaps or
// narration are off. The switches are flipped with a getter the real constants cannot be.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type CampaignName } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapNames, { campaignRefOf } from "../src/account/RecapNames";
import Recaps from "../src/account/Recaps";
import { nameWords as words } from "../src/content/recap-names";

const live = vi.hoisted(() => ({ recaps: true, audio: true }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.recaps;
    },
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  cleanup();
  live.recaps = true;
  live.audio = true;
});

const REF = "200000000000000001.cmp1.1791000000";

function open() {
  const details = document.querySelector("details") as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

// The part keeps an (empty) alert for each name, so the one that says something is the one to read.
async function alertNow(): Promise<HTMLElement> {
  await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent)).toBe(true));
  return screen.getAllByRole("alert").find((a) => a.textContent)!;
}

function box(label: string) {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("the address", () => {
  it("is the recap's address without its last part", () => {
    expect(campaignRefOf(REF)).toBe("200000000000000001.cmp1");
  });
});

describe("the part", () => {
  it("is folded and loads nothing until it is opened", async () => {
    const api = mockApi("table");
    render(<RecapNames api={api} recapRef={REF} />);
    expect(screen.getByText(words.heading)).toBeTruthy();
    expect(screen.queryByLabelText(words.label("Ka'zeth"))).toBeNull();
    expect((api as { calls: string[] }).calls).not.toContain(`campaignNames:200000000000000001.cmp1`);
    open();
    expect(await screen.findByLabelText(words.label("Ka'zeth"))).toBeTruthy();
    expect(box(words.label("Brynwater")).value).toBe("Brin-water");
    expect(box(words.label("Ka'zeth")).value).toBe("");
    expect(screen.getByText(words.secret)).toBeTruthy();
  });

  it("saves one name with a one-line confirmation, and sends the tidied words", async () => {
    const api = mockApi("table");
    render(<RecapNames api={api} recapRef={REF} />);
    open();
    const input = await screen.findByLabelText(words.label("Ka'zeth"));
    fireEvent.input(input, { target: { value: "  Kah-zeth  " } });
    fireEvent.click(input.parentElement!.querySelector("button[id^='save-said-as']")!);
    expect(await screen.findByText(words.saved)).toBeTruthy();
    expect((api as { calls: string[] }).calls).toContain(
      `saveSaidAs:200000000000000001.cmp1:${"a".repeat(32)}:Kah-zeth`,
    );
    expect(box(words.label("Brynwater")).value).toBe("Brin-water"); // the other name is untouched
  });

  it("clears a spelling when the box is emptied, and says so", async () => {
    render(<RecapNames api={mockApi("table")} recapRef={REF} />);
    open();
    const input = await screen.findByLabelText(words.label("Brynwater"));
    fireEvent.input(input, { target: { value: "" } });
    fireEvent.click(input.parentElement!.querySelector("button[id^='save-said-as']")!);
    expect(await screen.findByText(words.cleared)).toBeTruthy();
  });

  it("refuses more than 80 letters before asking, and keeps the words", async () => {
    const api = mockApi("table");
    render(<RecapNames api={api} recapRef={REF} />);
    open();
    const input = await screen.findByLabelText(words.label("Ka'zeth"));
    fireEvent.input(input, { target: { value: "x".repeat(81) } });
    fireEvent.click(input.parentElement!.querySelector("button[id^='save-said-as']")!);
    expect((await alertNow()).textContent).toBe(words.tooLong);
    expect((api as { calls: string[] }).calls.some((c) => c.startsWith("saveSaidAs"))).toBe(false);
  });

  it.each([
    ["signed-out", words.signedOut],
    ["not-found", words.gone],
    ["bad-text", words.odd],
    ["server", words.failed],
    ["network", words.failed],
  ] as const)("says a %s refusal in plain words", async (kind, message) => {
    const api: AccountApi = {
      ...mockApi("table"),
      saveSaidAs: async () => {
        throw new ApiError(kind);
      },
    };
    render(<RecapNames api={api} recapRef={REF} />);
    open();
    const input = await screen.findByLabelText(words.label("Ka'zeth"));
    fireEvent.input(input, { target: { value: "Kah" } });
    fireEvent.click(input.parentElement!.querySelector("button[id^='save-said-as']")!);
    expect((await alertNow()).textContent).toBe(message);
  });

  it("sends one request for two quick taps", async () => {
    let calls = 0;
    let release: () => void = () => {};
    const api: AccountApi = {
      ...mockApi("table"),
      saveSaidAs: () => {
        calls += 1;
        return new Promise<void>((resolve) => (release = resolve));
      },
    };
    render(<RecapNames api={api} recapRef={REF} />);
    open();
    const input = await screen.findByLabelText(words.label("Ka'zeth"));
    fireEvent.input(input, { target: { value: "Kah" } });
    const button = input.parentElement!.querySelector("button[id^='save-said-as']")!;
    fireEvent.click(button);
    fireEvent.click(button);
    expect(calls).toBe(1);
    release();
    await screen.findByText(words.saved);
  });

  it("says when the names did not load, and when there are none", async () => {
    const failing: AccountApi = {
      ...mockApi("table"),
      campaignNames: async () => {
        throw new ApiError("network");
      },
    };
    const { unmount } = render(<RecapNames api={failing} recapRef={REF} />);
    open();
    expect((await alertNow()).textContent).toBe(words.loadFailed);
    unmount();
    const none: AccountApi = { ...mockApi("table"), campaignNames: async (): Promise<CampaignName[]> => [] };
    render(<RecapNames api={none} recapRef={REF} />);
    open();
    expect(await screen.findByText(words.none)).toBeTruthy();
  });
});

describe("the switches and an older API", () => {
  it("shows nothing while recaps are off, or while narration is", () => {
    const cases: [boolean, boolean][] = [
      [false, true],
      [true, false],
      [false, false],
    ];
    for (const [recaps, audio] of cases) {
      live.recaps = recaps;
      live.audio = audio;
      const { container, unmount } = render(<RecapNames api={mockApi("table")} recapRef={REF} />);
      expect(container.querySelector("details")).toBeNull();
      unmount();
    }
  });

  it("shows nothing from an API without the routes", () => {
    const api = mockApi("table");
    delete api.campaignNames;
    const { container } = render(<RecapNames api={api} recapRef={REF} />);
    expect(container.querySelector("details")).toBeNull();
  });

  it("sits on the recap's review page", async () => {
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${REF}`} />);
    expect(await screen.findByText(words.heading)).toBeTruthy();
    await waitFor(() => expect(document.querySelector("details.names")).not.toBeNull());
  });
});
