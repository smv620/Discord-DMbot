/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Plot check is part of the DM's recap page (#2124). Kept apart from the component's tests because
// importing the whole recap page changes how happy-dom delivers some events.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { plotWords as w } from "../src/content/recap-plot";
import { approveErrors, approveWords } from "../src/content/recap-approve";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get PLOT_CHECK_LIVE() {
      return live.on;
    },
    get RECAP_FIX_LIVE() {
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
const page = () => render(<Recaps api={mockApi("table")} path={`/account/recaps/${draft.ref}`} />);

it("is on the DM's recap page when switched on, and absent when off", async () => {
  page();
  await screen.findByRole("heading", { level: 2 });
  expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  cleanup();
  live.on = true;
  page();
  expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
});

it("keeps Approve greyed, with the reason, until every question has an answer", async () => {
  live.on = true;
  page();
  await screen.findByRole("heading", { name: w.heading });
  const post = await screen.findByRole("button", { name: approveWords.postText });
  await waitFor(() => expect((post as HTMLButtonElement).disabled).toBe(true));
  expect(screen.getByText(w.waitingOn(2))).toBeTruthy();
  for (const n of [1, 2]) {
    fireEvent.click(within(screen.getAllByRole("group")[n - 1]!).getByRole("button", { name: w.answerFor(w.answers.leave, n) }));
  }
  await waitFor(() => expect((screen.getByRole("button", { name: approveWords.postText }) as HTMLButtonElement).disabled).toBe(false));
});

it("puts Fix the recap's words in the box under Tell DMbot what to fix, and sends nothing", async () => {
  live.on = true;
  const api = mockApi("table");
  render(<Recaps api={api} path={`/account/recaps/${draft.ref}`} />);
  await screen.findByRole("heading", { name: w.heading });
  fireEvent.click(within(screen.getAllByRole("group")[0]!).getByRole("button", { name: w.answerFor(w.answers.fix, 1) }));
  const box = (await screen.findByLabelText("What should change?")) as HTMLTextAreaElement;
  await waitFor(() => expect(box.value).toContain("Ulfgar opened the gate."));
  expect((api as unknown as { calls: string[] }).calls.filter((c) => c.startsWith("sendFix"))).toHaveLength(0);
});

it("is held with its What's new entry, and goes live with it", async () => {
  const { PLOT_CHECK_LIVE: real } = await vi.importActual<typeof import("../src/content/recap-words")>("../src/content/recap-words");
  const { held } = await import("../src/content/whats-new");
  expect(held.map((h) => h.title).includes("Plot check on a recap")).toBe(!real);
});

it("when Approve says a question is still open (plot_open), says so and reads the check again", async () => {
  live.on = true;
  const api = mockApi("table");
  const real = api.plotCheck!;
  let reads = 0;
  // The first look says unavailable (so Approve is open); a question has landed since.
  api.plotCheck = async (ref) => (reads++ === 0 ? { state: "unavailable", approveBlocked: false, version: 1, textHash: "h", issues: [], versions: [] } : real(ref));
  api.approveRecap = async () => {
    throw new ApiError("plot-open");
  };
  render(<Recaps api={api} path={`/account/recaps/${draft.ref}`} />);
  await screen.findByText(w.unavailable);
  fireEvent.click(await screen.findByRole("button", { name: approveWords.postText }));
  expect(await screen.findByText(approveErrors["plot-open"] as string)).toBeTruthy();
  await waitFor(() => expect(screen.getAllByRole("group").length).toBeGreaterThan(1)); // the questions are there now
  await waitFor(() => expect((screen.getByRole("button", { name: approveWords.postText }) as HTMLButtonElement).disabled).toBe(true));
});
