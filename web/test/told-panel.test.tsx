/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Tavin now knows the guard is Ken" and Take back in the Story Graph's name panel (#3542), against the pretend API.
// DM only; the choice is offered only to holders who were not told; told, then Take back (asked once); each refusal worded.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import ToldPanel from "../src/account/ToldPanel";
import { savingWait } from "../src/content/work-progress";
import { toldWords as w } from "../src/content/told-words";

afterEach(cleanup);

const REF = "100.cmp1";
const open = async (ref = REF, entry = "n1", api = mockApi("table")) => {
  render(<ToldPanel api={api} campaignRef={ref} entryId={entry} entryName="Ken" />);
  return api;
};
const changes = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("changeName"));

describe("who has been told a secret name", () => {
  it("offers the choice only to the holder who was not told, and says who was told", async () => {
    await open();
    expect((await screen.findByRole("button", { name: w.tell("Tavin", "the guard", "Ken") })).textContent).toBe("Tavin now knows the guard is Ken");
    expect(screen.getByText(w.told("Wen", "the guard", "Ken"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.tell("Wen", "the guard", "Ken") })).toBeNull();
  });

  it("tells a character, then reads the list again", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.tell("Tavin", "the guard", "Ken") }));
    await screen.findByText(w.saidTold("Tavin", "the guard", "Ken"));
    expect(changes(api)).toEqual([`changeName:${REF}:n1:${JSON.stringify({ action: "told-add", alias: "sn1", character: "pc1" })}`]);
    await screen.findByText(w.told("Tavin", "the guard", "Ken"));
    expect(screen.queryByRole("button", { name: w.tell("Tavin", "the guard", "Ken") })).toBeNull();
  });

  it("asks once before taking back, and Keep it changes nothing", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.takeBackFor("Wen", "the guard") }));
    // a secret name can belong to a place or a group, so it is "names", not "people" (Supervisor, #3565)
    expect(screen.getByText("Take back? Wen will see the guard and Ken as two different names again.")).toBeTruthy();
    expect(changes(api)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(changes(api)).toEqual([]);
    fireEvent.click(await screen.findByRole("button", { name: w.takeBackFor("Wen", "the guard") }));
    fireEvent.click(screen.getByRole("button", { name: w.yesTakeBack }));
    await screen.findByText(w.saidBack("Wen", "the guard", "Ken"));
    expect(changes(api)).toEqual([`changeName:${REF}:n1:${JSON.stringify({ action: "told-remove", alias: "sn1", character: "pc2" })}`]);
    await screen.findByRole("button", { name: w.tell("Wen", "the guard", "Ken") });
  });

  it("shows DMbot's stage while the change is out, and clears it when it is answered", async () => {
    const api = mockApi("table");
    const real = api.changeName!.bind(api);
    let finish!: () => void;
    api.changeName = (...args: Parameters<typeof real>) =>
      new Promise((resolve) => {
        finish = () => resolve(real(...args));
      });
    await open(REF, "n1", api);
    fireEvent.click(await screen.findByRole("button", { name: w.tell("Tavin", "the guard", "Ken") }));
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => finish());
    await screen.findByText(w.saidTold("Tavin", "the guard", "Ken"));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });

  it("words a refusal plainly and keeps the list", async () => {
    await open(`${REF}.told-fails`);
    fireEvent.click(await screen.findByRole("button", { name: w.tell("Tavin", "the guard", "Ken") }));
    await screen.findByText(w.problems["bad-target"] as string);
    expect(screen.getByRole("button", { name: w.tell("Tavin", "the guard", "Ken") })).toBeTruthy();
  });

  it("a double press asks once", async () => {
    const api = await open();
    const button = await screen.findByRole("button", { name: w.tell("Tavin", "the guard", "Ken") });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(w.saidTold("Tavin", "the guard", "Ken"));
    expect(changes(api).length).toBe(1);
  });

  it("shows nothing for an entry with no secret name, and not for someone who can't change names", async () => {
    const api = await open(REF, "n2");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText(w.heading)).toBeNull();
    cleanup();
    await open(`${REF}.names-manager`, "n1", api);
    await screen.findByText(w.loadFailed);
    expect(screen.queryByRole("button", { name: w.tell("Tavin", "the guard", "Ken") })).toBeNull();
  });

  it("has no accessibility violations", async () => {
    await open();
    await screen.findByText(w.heading);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
