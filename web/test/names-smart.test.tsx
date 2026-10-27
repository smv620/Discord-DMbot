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
import { smartWords as s } from "../src/content/names-smart-words";
import { namesPage as w } from "../src/content/names-words";

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
// The box is its own file, fetched after the page (LazyPanel): wait for it unless the test is about it being absent.
const open = async (path = "/account/names/100.cmp1", api = mockApi("table"), withBox = true) => {
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: w.listLabel });
  if (withBox && live.on && !/names-manager|names-none/.test(path)) await screen.findByLabelText(s.label);
  return api;
};
const box = () => screen.getByLabelText(s.label) as HTMLInputElement;
const calls = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("smartNames"));

describe("Search by meaning", () => {
  it("is not there while the switch is off, and not for a server manager who can't change names", async () => {
    live.on = false;
    await open("/account/names/100.cmp1", mockApi("table"), false);
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
  });

  it("Search with nothing typed says what to type, and sends nothing", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.typeSome)).toBeTruthy();
    expect(calls(api)).toHaveLength(0);
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
    expect(screen.getByRole("button", { name: s.button }).getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: s.button })); // (a second press is ignored)
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
    expect(screen.getByText(s.showing)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: s.back }));
    await waitFor(() => expect(rows()).toHaveLength(before));
    expect(box().value).toBe("");
    await waitFor(() => expect(document.activeElement).toBe(box())); // the button is gone: the keyboard goes to the box
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

  it("using Find a name or a type while results show is back to the list", async () => {
    await open();
    const before = rows().length;
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "a" } });
    await waitFor(() => expect(screen.queryByText(s.showing)).toBeNull());
    await waitFor(() => expect(rows().length).toBeGreaterThan(1));
    expect(rows().length).toBeLessThanOrEqual(before);
  });

  it("a change to a shown name reads the search again, and Back while that is on its way is respected", async () => {
    const api = mockApi("table");
    const real = api.smartNames!.bind(api);
    let hold = false;
    let waiting = false;
    let release: () => void = () => {};
    api.smartNames = async (ref, words) => {
      if (hold) {
        waiting = true;
        await new Promise<void>((r) => (release = r));
      }
      return real(ref, words);
    };
    await open("/account/names/100.cmp1", api);
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    // a rename: the search is read again (held here), and Back is pressed meanwhile
    hold = true;
    fireEvent.click(screen.getByRole("button", { name: "Sister Ilse" }));
    const spelling = await screen.findByLabelText(w.spellLabel);
    fireEvent.input(spelling, { target: { value: "Sister Ilsa" } });
    fireEvent.click(screen.getByRole("button", { name: w.saveSpelling }));
    await waitFor(() => expect(waiting).toBe(true)); // (the search is read again after the rename)
    fireEvent.click(screen.getByRole("button", { name: s.back }));
    release();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(s.showing)).toBeNull(); // the late answer did not bring the results back
    expect(rows().length).toBeGreaterThan(1);
  });

  it("refuses more than 100 characters in plain words, and sends nothing", async () => {
    const api = await open();
    fireEvent.input(box(), { target: { value: "x".repeat(101) } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(s.tooLong)).toBeTruthy();
    expect(calls(api)).toHaveLength(0);
  });

  it("goes to the sign-in view when the session ended during a search", async () => {
    const api = mockApi("table");
    api.smartNames = async () => {
      throw new ApiError("signed-out");
    };
    await open("/account/names/100.cmp1", api);
    fireEvent.input(box(), { target: { value: "holy healer" } });
    fireEvent.click(screen.getByRole("button", { name: s.button }));
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });

  it("a search that hangs goes quiet after a minute with a way on, and a new press starts over", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      const api = mockApi("table");
      const real = api.smartNames!.bind(api);
      let hung = true;
      api.smartNames = async (ref, words) => {
        if (hung) return new Promise<never>(() => {}); // never answers
        return real(ref, words);
      };
      await open("/account/names/100.cmp1", api);
      fireEvent.input(box(), { target: { value: "holy healer" } });
      fireEvent.click(screen.getByRole("button", { name: s.button }));
      await screen.findByText(s.stage);
      expect(screen.queryByText(s.quiet)).toBeNull();
      await vi.advanceTimersByTimeAsync(61_000);
      expect(await screen.findByText(s.quiet)).toBeTruthy();
      hung = false;
      fireEvent.click(screen.getByRole("button", { name: s.button })); // not ignored now
      await waitFor(() => expect(rows()).toHaveLength(1));
      expect(screen.queryByText(s.quiet)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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
