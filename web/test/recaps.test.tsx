/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The DM's recap pages (#1386): the list, the review page, the three buttons, the edit round
// trip and the refusal a player gets. Text only; the audio parts come with #1383.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps, { refFromPath } from "../src/account/Recaps";
import { text } from "../src/content/recaps";
import { RECAPS_LIVE } from "../src/content/recap-words";

afterEach(cleanup);

const ready = mockRecaps()[0]!;
const reviewPath = `/account/recaps/${ready.ref}`;

function renderPage(path: string, api: AccountApi = mockApi("table")) {
  render(<Recaps api={api} path={path} />);
  return api as ReturnType<typeof mockApi>;
}

describe("the address", () => {
  it("reads the recap id from /account/recaps/<id>, and nothing from the list page", () => {
    expect(refFromPath(reviewPath)).toBe(ready.ref);
    expect(refFromPath(`${reviewPath}/`)).toBe(ready.ref);
    expect(refFromPath("/account/recaps")).toBeNull();
  });
});

describe("the list", () => {
  it("shows each recap newest first with a plain status", async () => {
    renderPage("/account/recaps");
    await screen.findByText("The ferry at dusk");
    expect(screen.getByText("Ready to read")).toBeTruthy();
    expect(screen.getByText("Posted")).toBeTruthy();
    const links = screen.getAllByRole("link", { name: text.open });
    expect(links[0]?.getAttribute("href")).toBe(`/account/recaps/${ready.ref}`);
  });

  it("says so when there are none", async () => {
    const api = { ...mockApi("table"), recaps: async () => [] };
    renderPage("/account/recaps", api);
    expect(await screen.findByText(RECAPS_LIVE ? text.empty : text.emptyOff)).toBeTruthy();
  });

  it("asks a signed-out person to sign in", async () => {
    renderPage("/account/recaps", mockApi("signed-out"));
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
  });
});

describe("the review page", () => {
  it("shows the recap without cues, and says only the DM sees it", async () => {
    renderPage(reviewPath);
    expect(
      await screen.findByText(/The party crossed the Brynwater/),
    ).toBeTruthy();
    expect(screen.queryByText(/\[music/)).toBeNull();
    expect(screen.getByText(text.onlyYou)).toBeTruthy();
    expect(screen.getByText("Mira is down to 4 hit points.")).toBeTruthy();
  });

  it("gives a player, or a stranger, the same 'not found' as a missing recap", async () => {
    renderPage("/account/recaps/200000000000000001.other.1");
    expect(await screen.findByText(text.notFound)).toBeTruthy();
    expect(screen.queryByText(text.post)).toBeNull();
  });

  it("saves an edit and shows it", async () => {
    const api = renderPage(reviewPath);
    fireEvent.click(await screen.findByRole("button", { name: text.edit }));
    const box = screen.getByLabelText(text.editLabel) as HTMLTextAreaElement;
    expect(box.value).toContain("[music: calm]");
    fireEvent.input(box, { target: { value: "A new ending." } });
    fireEvent.input(screen.getByLabelText(text.housekeepingLabel), {
      target: { value: "One thing\n\n  Another  " },
    });
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect(await screen.findByText(text.saved)).toBeTruthy();
    expect(screen.getByText("A new ending.")).toBeTruthy();
    expect(screen.getByText("Another")).toBeTruthy();
    expect(api.calls).toContain(`saveRecap:${ready.ref}`);
  });

  it("says why a save was refused", async () => {
    const api = mockApi("table");
    api.saveRecap = async () => {
      throw new ApiError("bad-text");
    };
    renderPage(reviewPath, api);
    fireEvent.click(await screen.findByRole("button", { name: text.edit }));
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/too long/);
  });

  it("has exactly the three decisions, and Post only asks the bot", async () => {
    const api = renderPage(reviewPath);
    await screen.findByText(text.onlyYou);
    expect(screen.getByRole("button", { name: text.post })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Try again (2 left)" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: text.drop })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.post }));
    expect(await screen.findByText(text.posting)).toBeTruthy();
    expect(api.calls).toContain(`postRecap:${ready.ref}`);
    expect(screen.queryByRole("button", { name: text.post })).toBeNull();
  });

  it("counts down Try again and greys it at none", async () => {
    renderPage(reviewPath);
    fireEvent.click(
      await screen.findByRole("button", { name: "Try again (2 left)" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Try again (1 left)" }),
    );
    const last = (await screen.findByRole("button", {
      name: text.again,
    })) as HTMLButtonElement;
    expect(last.disabled).toBe(true);
    expect(screen.getByText(text.noTriesLeft)).toBeTruthy();
  });

  it("keeps the recap and says so after Don't post", async () => {
    renderPage(reviewPath);
    fireEvent.click(await screen.findByRole("button", { name: text.drop }));
    expect(await screen.findByText(text.dropped)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.post })).toBeNull();
  });

  it("offers no buttons on a posted recap", async () => {
    renderPage(`/account/recaps/${mockRecaps()[1]!.ref}`);
    expect(await screen.findByText(text.posted)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows a recap that is still being made as waiting, with nothing to press", async () => {
    const api = mockApi("table");
    api.recap = async () => ({
      ...ready,
      working: true,
      state: "queued",
      text: "",
      canEdit: false,
      canPost: false,
    });
    renderPage(reviewPath, api);
    expect(await screen.findByText(text.working)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.post })).toBeNull();
  });
});

describe("the page keeps up with the bot", () => {
  it("sends one request when Post is tapped twice quickly", async () => {
    const api = renderPage(reviewPath);
    const post = await screen.findByRole("button", { name: text.post });
    fireEvent.click(post);
    fireEvent.click(post);
    await screen.findByText(text.posting);
    expect(api.calls.filter((c) => c.startsWith("postRecap"))).toHaveLength(1);
  });

  it("reads the recap again after a refusal, so the buttons match the truth", async () => {
    const api = mockApi("table");
    const fresh = {
      ...ready,
      status: "posted" as const,
      canEdit: false,
      canPost: false,
      canTryAgain: false,
      canDrop: false,
    };
    let reads = 0;
    api.recap = async () => (reads++ === 0 ? ready : fresh);
    api.decideRecap = async () => {
      throw new ApiError("not-allowed");
    };
    renderPage(reviewPath, api);
    fireEvent.click(await screen.findByRole("button", { name: text.post }));
    expect(await screen.findByText(text.posted)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.post })).toBeNull();
  });

  it("looks again while the recap is being posted, until it is posted", async () => {
    vi.useFakeTimers();
    try {
      const api = mockApi("table");
      const posting = {
        ...ready,
        status: "posting" as const,
        canEdit: false,
        canPost: false,
      };
      let reads = 0;
      api.recap = async () =>
        reads++ < 1 ? posting : { ...posting, status: "posted" as const };
      renderPage(reviewPath, api);
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByText(text.posting)).toBeTruthy();
      await vi.advanceTimersByTimeAsync(5000);
      expect(screen.getByText(text.posted)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows an error, not an empty list, when the API has no recap route", async () => {
    const { recaps: _recaps, ...rest } = mockApi("table");
    renderPage("/account/recaps", rest as AccountApi);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(text.empty)).toBeNull();
  });
});

describe("the API calls", () => {
  it("sends the edit as PUT with the custom header and the id in the path", async () => {
    const fetcher = vi.fn(
      async () => new Response(JSON.stringify(ready), { status: 200 }),
    );
    const api = httpApi(
      "https://api.example",
      fetcher as unknown as typeof fetch,
    );
    await api.saveRecap!(ready.ref, "New", ["A"]);
    const [url, init] = fetcher.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      `https://api.example/me/recaps/${encodeURIComponent(ready.ref)}`,
    );
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["X-DMbot-Request"]).toBe(
      "1",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      summary: "New",
      housekeeping: ["A"],
    });
  });

  it.each([["post"], ["again"], ["drop"]] as const)(
    "posts %s to its own route",
    async (action) => {
      const fetcher = vi.fn(
        async () => new Response(JSON.stringify(ready), { status: 200 }),
      );
      const api = httpApi(
        "https://api.example",
        fetcher as unknown as typeof fetch,
      );
      await api.decideRecap!(ready.ref, action);
      const [url, init] = fetcher.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ];
      expect(url.endsWith(`/${action}`)).toBe(true);
      expect(init.method).toBe("POST");
    },
  );

  it("turns the API's refusals into kinds", async () => {
    for (const [code, kind] of [
      ["not_found", "not-found"],
      ["not_editable", "not-editable"],
      ["bad_text", "bad-text"],
    ] as const) {
      const fetcher = vi.fn(
        async () =>
          new Response(JSON.stringify({ error: code }), { status: 409 }),
      );
      const api = httpApi(
        "https://api.example",
        fetcher as unknown as typeof fetch,
      );
      await expect(api.recap!("x")).rejects.toMatchObject({ kind });
    }
  });
});

describe("the hosting files", () => {
  it("serve every recap id from the one recaps page, uncached", async () => {
    const { readFileSync } = await import("node:fs");
    const redirects = readFileSync("public/_redirects", "utf8");
    expect(redirects).toMatch(
      /^\/account\/recaps\/\*\s+\/account\/recaps\s+200$/m,
    );
    const headers = readFileSync("public/_headers", "utf8");
    expect(headers).toMatch(/\/account\/recaps\*\n\s+Cache-Control: no-store/);
  });
});
