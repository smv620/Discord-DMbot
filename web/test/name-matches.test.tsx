/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "DMbot's guesses" on the Names page (#3602): a DM sees the matches DMbot made, says yes or takes one back,
// All answers the rest, a wait says what it is doing, a refusal says why, and with none waiting the part is not
// there at all. Nothing but names, a change number and a time is ever shown.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type MatchRow } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import NameMatches from "../src/account/NameMatches";
import { matchWords as w } from "../src/content/name-matches-words";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";
const row = (batch: string, spelling: string, keep: string): MatchRow => ({ batch, keepId: "a".repeat(32), keepName: keep, spelling, at: 1_790_000_000 });

function api(over: Partial<AccountApi> = {}, list: MatchRow[] = [row("11", "The Ferry Lady", "Old Tam"), row("12", "the smith", "Mira Voss")]): AccountApi {
  let now = [...list];
  return {
    ...mockApi("table"),
    nameMatches: async () => now.map((m) => ({ ...m })),
    answerMatch: async (_ref: string, batch: string) => {
      now = now.filter((m) => m.batch !== batch);
    },
    ...over,
  } as AccountApi;
}

describe("NameMatches", () => {
  it("lists each guess in plain words and says players still see two names", async () => {
    render(<NameMatches api={api()} campaignRef={REF} />);
    expect(await screen.findByText(w.line("The Ferry Lady", "Old Tam"))).toBeTruthy();
    expect(screen.getByText(w.line("the smith", "Mira Voss"))).toBeTruthy();
    expect(screen.getByText(w.lead)).toBeTruthy();
    expect(screen.getByText(w.countOf(2))).toBeTruthy();
  });

  it("says yes to one guess, tells the DM, and the guess leaves the list", async () => {
    const answered = vi.fn(async () => {});
    const a = api({ answerMatch: answered });
    render(<NameMatches api={a} campaignRef={REF} />);
    await screen.findByText(w.line("The Ferry Lady", "Old Tam"));
    fireEvent.click(screen.getAllByRole("button", { name: /Yes, that's right/ })[0]!);
    await waitFor(() => expect(answered).toHaveBeenCalledWith(REF, "11", "confirm", expect.any(Function)));
    expect(await screen.findByText(w.confirmed("The Ferry Lady", "Old Tam"))).toBeTruthy();
  });

  it("takes a guess back with Undo and says where the name goes", async () => {
    const answered = vi.fn(async () => {});
    render(<NameMatches api={api({ answerMatch: answered })} campaignRef={REF} />);
    await screen.findByText(w.line("the smith", "Mira Voss"));
    fireEvent.click(screen.getAllByRole("button", { name: /Undo/ })[1]!);
    await waitFor(() => expect(answered).toHaveBeenCalledWith(REF, "12", "undo", expect.any(Function)));
    expect(await screen.findByText(w.undone("the smith", "Mira Voss"))).toBeTruthy();
  });

  it("answers every guess with All, one at a time", async () => {
    const answered = vi.fn(async () => {});
    render(<NameMatches api={api({ answerMatch: answered })} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: /All 2 are right/ }));
    await waitFor(() => expect(answered).toHaveBeenCalledTimes(2));
    expect(answered.mock.calls.map((c) => (c as unknown[])[1])).toEqual(["11", "12"]);
    expect(await screen.findByText(w.confirmedMany(2))).toBeTruthy();
  });

  it("says why when an answer is refused, and keeps the rest of the list", async () => {
    const a = api({
      answerMatch: async () => {
        throw new ApiError("gone");
      },
    });
    render(<NameMatches api={a} campaignRef={REF} />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Yes, that's right/ }))[0]!);
    expect(await screen.findByText(w.gone)).toBeTruthy();
    expect(screen.getByText(w.line("the smith", "Mira Voss"))).toBeTruthy();
  });

  it("shows nothing at all when no guess is waiting", async () => {
    const { container } = render(<NameMatches api={api({}, [])} campaignRef={REF} />);
    await waitFor(() => expect(container.querySelector("#matches-h")).toBeNull());
  });

  it("says so when the list does not load", async () => {
    const a = api({
      nameMatches: async () => {
        throw new ApiError("server");
      },
    });
    render(<NameMatches api={a} campaignRef={REF} />);
    expect(await screen.findByText(w.failed)).toBeTruthy();
  });

  it("shows a wait while an answer is on the way", async () => {
    let finish: () => void = () => {};
    const a = api({ answerMatch: () => new Promise<void>((ok) => (finish = ok)) });
    render(<NameMatches api={a} campaignRef={REF} />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Yes, that's right/ }))[0]!);
    expect(await screen.findByText(new RegExp(w.stage))).toBeTruthy();
    finish();
    await screen.findByText(w.confirmed("The Ferry Lady", "Old Tam"));
  });
});
