/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Names page's Full / Partial campaign filter (#3675): the list of sessions and lore is hidden under
// Full campaign, read once when Partial is chosen, ticks are reported, going back clears them, and the
// pretend list is narrowed by the ticks.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import NameSources from "../src/account/NameSources";
import { sourceWords as words } from "../src/content/name-sources-words";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";

function pickScope(value: string): void {
  const select = screen.getByLabelText(words.label) as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("NameSources", () => {
  it("hides the list under Full campaign, the default, and reads nothing", () => {
    const api = mockApi("table");
    render(<NameSources api={api} campaignRef={REF} picked={[]} onPick={() => {}} />);
    expect((screen.getByLabelText(words.label) as HTMLSelectElement).value).toBe("full");
    expect(screen.queryByText(words.legend)).toBeNull();
    expect(api.calls.filter((c) => c.startsWith("nameSources"))).toEqual([]);
  });

  it("shows sessions and lore for Partial campaign and reports what is ticked", async () => {
    const api = mockApi("table");
    const onPick = vi.fn();
    render(<NameSources api={api} campaignRef={REF} picked={["s2"]} onPick={onPick} />);
    pickScope("partial");
    expect(await screen.findByText(words.legend)).toBeTruthy();
    expect(screen.getByText("Session 1")).toBeTruthy();
    expect(screen.getByText(`${words.lore}: The Brynwater handout`)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Session 1"));
    expect(onPick).toHaveBeenLastCalledWith(["s2", "s1"]);
    fireEvent.click(screen.getByLabelText("Session 2"));
    expect(onPick).toHaveBeenLastCalledWith([]);
  });

  it("clears the ticks when it goes back to Full campaign, and hides the list", async () => {
    const api = mockApi("table");
    const onPick = vi.fn();
    render(<NameSources api={api} campaignRef={REF} picked={["s1"]} onPick={onPick} />);
    pickScope("partial");
    await screen.findByText(words.legend);
    pickScope("full");
    expect(onPick).toHaveBeenLastCalledWith([]);
    await waitFor(() => expect(screen.queryByText(words.legend)).toBeNull());
  });

  it("says so when the sessions and lore don't load", async () => {
    const api = { ...mockApi("table"), nameSources: async () => Promise.reject(new Error("down")) };
    render(<NameSources api={api} campaignRef={REF} picked={[]} onPick={() => {}} />);
    pickScope("partial");
    expect(await screen.findByText(words.failed)).toBeTruthy();
  });

  it("narrows the pretend list by the sources", async () => {
    const api = mockApi("table");
    const query = { q: "", kind: "", sort: "name" as const, page: 1 };
    const all = await api.allNames!(REF, query);
    const some = await api.allNames!(REF, { ...query, sources: ["l1"] });
    expect(some.total).toBeGreaterThan(0);
    expect(some.total).toBeLessThan(all.total);
  });
});
