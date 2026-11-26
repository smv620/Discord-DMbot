/** @jsxImportSource preact */
// @vitest-environment happy-dom
// What "travels with" means on a waiting connection and a waiting claim (#5165, the website half of #5164): core's own words under the
// line, first letter capitalised, ending in a full stop; nothing for any other connection or when core sends none.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

vi.mock("../src/content/web-waiting", async (original) => {
  const real = await original<typeof import("../src/content/web-waiting")>();
  return {
    ...real,
    get WEB_WAITING_LIVE() {
      return true;
    },
  };
});

afterEach(() => cleanup());

const MEANS = "a companion who goes along, like a hireling, a familiar or a rescued prisoner";
const SAID = "A companion who goes along, like a hireling, a familiar or a rescued prisoner.";

async function open(means: string | undefined) {
  const api = mockApi("table");
  const real = api.webWaiting!.bind(api);
  api.webWaiting = async (...a) => {
    const got = await real(...a);
    return {
      ...got,
      backstory: got.backstory.map((b, i) => (i === 0 && means ? { ...b, means } : b)),
      claims: got.claims.map((c, i) => (i === 0 && means ? { ...c, means } : c)),
    };
  };
  render(<WebWaiting api={api} campaignRef="100.cmp1" />);
  await screen.findByRole("heading", { name: w.heading });
}

describe("a companion link", () => {
  it("says what travels with means under the connection and the claim", async () => {
    await open(MEANS);
    expect(screen.getAllByText(SAID)).toHaveLength(2);
  });

  it("says nothing when core sends nothing", async () => {
    await open(undefined);
    expect(screen.queryByText(SAID)).toBeNull();
  });
});
