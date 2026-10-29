/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The recap link on My Account and the recap list say nothing about recaps while they are off
// (#1401): RECAPS_LIVE is false until dev1 has checked a real recap (content/recap-words.ts).
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { mockApi } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { text } from "../src/content/recaps";

// A switch the tests can flip; the real constant is false.
const live = vi.hoisted(() => ({ value: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.value;
    },
  };
});

afterEach(() => {
  cleanup();
  live.value = false;
});

const linkText = text.linkToList;

describe("My Account's link to the recaps", () => {
  it("is not there while recaps are off", async () => {
    render(<Account api={mockApi("table")} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByText(linkText)).toBeNull();
    expect(document.querySelector('a[href="/account/recaps"]')).toBeNull();
  });

  it("is there once recaps are live, for someone with a campaign", async () => {
    live.value = true;
    render(<Account api={mockApi("table")} go={vi.fn()} />);
    const link = await screen.findByText(linkText);
    expect(link.getAttribute("href")).toBe("/account/recaps");
  });
});

describe("the recap list", () => {
  it("promises nothing while recaps are off and there is nothing to show", async () => {
    const api = { ...mockApi("table"), recaps: async () => [] };
    render(<Recaps api={api} path="/account/recaps" />);
    expect(await screen.findByText(text.emptyOff)).toBeTruthy();
    expect(screen.queryByText(text.lead)).toBeNull();
    expect(screen.queryByText(/emails you/)).toBeNull();
  });

  it("still lists recaps that exist (a test server) with the lead line", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" />);
    expect(await screen.findByText("The ferry at dusk")).toBeTruthy();
    expect(screen.getByText(text.lead)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("link", { name: /Open/ })[0]!);
  });

  it("says the email promise once recaps are live", async () => {
    live.value = true;
    const api = { ...mockApi("table"), recaps: async () => [] };
    render(<Recaps api={api} path="/account/recaps" />);
    expect(await screen.findByText(text.empty)).toBeTruthy();
    expect(screen.getByText(text.lead)).toBeTruthy();
  });
});
