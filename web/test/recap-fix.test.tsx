/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Tell DMbot what to fix" on the recap page (#2114): a note is sent, the page polls until the bot has
// sorted it, the DM keeps or removes points, and a rewrite uses one of the recap's tries. It shows only
// while RECAP_FIX_LIVE is true (false today), so these tests switch it on. The bot decides what the
// points are; the page shows them and sends back only the ids the DM kept.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import RecapFix from "../src/account/RecapFix";
import Recaps from "../src/account/Recaps";
import { FIX_MAX_LETTERS, fixWords as w } from "../src/content/recap-fix";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_FIX_LIVE() {
      return live.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
});

const draft = mockRecaps()[0]!;
const posted = mockRecaps()[1]!;
const path = (ref: string) => `/account/recaps/${ref}`;

async function open(api = mockApi("table"), ref = draft.ref) {
  live.on = true;
  render(<Recaps api={api} path={path(ref)} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const type = (value: string) =>
  fireEvent.input(screen.getByLabelText(w.label), { target: { value } });

async function sortedOnce(api = mockApi("table")) {
  await open(api);
  type("Fix the name");
  fireEvent.click(screen.getByRole("button", { name: w.send }));
  await screen.findByText("Fix the innkeeper's name", {}, { timeout: 5000 });
  return api as ReturnType<typeof mockApi>;
}

describe("the switch", () => {
  it("can only be on together with recaps", async () => {
    const real = await vi.importActual<
      typeof import("../src/content/recap-words")
    >("../src/content/recap-words");
    expect(!real.RECAP_FIX_LIVE || real.RECAPS_LIVE).toBe(true);
  });
});

describe("the box", () => {
  it("is not on the page while it is off", async () => {
    render(<Recaps api={mockApi("table")} path={path(draft.ref)} />);
    await screen.findByText(/The party crossed the Brynwater/);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("is not on a recap that was posted", async () => {
    live.on = true;
    render(<Recaps api={mockApi("table")} path={path(posted.ref)} />);
    await screen.findByText(posted.text);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("keeps Send off until there is something to send, and counts the letters", async () => {
    await open();
    const send = screen.getByRole("button", {
      name: w.send,
    }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    type("The innkeeper is Mara");
    expect(send.disabled).toBe(false);
    expect(screen.getByText(w.count(21))).toBeTruthy();
    expect(
      (screen.getByLabelText(w.label) as HTMLTextAreaElement).maxLength,
    ).toBe(FIX_MAX_LETTERS);
  });
});

describe("sorting a note", () => {
  it("polls until the points are ready, then shows corrections kept, style notes not, and the replies as they are", async () => {
    const api = await open();
    type("Fix the name and make it warmer");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findAllByText(w.sorting)).length).toBeGreaterThan(0);
    expect(
      await screen.findByText(
        "Fix the innkeeper's name",
        {},
        { timeout: 5000 },
      ),
    ).toBeTruthy();
    expect(
      api.calls.filter((c) => c.startsWith("fixResult")).length,
    ).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByText(
        "I can't add story that didn't happen. That's your call.",
      ),
    ).toBeTruthy();
    // Corrections start kept (the DM removes what they don't want); a style note is only kept on purpose.
    expect(
      screen.getByRole("button", {
        name: w.removeOne("Fix the innkeeper's name"),
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: w.keepOne("A warmer voice") }),
    ).toBeTruthy();
  }, 10000);

  it("shows what the bot said when it could not sort the note", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({ state: "failed", error: "sort_failed" });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["sort-failed"],
    );
  });

  it("says plainly when recaps are off for the plan", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({ state: "failed", error: "not_allowed" });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["not-allowed"],
    );
  });

  it("gives up after about two minutes, stops asking, and offers Check again on the same request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    let asked = 0;
    api.fixResult = async () => {
      asked += 1;
      return asked > 61
        ? {
            state: "done",
            points: {
              corrections: [{ id: "c1", text: "Late point" }],
              style: [],
              replies: [],
            },
          }
        : { state: "working" };
    };
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await vi.advanceTimersByTimeAsync(130_000);
    expect(await screen.findByText(w.slow)).toBeTruthy();
    const stopped = asked;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(asked).toBe(stopped); // it stopped asking
    fireEvent.click(screen.getByRole("button", { name: w.checkAgain }));
    await vi.advanceTimersByTimeAsync(6_000);
    expect(await screen.findByText("Late point")).toBeTruthy();
  });

  it("stops asking when the page is left", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    let asked = 0;
    api.fixResult = async () => {
      asked += 1;
      return { state: "working" };
    };
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await vi.advanceTimersByTimeAsync(5_000);
    cleanup();
    const before = asked;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(asked).toBe(before);
  });

  it("looks once more after a dropped connection while sorting", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    let n = 0;
    api.fixResult = async () => {
      n += 1;
      if (n === 1) throw new ApiError("network");
      return {
        state: "done",
        points: {
          corrections: [{ id: "c1", text: "After the blip" }],
          style: [],
          replies: [],
        },
      };
    };
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await vi.advanceTimersByTimeAsync(3_000);
    expect(await screen.findByText("After the blip")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("uses the too-soon words when the bot's note limit fails the request", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({ state: "failed", error: "too_soon" });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["too-soon"],
    );
  });
});

describe("the rewrite", () => {
  async function sorted(api = mockApi("table")) {
    await open(api);
    type("Fix the name");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await screen.findByText("Fix the innkeeper's name", {}, { timeout: 5000 });
    return api as ReturnType<typeof mockApi>;
  }

  it("sends only the points the DM kept, says what it costs, and the approval has to be given again", async () => {
    const api = await sorted();
    expect(screen.getByText(w.cost(2))).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: w.removeOne("Fix the innkeeper's name"),
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: w.keepOne("A warmer voice") }),
    );
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    await waitFor(() =>
      expect(api.calls).toContain(`rewriteWith:${draft.ref}:r1:c2|s1`),
    );
    expect(await screen.findByText(w.started)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.rewrite })).toBeNull();
  }, 10000);

  it("will not rewrite with nothing kept", async () => {
    await sorted();
    fireEvent.click(
      screen.getByRole("button", {
        name: w.removeOne("Fix the innkeeper's name"),
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: w.removeOne("Shorten the second part"),
      }),
    );
    expect(
      (screen.getByRole("button", { name: w.rewrite }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText(w.nothingKept)).toBeTruthy();
  }, 10000);

  it("says there are no tries left", async () => {
    const api = mockApi("table");
    api.rewriteWith = async () => {
      throw new ApiError("no-tries");
    };
    await sorted(api);
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["no-tries"],
    );
  }, 10000);
});

describe("fresh starts and out-of-date points", () => {
  it("lets a removed correction be put back", async () => {
    await sortedOnce();
    fireEvent.click(
      screen.getByRole("button", {
        name: w.removeOne("Fix the innkeeper's name"),
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: w.putBackOne("Fix the innkeeper's name"),
      }),
    );
    expect(
      screen.getByRole("button", {
        name: w.removeOne("Fix the innkeeper's name"),
      }),
    ).toBeTruthy();
  }, 10000);

  it("says how many points there are", async () => {
    await sortedOnce();
    expect(screen.getByText(w.sorted(3))).toBeTruthy();
  }, 10000);

  it("says so once when nothing was found to change", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({
      state: "done",
      points: { corrections: [], style: [], replies: [] },
    });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(w.nothing)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.rewrite })).toBeNull();
  });

  it("shows only the bot's own answer, not a second line, when it has one and nothing to change", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({
      state: "done",
      points: {
        corrections: [],
        style: [],
        replies: ["Nothing to change there."],
      },
    });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText("Nothing to change there.")).toBeTruthy();
    expect(screen.queryByText(w.nothing)).toBeNull();
  });

  it("says that a kept style note is saved for later recaps, and where to remove it", async () => {
    await sortedOnce();
    const lead = screen.getByText(/saved for this campaign's later recaps too/);
    expect(lead.textContent).toContain("⚙️ Manage DMbot → 📜 Recaps → ✍️ Recap style");
    expect(lead.textContent).toContain("newest 10");
  }, 10000);

  it("asks to send again when the rewrite's request has been swept away (404)", async () => {
    const api = mockApi("table");
    api.rewriteWith = async () => {
      throw new ApiError("not-found");
    };
    await sortedOnce(api);
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["bad-choice"],
    );
    expect(screen.queryByRole("button", { name: w.rewrite })).toBeNull();
  }, 10000);

  it("uses neutral words for a busy moment on Rewrite", async () => {
    const api = mockApi("table");
    api.rewriteWith = async () => {
      throw new ApiError("too-soon");
    };
    await sortedOnce(api);
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["too-soon-rewrite"],
    );
  }, 10000);

  it("treats a finished request with no points as a fault, not a long wait", async () => {
    const api = mockApi("table");
    api.fixResult = async () => ({ state: "done" });
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem.server,
    );
  });

  it("throws away old points when a new note is sent, and sends the new request's id", async () => {
    const api = mockApi("table");
    let n = 0;
    api.sendFix = async () => ({ requestId: `req${++n}` });
    api.fixResult = async (_ref, id) => ({
      state: "done",
      points: {
        corrections: [{ id: "c1", text: `Point from ${id}` }],
        style: [],
        replies: [],
      },
    });
    await open(api);
    type("one");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await screen.findByText("Point from req1");
    type("two");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await screen.findByText("Point from req2");
    expect(screen.queryByText("Point from req1")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    await waitFor(() =>
      expect(
        api.calls.some((c) => c.startsWith(`rewriteWith:${draft.ref}:req2:`)),
      ).toBe(true),
    );
  });

  it("drops the points when the API says they are out of date", async () => {
    const api = mockApi("table");
    api.rewriteWith = async () => {
      throw new ApiError("bad-choice");
    };
    await sortedOnce(api);
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["bad-choice"],
    );
    expect(screen.queryByRole("button", { name: w.rewrite })).toBeNull();
  }, 10000);

  it("turns Rewrite off, and says why, when there is no Try again left", async () => {
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => ({ ...(await recap(ref)), triesLeft: 0 });
    await sortedOnce(api);
    expect(screen.getByText(w.noTriesLeft)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: w.rewrite }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  }, 10000);
});

describe("the line after a rewrite starts", () => {
  it("goes when the new draft is ready and the recap can change again", async () => {
    live.on = true;
    const api = mockApi("table");
    const { rerender } = render(
      <RecapFix api={api} recap={draft} onRecap={() => undefined} />,
    );
    type("Fix the name");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    await screen.findByText("Fix the innkeeper's name", {}, { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: w.rewrite }));
    // The parent now holds the recap as being written: it cannot be changed yet.
    rerender(
      <RecapFix
        api={api}
        recap={{ ...draft, canEdit: false, working: true }}
        onRecap={() => undefined}
      />,
    );
    expect(await screen.findByText(w.started)).toBeTruthy();
    // The new draft is ready: it can change again, and the old line is gone.
    rerender(
      <RecapFix
        api={api}
        recap={{ ...draft, canEdit: true }}
        onRecap={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.queryByText(w.started)).toBeNull());
  }, 10000);
});

describe("refusals when sending", () => {
  it.each([
    ["too-big", w.problem["too-big"]],
    ["too-soon", w.problem["too-soon"]],
    ["not-editable", w.problem["not-editable"]],
    ["not-allowed", w.problem["not-allowed"]],
  ])("shows the words for %s", async (kind, words) => {
    const api = mockApi("table");
    api.sendFix = async () => {
      throw new ApiError(kind as "too-big");
    };
    await open(api);
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(words);
  });
});

describe("over the wire", () => {
  const reply = (
    status: number,
    body: unknown,
    seen: { url: string; body: string }[] = [],
  ) =>
    httpApi("https://api.example.test", (async (
      url: string,
      init: RequestInit,
    ) => {
      seen.push({ url, body: String(init.body ?? "") });
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("sends the note, reads the points, and rewrites with the kept ids", async () => {
    const seen: { url: string; body: string }[] = [];
    const real = reply(
      200,
      {
        requestId: "abc",
        state: "done",
        recap: { ref: "1.a.5", text: "t" },
        triesLeft: 1,
        needsApproval: true,
      },
      seen,
    );
    // The API answers 202 to a note, as it does on the wire.
    await reply(202, { requestId: "abc" }, seen).sendFix!("1.a.5", "Fix it");
    await real.fixResult!("1.a.5", "abc");
    const done = await real.rewriteWith!("1.a.5", "abc", ["c1"], ["s1"]);
    expect(seen.map((s) => s.url)).toEqual([
      "https://api.example.test/me/recaps/1.a.5/instructions",
      "https://api.example.test/me/recaps/1.a.5/instructions/abc",
      "https://api.example.test/me/recaps/1.a.5/rewrite",
    ]);
    expect(seen[0]?.body).toBe(JSON.stringify({ text: "Fix it" }));
    expect(seen[2]?.body).toBe(
      JSON.stringify({ requestId: "abc", corrections: ["c1"], style: ["s1"] }),
    );
    expect(done.triesLeft).toBe(1);
    expect(done.needsApproval).toBe(true);
  });

  it("reads the refusals", async () => {
    await expect(
      reply(403, { error: "not_allowed" }).sendFix!("r", "x"),
    ).rejects.toMatchObject({ kind: "not-allowed" });
    await expect(
      reply(409, { error: "no_tries" }).rewriteWith!("r", "i", ["c1"], []),
    ).rejects.toMatchObject({ kind: "no-tries" });
    await expect(
      reply(409, { error: "not_editable" }).sendFix!("r", "x"),
    ).rejects.toMatchObject({ kind: "not-editable" });
    await expect(
      reply(413, { error: "too_big" }).sendFix!("r", "x"),
    ).rejects.toMatchObject({ kind: "too-big" });
    await expect(
      reply(429, { error: "too_soon" }).sendFix!("r", "x"),
    ).rejects.toMatchObject({ kind: "too-soon" });
    await expect(
      reply(422, { error: "bad_choice" }).rewriteWith!("r", "i", ["zz"], []),
    ).rejects.toMatchObject({ kind: "bad-choice" });
  });
});
