/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A yes on a waiting connection says whether the bot showed it to the players or kept it hidden (#3974, #3986), and
// "All true" says the same for all of them, in the plural and the singular. The shown and held claims are on a campaign
// whose ref ends `.shown-held` in the pretend API, so the other tests keep their claims.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { bulkWords as b } from "../src/content/waiting-bulk-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(cleanup);

let n = 0;
async function open(ending: string, api = mockApi("table")) {
  render(<WebWaiting api={api} campaignRef={`100.shown${(n += 1)}${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
/** A line the page joins with the others in one paragraph, so it is found by its words. */
const saidLine = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
/** The yes button of the claim with this statement. */
const yesFor = (statement: string) => within(screen.getByText(statement).closest(".panel") as HTMLElement).getByRole("button", { name: /Yes, that's true/ });

describe("a yes says whether the bot showed it or kept it hidden", () => {
  it("a yes on a claim the bot showed adds the players-can-see line", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("Tavin is Mira's cousin"));
    expect(await screen.findByText(w.shownOne)).toBeTruthy();
    expect(screen.queryByText(w.heldOne)).toBeNull();
  });

  it("a yes on a claim the bot held adds the kept-hidden line", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("Mira Voss grew up in Far Hall"));
    expect(await screen.findByText(w.heldOne)).toBeTruthy();
    expect(screen.queryByText(w.shownOne)).toBeNull();
  });

  it("no extra line without shown or held", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("Mira Voss knows Far Hall"));
    expect(await screen.findByText(w.done.confirm)).toBeTruthy();
    expect(screen.queryByText(w.shownOne)).toBeNull();
    expect(screen.queryByText(w.heldOne)).toBeNull();
  });

  it("All true counts shown and held across the answers, in the singular", async () => {
    await open(".shown-held");
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(saidLine(b.shownMany(1)), {}, { timeout: 8000 })).toBeTruthy();
    expect(screen.getByText(saidLine(b.heldMany(1)))).toBeTruthy();
  }, 15000);

  it("All true says the plural when several were shown", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => ({ shown: 1 });
    await open("", api);
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(saidLine(b.shownMany(2)), {}, { timeout: 8000 })).toBeTruthy();
  }, 15000);
});
