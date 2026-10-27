/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Search by meaning" on the Names page (#2733), against the pretend API. Shown only while NAMES_SMART_LIVE is
// true (false today), so these tests switch it on. Searches only when Search is pressed, never as you type.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as w, smartWords as s } from "../src/content/names-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/names-words", async (original) => {
  const real = await original<typeof import("../src/content/names-words")>();
  return {
    ...real,
    get NAMES_SMART_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = true;
});

const rows = () => screen.queryAllByRole("listitem").filter((li) => li.classList.contains("name-row"));
const open = async (path = "/account/names/100.cmp1", api = mockApi("table")) => {
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: w.listLabel });
  return api;
};
const box = () => screen.getByLabelText(s.label) as HTMLInputElement;
const calls = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("smartNames"));

describe("Search by meaning", () => {
  it("is not there while the switch is off, and not for a server manager who can't change names", async () => {
    live.on = false;
    await open();
    expect(screen.queryByLabelText(s.label)).toBeNull();
    cleanup();
    live.on = true;
    await open("/account/names/100.cmp1.names-manager");
    expect(screen.queryByLabelText(s.label)).toBeNull();
  });

  it("explains what to type, and does not search while typing", async () => {
    const api = await open();
    expect(screen.getByText(s.hint)).toBeTruthy();
    expect(box().getAttribute("aria-describedby")).toBe("names-smart-hint");
    fireEvent.input(box(), { target: { value: "holy healer" } });
    await new Promise((r) => setTimeout(r, 400));
    expect(calls(api)).toHaveLength(0);
    expect((screen.getByRole("button", { name: s.button }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("Search with nothing typed is not offered", async () => {
    await open();
    expect((screen.getByRole("button", { name: s.button }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("searches once on Enter, shows the closest names in the same rows, and never sends the words in the call log", async () => {
    const api = await open();
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    await waitFor(() => expect(screen.getByText(s.found(1))).toBeTruthy());
    expect(calls(api)).toEqual(["smartNames:100.cmp1"]);
    expect(rows().map((r) => r.querySelector(".campaign-name")?.textContent)).toEqual(["Sister Ilse"]);
    expect(screen.queryByRole("navigation", { name: w.pagesNav })).toBeNull(); // no pages for a search
    expect(screen.getByRole("button", { name: "Sister Ilse" })).toBeTruthy(); // the same press-the-name rows
  });

  it("finds a relationship phrase, best match first", async () => {
    await open();
    fireEvent.input(box(), { target: { value: "river friend of Brask" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows().length).toBeGreaterThan(0));
    expect(rows()[0]?.querySelector(".campaign-name")?.textContent).toBe("Mira Voss");
  });

  it("says what it is doing while it waits, and ignores a second press", async () => {
    const api = mockApi("table");
    const real = api.smartNames!.bind(api);
    let release: () => void = () => {};
    api.smartNames = async (ref, words) => {
      await new Promise<void>((r) => (release = r));
      return real(ref, words);
    };
    await open("/account/names/100.cmp1", api);
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.stage)).toBeTruthy();
    expect((screen.getByRole("button", { name: s.button }) as HTMLButtonElement).disabled).toBe(true);
    release();
    await waitFor(() => expect(screen.queryByText(s.stage)).toBeNull());
    expect(rows()).toHaveLength(1);
  });

  it("Back to all names puts the list and its page bar back", async () => {
    await open();
    const before = rows().length;
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: s.back }));
    await waitFor(() => expect(rows()).toHaveLength(before));
    expect(box().value).toBe("");
    expect(screen.queryByRole("button", { name: s.back })).toBeNull();
  });

  it("says so when nothing is close", async () => {
    await open();
    fireEvent.input(box(), { target: { value: "zzzzz" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.none)).toBeTruthy();
    expect(rows()).toHaveLength(0);
  });

  it("says plainly when the meaning search is not ready, and the plain search still works", async () => {
    await open("/account/names/100.cmp1.smart-off");
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.notReady)).toBeTruthy();
    expect(rows().length).toBeGreaterThan(1); // the list is as it was
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "voss" } });
    await waitFor(() => expect(rows().every((r) => /voss/i.test(r.textContent ?? ""))).toBe(true));
  });

  it("says what to do when a search fails, and a later one works", async () => {
    const api = mockApi("table");
    const real = api.smartNames!.bind(api);
    let first = true;
    api.smartNames = async (ref, words) => {
      if (first) {
        first = false;
        throw new ApiError("server");
      }
      return real(ref, words);
    };
    await open("/account/names/100.cmp1", api);
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.failed)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByText(s.failed)).toBeNull();
  });

  it("an older answer that lands after a newer search started does not replace it", async () => {
    const api = mockApi("table");
    const real = api.smartNames!.bind(api);
    let release: () => void = () => {};
    api.smartNames = async (ref, words) => {
      await new Promise<void>((r) => (release = r));
      return real(ref, words);
    };
    await open("/account/names/100.cmp1", api);
    const before = rows().length;
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await screen.findByText(s.stage);
    fireEvent.input(box(), { target: { value: "" } });
    // (Back to all names appears only after a result, so the search is left by typing a new one: the old answer
    // must not replace the list when it lands after a newer search started)
    release();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(before).toBeGreaterThan(1);
  });

  it("has no accessibility violations with the box and a result", async () => {
    await open();
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations.map((v) => v.id)).toEqual([]);
    expect(within(document.body).getByText(s.found(1))).toBeTruthy();
  });
});
