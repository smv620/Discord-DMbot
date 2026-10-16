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
    expect(screen.getByRole("link", { name: text.open }).getAttribute("href")).toBe(
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
    expect(screen.getByRole("link", { name: text.signIn })).toBeTruthy();
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
    expect(titles).toEqual(["Session 2", "Session 1"]);
    expect(screen.getByText(/3 lines/)).toBeTruthy();
    expect(screen.getByRole("link", { name: `${text.open}: Session 2` }).getAttribute("href")).toBe(
      `/account/transcripts/${REF}/2`,
    );
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
    expect(screen.getByText(cleanedNote)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.versions.cleaned }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText(/did you see the match/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: text.versions.heard }));
    expect(await screen.findByText(/Kazeth draws his sword/)).toBeTruthy();
    expect(screen.getByText(heardNote)).toBeTruthy();
    expect(screen.queryByText(cleanedNote)).toBeNull();
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
    expect(fold.textContent).not.toContain(cleanedNote);
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
