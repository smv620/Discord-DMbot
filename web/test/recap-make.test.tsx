/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Recaps for past sessions (#2610; core #2608, #2609), against the pretend API with the switch on: make, make
// again with the one question when a recap is approved, add one someone wrote (the right-to-use box only
// the first time), the shared wait panel while it runs, and nothing at all for a player.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapMake from "../src/account/RecapMake";
import { makeWords as w } from "../src/content/recap-make";

/** A label as an exact pattern (the words hold brackets). */
const rx = (label: string): RegExp => new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

vi.mock("../src/content/recap-make", async (original) => ({
  ...(await original<typeof import("../src/content/recap-make")>()),
  RECAP_MAKE_LIVE: true,
}));

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function open(campaign = "100.cmp1") {
  const api = mockApi("table");
  render(<RecapMake api={api} campaignRef={campaign} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("the list of past sessions", () => {
  it("offers Make the recap where there is none and Make it again where there is one", async () => {
    await open();
    expect(screen.getAllByRole("button", { name: w.make })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: w.again })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: w.add })).toHaveLength(3);
  });

  it("shows nothing to a player (the API says not allowed)", async () => {
    const api = mockApi("table");
    const { container } = render(<RecapMake api={api} campaignRef="100.player" />);
    await waitFor(() => expect(api.calls).toContain("pastSessions:100.player"));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("making a recap", () => {
  it("makes one for a session with none and shows the wait panel until it is ready", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.make }));
    expect(await screen.findByText(w.stage["writing"] as string)).toBeTruthy();
    expect(api.calls).toContain("makeRecap:100.cmp1:1790000000");
    expect(screen.getByText(/1 of 3 parts/)).toBeTruthy();
    await vi.advanceTimersByTimeAsync(7_000);
    await waitFor(() => expect(screen.queryByText(w.stage["writing"] as string)).toBeNull());
  });

  it("asks one question before replacing an approved recap, and keeps it on No", async () => {
    const api = await open();
    const again = screen.getAllByRole("button", { name: w.again })[0]!; // the newest session: approved
    fireEvent.click(again);
    expect(await screen.findByText(w.askReplaceApproved)).toBeTruthy();
    expect(api.calls).toContain("makeRecap:100.cmp1:1791000000");
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(screen.queryByText(w.askReplaceApproved)).toBeNull();
    expect(api.calls.filter((c) => c.startsWith("makeRecap")).length).toBe(1);
  });

  it("sends the second request with replace after Yes", async () => {
    const api = await open();
    fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
    fireEvent.click(await screen.findByRole("button", { name: w.yesAgain }));
    await waitFor(() => expect(api.calls).toContain("makeRecap:100.cmp1:1791000000:replace"));
  });

  it("says in plain words when it cannot be made", async () => {
    const api = mockApi("table");
    api.makeRecap = async () => {
      throw new ApiError("no-transcript");
    };
    render(<RecapMake api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: w.make }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["no-transcript"]);
  });
});

describe("adding a recap someone wrote", () => {
  async function fill(extra?: () => void) {
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[1]!); // the session with no recap
    const form = await screen.findByRole("heading", { name: w.form.heading });
    expect(document.activeElement).toBe(form);
    fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "The party crossed the river." } });
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
    extra?.();
  }

  it("asks for the right to use it the first time, and sends it with the recap", async () => {
    const api = await open();
    await fill();
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["right-not-confirmed"]);
    expect(api.calls.some((c) => c.startsWith("addRecap"))).toBe(false);
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect(await screen.findByText(w.stage["writing"] as string)).toBeTruthy();
    expect(api.calls).toContain("addRecap:100.cmp1:1790000000:text");
  });

  it("does not ask again once it was given", async () => {
    const api = await open();
    await fill(() => fireEvent.click(screen.getByLabelText(rx(w.form.right))));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await screen.findByText(w.stage["writing"] as string);
    cleanup();
    render(<RecapMake api={api} campaignRef="100.cmp1" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[2]!);
    await screen.findByRole("heading", { name: w.form.heading });
    expect(screen.queryByLabelText(rx(w.form.right))).toBeNull();
  });

  it("asks before replacing a recap that is there", async () => {
    const api = await open();
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!); // the approved one
    await screen.findByRole("heading", { name: w.form.heading });
    fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.yesReplace }));
    await waitFor(() => expect(api.calls).toContain("addRecap:100.cmp1:1791000000:text:replace"));
  });

  it("says when a Google Doc cannot be read", async () => {
    await open();
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[1]!);
    await screen.findByRole("heading", { name: w.form.heading });
    fireEvent.click(screen.getByLabelText(w.form.link));
    fireEvent.input(screen.getByLabelText(rx(w.form.link_)), { target: { value: "https://docs.example.com/private" } });
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["link-private"]);
  });
});

describe("a recap that is already being made", () => {
  it("is watched when the page opens, and a failure can be closed to try again", async () => {
    const api = mockApi("table");
    const now = Math.floor(Date.now() / 1000);
    let looks = 0; // the first look finds it being made; after it failed, the list says so
    api.pastSessions = async () => ({
      rightConfirmed: true,
      sessions: [
        {
          sessionAt: 1790000000,
          sessionNumber: 3,
          sessionSeconds: 7200,
          recap: { ref: "mock.working.1", state: looks++ === 0 ? "working" : "failed", status: "draft", held: false, approved: false },
        },
      ],
    });
    api.recap = async (ref) => ({
      ...(await mockApi("table").recap!("200000000000000001.cmp1.1791000000")),
      ref,
      working: false,
      state: "failed",
      error: "",
      progress: { stage: "writing", startedAt: now, updatedAt: now, serverNow: now, state: "failed", reason: "recap_failed" },
    });
    render(<RecapMake api={api} campaignRef="100.cmp1" />);
    expect(await screen.findByText(w.failedReason["recap_failed"] as string)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.close }));
    expect(await screen.findByRole("button", { name: w.again })).toBeTruthy();
  });
});
