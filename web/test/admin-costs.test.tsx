/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin Costs panel (#3671): a figure with what it covers and its own cost-page link, the
// honest "not recorded / not connected" lines, a wait that says it is reading, and a failure
// that offers another try.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AdminApi, AdminApiError, type CostsView } from "../src/admin/api";
import Costs, { recentMonths } from "../src/admin/Costs";
import { dollars } from "../src/content/admin";

afterEach(cleanup);

const view: CostsView = {
  month: "2027-01",
  services: [
    {
      id: "deepgram",
      name: "Deepgram",
      covers: "Turns voices into text.",
      link: "https://console.deepgram.com/billing",
      state: "read",
      amount_cents: 575,
      detail: "1 project",
      read_at: 1_800_000_000,
    },
    {
      id: "voyage",
      name: "Voyage",
      covers: "Search by meaning.",
      link: "https://dashboard.voyageai.com/organization/usage",
      state: "not_recorded",
      amount_cents: null,
      detail: "Not recorded yet. Open its page for the figure.",
      read_at: null,
    },
  ],
  spent_cents: 575,
  counted: ["deepgram"],
  income: ["free", "try-me", "table", "two-table", "guild"].map((plan) => ({
    plan,
    name: plan,
    state: "not_available" as const,
    detail: "Not available yet.",
  })),
};

const apiWith = (costs: AdminApi["costs"]): AdminApi =>
  ({ costs }) as unknown as AdminApi;

describe("Costs", () => {
  it("lists the months newest first", () => {
    const m = recentMonths(new Date(Date.UTC(2027, 0, 15)));
    expect(m.slice(0, 3)).toEqual(["2027-01", "2026-12", "2026-11"]);
    expect(m).toHaveLength(12);
    expect(dollars(575)).toBe("$5.75");
  });

  it("shows each service's figure, what it covers and its own cost page", async () => {
    render(<Costs api={apiWith(async () => view)} onSignedOut={() => {}} />);
    await screen.findByText(/\$5\.75 spent so far, from 1 service/);
    expect(screen.getByText("Turns voices into text.")).toBeTruthy();
    expect(screen.getByText(/Not recorded yet/)).toBeTruthy();
    const links = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(links).toContain("https://console.deepgram.com/billing");
    expect(links).toContain(
      "https://dashboard.voyageai.com/organization/usage",
    );
    expect(screen.getAllByText("Not available yet")).toHaveLength(10);
  });

  it("says it is reading while it waits", async () => {
    let done: (v: CostsView) => void = () => {};
    render(
      <Costs
        api={apiWith(() => new Promise<CostsView>((ok) => (done = ok)))}
        onSignedOut={() => {}}
      />,
    );
    expect(screen.getByRole("status").textContent).toMatch(
      /Reading each service… 0s so far/,
    );
    done(view);
    await screen.findByText(/spent so far/);
  });

  it("asks again for another month", async () => {
    const costs = vi.fn(async () => view);
    render(<Costs api={apiWith(costs)} onSignedOut={() => {}} />);
    await screen.findByText(/spent so far/);
    fireEvent.change(screen.getByLabelText("Month"), {
      target: { value: recentMonths()[1] },
    });
    await waitFor(() =>
      expect(costs).toHaveBeenLastCalledWith(recentMonths()[1]),
    );
  });

  it("offers another try when it fails, and signs out when the session ended", async () => {
    const costs = vi
      .fn()
      .mockRejectedValueOnce(new AdminApiError("down"))
      .mockResolvedValue(view);
    render(<Costs api={apiWith(costs)} onSignedOut={() => {}} />);
    await screen.findByText(/Couldn't read the costs/);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText(/spent so far/);

    const out = vi.fn();
    render(
      <Costs
        api={apiWith(() => Promise.reject(new AdminApiError("signed-out")))}
        onSignedOut={out}
      />,
    );
    await waitFor(() => expect(out).toHaveBeenCalled());
  });
});
