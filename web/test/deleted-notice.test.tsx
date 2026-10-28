/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Deleted <name>" line left for My Account when a campaign is deleted from its own page (#2879). It is kept in
// session storage, shown once, and never breaks the page when storage is blocked.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { takeDeleted, rememberDeleted } from "../src/account/deleted-notice";
import { mockApi } from "../src/account/mock";
import { campaignDeleteWords } from "../src/content/campaign-delete";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("the deleted notice", () => {
  it("round trip: what was remembered is taken once, and a second take is null", () => {
    rememberDeleted("Lost Hollow", true);
    expect(takeDeleted()).toEqual({ name: "Lost Hollow", channelsLeft: true });
    expect(takeDeleted()).toBeNull();
  });

  it("storage that throws gives null and does not throw", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(takeDeleted()).toBeNull();
  });

  it("writing to blocked storage does not throw", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(() => rememberDeleted("Lost Hollow", false)).not.toThrow();
  });

  it("My Account shows the line once after a delete, and not again on a reload", async () => {
    rememberDeleted("Lost Hollow", true);
    const first = render(<Account api={mockApi("lost-server")} go={vi.fn()} search="" />);
    await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(screen.getByText(`${campaignDeleteWords.deleted("Lost Hollow")} ${campaignDeleteWords.channelsLeft}`)).toBeTruthy();
    first.unmount();
    cleanup();
    render(<Account api={mockApi("lost-server")} go={vi.fn()} search="" />);
    await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(screen.queryByText(campaignDeleteWords.deleted("Lost Hollow"), { exact: false })).toBeNull();
  });
});
