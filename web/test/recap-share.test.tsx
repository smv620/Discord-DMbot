/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Sharing a recap by a link (#2119): the state in the list, the share controls on a posted recap,
// the public page that anyone with the link reads, and the headers that keep the secret address
// out of caches and referrers. It shows only while RECAP_SHARE_LIVE is true (false today).
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import PublicRecapPage, { tokenFromPath } from "../src/account/PublicRecap";
import Recaps from "../src/account/Recaps";
import { publicWords, shareWords } from "../src/content/recap-share";
import { statusLine } from "../src/content/recaps";

const live = vi.hoisted(() => ({ share: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_SHARE_LIVE() {
      return live.share;
    },
    // Sharing needs recaps on too: the tests run as they will once both are live.
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  live.share = false;
});

const [draft, posted] = mockRecaps();
const postedPath = `/account/recaps/${posted!.ref}`;

describe("the list", () => {
  it("says Shared for a posted recap with a link on, only while sharing is live", () => {
    const row = { ...posted!, shared: true };
    expect(statusLine(row)).toBe("Posted");
    live.share = true;
    expect(statusLine(row)).toBe(shareWords.listTag);
    expect(statusLine({ ...row, shared: false })).toBe("Posted");
    expect(statusLine({ ...draft!, shared: true })).not.toBe(
      shareWords.listTag,
    ); // a draft is never shared
  });

  it("lists the newest session first, whatever order the API sends", async () => {
    const api = mockApi("table");
    api.recaps = async () => [
      { ...posted!, ref: "1.a.100", title: "Older", sessionAt: 100 },
      { ...posted!, ref: "1.a.300", title: "Newest", sessionAt: 300 },
      { ...posted!, ref: "1.a.200", title: "Middle", sessionAt: 200 },
    ];
    delete (api as { recapList?: unknown }).recapList;
    render(<Recaps api={api} path="/account/recaps" />);
    await screen.findByText("Newest");
    const names = screen
      .getAllByText(/^(Older|Newest|Middle)$/)
      .map((n) => n.textContent);
    expect(names).toEqual(["Newest", "Middle", "Older"]);
  });
});

describe("the share controls", () => {
  async function open(path = postedPath) {
    live.share = true;
    const api = mockApi("table");
    render(<Recaps api={api} path={path} />);
    return api;
  }

  it("are not on the page while sharing is off", async () => {
    const api = mockApi("table");
    render(<Recaps api={api} path={postedPath} />);
    await screen.findByText("The vault stayed shut.");
    expect(
      screen.queryByRole("heading", { name: shareWords.heading }),
    ).toBeNull();
  });

  it("say a draft has to be posted first", async () => {
    await open(`/account/recaps/${draft!.ref}`);
    expect(await screen.findByText(shareWords.notPosted)).toBeTruthy(); // approve it first
    expect(screen.queryByRole("button", { name: shareWords.make })).toBeNull();
  });

  it("are there for a recap the DM approved and did not post, only when asked for", async () => {
    live.share = true;
    const api = mockApi("table");
    await api.approveRecap!(draft!.ref, "text"); // approved, not posted
    render(<Recaps api={api} path={`/account/recaps/${draft!.ref}`} />);
    expect(await screen.findByText(shareWords.offLeadApproved(shareWords.audio))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("shareRecap"))).toBe(false); // nothing made by approving
    fireEvent.click(screen.getByRole("button", { name: shareWords.make }));
    expect(await screen.findByLabelText(shareWords.linkLabel)).toBeTruthy();
    expect(api.calls).toContain(`shareRecap:${draft!.ref}:on`);
  });

  it("make a link with the page's own address, then turn it off or make a new one", async () => {
    const api = await open();
    fireEvent.click(
      await screen.findByRole("button", { name: shareWords.make }),
    );
    const field = (await screen.findByLabelText(
      shareWords.linkLabel,
    )) as HTMLInputElement;
    expect(field.value).toBe(`${window.location.origin}/r/secret1`);
    expect(screen.getByText(/treat the link like a password/)).toBeTruthy();
    expect(api.calls).toContain(`shareRecap:${posted!.ref}:on`);
    fireEvent.click(screen.getByRole("button", { name: shareWords.fresh }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText(shareWords.linkLabel) as HTMLInputElement).value,
      ).toMatch(/\/r\/secret2$/),
    );
    expect(await screen.findByText(shareWords.doneFresh)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: shareWords.off }));
    expect(await screen.findByText(shareWords.doneOff)).toBeTruthy();
    expect(screen.queryByLabelText(shareWords.linkLabel)).toBeNull();
    expect(screen.getByRole("button", { name: shareWords.make })).toBeTruthy();
  });

  it("copy the link, and say what to do where copying is not allowed", async () => {
    await open();
    fireEvent.click(
      await screen.findByRole("button", { name: shareWords.make }),
    );
    await screen.findByLabelText(shareWords.linkLabel);
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: write },
      configurable: true,
    });
    fireEvent.click(screen.getByRole("button", { name: shareWords.copy }));
    await waitFor(() =>
      expect(write).toHaveBeenCalledWith(`${window.location.origin}/r/secret1`),
    );
    expect(
      await screen.findByRole("button", { name: shareWords.copied }),
    ).toBeTruthy();
    write.mockRejectedValue(new Error("no"));
    fireEvent.click(screen.getByRole("button", { name: shareWords.copied })); // it still reads Copied for a moment
    expect((await screen.findByRole("alert")).textContent).toBe(
      shareWords.copyFailed,
    );
  });

  it("show the API's refusal in plain words", async () => {
    live.share = true;
    const api = mockApi("table");
    api.shareRecap = async () => {
      throw new ApiError("not-posted");
    };
    render(<Recaps api={api} path={postedPath} />);
    fireEvent.click(
      await screen.findByRole("button", { name: shareWords.make }),
    );
    expect((await screen.findByRole("alert")).textContent).toBe(
      shareWords.problem["not-posted"],
    );
  });
});

describe("the public page", () => {
  const show = (api = mockApi("table"), path = "/r/secret1") => {
    live.share = true;
    return render(<PublicRecapPage api={api} path={path} />);
  };

  it("reads the secret out of the address", () => {
    expect(tokenFromPath("/r/abc123")).toBe("abc123");
    expect(tokenFromPath("/r/abc123/")).toBe("abc123");
    expect(tokenFromPath("/r/")).toBeNull();
    expect(tokenFromPath("/r/%E0")).toBeNull(); // mangled: a dead link, not a crash
    expect(tokenFromPath("/account/recaps/x")).toBeNull();
  });

  it("says a recap the DM approved and shared was not posted, instead of a posted day (#2419)", async () => {
    const api = mockApi("table");
    const real = api.publicRecap!.bind(api);
    await api.shareRecap!(posted!.ref, "on");
    api.publicRecap = async (token) => ({ ...(await real(token)), postedAt: null });
    show(api, "/r/secret1");
    await screen.findByRole("heading", { name: posted!.title });
    expect(screen.getByText(publicWords.approved)).toBeTruthy();
    expect(screen.queryByText(/^Posted /)).toBeNull();
  });

  it("never shows the checks' notes, even if the answer carried them (#2297)", async () => {
    const api = mockApi("table");
    const real = api.publicRecap!.bind(api);
    await api.shareRecap!(posted!.ref, "on");
    api.publicRecap = async (token) =>
      ({ ...(await real(token)), criticNotes: ["Check: a note only the DM may read."] }) as never;
    show(api, "/r/secret1");
    await screen.findByRole("heading", { name: posted!.title });
    expect(document.body.textContent).not.toContain("Check: a note only the DM may read.");
    expect(screen.queryByText("What the checks said")).toBeNull();
  });

  it("shows the title and text and nothing else, without a sign-in", async () => {
    const api = mockApi("table");
    await api.shareRecap!(posted!.ref, "on");
    show(api, "/r/secret1");
    expect(
      await screen.findByRole("heading", { name: posted!.title }),
    ).toBeTruthy();
    expect(screen.getByText(posted!.text)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull(); // no editing, no sharing, no sign-in
    expect(screen.queryByRole("link")).toBeNull();
    expect(api.calls).toContain("publicRecap:secret1");
  });

  it("says one plain thing for every dead link", async () => {
    show(mockApi("table"), "/r/nope");
    expect(await screen.findByText(publicWords.dead)).toBeTruthy();
    cleanup();
    show(mockApi("table"), "/r/");
    expect(await screen.findByText(publicWords.dead)).toBeTruthy();
  });

  it("says nothing is open while sharing is not live, without asking the API", async () => {
    const api = mockApi("table");
    render(<PublicRecapPage api={api} path="/r/secret1" />);
    expect(await screen.findByText(publicWords.dead)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("publicRecap"))).toBe(false);
  });

  it("says it could not load, with a way to try again, when the connection fails", async () => {
    const api = mockApi("table");
    let n = 0;
    api.publicRecap = async () => {
      n += 1;
      if (n === 1) throw new ApiError("network");
      return { title: "T", text: "t", postedAt: 1, audio: {} };
    };
    show(api);
    expect(await screen.findByText(publicWords.trouble)).toBeTruthy();
    expect(screen.queryByText(publicWords.dead)).toBeNull(); // not told to bother the DM
    fireEvent.click(screen.getByRole("button", { name: publicWords.tryAgain }));
    expect(await screen.findByRole("heading", { name: "T" })).toBeTruthy();
  });

  it("asks the reader to wait when there have been too many tries", async () => {
    const api = mockApi("table");
    api.publicRecap = async () => {
      throw new ApiError("slow-down");
    };
    show(api);
    expect(await screen.findByText(publicWords.busy)).toBeTruthy();
  });

  it("plays the voice and music when the API sends them", async () => {
    const api = mockApi("table");
    api.publicRecap = async () => ({
      title: "T",
      text: "One.\n\nTwo.",
      postedAt: 1790000000,
      audio: {
        voice: "https://api.example.test/recaps/x/audio/voice",
        music: "https://api.example.test/recaps/x/audio/music",
      },
    });
    const { container } = show(api);
    await screen.findByRole("heading", { name: "T" });
    expect(container.querySelectorAll("audio")).toHaveLength(2);
    expect(screen.getByText("One.")).toBeTruthy();
    expect(screen.getByText("Two.")).toBeTruthy();
  });
});

describe("over the wire", () => {
  it("shares to the recap's own address and reads the public page by its secret", async () => {
    const seen: { url: string; body: string }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      seen.push({ url, body: String(init.body ?? "") });
      return new Response(
        JSON.stringify({
          on: true,
          path: "/r/x",
          title: "T",
          text: "t",
          postedAt: 1,
          audio: {},
        }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;
    const real = httpApi("https://api.example.test", fetcher);
    await real.shareRecap!("1.a.5", "new");
    await real.publicRecap!("se/cret");
    expect(seen[0]).toEqual({
      url: "https://api.example.test/me/recaps/1.a.5/share",
      body: JSON.stringify({ action: "new" }),
    });
    expect(seen[1]?.url).toBe("https://api.example.test/recaps/se%2Fcret");
  });

  it("reads a shared recap with no cookie and no custom header", async () => {
    const seen: RequestInit[] = [];
    const fetcher = (async (_url: string, init: RequestInit) => {
      seen.push(init);
      return new Response(
        JSON.stringify({ title: "T", text: "t", postedAt: 1, audio: {} }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;
    await httpApi("https://api.example.test", fetcher).publicRecap!("x");
    expect(seen[0]?.credentials).toBe("omit");
    expect(
      Object.keys((seen[0]?.headers as Record<string, string>) ?? {}),
    ).toEqual(["Accept"]);
  });

  it("reads the refusals", async () => {
    const reply = (status: number, error: string) =>
      httpApi(
        "https://api.example.test",
        (async () =>
          new Response(JSON.stringify({ error }), {
            status,
          })) as unknown as typeof fetch,
      );
    await expect(
      reply(409, "not_posted").shareRecap!("r", "on"),
    ).rejects.toMatchObject({ kind: "not-posted" });
    await expect(
      reply(404, "not_found").publicRecap!("t"),
    ).rejects.toMatchObject({ kind: "not-found" });
    await expect(
      reply(429, "slow_down").publicRecap!("t"),
    ).rejects.toMatchObject({ kind: "slow-down" });
  });
});

describe("the headers and the page", () => {
  const headers = readFileSync("public/_headers", "utf8");
  const block = (path: string): string[] => {
    const lines = headers.split("\n");
    const at = lines.indexOf(path);
    const out: string[] = [];
    for (let i = at + 1; i < lines.length && lines[i]?.startsWith(" "); i += 1)
      out.push((lines[i] ?? "").trim());
    return out;
  };

  it("keep a link's secret out of caches, search and referrers", () => {
    const lines = block("/r/*");
    expect(lines).toContain("Cache-Control: no-store");
    expect(lines).toContain("X-Robots-Tag: noindex, nofollow");
    expect(lines.indexOf("! Referrer-Policy")).toBeGreaterThanOrEqual(0);
    expect(lines.indexOf("Referrer-Policy: no-referrer")).toBeGreaterThan(
      lines.indexOf("! Referrer-Policy"),
    );
  });

  it("serve every link from the one page", () => {
    expect(readFileSync("public/_redirects", "utf8")).toMatch(
      /^\/r\/\*\s+\/r\s+200$/m,
    );
  });

  it("has no menu and no footer, and asks not to be listed", () => {
    const page = readFileSync("src/pages/r.astro", "utf8");
    expect(page).toMatch(/\bbare\b/);
    expect(page).toMatch(/\bnoindex\b/);
    expect(page).toMatch(/\bnofollow\b/);
  });
});
