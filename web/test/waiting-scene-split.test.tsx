/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A yes on a waiting connection says when the bot kept it for the characters who were there, or hid it because the party
// was split and it could not tell which group heard it (#4035, copy of #3989's shown and held lines). "All true" says the
// same for all of them, in the plural and the singular. The scene and split claims are on campaign refs ending `.shown-held`.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { bulkWords as b } from "../src/content/waiting-bulk-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(cleanup);

let n = 0;
async function open(ending: string, api = mockApi("table")) {
  render(<WebWaiting api={api} campaignRef={`100.scene${(n += 1)}${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
/** A line the page joins with the others in one paragraph, so it is found by its words. */
const saidLine = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
/** The yes button of the claim with this statement. */
const yesFor = (statement: string) => within(screen.getByText(statement).closest(".panel") as HTMLElement).getByRole("button", { name: /Yes, that's true/ });

describe("a yes says whether the bot kept it for those who were there, or hid it because the party was split", () => {
  it("a yes on a claim said while the party was split adds the only-those-there line", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("The Ferryman sold Mira the map"));
    expect(await screen.findByText(w.sceneOne)).toBeTruthy();
    expect(screen.queryByText(w.splitOne)).toBeNull();
  });

  it("a yes on a split claim adds the who-heard-it line", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("Roan heard the bell in the tower"));
    expect(await screen.findByText(w.splitOne)).toBeTruthy();
    expect(screen.queryByText(w.sceneOne)).toBeNull();
  });

  it("no extra line for an ordinary yes", async () => {
    await open(".shown-held");
    fireEvent.click(yesFor("Mira Voss knows Far Hall"));
    expect(await screen.findByText(w.done.confirm)).toBeTruthy();
    expect(screen.queryByText(w.sceneOne)).toBeNull();
    expect(screen.queryByText(w.splitOne)).toBeNull();
  });

  it("All true says the singular of each", async () => {
    await open(".shown-held");
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(saidLine(b.sceneMany(1)), {}, { timeout: 8000 })).toBeTruthy();
    expect(screen.getByText(saidLine(b.splitMany(1)))).toBeTruthy();
  }, 15000);

  // The count is the two plain claims of the default campaign (mock.ts), each answered with the same scene line.
  it("All true says the plural of each when several were scene and split", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => ({ scene: 1 });
    await open("", api);
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(saidLine(b.sceneMany(2)), {}, { timeout: 8000 })).toBeTruthy();
  }, 15000);

  it("All true says the kept-hidden plural when several were split", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => ({ split: 1 });
    await open("", api);
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(saidLine(b.splitMany(2)), {}, { timeout: 8000 })).toBeTruthy();
  }, 15000);
});
