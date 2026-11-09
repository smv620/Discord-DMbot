/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Words DMbot stopped asking about" (#3577), against the pretend API: the list with each answer in plain words, a button
// that lifts one (the sentence is the issue's), a refusal worded, the empty sentence, and never a word for someone who is
// not a game master of the campaign.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import KindOffersPanel from "../src/account/KindOffersPanel";
import { kindOffersWords as w } from "../src/content/kind-offers-words";
import { savingWait } from "../src/content/work-progress";

afterEach(cleanup);

const REF = "100.cmp1";
const open = async (ref = REF, api = mockApi("table")) => {
  render(<KindOffersPanel api={api} campaignRef={ref} />);
  return api;
};
const lifts = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("liftKindOffer"));

describe("the words DMbot stopped asking about", () => {
  it("lists each answer in quotes with Never or the asked-again words", async () => {
    await open();
    await screen.findByText(w.heading);
    expect(screen.getByText("“sworn on the ashes of”").nextElementSibling?.textContent).toBe(w.never);
    expect(screen.getByText("“carries the lantern for”").nextElementSibling?.textContent).toBe(w.later);
    expect(screen.getByText(w.lead)).toBeTruthy(); // (says why the list is there and what the two answers meant)
  });

  it("lifts one, says what happens next and leaves the other", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.liftFor("sworn on the ashes of") }));
    await screen.findByText(w.lifted("sworn on the ashes of"));
    expect(w.lifted("sworn on the ashes of")).toBe("DMbot may ask about “sworn on the ashes of” again after your next session.");
    expect(lifts(api)).toEqual([`liftKindOffer:${REF}:sworn on the ashes of`]);
    expect(screen.queryByRole("button", { name: w.liftFor("sworn on the ashes of") })).toBeNull();
    expect(screen.getByRole("button", { name: w.liftFor("carries the lantern for") })).toBeTruthy();
  });

  it("shows DMbot's stage while a lift is out, and clears it when it is answered", async () => {
    const api = mockApi("table");
    const real = api.liftKindOffer!.bind(api);
    let finish!: () => void;
    api.liftKindOffer = (...args: Parameters<typeof real>) =>
      new Promise((resolve) => {
        finish = () => resolve(real(...args));
      });
    await open(REF, api);
    fireEvent.click(await screen.findByRole("button", { name: w.liftFor("sworn on the ashes of") }));
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => finish());
    await screen.findByText(w.lifted("sworn on the ashes of"));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });

  it("says the empty sentence when every answer is lifted", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: w.liftFor("sworn on the ashes of") }));
    await screen.findByText(w.lifted("sworn on the ashes of"));
    fireEvent.click(await screen.findByRole("button", { name: w.liftFor("carries the lantern for") }));
    await screen.findByText(w.empty);
  });

  it("asks once when pressed twice", async () => {
    const api = await open();
    const button = await screen.findByRole("button", { name: w.liftFor("sworn on the ashes of") });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(w.lifted("sworn on the ashes of"));
    expect(lifts(api)).toHaveLength(1);
  });

  it("words a refusal plainly and keeps the list", async () => {
    await open(`${REF}.lift-fails`);
    fireEvent.click(await screen.findByRole("button", { name: w.liftFor("sworn on the ashes of") }));
    await screen.findByText(w.problems["server"] as string);
    expect(screen.getByRole("button", { name: w.liftFor("sworn on the ashes of") })).toBeTruthy();
  });

  it("shows nothing for someone who is not a game master of it", async () => {
    const api = await open(`${REF}.names-manager`);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText(w.heading)).toBeNull();
    expect(document.body.textContent).not.toContain("sworn on the ashes of");
    expect(api.calls.some((c) => c.startsWith("liftKindOffer"))).toBe(false);
  });

  it("has no accessibility violations", async () => {
    await open();
    await screen.findByText(w.heading);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
