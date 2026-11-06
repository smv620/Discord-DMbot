/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story timeline page (#2462): sessions in order, each with its number, day, length, recap and
// the names that came out at the table; a player's view has plain titles and no recap address.
import axe from "axe-core";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import TimelinePage, { refFromPath } from "../src/account/Timeline";
import { playedDay } from "../src/content/recaps";
import { problem, text } from "../src/content/timeline";

afterEach(cleanup);

const REF = "100.cmp1";
const page = (api: AccountApi, ref = REF) => render(<TimelinePage api={api} path={`/account/timeline/${ref}`} />);
const session = (n: number) => screen.getByRole("heading", { level: 2, name: text.session(n) }).closest("li") as HTMLElement;

describe("the address", () => {
  it("reads the campaign out of the path", () => {
    expect(refFromPath(`/account/timeline/${REF}`)).toBe(REF);
    expect(refFromPath(`/account/timeline/${REF}/`)).toBe(REF);
    expect(refFromPath("/account/timeline")).toBeNull();
    expect(refFromPath("/account/timeline/%E0%A4%A")).toBeNull(); // a broken escape is no campaign
  });
});

describe("the sessions", () => {
  it("come in order, each with its day and its length", async () => {
    page(mockApi("table"));
    await screen.findByRole("heading", { level: 2, name: text.session(1) });
    const order = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(order).toEqual([text.session(1), text.session(2), text.session(3)]);
    expect(within(session(1)).getByText(new RegExp(`${playedDay(1_790_000_000)} · 3 h`))).toBeTruthy();
    expect(within(session(2)).getByText(/2 h$/)).toBeTruthy();
    expect(within(session(3)).getByText(new RegExp(text.running))).toBeTruthy(); // no end yet: no length
  });

  it("show the recap, linked for a game master, with whether it was posted", async () => {
    page(mockApi("table"));
    const link = await screen.findByRole("link", { name: text.openRecap("The river crossing") });
    expect(link.getAttribute("href")).toBe(`/account/recaps/${encodeURIComponent("100.cmp1.1790000000")}`);
    expect(within(session(1)).getByText(`(${text.recapPosted})`)).toBeTruthy();
    expect(within(session(2)).getByText(`(${text.recapApproved})`)).toBeTruthy();
    expect(within(session(3)).getByText(text.noRecap)).toBeTruthy();
  });

  it("say what came out at the table, or that nothing did", async () => {
    page(mockApi("table"));
    await screen.findByText("Ferryman Odo, Hollow Mill");
    expect(within(session(2)).getByText(text.revealedNone)).toBeTruthy();
  });

  it("give a player the title without a link, and no unposted recap", async () => {
    page(mockApi("table"), `${REF}.tl-player`);
    await screen.findByText("The river crossing");
    expect(screen.queryByRole("link", { name: /recap/i })).toBeNull();
    expect(within(session(2)).getByText(text.noRecap)).toBeTruthy();
  });
});

describe("when it cannot show", () => {
  it("says there are no sessions yet, in words", async () => {
    page(mockApi("table"), `${REF}.tl-empty`);
    expect(await screen.findByText(text.none)).toBeTruthy();
  });

  it("says a campaign it cannot find, a plan that does not keep sessions, a signed-out person and a failure", async () => {
    const fail = (kind: string): AccountApi =>
      ({ ...mockApi("table"), timeline: async () => { throw new ApiError(kind as never); } }) as AccountApi;
    for (const [kind, words] of [
      ["not-found", text.notFound],
      ["not-in-plan", text.notInPlan],
      ["server", text.failed],
      ["slow-down", text.tooMany],
    ] as const) {
      page(fail(kind));
      expect((await screen.findByRole("alert")).textContent, kind).toBe(words);
      cleanup();
    }
    page(fail("signed-out"));
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    expect(problem("anything")).toBe(text.failed);
  });

  it("copes with an API that cannot say yet", async () => {
    const api = { ...mockApi("table") } as AccountApi;
    delete api.timeline;
    page(api);
    expect((await screen.findByRole("alert")).textContent).toBe(text.notFound);
  });
});

describe("the page itself", () => {
  it("has the breadcrumb and tabs, with Story marked, and no accessibility problems", async () => {
    page(mockApi("table"));
    await screen.findByRole("heading", { level: 2, name: text.session(1) });
    const tabs = within(screen.getByRole("navigation", { name: "Parts of this campaign" }));
    expect(tabs.getAllByRole("link").filter((a) => a.getAttribute("aria-current")).map((a) => a.textContent)).toEqual(["Story"]);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

describe("the site's rules for the page", () => {
  const read = (f: string) => readFileSync(`${process.cwd()}/public/${f}`, "utf8");
  it("serves every campaign's timeline from the one page, and never stores or indexes it", () => {
    expect(read("_redirects")).toContain("/account/timeline/*  /account/timeline  200");
    expect(read("_headers")).toMatch(
      /\/account\/timeline\*\n {2}Cache-Control: no-store\n {2}X-Robots-Tag: noindex, nofollow/,
    );
  });
});
