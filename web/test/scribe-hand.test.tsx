/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The hand writing and turning a page while a recap is being written (owner request, 2026-11-09), with one fixed line of what
// DMbot is thinking about at each step: the owner's three lines on the steps they named, nothing for a step with no line, a
// still hand when the work has stalled or is waiting, and a drawing that is hidden from screen readers.
import { act, cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import ScribeHand, { scribeLine } from "../src/account/ScribeHand";
import { text } from "../src/content/recaps";

afterEach(cleanup);

describe("the thinking lines", () => {
  it("has the owner's three lines on the steps they named", () => {
    expect(scribeLine("recap_reading")).toBe("Thinking… who was important in the scene?");
    expect(scribeLine("recap_beats")).toBe("Contemplating… what events were noteworthy?");
    expect(scribeLine("recap_checking")).toBe("Considering… did I cover all the significant details?");
  });
  it("has a line for every step that is being worked on, and none for a step it does not know", () => {
    for (const stage of ["recap_names", "recap_writing", "recap_final_read", "recap_finishing"]) expect(scribeLine(stage), stage).toBeTruthy();
    for (const stage of ["recap_check_waiting", "recap_ready", "recap_failed", "something_new", "", null, undefined]) expect(scribeLine(stage), String(stage)).toBeNull();
  });
});

describe("the hand", () => {
  it("moves while the work moves, and is hidden from screen readers", () => {
    render(<ScribeHand stage="recap_beats" moving={true} />);
    expect(document.querySelector(".scribe-moving")).not.toBeNull();
    expect(document.querySelector("svg.scribe-art")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByText(text.scribe["recap_beats"] as string)).toBeTruthy();
  });
  it("is still when the work is not moving, and shows only the hand for a step with no line", () => {
    render(<ScribeHand stage="recap_check_waiting" moving={false} />);
    expect(document.querySelector(".scribe-moving")).toBeNull();
    expect(document.querySelector(".scribe-line")).toBeNull();
  });
});

describe("on the recap page", () => {
  const base = mockRecaps()[0]!;
  const working = (over: Partial<Recap>): Recap => ({ ...base, status: "draft", working: true, text: "", summary: "", ...over });
  const show = async (recap: Recap) => {
    const api = mockApi("table");
    api.recap = async () => recap;
    render(<Recaps api={api} path={`/account/recaps/${base.ref}`} />);
    await screen.findByText(text.working, { exact: false }).catch(() => undefined);
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
  };
  it("shows the line for the step it is on, with the hand moving", async () => {
    await show(working({ progress: { stage: "recap_beats", state: "running" } }));
    expect(screen.getByText(text.scribe["recap_beats"] as string)).toBeTruthy();
    expect(document.querySelector(".scribe-moving")).not.toBeNull();
  });
  it("keeps the hand still, and says nothing of a step, when the work has stalled", async () => {
    await show(working({ progress: { stage: "recap_writing", state: "stalled" } }));
    expect(document.querySelector(".scribe")).not.toBeNull();
    expect(document.querySelector(".scribe-moving")).toBeNull();
  });
  it("shows no hand when the recap is not being made", async () => {
    const api = mockApi("table");
    api.recap = async () => ({ ...base, working: false });
    render(<Recaps api={api} path={`/account/recaps/${base.ref}`} />);
    await act(async () => void (await new Promise((r) => setTimeout(r, 30))));
    expect(document.querySelector(".scribe")).toBeNull();
  });
});
