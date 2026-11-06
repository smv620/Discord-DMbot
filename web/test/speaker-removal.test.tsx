/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Remove this speaker's lines" on the Transcripts page (#3305 PR 2): a game master sees the people without Discord, is asked
// once, the removal is followed to its end and the person goes from the list. A player, an older API or a failed list shows
// nothing; "nothing left" is information, not a failure.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SpeakerRemovalPanel from "../src/account/SpeakerRemoval";
import { speakerRemovalWords as w } from "../src/content/speaker-removal-words";
import { importConsentLine } from "../src/content/privacy-channels";
import { entries, held } from "../src/content/whats-new";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/speaker-removal-switch", () => ({
  get SPEAKER_REMOVAL_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = true;
});

const REF = "100.cmp1";
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
};

describe("the speakers without Discord", () => {
  it("shows nothing while the switch is off, for an older API, and when the list is refused", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    expect(screen.queryByText(w.heading)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("campaignSpeakers"))).toBe(false);
    cleanup();
    live.on = true;
    const old = { ...mockApi("table") };
    delete old.campaignSpeakers;
    render(<SpeakerRemovalPanel api={old} campaignRef={REF} />);
    expect(screen.queryByText(w.heading)).toBeNull();
    cleanup();
    const refused = mockApi("table");
    refused.campaignSpeakers = async () => {
      throw new ApiError("not-allowed"); // a player
    };
    render(<SpeakerRemovalPanel api={refused} campaignRef={REF} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(w.heading)).toBeNull();
  });

  it("lists each person with their lines and a button that names them", async () => {
    render(<SpeakerRemovalPanel api={mockApi("table")} campaignRef={REF} />);
    expect(await screen.findByText(w.heading)).toBeTruthy();
    expect(screen.getByText("Old Pip")).toBeTruthy();
    expect(screen.getByText(w.linesLabel(12))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.buttonFor("The Ferryman") })).toBeTruthy();
  });

  it("lists a ticked-in member under their own heading, and does not say their name is removed", async () => {
    const api = mockApi("table");
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    expect(await screen.findByText(w.memberHeading)).toBeTruthy();
    expect(screen.getByText(w.memberIntro)).toBeTruthy();
    expect(screen.getByText(w.outsideHeading)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Ash") }));
    expect(screen.getByText(w.confirm("Ash", true))).toBeTruthy();
    expect(w.confirm("Ash", true)).not.toContain("their name too");
    expect(w.confirm("Old Pip")).toContain("their name too");
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    await waitFor(() => expect(api.calls).toContain("removeSpeaker:100.cmp1:9001"));
  });

  it("asks once, and Keep sends nothing", async () => {
    const api = mockApi("table");
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    expect(screen.getByText(w.confirm("Old Pip"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(screen.queryByText(w.confirm("Old Pip"))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("removeSpeaker"))).toBe(false);
  });

  it("removes a person, follows the wait, says how much went and drops them from the list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    await waitFor(() => expect(api.calls).toContain("removeSpeaker:100.cmp1:-1"));
    expect(await screen.findByText(w.stage.removing!)).toBeTruthy(); // the shared wait, while it runs
    await settle();
    expect(await screen.findByText(w.done(12, 2))).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Old Pip")).toBeNull());
    expect(screen.getByText("The Ferryman")).toBeTruthy();
  });

  it("says there is nothing left when the API says so, and reads the list again", async () => {
    const api = mockApi("table");
    api.removeSpeaker = async () => {
      throw new ApiError("nothing");
    };
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    expect(await screen.findByText(w.nothing)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("campaignSpeakers")).length).toBe(2);
  });

  it("keeps the result when the list cannot be read again after a removal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    const real = api.campaignSpeakers!;
    let reads = 0;
    api.campaignSpeakers = async (ref) => {
      if (++reads > 1) throw new ApiError("network"); // the first read works, the one after the removal does not
      return real(ref);
    };
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    await settle();
    expect(await screen.findByText(w.done(12, 2))).toBeTruthy(); // still there: the panel did not vanish
    expect(screen.getByText(w.heading)).toBeTruthy();
  });

  it("sends one removal when the confirm button is pressed twice at once", async () => {
    const api = mockApi("table");
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    const yes = screen.getByRole("button", { name: w.confirmYes });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("removeSpeaker")).length).toBe(1));
  });

  it("says a failed removal removed nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.speakerRemoval = async () => ({ stage: "removing", state: "failed", startedAt: 1, updatedAt: 2, serverNow: 3, reason: "error" });
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    await settle();
    expect(await screen.findByText(w.failedDo)).toBeTruthy();
  });

  it("shows the too-soon words when another removal is already going", async () => {
    const api = mockApi("table");
    api.removeSpeaker = async () => {
      throw new ApiError("too-soon");
    };
    render(<SpeakerRemovalPanel api={api} campaignRef={REF} />);
    await screen.findByText(w.heading);
    fireEvent.click(screen.getByRole("button", { name: w.buttonFor("Old Pip") }));
    fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
    expect(await screen.findByText(w.problems["too-soon"]!)).toBeTruthy();
  });
});

describe("the words", () => {
  it("hold the What's new entry back while the switch is off", () => {
    expect(held.some((h) => h.title === "Remove a person's lines from a campaign")).toBe(true);
    expect(entries.some((e) => e.title === "Remove a person's lines from a campaign")).toBe(false);
  });
});

describe("the privacy line", () => {
  it("says a member deletes their own lines from Discord and that removed lines stay removed in that campaign", () => {
    expect(importConsentLine.text).toContain("A server member can delete their own lines from Discord");
    expect(importConsentLine.text).toContain("stay removed from that campaign");
    expect(importConsentLine.text).not.toContain("on the website");
  });
});
