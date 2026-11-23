/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The wait while a file is built (#3347 row 8), for the house rules and homebrew downloads: the building line shows while the
// file is with DMbot, a slow line after a minute, and nothing when nothing runs.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi } from "../src/account/api";
import HomebrewPageView from "../src/account/Homebrew";
import { mockApi } from "../src/account/mock";
import HouseRulesPage from "../src/account/HouseRules";
import { text as homebrewText } from "../src/content/homebrew";
import { text as houseRulesText } from "../src/content/house-rules";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const REF = "200000000000000001.cmp1";

describe("the building line while a house rules file is built", () => {
  it("shows the building line while the file is with DMbot, and removes it when it comes back", async () => {
    const api = mockApi("table");
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    const file = api.houseRulesFile!.bind(api);
    api.houseRulesFile = async (ref) => {
      await gate;
      return file(ref);
    };
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<HouseRulesPage api={api} path={`/account/house-rules/${REF}`} />);
    fireEvent.click(await screen.findByRole("button", { name: houseRulesText.download }));
    expect(await screen.findByText(houseRulesText.buildingFile)).toBeTruthy();
    release();
    await waitFor(() => expect(screen.queryByText(houseRulesText.buildingFile)).toBeNull());
  });

  it("shows nothing when nothing is running, and says what to do when it is slow", async () => {
    const api = mockApi("table");
    render(<HouseRulesPage api={api} path={`/account/house-rules/${REF}`} />);
    await screen.findByRole("button", { name: houseRulesText.download });
    expect(screen.queryByText(houseRulesText.buildingFile)).toBeNull();
    cleanup();
    // The fake clock is set before the page is drawn, as the other slow-line tests do, since the wait reads Date.now.
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date(1_900_000_000 * 1000));
    const slow = mockApi("table");
    slow.houseRulesFile = () => new Promise(() => undefined);
    render(<HouseRulesPage api={slow} path={`/account/house-rules/${REF}`} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: houseRulesText.download }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(59_000);
    });
    expect(screen.getByText(houseRulesText.buildingFile)).toBeTruthy();
    expect(screen.queryByText(houseRulesText.buildingSlow)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(houseRulesText.buildingSlow)).toBeTruthy();
  });
});

describe("the building line while a homebrew file is built", () => {
  it("shows the building line while the file is with DMbot, and removes it when it comes back", async () => {
    const api = mockApi("table");
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    const file = (api as Required<AccountApi>).homebrewFile.bind(api);
    api.homebrewFile = async (ref) => {
      await gate;
      return file(ref);
    };
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<HomebrewPageView api={api} path={`/account/homebrew/${REF}`} />);
    fireEvent.click(await screen.findByRole("button", { name: homebrewText.download }));
    expect(await screen.findByText(homebrewText.buildingFile)).toBeTruthy();
    release();
    await waitFor(() => expect(screen.queryByText(homebrewText.buildingFile)).toBeNull());
  });
});
