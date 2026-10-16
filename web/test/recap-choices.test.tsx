/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The DM's three recap choices on the recap page (#1468): a switch saves when pressed and says so in the panel's
// "Saved." line, the narration switch only while narration is on and the plan has it, nothing before
// recaps are on, one request for two quick taps, and plain refusals.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AccountApi, ApiError, type RecapChoices } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapChoicesPart from "../src/account/RecapChoices";
import Recaps from "../src/account/Recaps";
import { choiceWords as words } from "../src/content/recap-choices";

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

function show(over: Partial<AccountApi> = {}) {
  const api = { ...mockApi("table"), ...over } as AccountApi;
  render(<RecapChoicesPart api={api} recapRef={REF} />);
  return api as AccountApi & { calls: string[] };
}

const box = (label: string) => screen.getByLabelText(new RegExp(`^${label}`)) as HTMLInputElement;

describe("the switches", () => {
  it("shows the three, as the API says them, with how the plan has the narration", async () => {
    show();
    await screen.findByText(words.sameOnDiscord);
    expect(box(words.labels.text).checked).toBe(true);
    expect(box(words.labels.email).checked).toBe(true);
    expect(screen.getByLabelText(`${words.labels.audio} (part of your plan)`)).toBeTruthy();
  });

  it("saves one change with the panel's line, and sends the campaign's address", async () => {
    const api = show();
    await screen.findByText(words.sameOnDiscord);
    fireEvent.click(box(words.labels.email));
    expect(await screen.findByText(words.saved.email.off)).toBeTruthy();
    expect(api.calls).toContain("setRecapChoice:200000000000000001.cmp1:email:false");
    expect(box(words.labels.email).checked).toBe(false);
    expect(screen.getByText(words.emailOff)).toBeTruthy(); // the panel says it with the email off
    fireEvent.click(box(words.labels.email));
    expect(await screen.findByText(words.saved.email.on)).toBeTruthy();
    expect(screen.queryByText(words.emailOff)).toBeNull();
  });

  it("announces the result in a live region, and a refusal as an alert", async () => {
    show({
      setRecapChoice: async () => {
        throw new ApiError("server");
      },
    });
    await screen.findByText(words.sameOnDiscord);
    fireEvent.click(box(words.labels.text));
    expect((await screen.findByRole("alert")).textContent).toBe(words.failed);
    expect(box(words.labels.text).checked).toBe(true); // the switch shows what is really saved
    cleanup();
    show();
    await screen.findByText(words.sameOnDiscord);
    fireEvent.click(box(words.labels.text));
    expect((await screen.findByText(words.saved.text.off)).getAttribute("role")).toBe("status");
  });

  it.each([
    ["not-in-plan", words.notInPlan],
    ["signed-out", words.signedOut],
    ["not-found", words.gone],
    ["network", words.failed],
  ] as const)("says a %s refusal in plain words", async (kind, message) => {
    show({
      setRecapChoice: async () => {
        throw new ApiError(kind);
      },
    });
    await screen.findByText(words.sameOnDiscord);
    fireEvent.click(box(words.labels.text));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
  });

  it("sends one request for two quick taps", async () => {
    let release: (c: RecapChoices) => void = () => {};
    const setRecapChoice = vi.fn(() => new Promise<RecapChoices>((done) => (release = done)));
    show({ setRecapChoice });
    await screen.findByText(words.sameOnDiscord);
    fireEvent.click(box(words.labels.text));
    fireEvent.click(box(words.labels.email));
    expect(setRecapChoice).toHaveBeenCalledTimes(1);
    release({ text: false, audio: true, email: true, canAudio: true, audioHow: "" });
    await screen.findByText(words.saved.text.off);
  });
});

describe("when it shows", () => {
  it("shows nothing while recaps are off", () => {
    live.recaps = false;
    const { container } = render(<RecapChoicesPart api={mockApi("table")} recapRef={REF} />);
    expect(container.innerHTML).toBe("");
  });

  it("has no narration switch while narration is off, or when the plan does not have it", async () => {
    live.audio = false;
    show();
    await screen.findByText(words.sameOnDiscord);
    expect(screen.queryByLabelText(new RegExp(`^${words.labels.audio}`))).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    cleanup();
    live.audio = true;
    show({
      recapChoices: async () => ({ text: true, audio: true, email: true, canAudio: false, audioHow: "" }),
    });
    await screen.findByText(words.sameOnDiscord);
    expect(screen.queryByLabelText(new RegExp(`^${words.labels.audio}`))).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("shows nothing from an API without the routes, and says so when the choices did not load", async () => {
    const api = mockApi("table");
    delete api.recapChoices;
    const { container } = render(<RecapChoicesPart api={api} recapRef={REF} />);
    expect(container.innerHTML).toBe("");
    cleanup();
    show({
      recapChoices: async () => {
        throw new ApiError("network");
      },
    });
    expect((await screen.findByRole("alert")).textContent).toBe(words.loadFailed);
  });

  it("sits on the recap's review page", async () => {
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${REF}`} />);
    expect(await screen.findByText(words.heading)).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText(new RegExp(`^${words.labels.text}`))).toBeTruthy());
  });
});
