/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "What DMbot recorded" on the admin Costs page (#3671): DMbot's own sums by service, plan and feature, beside the providers' figures.
// Services that are never priced show units and "not priced" (never $0), a refused row says which was refused, a sample run's test
// spend is shown apart and kept out of the plan and feature totals, a wait says it is reading, and a failure offers another try.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminApiError, type AdminApi, type RecordedRow } from "../src/admin/api";
import Recorded, { sumBy } from "../src/admin/Recorded";
import { microDollars, recordedText as w } from "../src/content/admin";

afterEach(cleanup);

const row = (r: Partial<RecordedRow> & Pick<RecordedRow, "service" | "feature">): RecordedRow => ({
  model: "m",
  plan: "table",
  source: "play",
  calls: 10,
  units_in: 1000,
  units_out: 100,
  micro_usd: 1_000_000,
  refused: 0,
  unpriced: 0,
  ...r,
});

const rows: RecordedRow[] = [
  row({ service: "anthropic", feature: "recap", micro_usd: 2_500_000 }),
  row({ service: "anthropic", feature: "names", plan: "guild", micro_usd: 500_000 }),
  row({ service: "deepgram", feature: "transcribe", units_in: 5400, units_out: 0, micro_usd: 410_000 }),
  row({ service: "elevenlabs", feature: "recap_audio", calls: 3, units_in: 9000, units_out: 0, micro_usd: 0, unpriced: 3 }),
  row({ service: "anthropic", feature: "sidebar", plan: "free", calls: 0, micro_usd: 0, refused: 4 }),
  row({ service: "anthropic", feature: "recap", source: "sample", micro_usd: 700_000, calls: 25 }),
];

const apiWith = (read: AdminApi["costsRecorded"]): AdminApi => ({ costsRecorded: read }) as AdminApi;

describe("the sums", () => {
  it("add the rows by one field, most expensive first", () => {
    const s = sumBy(rows.filter((r) => r.source === "play"), "service");
    expect(s.map((x) => x.key)).toEqual(["anthropic", "deepgram", "elevenlabs"]);
    expect(s[0]!.micro).toBe(3_000_000);
    expect(s[0]!.refused).toBe(4);
  });

  it("show a figure under a cent with four places and a whole one with two", () => {
    expect(microDollars(214000)).toBe("$0.21");
    expect(microDollars(2100)).toBe("$0.0021");
    expect(microDollars(0)).toBe("$0.00");
  });
});

describe("the section", () => {
  it("shows sums by service, plan and feature, with units for a service, and never $0 for one that is not priced", async () => {
    render(<Recorded api={apiWith(async () => ({ month: "2027-01", rows }))} month="2027-01" onSignedOut={() => {}} />);
    await screen.findByRole("heading", { name: w.byService });
    const service = within(screen.getByRole("heading", { name: w.byService }).parentElement as HTMLElement);
    expect(service.getByText("5,400 audio seconds")).toBeTruthy();
    expect(service.getByText("9,000 credits")).toBeTruthy();
    expect(service.getByText(w.notPriced)).toBeTruthy();
    expect(service.getByText("$3.00")).toBeTruthy(); // anthropic's play use: 2.50 + 0.50, not the sample's 0.70
    expect(screen.getByRole("heading", { name: w.byPlan })).toBeTruthy();
    expect(screen.getByRole("heading", { name: w.byFeature })).toBeTruthy();
    expect(screen.getAllByText(w.refused(4), { exact: false }).length).toBeGreaterThan(0);
  });

  it("shows the plan's words, and keeps the sample's test spend apart from the totals", async () => {
    render(<Recorded api={apiWith(async () => ({ month: "2027-01", rows }))} month="2027-01" onSignedOut={() => {}} />);
    await screen.findByRole("heading", { name: w.byPlan });
    const plans = within(screen.getByRole("heading", { name: w.byPlan }).parentElement as HTMLElement);
    expect(plans.getByText(w.plans.guild!)).toBeTruthy();
    const feature = within(screen.getByRole("heading", { name: w.byFeature }).parentElement as HTMLElement);
    expect(feature.getByText("$2.50")).toBeTruthy(); // recap's real use only: the sample's 0.70 is not in it
    expect(screen.getByRole("heading", { name: w.sampleHeading })).toBeTruthy();
    expect(screen.getByText("25 calls · $0.70")).toBeTruthy();
  });

  it("says nothing was recorded for an empty month", async () => {
    render(<Recorded api={apiWith(async () => ({ month: "2027-01", rows: [] }))} month="2027-01" onSignedOut={() => {}} />);
    expect(await screen.findByText(w.none)).toBeTruthy();
  });

  it("says it is reading while it waits, with how long", async () => {
    vi.useFakeTimers();
    render(<Recorded api={apiWith(() => new Promise(() => {}))} month="2027-01" onSignedOut={() => {}} />);
    expect(screen.getByText(w.reading(0))).toBeTruthy();
    await vi.advanceTimersByTimeAsync(3000);
    expect(screen.getByText(w.reading(3))).toBeTruthy();
    vi.useRealTimers();
  });

  it("offers another try after a failure, and goes back to sign-in when the session ended", async () => {
    let tries = 0;
    render(
      <Recorded
        api={apiWith(async () => {
          tries += 1;
          if (tries === 1) throw new AdminApiError("down");
          return { month: "2027-01", rows };
        })}
        month="2027-01"
        onSignedOut={() => {}}
      />,
    );
    expect(await screen.findByText(w.down)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    expect(await screen.findByRole("heading", { name: w.byService })).toBeTruthy();
    cleanup();
    const out = vi.fn();
    render(
      <Recorded
        api={apiWith(async () => {
          throw new AdminApiError("signed-out");
        })}
        month="2027-01"
        onSignedOut={out}
      />,
    );
    await waitFor(() => expect(out).toHaveBeenCalled());
  });

  it("is not there when the API cannot read the records", () => {
    const { container } = render(<Recorded api={{} as AdminApi} month="2027-01" onSignedOut={() => {}} />);
    expect(container.textContent).toBe("");
  });
});
