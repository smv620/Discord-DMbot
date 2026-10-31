/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "See as…" on the Story graph (#2999, slice 2): a DM looks at the web as one character, or the party, knows it.
// Shown in the Players View only, read-only while on, and cleared by Full View or "Back to the party".
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const at = (suffix = ""): string => `/account/web/100.cmp1${suffix}`;
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));
async function open(suffix = "", api = mockApi("table")) {
  render(<Web api={api} path={at(suffix)} />);
  await screen.findByText(/names?, \d+ lines?/);
  return api;
}
/** Opens the DM's Players View, where the picker is. */
async function openPlayersView(api = mockApi("table")) {
  await open("", api);
  fireEvent.click(screen.getByLabelText(w.playersView));
  await screen.findByLabelText(w.seeAsLabel);
  return api;
}
const box = (): HTMLInputElement => screen.getByLabelText(w.seeAsLabel) as HTMLInputElement;

describe("See as…", () => {
  it("shows the picker to a DM in Players View only", async () => {
    await open();
    expect(screen.queryByLabelText(w.seeAsLabel)).toBeNull();
    fireEvent.click(screen.getByLabelText(w.playersView));
    expect(await screen.findByLabelText(w.seeAsLabel)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(w.fullView));
    await waitFor(() => expect(screen.queryByLabelText(w.seeAsLabel)).toBeNull());
  });

  it("a player never sees the picker", async () => {
    await open(".web-player");
    expect(screen.queryByLabelText(w.seeAsLabel)).toBeNull();
    expect(screen.queryByLabelText(w.playersView)).toBeNull();
  });

  it("typing asks the list after the pause and shows what it finds", async () => {
    const api = await openPlayersView();
    fireEvent.input(box(), { target: { value: "Guard" } });
    await waitFor(() => expect(calls(api, "webViewers").some((c) => c === "webViewers:100.cmp1:Guard")).toBe(true), { timeout: 2000 });
    expect(await screen.findByRole("button", { name: "Guard 1" })).toBeTruthy();
  });

  it("shows \"Showing 25 of N\" when there are more than the list holds", async () => {
    await openPlayersView();
    fireEvent.input(box(), { target: { value: "Guard" } });
    expect(await screen.findByText(w.seeAsMore(25, 28), {}, { timeout: 2000 })).toBeTruthy();
  });

  it("picking a character reads the web as them and says so", async () => {
    const api = await openPlayersView();
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    expect(await screen.findByText(w.seeAsNow("Cerric"))).toBeTruthy();
    expect(calls(api, "seeAs").pop()).toBe("seeAs:100.cmp1:pc-cerric");
  });

  it("Back to the party clears it", async () => {
    const api = await openPlayersView();
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    await screen.findByText(w.seeAsNow("Cerric"));
    fireEvent.click(screen.getByRole("button", { name: w.seeAsBack }));
    await waitFor(() => expect(screen.queryByText(w.seeAsNow("Cerric"))).toBeNull());
    expect(calls(api, "seeAs")).toHaveLength(1);
  });

  it("Full View clears it", async () => {
    await openPlayersView();
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    await screen.findByText(w.seeAsNow("Cerric"));
    fireEvent.click(screen.getByLabelText(w.fullView));
    await waitFor(() => expect(screen.queryByText(w.seeAsNow("Cerric"))).toBeNull());
  });

  it("nothing can be changed while seeing as someone", async () => {
    await open();
    // In Full View the rows open with their controls: the test would pass vacuously if they never showed.
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    expect(screen.getAllByText(w.reveal, { exact: false }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText(w.playersView));
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    await screen.findByText(w.seeAsNow("Cerric"));
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    expect(screen.queryAllByText(w.reveal, { exact: false })).toHaveLength(0);
    expect(screen.queryAllByRole("button", { name: /^Change / })).toHaveLength(0);
    expect(screen.getByText(w.playersViewNote)).toBeTruthy();
  });

  it("marks the character being seen as the current choice", async () => {
    await openPlayersView();
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    await screen.findByText(w.seeAsNow("Cerric"));
    expect(screen.getByRole("button", { name: "Cerric" }).getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("button", { name: w.seeAsParty }).getAttribute("aria-current")).toBeNull();
  });

  it("a failed look clears the results of the older question", async () => {
    const api = await openPlayersView();
    fireEvent.input(screen.getByLabelText(w.seeAsLabel), { target: { value: "Guard" } });
    expect(await screen.findByRole("button", { name: "Guard 1" }, { timeout: 2000 })).toBeTruthy();
    api.webViewers = async () => {
      throw new ApiError("network");
    };
    fireEvent.input(screen.getByLabelText(w.seeAsLabel), { target: { value: "Gu" } });
    expect(await screen.findByText(w.seeAsLoadFailed, {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Guard 1" })).toBeNull();
  });

  it("a failed list shows the message and keeps the box", async () => {
    const api = mockApi("table");
    api.webViewers = async () => {
      throw new ApiError("network");
    };
    await openPlayersView(api);
    expect(await screen.findByText(w.seeAsLoadFailed)).toBeTruthy();
    fireEvent.input(box(), { target: { value: "Mi" } });
    expect(box().value).toBe("Mi");
    expect(box().disabled).toBe(false);
  });

  it("a character who has left the story says so and reads the Players View again", async () => {
    const api = mockApi("table");
    const real = api.webGraph!;
    api.webGraph = async (ref, q) => {
      if (q.seeAs !== undefined) throw new ApiError("not-found");
      return real(ref, q);
    };
    await openPlayersView(api);
    fireEvent.click(await screen.findByRole("button", { name: "Cerric" }));
    expect(await screen.findByText(w.seeAsGone)).toBeTruthy();
    expect(screen.queryByText(w.seeAsNow("Cerric"))).toBeNull();
  });
});
