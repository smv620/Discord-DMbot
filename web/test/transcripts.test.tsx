/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The session transcript pages (#1501): the campaigns list, a campaign's sessions, and a session
// read in two versions with a download; for anyone in the server, so nothing here is DM-only.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts, { placeFromPath } from "../src/account/Transcripts";
import { cleanedNote, heardNote, problem, text } from "../src/content/transcripts";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";
const page = (api: AccountApi, path: string) => render(<Transcripts api={api} path={path} />);

describe("the address", () => {
  it("says where in the pages it points", () => {
    expect(placeFromPath("/account/transcripts")).toBeNull();
    expect(placeFromPath("/account/transcripts/")).toBeNull();
    expect(placeFromPath(`/account/transcripts/${REF}`)).toEqual({ ref: REF, number: null });
    expect(placeFromPath(`/account/transcripts/${REF}/7`)).toEqual({ ref: REF, number: 7 });
    expect(placeFromPath(`/account/transcripts/${REF}/x`)).toBeNull();
  });
});

describe("the campaigns", () => {
  it("lists each campaign with its server and how many sessions, and links to it", async () => {
    page(mockApi("table"), "/account/transcripts");
    expect(await screen.findByText("The Brynwater Crossing")).toBeTruthy();
    expect(screen.getByText(text.campaignLine("Thursday Table", 2))).toBeTruthy();
    expect(screen.getByRole("link", { name: text.openCampaign("The Brynwater Crossing") }).getAttribute("href")).toBe(
      `/account/transcripts/${REF}`,
    );
    expect(screen.getByText(text.lead)).toBeTruthy();
  });

  it("says plainly when there are none", async () => {
    const api = { ...mockApi("table"), transcriptCampaigns: async () => [] } as AccountApi;
    page(api, "/account/transcripts");
    expect(await screen.findByText(text.empty)).toBeTruthy();
  });

  it("sends a signed-out person to sign in", async () => {
    const api = {
      ...mockApi("table"),
      transcriptCampaigns: async () => {
        throw new ApiError("signed-out");
      },
    } as AccountApi;
    page(api, "/account/transcripts");
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    // The page's own panel only says it; the one Sign in button is the shared line's (#2301).
    expect(screen.queryByRole("link", { name: text.signIn })).toBeNull();
  });

  it("says it did not load, in plain words, when the API is down", async () => {
    const api = {
      ...mockApi("table"),
      transcriptCampaigns: async () => {
        throw new ApiError("network");
      },
    } as AccountApi;
    page(api, "/account/transcripts");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.failed);
  });

  it("says nothing is there on an API that has no transcripts yet", async () => {
    const { transcriptCampaigns: _gone, ...older } = mockApi("table");
    page(older as AccountApi, "/account/transcripts");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.failed);
  });
});

describe("a campaign's sessions", () => {
  it("lists them newest first with the day and the number of lines", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}`);
    expect(await screen.findByRole("heading", { name: "The Brynwater Crossing" })).toBeTruthy();
    const titles = screen.getAllByText(/^Session \d$/).map((e) => e.textContent);
    expect(titles).toEqual(["Session 3", "Session 2", "Session 1"]);
    expect(screen.getByText(/3 lines/)).toBeTruthy();
    expect(screen.getByRole("link", { name: `${text.open}: Session 2` }).getAttribute("href")).toBe(
      `/account/transcripts/${REF}/2`,
    );
  });

  it("says a session's transcript was deleted, with no line count or Read link (#1548)", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}`);
    expect(await screen.findByText(/Transcript deleted on/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: `${text.open}: Session 3` })).toBeNull();
    expect(screen.getByRole("link", { name: `${text.open}: Session 2` })).toBeTruthy();
  });

  it("an older API with no deletion time lists sessions as before", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      transcriptSessions: async (ref: string) => {
        const found = await mock.transcriptSessions!(ref);
        return {
          ...found,
          sessions: found.sessions.slice(1).map(({ transcriptsDeletedAt: _gone, ...rest }) => rest),
        };
      },
    } as AccountApi;
    page(api, `/account/transcripts/${REF}`);
    expect(await screen.findByText(/3 lines/)).toBeTruthy();
    expect(screen.queryByText(/Transcript deleted on/)).toBeNull();
  });

  it("is not found for a campaign that is not there or not theirs", async () => {
    page(mockApi("table"), "/account/transcripts/999.nope");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.notFound);
  });

  it("is told the plan has no transcripts, in words that name no plan", async () => {
    const api = {
      ...mockApi("table"),
      transcriptSessions: async () => {
        throw new ApiError("not-in-plan");
      },
    } as AccountApi;
    page(api, `/account/transcripts/${REF}`);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", problem("not-in-plan"));
    expect(problem("not-in-plan")).toContain("Ask the campaign's owner");
  });
});

describe("reading a session", () => {
  it("opens on the cleaned version with the bot's note, then switches to as heard", async () => {
    const api = mockApi("table");
    page(api, `/account/transcripts/${REF}/2`);
    expect(await screen.findByText(/Ka'zeth draws his sword/)).toBeTruthy();
    expect(screen.getByText(text.difference.cleaned)).toBeTruthy();
    expect(screen.getByText(text.shareLine)).toBeTruthy();
    expect(screen.getByText(text.names)).toBeTruthy();
    // The bot's full note is one fold away, with what to use on this page instead of /transcript.
    const fold = screen.getByText(text.about).closest("details") as HTMLElement;
    expect(fold.textContent).toContain(cleanedNote);
    expect(fold.textContent).toContain(text.webNote);
    expect(screen.getByRole("button", { name: text.versions.cleaned }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText(/did you see the match/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: text.versions.heard }));
    expect(await screen.findByText(/Kazeth draws his sword/)).toBeTruthy();
    expect(screen.getByText(text.difference.heard)).toBeTruthy();
    const heardFold = screen.getByText(text.about).closest("details") as HTMLElement;
    expect(heardFold.textContent).toContain(heardNote);
    expect(heardFold.textContent).not.toContain(cleanedNote);
    expect(screen.getByText(/did you see the match/)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.versions.heard }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows each line as the export wrote it, nothing added", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}/2`);
    const lines = (await screen.findAllByRole("listitem")).map((li) => li.textContent);
    expect(lines).toContain("[0:00:12] (Speaker 1) {Cerric}: We cross the river at dusk.");
  });

  it("keeps the file's other notes in a fold, without repeating the version's note twice", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}/2`);
    const fold = (await screen.findByText(text.about)).closest("details") as HTMLElement;
    expect(fold.textContent).toContain("DMbot transcript: The Brynwater Crossing, session 2");
    expect(fold.textContent).not.toContain(heardNote);
  });

  it("saves the same text as a file, named as the bot names it", async () => {
    const api = mockApi("table");
    const file = vi.spyOn(api as Required<AccountApi>, "transcriptFile");
    const created: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        created.push(this.download);
      });
    page(api, `/account/transcripts/${REF}/2`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(file).toHaveBeenCalledWith(REF, 2, "cleaned");
    expect(created).toEqual(["the-brynwater-crossing-session-2-cleaned.txt"]);
    click.mockRestore();
  });

  it("says when saving failed, and lets them try again", async () => {
    const api = {
      ...mockApi("table"),
      transcriptFile: vi.fn(async () => {
        throw new ApiError("network");
      }),
    } as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    expect(await screen.findByText(text.downloadFailed)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.download })).toBeTruthy();
  });

  it("is not found for a session that is not there", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}/9`);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.notFound);
  });
});

describe("how long the campaign keeps transcripts (#1575)", () => {
  it("has the four wordings", () => {
    expect(text.keepLine(30)).toBe("Transcripts are kept for 30 days after each session, then deleted.");
    expect(text.keepLine(90)).toBe("Transcripts are kept for 90 days after each session, then deleted.");
    expect(text.keepLine(365)).toBe("Transcripts are kept for 1 year after each session, then deleted.");
    expect(text.keepLine(null)).toBe("Transcripts are kept until the game master deletes them.");
    expect(text.keepLine(undefined)).toBeNull(); // an older API says nothing
  });

  it("says it under the campaign in the list, without the 'within a day' line", async () => {
    page(mockApi("table"), "/account/transcripts");
    expect(await screen.findByText(text.keepLine(90)!)).toBeTruthy();
    expect(screen.queryByText(text.keepSoon)).toBeNull();
  });

  it("says it on the campaign's page, with the 'within a day' line only when there is a limit", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}`);
    expect(await screen.findByText(`${text.keepLine(90)} ${text.keepSoon}`)).toBeTruthy();
    cleanup();
    const mock = mockApi("table");
    const forever = {
      ...mock,
      transcriptSessions: async (ref: string) => ({ ...(await mock.transcriptSessions!(ref)), keepDays: null }),
    } as AccountApi;
    page(forever, `/account/transcripts/${REF}`);
    expect(await screen.findByText(text.keepLine(null)!)).toBeTruthy();
    expect(screen.queryByText(new RegExp(text.keepSoon))).toBeNull();
  });

  it("says nothing on an older API with no keepDays", async () => {
    const mock = mockApi("table");
    const older = {
      ...mock,
      transcriptCampaigns: async () =>
        (await mock.transcriptCampaigns!()).map(({ keepDays: _gone, ...rest }) => rest),
      transcriptSessions: async (ref: string) => {
        const { keepDays: _gone, ...rest } = await mock.transcriptSessions!(ref);
        return rest;
      },
    } as AccountApi;
    page(older, "/account/transcripts");
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByText(/Transcripts are kept/)).toBeNull();
    cleanup();
    page(older, `/account/transcripts/${REF}`);
    await screen.findByRole("heading", { name: "The Brynwater Crossing" });
    expect(screen.queryByText(/Transcripts are kept/)).toBeNull();
  });
});

describe("a session whose transcript was deleted (#1548)", () => {
  it("says so on its own page, with no versions, download or lines", async () => {
    page(mockApi("table"), `/account/transcripts/${REF}/3`);
    expect(await screen.findByText(/This transcript was deleted on/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.download })).toBeNull();
    expect(screen.queryByRole("button", { name: text.versions.cleaned })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("character labels and Show more (#1512)", () => {
  const view = (lines: string[], next: number | null) => ({
    number: 2,
    version: "cleaned" as const,
    fileName: "x.txt",
    notes: ["DMbot transcript: x"],
    lines,
    next,
  });

  it("shows the character label the bot's export wrote, as part of the line", async () => {
    const api = {
      ...mockApi("table"),
      transcript: async () => view(["[0:00:12] (Speaker 1) {Cerric}: We cross the river."], null),
    } as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    expect(await screen.findByText("[0:00:12] (Speaker 1) {Cerric}: We cross the river.")).toBeTruthy();
  });

  it("says nothing about more when there is no more", async () => {
    const api = { ...mockApi("table"), transcript: async () => view(["line one"], null) } as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    await screen.findByText("line one");
    expect(screen.queryByRole("button", { name: text.showMore })).toBeNull();
  });

  it("loads the next page when asked and joins it on, until there is no more", async () => {
    const asked: (number | undefined)[] = [];
    const api = {
      ...mockApi("table"),
      transcript: vi.fn(async (_ref: string, _n: number, _v: string, offset?: number) => {
        asked.push(offset);
        return offset === 2000 ? view(["second page"], null) : view(["first page"], 2000);
      }),
    } as unknown as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    expect(await screen.findByText("first page")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.showMore }));
    expect(await screen.findByText("second page")).toBeTruthy();
    expect(screen.getByText("first page")).toBeTruthy(); // joined, not replaced
    expect(screen.queryByRole("button", { name: text.showMore })).toBeNull();
    expect(asked).toEqual([0, 2000].map((n) => (n === 0 ? undefined : n)));
  });

  it("says so, in plain words, when the next page fails, and lets them try again", async () => {
    let calls = 0;
    const api = {
      ...mockApi("table"),
      transcript: vi.fn(async (_ref: string, _n: number, _v: string, offset?: number) => {
        if (offset === undefined) return view(["first page"], 2000);
        calls += 1;
        if (calls === 1) throw new ApiError("network");
        return view(["second page"], null);
      }),
    } as unknown as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    await screen.findByText("first page");
    fireEvent.click(screen.getByRole("button", { name: text.showMore }));
    expect(await screen.findByText(text.moreFailed)).toBeTruthy();
    expect(screen.getByText("first page")).toBeTruthy(); // what was read stays
    fireEvent.click(screen.getByRole("button", { name: text.showMore }));
    expect(await screen.findByText("second page")).toBeTruthy();
  });

  it("starts the lines again from the top when the version changes", async () => {
    const api = {
      ...mockApi("table"),
      transcript: vi.fn(async (_ref: string, _n: number, version: string, offset?: number) =>
        offset === 2000
          ? view(["more of " + version], null)
          : { ...view(["first of " + version], 2000), version },
      ),
    } as unknown as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    await screen.findByText("first of cleaned");
    fireEvent.click(screen.getByRole("button", { name: text.showMore }));
    await screen.findByText("more of cleaned");
    fireEvent.click(screen.getByRole("button", { name: text.versions.heard }));
    expect(await screen.findByText("first of heard")).toBeTruthy();
    expect(screen.queryByText("more of cleaned")).toBeNull();
  });

  it("tells a transcript too big to show, in plain words", async () => {
    const api = {
      ...mockApi("table"),
      transcript: async () => {
        throw new ApiError("too-big");
      },
    } as AccountApi;
    page(api, `/account/transcripts/${REF}/2`);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.tooBig);
  });
});
