/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Shared rules page (#2460 part 2): what each campaign you run shares and uses, read-only, with
// the exact Discord path to change it; no share code on the page.
import { cleanup, render, screen, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SharingPage from "../src/account/Sharing";
import { problem, text } from "../src/content/sharing";

afterEach(cleanup);

const page = (api: AccountApi) => render(<SharingPage api={api} />);

describe("the Shared rules page", () => {
  it("shows what a campaign shares and what it uses, under its name and server", async () => {
    page(mockApi("table"));
    expect(await screen.findByRole("heading", { level: 2, name: "The Brynwater Crossing" })).toBeTruthy();
    expect(screen.getByText(text.server("Thursday Table"))).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: text.sharesHeading })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: text.followsHeading })).toBeTruthy();
    expect(screen.getByText("Brynwater table rules")).toBeTruthy();
    expect(screen.getByText("Frostmaiden pack")).toBeTruthy();
  });

  it("says whether it is still shared, whether a code is on, and which are no longer updated", async () => {
    page(mockApi("table"));
    const rules = (await screen.findByText("Brynwater table rules")).closest("li") as HTMLElement;
    for (const t of [text.kinds["house-rules"] ?? "", text.version(3), text.sharing, text.codeOn])
      expect(within(rules).getByText(t)).toBeTruthy();
    const stopped = screen.getByText("Marsh creatures").closest("li") as HTMLElement;
    expect(within(stopped).getByText(text.stopped)).toBeTruthy();
    expect(within(stopped).queryByText(text.codeOn)).toBeNull();
    expect(within(stopped).queryByText(text.codeOff)).toBeNull(); // only a shared set has a code to speak of
    const old = screen.getByText("Old Guard rules").closest("li") as HTMLElement;
    expect(within(old).getByText(text.noLongerUpdated)).toBeTruthy();
    expect(within(old).getByText(text.from("Grey Keep"))).toBeTruthy();
    const frost = screen.getByText("Frostmaiden pack").closest("li") as HTMLElement;
    expect(within(frost).getByText(text.updating)).toBeTruthy();
  });

  it("names the exact Discord path to change each kind, and has no button that changes anything", async () => {
    page(mockApi("table"));
    await screen.findByText("Brynwater table rules");
    expect(screen.getAllByText(text.changeRules).length).toBe(2); // the shared set and the followed set
    expect(screen.getAllByText(text.changePacks).length).toBe(2);
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(text.changePacks).toContain("⚙️ Settings → 📚 Shared rules → 📦 Homebrew packs");
  });

  it("never shows a share code", async () => {
    const api = mockApi("table");
    const got = await api.sharedRules!();
    expect(JSON.stringify(got)).not.toMatch(/[A-Z2-7]{10}/);
    expect(Object.keys(got[0]!.publishes[0]!)).not.toContain("code");
  });

  it("says plainly when there is nothing, and how to start", async () => {
    page({ ...mockApi("table"), sharedRules: async () => [] } as AccountApi);
    expect(await screen.findByText(text.empty)).toBeTruthy();
    expect(text.empty).toContain("⚙️ Settings");
  });

  it("sends a signed-out person to sign in, and says a failure in words", async () => {
    page({
      ...mockApi("table"),
      sharedRules: async () => {
        throw new ApiError("signed-out");
      },
    } as AccountApi);
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    cleanup();
    page({
      ...mockApi("table"),
      sharedRules: async () => {
        throw new ApiError("server");
      },
    } as AccountApi);
    expect((await screen.findByRole("alert")).textContent).toBe(problem("server"));
    expect(problem("slow-down")).toBe(text.tooMany);
  });

  it("copes with an API that cannot say yet", async () => {
    const api = { ...mockApi("table") } as AccountApi;
    delete api.sharedRules;
    page(api);
    expect((await screen.findByRole("alert")).textContent).toBe(text.failed);
  });

  it("has no accessibility problems", async () => {
    page(mockApi("table"));
    await screen.findByText("Brynwater table rules");
    // No layout in happy-dom, so contrast is measured from the stylesheet (a11y-contrast.test.ts).
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(
      result.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
    ).toEqual([]);
  });
});
