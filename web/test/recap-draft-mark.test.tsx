/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A recap draft that DMbot is still checking (#3861): faded, with a DRAFT mark over it that is not in the text and takes no
// clicks; a final recap has neither. The progress line stays.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import DraftPreview from "../src/account/DraftPreview";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { text } from "../src/content/recaps";
import type { Recap } from "../src/account/api";

afterEach(cleanup);

describe("the draft preview", () => {
  it("fades the text and puts the mark over it while the draft is being checked", () => {
    render(<DraftPreview checking={true}><p>The party crossed the river.</p></DraftPreview>);
    expect(document.querySelector(".draft-preview-text")?.textContent).toBe("The party crossed the river.");
    const mark = document.querySelector(".draft-preview-mark");
    expect(mark?.textContent).toBe("DRAFT");
    expect(mark?.getAttribute("aria-hidden")).toBe("true"); // (an overlay, not words in the text)
    expect(document.querySelector(".draft-preview-text")?.textContent).not.toContain("DRAFT");
  });
  it("draws the text as it was, with no fade and no mark, once it is final", () => {
    render(<DraftPreview checking={false}><p>The party crossed the river.</p></DraftPreview>);
    expect(screen.getByText("The party crossed the river.")).toBeTruthy();
    expect(document.querySelector(".draft-preview")).toBeNull();
    expect(document.querySelector(".draft-preview-mark")).toBeNull();
  });
});

describe("on the recap page", () => {
  const base = mockRecaps()[0]!;
  const draft = (over: Partial<Recap>): Recap => ({ ...base, status: "draft", text: "The party crossed the river.", ...over });
  const pageWith = (recap: Recap) => {
    const api = mockApi("table");
    api.recap = async () => recap;
    return api;
  };
  it("marks a draft that is still being checked, and keeps the progress line", async () => {
    render(<Recaps api={pageWith(draft({ working: true }))} path={`/account/recaps/${base.ref}`} />);
    expect(await screen.findByText(text.working)).toBeTruthy();
    expect(document.querySelector(".draft-preview-mark")?.textContent).toBe(text.draftMark);
  });
  it("shows a finished draft with neither", async () => {
    render(<Recaps api={pageWith(draft({ working: false }))} path={`/account/recaps/${base.ref}`} />);
    await screen.findByText("The party crossed the river.");
    expect(document.querySelector(".draft-preview-mark")).toBeNull();
  });
});
