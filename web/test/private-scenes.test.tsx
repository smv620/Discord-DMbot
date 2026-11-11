/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "🤫 Private scenes" on the Names page (#4102): the list as Discord words it, and Not private, which DMbot never undoes. DM only.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import PrivateScenes from "../src/account/PrivateScenes";
import { namesPage } from "../src/content/names-words";
import { sceneWords as w } from "../src/content/private-scenes-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/private-scenes-switch", () => ({
  get PRIVATE_SCENES_LIVE() {
    return live.on;
  },
}));

const zone = process.env.TZ;
afterEach(() => {
  cleanup();
  live.on = true;
  if (zone === undefined) delete process.env.TZ;
  else process.env.TZ = zone;
});
const REF = "100.cmp1";

async function open() {
  process.env.TZ = "UTC";
  const api = mockApi("table");
  render(<PrivateScenes api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: new RegExp(w.heading) });
  return api;
}

describe("the list", () => {
  it("shows each scene with names, the day, about how long and how many lines, and no words", async () => {
    await open();
    expect(screen.getByRole("heading").textContent).toContain("(2)");
    expect(screen.getByText(w.lead)).toBeTruthy();
    expect(screen.getByText(w.who("Rook", "Mira Voss"))).toBeTruthy();
    expect(screen.getByText(new RegExp(w.detail("14 Nov 2023", 12, 9).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeTruthy();
    expect(screen.getByText(/about 1 minute · 4 lines/)).toBeTruthy();
  });
});

describe("Not private", () => {
  it("takes the scene off the list and says so", async () => {
    const api = await open();
    const li = screen.getByText(w.who("Rook", "Mira Voss")).closest("li") as HTMLElement;
    fireEvent.click(within(li).getByRole("button", { name: w.notPrivate }));
    expect(await screen.findByText(w.done("Rook", "Mira Voss"))).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(w.who("Rook", "Mira Voss"))).toBeNull());
    expect(api.calls.filter((c) => c.startsWith("notPrivateScene:"))).toHaveLength(1);
  });

  it("says when it was already gone, and the list is up to date", async () => {
    const api = await open();
    const real = api.notPrivateScene!.bind(api);
    // (another tab marked it first)
    await real(REF, "d".repeat(32));
    const li = screen.getByText(w.who("Rook", "Mira Voss")).closest("li") as HTMLElement;
    fireEvent.click(within(li).getByRole("button", { name: w.notPrivate }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(w.who("Rook", "Mira Voss"))).toBeNull());
  });

  it("says when nobody is waiting", async () => {
    const api = await open();
    for (const id of ["d".repeat(32), "e".repeat(32)]) await api.notPrivateScene!(REF, id);
    cleanup();
    render(<PrivateScenes api={api} campaignRef={REF} />);
    expect(await screen.findByText(w.empty)).toBeTruthy();
  });
});

describe("the Names page", () => {
  it("shows it to a DM when its switch is on, and not while it is off", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    expect(await screen.findByRole("heading", { name: new RegExp(w.heading) })).toBeTruthy();
    cleanup();
    live.on = false;
    const api = mockApi("table");
    render(<Names api={api} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    expect(api.calls.some((c) => c.startsWith("privateScenes:"))).toBe(false);
  });
});
