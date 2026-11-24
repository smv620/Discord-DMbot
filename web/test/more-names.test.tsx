/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "📝 More names" on the Questions page (#5081): the press, the words after it (Discord's own), the wait it shows and its refusals.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { moreNamesWords as w } from "../src/content/more-names-words";
import { ApiError } from "../src/account/api";
import MoreNames from "../src/account/MoreNames";
import { mockApi } from "../src/account/mock";

afterEach(() => cleanup());

describe("the words", () => {
  it("say what was made and what still waits, as Discord does", () => {
    expect(w.result(12, 40)).toBe("📝 12 more names to check from your imported sessions. Say which are in your game under New names to check. 40 more wait: press 📝 More names again after these.");
    expect(w.result(1, 0)).toBe("📝 1 more name to check from your imported sessions. Say which are in your game under New names to check.");
    expect(w.result(0, 5)).toBe("DMbot already knew those names. 5 more wait: press 📝 More names again.");
    expect(w.result(0, 1)).toBe("DMbot already knew those names. 1 more waits: press 📝 More names again.");
    expect(w.result(0, 0)).toBe("✅ No more names are waiting.");
  });
});

describe("the button", () => {
  it("presses, says the result, and the second press says none are left", async () => {
    const api = mockApi("table");
    render(<MoreNames api={api} campaignRef="100.cmp1" />);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(`${w.result(12, 40)} ${w.countLater}`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(`${w.result(0, 0)} ${w.countLater}`)).toBeTruthy();
  });

  it("shows that DMbot is working while it waits, and the button is off meanwhile", async () => {
    const api = mockApi("table");
    let finish: (v: { made: number; left: number }) => void = () => {};
    api.moreNames = () => new Promise((ok) => (finish = ok));
    render(<MoreNames api={api} campaignRef="100.cmp1" />);
    const button = screen.getByRole("button", { name: w.button }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(await screen.findByText(w.working)).toBeTruthy();
    expect(button.disabled).toBe(true);
    finish({ made: 3, left: 0 });
    expect(await screen.findByText(`${w.result(3, 0)} ${w.countLater}`)).toBeTruthy();
    expect(button.disabled).toBe(false);
  });

  it("says why when it is refused, and when it fails", async () => {
    const api = mockApi("table");
    api.moreNames = vi.fn().mockRejectedValueOnce(new ApiError("only-dm")).mockRejectedValueOnce(new ApiError("too-soon")).mockRejectedValueOnce(new ApiError("server"));
    render(<MoreNames api={api} campaignRef="100.cmp1" />);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(w.notAllowed)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(w.tooSoon)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(w.failed)).toBeTruthy();
  });

  it("shows nothing when the API has no such call (an older server)", () => {
    const api = mockApi("table");
    delete api.moreNames;
    const { container } = render(<MoreNames api={api} campaignRef="100.cmp1" />);
    expect(container.textContent).toBe("");
  });
});

describe("on the Questions page", () => {
  it("is only there while its switch is on", async () => {
    vi.resetModules();
    vi.doMock("../src/content/more-names-switch", () => ({ MORE_NAMES_LIVE: true }));
    const { default: Questions } = await import("../src/account/Questions");
    const api = mockApi("table");
    api.myQuestions = async () => ({
      serverNow: 1,
      campaigns: [{ ref: "100.cmp1", name: "Test", updatedAt: 1, kinds: [{ kind: "more_names", count: 52, oldest: 1 }] }],
    }) as never;
    render(<Questions api={api} />);
    expect(await screen.findByRole("button", { name: w.button })).toBeTruthy();
    vi.doUnmock("../src/content/more-names-switch");
  });

  it("is not there while its switch is off (the shipped default)", async () => {
    vi.resetModules();
    vi.doMock("../src/content/more-names-switch", () => ({ MORE_NAMES_LIVE: false }));
    const { default: Questions } = await import("../src/account/Questions");
    const api = mockApi("table");
    api.myQuestions = async () => ({
      serverNow: 1,
      campaigns: [{ ref: "100.cmp1", name: "Test", updatedAt: 1, kinds: [{ kind: "more_names", count: 52, oldest: 1 }] }],
    }) as never;
    render(<Questions api={api} />);
    await screen.findByText(/More names from your sessions/);
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
    vi.doUnmock("../src/content/more-names-switch");
  });
});
