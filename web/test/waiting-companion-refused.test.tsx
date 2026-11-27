/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A refused keep of a companion (#5348; core #5346): the page says why, in the store's own sentence, and what to press.
// Other refusals keep their own words, and the sentence is never in the title.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(() => cleanup());

const REF = "100.cmp1.companion";
const SENTENCE = "Only a character who isn't a player character can be a companion.";
const LINE = `DMbot can't keep that one. ${SENTENCE} Press ❌ Leave it out to move on.`;

async function page(api: AccountApi) {
  render(<WebWaiting api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: w.heading });
}

describe("a refused companion keep", () => {
  it("says why and what to press, with the store's sentence as given", async () => {
    const api = mockApi("table");
    await page(api);
    const keep = screen.getAllByRole("button", { name: w.confirm })[0]!;
    fireEvent.click(keep);
    expect(await screen.findByText(LINE)).toBeTruthy();
  });

  it("the item stays, and Leave it out still works", async () => {
    const api = mockApi("table");
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    await screen.findByText(LINE);
    expect(screen.getByText("Glyph travels with Cerric", { exact: false })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: w.leaveOut }).length).toBeGreaterThan(0);
  });

  it("a companion error with no sentence says the general words", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw new ApiError("companion");
    };
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    await waitFor(() => expect(screen.queryByText(LINE)).toBeNull());
    expect(screen.queryByText(/can't keep that one/)).toBeNull();
  });

  it("another refusal still says its own words, not the companion line", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw Object.assign(new ApiError("gone"), { note: "Some other refusal" });
    };
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    await waitFor(() => expect(screen.queryByText(/can't keep that one/)).toBeNull());
  });

  it("the sentence is not in the document title", async () => {
    const api = mockApi("table");
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    await screen.findByText(LINE);
    expect(document.title).not.toContain(SENTENCE);
  });
});
