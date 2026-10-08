/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin page (#772) against a stubbed API: sign in both ways, the same words for every
// refusal, sign out with the CSRF token, and the page never linked from the site.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Admin from "../src/admin/Admin";
import { type AdminApi, AdminApiError, type AdminMe, httpAdminApi } from "../src/admin/api";
import { text } from "../src/content/admin";

afterEach(cleanup);

const ME: AdminMe = { email: "owner@example.com", csrf: "csrf-1" };

function stub(over: Partial<AdminApi> = {}): AdminApi {
  let me: AdminMe | null = null;
  return {
    me: vi.fn(async () => me),
    googleUrl: () => "https://api.example/admin/auth/google/start",
    signIn: vi.fn(async (_email: string, password: string) => {
      if (password !== "right password here") throw new AdminApiError("wrong");
      me = ME;
    }),
    signOut: vi.fn(async () => {
      me = null;
    }),
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
    await expect(httpAdminApi("/api", fake(500)).signIn("a", "b")).rejects.toMatchObject({
      kind: "down",
    });
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
