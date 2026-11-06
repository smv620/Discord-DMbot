/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A recap DMbot couldn't check for 3 days (#2297, #2305): the DM reads the draft with the sentences
// DMbot would have looked at twice marked, and may use it as it is. Nothing here is shared.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi, type Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { text } from "../src/content/recaps";

afterEach(cleanup);

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;
const DRAFT = ["The party crossed the river.", "They were afraid of the king.", "Mira waved goodbye."];
const unchecked = (over: Partial<Recap> = {}): Recap => ({
  ...base,
  working: true,
  waitingForCheck: true,
  text: "",
  summary: "",
  housekeeping: [],
  canEdit: false,
  canPost: false,
  canTryAgain: false,
  uncheckedSince: 1,
  uncheckedDraft: DRAFT,
  uncheckedSentences: [2],
  canApproveUnchecked: true,
  ...over,
});

function pageWith(recap: Recap, extra: Partial<AccountApi> = {}): AccountApi {
  const api = mockApi("table");
  api.recap = async () => recap;
  api.approveUnchecked = async () => ({ ...base, text: DRAFT.join(" ") });
  return Object.assign(api, extra);
}

describe("a draft unchecked for 3 days", () => {
  it("shows the draft to read, with the sentences to look at twice marked", async () => {
    render(<Recaps api={pageWith(unchecked())} path={path} />);
    expect(await screen.findByRole("heading", { name: text.uncheckedHeading })).toBeTruthy();
    for (const sentence of DRAFT) expect(screen.getByText(sentence, { exact: false })).toBeTruthy();
    const marks = document.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]!.textContent).toContain("They were afraid of the king.");
    expect(marks[0]!.textContent).toContain(text.uncheckedMark); // said in words too, not by colour alone
  });

  it("shows nothing before there is a draft to read", async () => {
    render(<Recaps api={pageWith(unchecked({ uncheckedDraft: [] }))} path={path} />);
    expect(await screen.findByText(text.waitingForCheck)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: text.uncheckedHeading })).toBeNull();
  });

  it("offers the approval only when the API takes it", async () => {
    render(<Recaps api={pageWith(unchecked({ canApproveUnchecked: false }))} path={path} />);
    await screen.findByRole("heading", { name: text.uncheckedHeading });
    expect(screen.queryByRole("button", { name: text.uncheckedUse })).toBeNull();
  });

  it("uses the draft as it is on a press, and only on a press", async () => {
    const asked: string[] = [];
    const api = pageWith(unchecked(), {
      approveUnchecked: async (ref) => {
        asked.push(ref);
        return { ...base, text: DRAFT.join(" ") };
      },
    });
    render(<Recaps api={api} path={path} />);
    const button = await screen.findByRole("button", { name: text.uncheckedUse });
    expect(asked).toHaveLength(0);
    fireEvent.click(button);
    await waitFor(() => expect(asked).toEqual([base.ref]));
    await waitFor(() => expect(screen.queryByRole("heading", { name: text.uncheckedHeading })).toBeNull());
  });

  it("asks once when the button is pressed twice quickly", async () => {
    let asked = 0;
    const api = pageWith(unchecked(), {
      approveUnchecked: async () => {
        asked += 1;
        await new Promise((r) => setTimeout(r, 30));
        return { ...base, text: DRAFT.join(" ") };
      },
    });
    render(<Recaps api={api} path={path} />);
    const button = await screen.findByRole("button", { name: text.uncheckedUse });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByRole("heading", { name: text.uncheckedHeading })).toBeNull());
    expect(asked).toBe(1);
  });

  it("says what comes next when the API doesn't take the approval yet", async () => {
    render(<Recaps api={pageWith(unchecked({ canApproveUnchecked: false }))} path={path} />);
    expect(await screen.findByText(text.uncheckedKeepTrying)).toBeTruthy();
  });

  it("says so when it didn't work and keeps the draft", async () => {
    const api = pageWith(unchecked(), {
      approveUnchecked: async () => {
        throw new ApiError("server");
      },
    });
    render(<Recaps api={api} path={path} />);
    fireEvent.click(await screen.findByRole("button", { name: text.uncheckedUse }));
    expect(await screen.findByText(text.uncheckedFailed)).toBeTruthy();
    expect(screen.getByRole("heading", { name: text.uncheckedHeading })).toBeTruthy();
  });

  it("is not shown for a recap that is not waiting any more", async () => {
    render(<Recaps api={pageWith({ ...base, uncheckedDraft: DRAFT, working: false })} path={path} />);
    await screen.findByRole("heading", { level: 2 });
    expect(screen.queryByRole("heading", { name: text.uncheckedHeading })).toBeNull();
  });
});

describe("the pretend API's unchecked draft", () => {
  const demo = "/account/recaps/200000000000000001.cmp9.1789000000";

  it("opens as it is, and axe finds nothing serious on the way", async () => {
    const api = mockApi("table");
    render(<Recaps api={api} path={demo} />);
    await screen.findByRole("heading", { name: text.uncheckedHeading });
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: text.uncheckedUse }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: text.uncheckedHeading })).toBeNull());
    expect(api.calls).toContain("approveUnchecked:200000000000000001.cmp9.1789000000");
  });
});
