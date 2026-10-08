/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin page (#772) against a stubbed API: sign in both ways, the same words for every
// refusal, sign out with the CSRF token, and the page never linked from the site.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Admin from "../src/admin/Admin";
import {
  type AdminApi,
  AdminApiError,
  type AdminMe,
  type GrantsView,
  httpAdminApi,
} from "../src/admin/api";
import { grantError, NOTE_MAX, text, who } from "../src/content/admin";

afterEach(cleanup);

const ME: AdminMe = { email: "owner@example.com", csrf: "csrf-1" };

function stub(over: Partial<AdminApi> = {}): AdminApi {
  let me: AdminMe | null = null;
  return {
    me: vi.fn(async () => me),
    ways: vi.fn(async () => ({ google: true, password: true })),
    googleUrl: () => "https://api.example/admin/auth/google/start",
    signIn: vi.fn(async (_email: string, password: string) => {
      if (password !== "right password here") throw new AdminApiError("wrong");
      me = ME;
    }),
    signOut: vi.fn(async () => {
      me = null;
    }),
    grants: vi.fn(async () => ({ free: [], grants: [], log: [] })),
    give: vi.fn(async () => ({ action: "grant" as const, name: null })),
    revoke: vi.fn(async () => undefined),
    ...over,
  };
}

function fill(label: string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

describe("the admin page", () => {
  it("offers Google and the password when signed out", async () => {
    render(<Admin api={stub()} search="" />);
    const google = await screen.findByRole("link", { name: text.google });
    expect(google.getAttribute("href")).toBe("https://api.example/admin/auth/google/start");
    expect(screen.getByLabelText(text.password).getAttribute("type")).toBe("password");
  });

  it("signs in with the password, then signs out with the CSRF token", async () => {
    const api = stub();
    render(<Admin api={api} search="" />);
    await screen.findByLabelText(text.email);
    fill(text.email, " owner@example.com ");
    fill(text.password, "right password here");
    fireEvent.click(screen.getByRole("button", { name: text.signIn }));
    expect(await screen.findByText(text.signedInAs(ME.email))).toBeTruthy();
    expect(api.signIn).toHaveBeenCalledWith("owner@example.com", "right password here");
    fireEvent.click(screen.getByRole("button", { name: text.signOut }));
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    expect(api.signOut).toHaveBeenCalledWith("csrf-1");
  });

  it("a refusal says the same thing and clears the password", async () => {
    render(<Admin api={stub()} search="" />);
    await screen.findByLabelText(text.email);
    fill(text.email, "owner@example.com");
    fill(text.password, "a wrong password");
    fireEvent.click(screen.getByRole("button", { name: text.signIn }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.wrong);
    expect((screen.getByLabelText(text.password) as HTMLInputElement).value).toBe("");
  });

  it("a flood says busy, not that the password is wrong, and focus goes back to it", async () => {
    const signIn = vi.fn(async () => {
      throw new AdminApiError("busy");
    });
    render(<Admin api={stub({ signIn })} search="" />);
    await screen.findByLabelText(text.email);
    fill(text.email, "owner@example.com");
    fill(text.password, "right password here");
    fireEvent.click(screen.getByRole("button", { name: text.signIn }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.busy);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(text.password)));
  });

  it("shows the password on request, and hides it again", async () => {
    render(<Admin api={stub()} search="" />);
    const box = await screen.findByLabelText(text.password);
    fireEvent.click(screen.getByRole("button", { name: text.showPassword }));
    expect(box.getAttribute("type")).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: text.hidePassword }));
    expect(box.getAttribute("type")).toBe("password");
  });

  it("shows only the sign-ins the server has set up", async () => {
    const ways = vi.fn(async () => ({ google: false, password: true }));
    render(<Admin api={stub({ ways })} search="" />);
    await screen.findByLabelText(text.email);
    expect(screen.queryByRole("link", { name: text.google })).toBeNull();
    expect(screen.queryByText(text.or)).toBeNull();
    cleanup();
    const googleOnly = vi.fn(async () => ({ google: true, password: false }));
    render(<Admin api={stub({ ways: googleOnly })} search="" />);
    await screen.findByRole("link", { name: text.google });
    expect(screen.queryByLabelText(text.password)).toBeNull();
  });

  it("moves focus to the heading after signing in", async () => {
    const heading = document.createElement("h1");
    heading.id = "admin-heading";
    heading.tabIndex = -1;
    document.body.append(heading);
    try {
      render(<Admin api={stub()} search="" />);
      await screen.findByLabelText(text.email);
      fill(text.email, "owner@example.com");
      fill(text.password, "right password here");
      fireEvent.click(screen.getByRole("button", { name: text.signIn }));
      await screen.findByText(text.signedInAs(ME.email));
      await waitFor(() => expect(document.activeElement).toBe(heading));
    } finally {
      heading.remove();
    }
  });

  it("still says you're signed out if the page can't load after it", async () => {
    const api = stub();
    render(<Admin api={api} search="" />);
    await screen.findByLabelText(text.email);
    fill(text.email, "owner@example.com");
    fill(text.password, "right password here");
    fireEvent.click(screen.getByRole("button", { name: text.signIn }));
    fireEvent.click(await screen.findByRole("button", { name: text.signOut }));
    api.ways = vi.fn(async () => Promise.reject(new AdminApiError("down")));
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    expect(screen.getByText(text.down)).toBeTruthy();
  });

  it("words refusals for the sign-ins the server has", async () => {
    const ways = vi.fn(async () => ({ google: false, password: true }));
    render(<Admin api={stub({ ways })} search="" />);
    await screen.findByLabelText(text.email);
    fill(text.email, "owner@example.com");
    fill(text.password, "a wrong password");
    fireEvent.click(screen.getByRole("button", { name: text.signIn }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.wrongNoGoogle);
    cleanup();
    const googleOnly = vi.fn(async () => ({ google: true, password: false }));
    render(<Admin api={stub({ ways: googleOnly })} search="?signin=failed" />);
    await screen.findByRole("link", { name: text.google });
    expect(screen.getByRole("alert").textContent).toBe(text.googleFailedNoPassword);
  });

  it("ignores a signin= value that isn't one of its own", async () => {
    render(<Admin api={stub()} search="?signin=toString" />);
    await screen.findByLabelText(text.email);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says when Google's sign-in came back refused", async () => {
    render(<Admin api={stub()} search="?signin=failed" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.googleFailed);
  });

  it("says when the API can't be reached, with Try again", async () => {
    const me = vi.fn(async () => {
      throw new AdminApiError("down");
    });
    render(<Admin api={stub({ me })} search="" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.down);
    fireEvent.click(screen.getByRole("button", { name: text.tryAgain }));
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2));
  });
});

describe("httpAdminApi", () => {
  function fake(status: number, body?: unknown) {
    return vi.fn<typeof fetch>(
      async () =>
        new Response(body === undefined ? null : JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    );
  }

  it("reads who is signed in, and null when nobody is", async () => {
    expect(await httpAdminApi("https://api.example", fake(200, ME)).me()).toEqual(ME);
    expect(await httpAdminApi("https://api.example", fake(401)).me()).toBeNull();
  });

  it("signs in with the site's header and the cookie, never putting secrets in the URL", async () => {
    const fetcher = fake(204);
    await httpAdminApi("https://api.example/", fetcher).signIn("a@b.example", "pw pw pw pw");
    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/admin/auth/password");
    expect(init?.credentials).toBe("include");
    expect(init?.headers).toMatchObject({ "X-DMbot-Request": "1" });
    expect(String(url)).not.toContain("pw");
  });

  it("maps a refusal to 'wrong' and other failures to 'down'", async () => {
    await expect(httpAdminApi("/api", fake(401)).signIn("a", "b")).rejects.toMatchObject({
      kind: "wrong",
    });
    await expect(httpAdminApi("/api", fake(503)).signIn("a", "b")).rejects.toMatchObject({
      kind: "busy",
    });
    await expect(httpAdminApi("/api", fake(500)).signIn("a", "b")).rejects.toMatchObject({
      kind: "down",
    });
  });

  it("asks which sign-ins are set up", async () => {
    const ways = { google: false, password: true };
    expect(await httpAdminApi("/api", fake(200, ways)).ways()).toEqual(ways);
    await expect(httpAdminApi("/api", fake(404)).ways()).rejects.toMatchObject({ kind: "off" });
  });

  it("signs out with the CSRF token in a header", async () => {
    const fetcher = fake(204);
    await httpAdminApi("/api", fetcher).signOut("csrf-1");
    expect(fetcher.mock.calls[0]?.[1]?.headers).toMatchObject({ "X-Admin-CSRF": "csrf-1" });
  });
});

describe("the page is never linked from the site", () => {
  // vitest runs from web/; import.meta.url isn't a file URL under happy-dom.
  const src = join(process.cwd(), "src");
  const files = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      return statSync(full).isDirectory() ? files(full) : [full];
    });

  it("no other page or component links to /admin", () => {
    const linking = files(src)
      .filter((f) => !f.includes("/admin") && !f.endsWith("admin.astro"))
      .filter((f) => /href=["'{`]\/admin\b|["']\/admin["']/.test(readFileSync(f, "utf8")));
    expect(linking).toEqual([]);
  });
});

describe("ux follow-ups", () => {
  it("a failed sign-out says you're still signed in", async () => {
    const api = stub({ signOut: vi.fn(async () => Promise.reject(new AdminApiError("down"))) });
    api.me = vi.fn(async () => ME);
    render(<Admin api={api} search="" />);
    fireEvent.click(await screen.findByRole("button", { name: text.signOut }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.signOutFailed);
  });

  it("says how to switch the page on when the server has it off", async () => {
    const me = vi.fn(async () => Promise.reject(new AdminApiError("off")));
    render(<Admin api={stub({ me })} search="" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.off);
  });

  it("says when Google sign-in isn't set up", async () => {
    render(<Admin api={stub()} search="?signin=off" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.googleOff);
  });

  it("maps a 404 from /admin/me to 'off'", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(null, { status: 404 }));
    await expect(httpAdminApi("/api", fetcher).me()).rejects.toMatchObject({ kind: "off" });
  });
});

describe("free access (#773)", () => {
  const FRIEND = "123456789012345678";
  const LATE = "223456789012345678";
  const view = (): GrantsView => ({
    free: [{ discordId: "100000000000000001", name: null }],
    grants: [
      {
        discordId: FRIEND,
        name: "Sam",
        level: "guild",
        endsAt: 1_801_440_000, // stops at 2027-02-01 00:00 UTC: the last day is Jan 31
        note: "playtester",
        grantedBy: "owner@example.com",
        grantedAt: 1_800_000_000,
      },
    ],
    log: [
      { at: 1_800_000_000, by: "owner@example.com", action: "grant", discordId: FRIEND, name: "Sam" },
    ],
  });

  function signedIn(over: Partial<AdminApi> = {}) {
    const api = stub({ me: vi.fn(async () => ME), grants: vi.fn(async () => view()), ...over });
    render(<Admin api={api} search="" />);
    return api;
  }

  const row = async (id: string) => {
    await screen.findByText(text.alwaysFree);
    return document.querySelector(`[data-grant="${id}"]`) as HTMLElement;
  };

  it("lists the free list (not revocable), each grant by name, and the recent changes", async () => {
    signedIn();
    const free = (await screen.findByText(text.alwaysFree)).closest("li") as HTMLElement;
    expect(free.textContent).toContain("100000000000000001");
    expect(within(free).queryByRole("button")).toBeNull();
    const sam = await row(FRIEND);
    expect(sam.querySelector("strong")?.textContent).toBe("Sam");
    expect(sam.textContent).toContain(FRIEND); // the id stays, smaller, under the name
    expect(sam.textContent).toContain("Same as Guild");
    expect(sam.textContent).toContain(text.until("Jan 31, 2027"));
    expect(sam.textContent).toContain("playtester");
    expect(sam.textContent).toContain(text.setBy("owner@example.com", "Jan 15, 2027"));
    expect(
      screen.getByText(text.logLine("Jan 15, 2027", "owner@example.com", "grant", "Sam")),
    ).toBeTruthy();
  });

  it("someone with no name shows by id, and shortened in sentences", async () => {
    const nameless = view();
    nameless.grants[0] = { ...nameless.grants[0]!, name: null };
    signedIn({ grants: vi.fn(async () => nameless) });
    const sam = await row(FRIEND);
    expect(sam.querySelector("strong")?.textContent).toBe(FRIEND);
    fireEvent.click(within(sam).getByRole("button", { name: text.revoke }));
    expect(sam.textContent).toContain(text.confirmRevoke("1234…5678"));
    expect(who(null, FRIEND)).toBe("1234…5678");
    expect(who("Sam", FRIEND)).toBe("Sam");
  });

  it("gives free access with the CSRF token and says who by name", async () => {
    const give = vi.fn(async () => ({ action: "grant" as const, name: "Sam" }));
    const api = signedIn({ give });
    await screen.findByLabelText(text.idLabel);
    fireEvent.input(screen.getByLabelText(text.idLabel), { target: { value: ` ${LATE} ` } });
    fireEvent.click(screen.getByLabelText(text.levels.unlimited ?? ""));
    fireEvent.input(screen.getByLabelText(text.noteLabel), { target: { value: "a friend" } });
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    expect(await screen.findByText(text.added("Sam"))).toBeTruthy();
    expect(api.give).toHaveBeenCalledWith("csrf-1", {
      discordId: LATE,
      level: "unlimited",
      endsOn: null,
      note: "a friend",
    });
  });

  it("the level is two choices, Same as Guild first", async () => {
    signedIn();
    await screen.findByLabelText(text.idLabel);
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((r) => r.value)).toEqual(["guild", "unlimited"]);
    expect(radios[0]?.checked).toBe(true);
    expect(screen.getByText(text.levelHint)).toBeTruthy();
  });

  it("the end date can't be today or before, and No end date clears it", async () => {
    signedIn();
    const box = (await screen.findByLabelText(text.endLabel)) as HTMLInputElement;
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    expect(box.min).toBe(tomorrow);
    expect(screen.queryByRole("button", { name: text.noEndButton })).toBeNull();
    fireEvent.input(box, { target: { value: "2099-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: text.noEndButton }));
    expect(box.value).toBe("");
  });

  it("a refused id says what to fix, next to the button, and goes back to the box", async () => {
    const give = vi.fn(async () => Promise.reject(new AdminApiError("bad-id")));
    signedIn({ give });
    await screen.findByLabelText(text.idLabel);
    fireEvent.input(screen.getByLabelText(text.idLabel), { target: { value: "12345" } });
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    const news = await screen.findByText(grantError("bad-id") ?? "");
    expect(news.closest("form")).toBeTruthy(); // right above the button, not a screen up
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(text.idLabel)));
  });

  it("the agreed words reach the owner, built from one note length", () => {
    expect(grantError("past-date")).toBe(
      "Pick an end date after today, or leave it empty so it never ends.",
    );
    expect(grantError("bad-level")).toBe("Pick how much: Same as Guild, or No limits.");
    expect(grantError("bad-id")).toBe(
      "That isn't a Discord account number. In Discord, right-click the person and pick Copy User ID.",
    );
    expect(grantError("long-note")).toBe(`Keep the note to ${NOTE_MAX} characters or fewer.`);
    expect(NOTE_MAX).toBe(200);
  });

  it("Change fills the form for that person: their name, a locked id, Save changes, Start over", async () => {
    const api = signedIn();
    const sam = await row(FRIEND);
    fireEvent.click(within(sam).getByRole("button", { name: text.change }));
    expect(screen.getByRole("heading", { name: text.changeHeading("Sam") })).toBeTruthy();
    const id = screen.getByLabelText(text.idLabel) as HTMLInputElement;
    expect(id.value).toBe(FRIEND);
    expect(id.readOnly).toBe(true);
    expect((screen.getByLabelText(text.endLabel) as HTMLInputElement).value).toBe("2027-01-31");
    expect((screen.getByLabelText(text.noteLabel) as HTMLInputElement).value).toBe("playtester");
    expect(screen.queryByRole("button", { name: text.add })).toBeNull();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText(text.levels.guild ?? "")),
    );
    fireEvent.click(screen.getByLabelText(text.levels.unlimited ?? ""));
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await waitFor(() => expect(api.give).toHaveBeenCalled());
    expect(api.give).toHaveBeenCalledWith("csrf-1", {
      discordId: FRIEND,
      level: "unlimited",
      endsOn: "2027-01-31",
      note: "playtester",
    });
    // Saved: back to adding.
    expect(await screen.findByRole("heading", { name: text.addHeading })).toBeTruthy();
  });

  it("Start over clears the change", async () => {
    signedIn();
    const sam = await row(FRIEND);
    fireEvent.click(within(sam).getByRole("button", { name: text.change }));
    fireEvent.click(screen.getByRole("button", { name: text.startOver }));
    expect((screen.getByLabelText(text.idLabel) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(text.idLabel) as HTMLInputElement).readOnly).toBe(false);
    expect(screen.getByRole("heading", { name: text.addHeading })).toBeTruthy();
  });

  it("an empty id is caught on the page, with the words next to the form", async () => {
    const api = signedIn();
    await screen.findByLabelText(text.idLabel);
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    expect(await screen.findByText(grantError("no-id") ?? "")).toBeTruthy();
    expect(api.give).not.toHaveBeenCalled();
  });

  it("revoke asks first, by name, then revokes and says so", async () => {
    const api = signedIn();
    const sam = await row(FRIEND);
    fireEvent.click(within(sam).getByRole("button", { name: text.revoke }));
    expect(sam.textContent).toContain(text.confirmRevoke("Sam"));
    fireEvent.click(within(sam).getByRole("button", { name: text.cancel }));
    expect(api.revoke).not.toHaveBeenCalled();
    fireEvent.click(within(sam).getByRole("button", { name: text.revoke }));
    fireEvent.click(within(sam).getByRole("button", { name: text.yesRevoke }));
    const done = await screen.findByText(text.revoked("Sam"));
    expect(api.revoke).toHaveBeenCalledWith("csrf-1", FRIEND);
    await waitFor(() => expect(document.activeElement).toBe(done));
  });

  it("a revoke that finds nothing says so and reloads the list", async () => {
    const revoke = vi.fn(async () => Promise.reject(new AdminApiError("no-grant")));
    const api = signedIn({ revoke });
    const sam = await row(FRIEND);
    fireEvent.click(within(sam).getByRole("button", { name: text.revoke }));
    fireEvent.click(within(sam).getByRole("button", { name: text.yesRevoke }));
    expect(await screen.findByText(grantError("no-grant") ?? "")).toBeTruthy();
    await waitFor(() => expect(api.grants).toHaveBeenCalledTimes(2));
  });

  it("an ended grant says Ended and how to renew, and comes after the live ones", async () => {
    const past: GrantsView = {
      ...view(),
      grants: [
        { ...view().grants[0]!, discordId: LATE, name: null, endsAt: 1_000_000_000 },
        ...view().grants,
      ],
    };
    signedIn({ grants: vi.fn(async () => past) });
    await screen.findByText(FRIEND);
    const rows = [...document.querySelectorAll("[data-grant]")].map((r) =>
      r.getAttribute("data-grant"),
    );
    expect(rows).toEqual([FRIEND, LATE]);
    const old = document.querySelector(`[data-grant="${LATE}"]`) as HTMLElement;
    expect(old.textContent).toContain(text.ended("Sep 9, 2001"));
    expect(text.ended("Sep 9, 2001")).toContain("Tap Change");
  });

  it("an ended session goes back to sign-in with the timed-out words", async () => {
    let signedInNow = true;
    const grants = vi.fn(async () => {
      if (!signedInNow) throw new AdminApiError("signed-out");
      return view();
    });
    const give = vi.fn(async () => {
      signedInNow = false;
      throw new AdminApiError("signed-out");
    });
    const me = vi.fn(async () => (signedInNow ? ME : null));
    render(<Admin api={stub({ me, grants, give })} search="" />);
    await screen.findByLabelText(text.idLabel);
    fireEvent.input(screen.getByLabelText(text.idLabel), { target: { value: FRIEND } });
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    expect(await screen.findByText(text.timedOut)).toBeTruthy();
    expect(await screen.findByLabelText(text.password)).toBeTruthy();
  });

  it("the page switched off while open shows that, not 'can't reach DMbot'", async () => {
    let on = true;
    const grants = vi.fn(async () => {
      if (!on) throw new AdminApiError("off");
      return view();
    });
    const me = vi.fn(async () => {
      if (!on) throw new AdminApiError("off");
      return ME;
    });
    const give = vi.fn(async () => {
      on = false;
      throw new AdminApiError("off");
    });
    render(<Admin api={stub({ me, grants, give })} search="" />);
    await screen.findByLabelText(text.idLabel);
    fireEvent.input(screen.getByLabelText(text.idLabel), { target: { value: FRIEND } });
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.off);
  });

  it("maps the API's refusals to plain problems", async () => {
    const answer = (status: number, body?: unknown) =>
      vi.fn<typeof fetch>(
        async () =>
          new Response(body === undefined ? null : JSON.stringify(body), {
            status,
            headers: { "Content-Type": "application/json" },
          }),
      );
    const request = { discordId: FRIEND, level: "guild" as const, endsOn: null, note: "" };
    for (const [code, kind] of [
      ["bad_id", "bad-id"],
      ["bad_level", "bad-level"],
      ["past_date", "past-date"],
      ["long_note", "long-note"],
      ["bad_note", "bad-note"],
      ["already_free", "already-free"],
      ["sign_in_again", "signed-out"],
      ["not_found", "off"],
    ] as const) {
      await expect(
        httpAdminApi("/api", answer(400, { error: code })).give("c", request),
      ).rejects.toMatchObject({ kind });
    }
    for (const [status, code] of [
      [400, "bad_request"],
      [403, "not_allowed"],
      [413, "too_long"],
    ] as const) {
      await expect(
        httpAdminApi("/api", answer(status, { error: code })).give("c", request),
      ).rejects.toMatchObject({ kind: "stale" });
    }
    await expect(
      httpAdminApi("/api", answer(404, { error: "no_grant" })).revoke("c", FRIEND),
    ).rejects.toMatchObject({ kind: "no-grant" });
    await expect(httpAdminApi("/api", answer(401)).grants()).rejects.toMatchObject({
      kind: "signed-out",
    });
    const fetcher = answer(200, { action: "change", name: "Sam" });
    expect(await httpAdminApi("/api", fetcher).give("csrf-9", request)).toEqual({
      action: "change",
      name: "Sam",
    });
    expect(fetcher.mock.calls[0]?.[1]?.headers).toMatchObject({ "X-Admin-CSRF": "csrf-9" });
  });
});
