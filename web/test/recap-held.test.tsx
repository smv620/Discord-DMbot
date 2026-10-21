/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A recap held for the DM's answers about names (#2210, #2169): the list says it is waiting, and
// the page says one line about what to do, in place of the draft and every button that acts on it.
// The fields (`waitingForNames`, `namesToAnswer`) are standing in for the API's (#2174).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { heldWords, isHeld, statusLine, text } from "../src/content/recaps";

afterEach(cleanup);

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;

function withHeld(extra: Partial<Recap>): AccountApi {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  const list = api.recaps!.bind(api);
  api.recap = async (ref) => ({ ...(await read(ref)), ...extra });
  api.recaps = async () => (await list()).map((r) => ({ ...r, ...extra }));
  return api;
}

describe("the words", () => {
  it("count the questions, and say 1 question for one", () => {
    expect(heldWords.line(3)).toBe(
      "DMbot will write this recap once you answer the 3 questions about names in Discord: 📝 Check new names on the DM screen.",
    );
    expect(heldWords.line(1)).toContain("the 1 question about names");
    expect(heldWords.line(1)).not.toContain("1 questions");
  });

  it("still make sense with no count", () => {
    for (const none of [undefined, 0]) {
      expect(heldWords.line(none)).toBe(
        "DMbot will write this recap once you answer its questions about names in Discord: 📝 Check new names on the DM screen.",
      );
    }
  });

  it("put the status first for a held draft, and leave the others alone", () => {
    expect(statusLine({ ...base, status: "draft", working: true, waitingForNames: true })).toBe(
      "Waiting for your answers about names",
    );
    expect(statusLine({ ...base, status: "draft", working: true })).toBe("Being made");
    expect(statusLine({ ...base, status: "posted", waitingForNames: false })).toBe("Posted");
  });
});

describe("what counts as held", () => {
  it("is the flag alone, whatever the entry's status or state, so the list and the page agree", () => {
    // The API's held entry (#2174): state "waiting", status "draft", the flag set.
    expect(isHeld({ ...base, state: "waiting", status: "draft", waitingForNames: true })).toBe(true);
    expect(isHeld({ ...base, state: "waiting", status: "posted", waitingForNames: true })).toBe(true);
    expect(isHeld({ ...base, status: "draft", waitingForNames: false })).toBe(false);
    const { waitingForNames: _none, ...older } = { ...base, waitingForNames: undefined };
    expect(isHeld(older as Recap)).toBe(false); // an API without the field
  });

  it("shows the API's own held entry as held, on the page", async () => {
    render(
      <Recaps
        api={withHeld({
          state: "waiting",
          status: "draft",
          working: false,
          text: "",
          summary: "",
          canEdit: false,
          canPost: false,
          canTryAgain: false,
          canDrop: false,
          waitingForNames: true,
          namesToAnswer: 4,
        })}
        path={path}
      />,
    );
    expect(await screen.findByText(heldWords.line(4))).toBeTruthy();
  });

  it("puts held before a failed state in the list, as the page does", () => {
    const failed = { ...base, status: "draft" as const, state: "failed", waitingForNames: true };
    expect(statusLine(failed)).toBe(heldWords.status);
  });

  it("asks nothing about names or choices while held", async () => {
    const api = withHeld({ status: "draft", waitingForNames: true, namesToAnswer: 2 });
    const names = vi.fn(api.campaignNames!.bind(api));
    const choices = vi.fn(api.recapChoices!.bind(api));
    api.campaignNames = names;
    api.recapChoices = choices;
    render(<Recaps api={api} path={path} />);
    await screen.findByText(heldWords.line(2));
    expect(names).not.toHaveBeenCalled();
    expect(choices).not.toHaveBeenCalled();
  });
});

describe("the list", () => {
  it("says a held recap is waiting for the answers, in place of 'Being made'", async () => {
    render(<Recaps api={withHeld({ waitingForNames: true, namesToAnswer: 2, working: true })} path="/account/recaps" />);
    expect((await screen.findAllByText(heldWords.status)).length).toBeGreaterThan(0);
    expect(screen.queryByText("Being made")).toBeNull();
  });

  it("is as before when nothing is held, or the API is older", async () => {
    render(<Recaps api={withHeld({ waitingForNames: false })} path="/account/recaps" />);
    await screen.findAllByText(/Ready to read|Posted|Being made/);
    expect(screen.queryByText(heldWords.status)).toBeNull();
  });
});

describe("the page", () => {
  it("shows one line instead of the draft, with the number of questions", async () => {
    render(<Recaps api={withHeld({ status: "draft", waitingForNames: true, namesToAnswer: 2 })} path={path} />);
    expect(await screen.findByText(heldWords.line(2))).toBeTruthy();
    expect(screen.queryByText(/The party crossed/)).toBeNull(); // the draft stays hidden
  });

  it("says '1 question' for one", async () => {
    render(<Recaps api={withHeld({ status: "draft", waitingForNames: true, namesToAnswer: 1 })} path={path} />);
    expect(await screen.findByText(/the 1 question about names/)).toBeTruthy();
  });

  it("renders none of Edit, Make it again, Post, Don't post, Approve or the audio panel while held", async () => {
    render(
      <Recaps
        api={withHeld({
          status: "draft",
          waitingForNames: true,
          namesToAnswer: 2,
          canEdit: true,
          canPost: true,
          canTryAgain: true,
          canDrop: true,
          triesLeft: 2,
        })}
        path={path}
      />,
    );
    await screen.findByText(heldWords.line(2));
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryByText(text.beforeChoosing)).toBeNull();
    expect(screen.queryByText(text.onlyYou)).toBeNull();
    // The way back to the list stays.
    expect(screen.getByRole("link", { name: text.back })).toBeTruthy();
  });

  it("goes back to the normal page when it is no longer held", async () => {
    render(<Recaps api={withHeld({ status: "draft", waitingForNames: false, namesToAnswer: 0 })} path={path} />);
    expect(await screen.findByText(/The party crossed/)).toBeTruthy();
    expect(screen.queryByText(/DMbot will write this recap once/)).toBeNull();
  });
});

describe("while held", () => {
  it("does not look again every few seconds, even for a recap marked as working", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = withHeld({ status: "draft", working: true, waitingForNames: true, namesToAnswer: 2 });
      const read = vi.fn(api.recap!.bind(api));
      api.recap = read;
      render(<Recaps api={api} path={path} />);
      await screen.findByText(heldWords.line(2));
      const opened = read.mock.calls.length;
      await vi.advanceTimersByTimeAsync(30_000);
      expect(read.mock.calls.length).toBe(opened); // no poll
    } finally {
      vi.useRealTimers();
    }
  });
});
