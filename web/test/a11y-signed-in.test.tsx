/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Accessibility of the admin page and the Import page (#2249): axe-core on each, at the "serious"
// and "critical" level, with every panel and step showing; plus what axe cannot see in happy-dom
// (the page bar's names and current page, groups for radios, a live region for each message).
// The account pages, recaps, support and Tickets are in a11y-islands and a11y-recaps-support.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import Admin from "../src/admin/Admin";
import type { AdminApi, AdminLogEntry, GrantsView } from "../src/admin/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import { importPage as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ import: true }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.import;
    },
  };
});

afterEach(() => {
  cleanup();
  live.import = true;
});

async function serious(): Promise<string[]> {
  const result = await axe.run(document.body, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const logEntry = (n: number): AdminLogEntry => ({
  at: 1_790_000_000 - n * 86_400,
  by: "owner@example.com",
  action: n % 3 === 0 ? "revoke" : n % 2 === 0 ? "change" : "grant",
  discordId: `1000000000000000${String(n).padStart(2, "0")}`,
  name: n % 2 ? "Belleros" : null,
});

const GRANTS: GrantsView = {
  free: [
    { discordId: "100000000000000001", name: "Stormy", source: "list" } as never,
  ],
  grants: [
    {
      discordId: "100000000000000002",
      name: "Belleros",
      level: "guild",
      endsAt: 1_795_000_000,
      note: "Test table",
      grantedBy: "owner@example.com",
      grantedAt: 1_790_000_000,
    },
    {
      discordId: "100000000000000003",
      name: null,
      level: "unlimited",
      endsAt: null,
      note: "",
      grantedBy: "owner@example.com",
      grantedAt: 1_790_000_000,
      alsoFree: true,
    },
  ],
  log: Array.from({ length: 5 }, (_, i) => logEntry(i + 1)),
  logPages: 3,
};

function adminApi(over: Partial<AdminApi> = {}): AdminApi {
  return {
    me: async () => ({ email: "owner@example.com", csrf: "c1" }),
    ways: async () => ({ google: true, password: true }),
    googleUrl: () => "https://api.example/admin/auth/google/start",
    signIn: async () => undefined,
    signOut: async () => undefined,
    grants: async () => GRANTS,
    grantsLog: async (page: number) => ({ log: [logEntry(page)], page, pages: 3 }),
    give: async () => ({ action: "grant" as const, name: null }),
    revoke: async () => undefined,
    tickets: async () => ({
      tickets: [],
      counts: { new: 0, answered: 0, closed: 0, escalated: 0 },
      page: 1,
      pages: 1,
    }),
    setTicketStatus: async () => undefined,
    replyToTicket: async () => undefined,
    ...over,
  } as AdminApi;
}

describe("the admin page", () => {
  it("signed out, has nothing serious or critical", async () => {
    render(<Admin api={adminApi({ me: async () => null })} search="" />);
    await screen.findByLabelText("Email");
    expect(await serious()).toEqual([]);
  });

  it("signed in, with free access, grants, a page bar and the form, has nothing serious or critical", async () => {
    render(<Admin api={adminApi()} search="" />);
    await screen.findByRole("heading", { name: /free access/i });
    expect(await serious()).toEqual([]);
  });
});

describe("the Import page", () => {
  it("at the file step has nothing serious or critical", async () => {
    render(<Import api={mockApi("table")} path="/account/import/100.cmp1" />);
    await screen.findByLabelText(w.pickLabel, { selector: "input" });
    expect(await serious()).toEqual([]);
  });

  it("through each step to the progress view has nothing serious or critical", async () => {
    const api = mockApi("table");
    render(<Import api={api} path="/account/import/100.cmp1" />);
    const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
    const file = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    fireEvent.change(input);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("button", { name: w.checkButton });
    expect(await serious()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    expect(await serious()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await screen.findByText(w.progressHeading);
    expect(await serious()).toEqual([]);
  });
});

/** The accessible name of an element: its aria-label, else its text without symbols that are hidden. */
function nameOf(el: Element): string {
  const label = el.getAttribute("aria-label");
  if (label) return label.trim();
  const clone = el.cloneNode(true) as Element;
  clone.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
  return (clone.textContent ?? "").trim();
}

/** Every button names itself in words: a symbol on its own (⏳, 🔊, ✕, ×) is not a name. */
function symbolOnlyButtons(): string[] {
  return [...document.querySelectorAll("button, [role=button]")]
    .filter((b) => !/[\p{L}\p{N}]/u.test(nameOf(b)))
    .map((b) => b.outerHTML.slice(0, 80));
}

/** Radios and checkboxes that are not inside a fieldset with a legend. */
function ungrouped(): string[] {
  return [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')]
    .filter((i) => {
      const set = i.closest("fieldset");
      return !set || !set.querySelector(":scope > legend");
    })
    .map((i) => nameOf(i.closest("label") ?? i) || i.outerHTML.slice(0, 60));
}

describe("what axe cannot see in happy-dom (#2249)", () => {
  it("admin: every button has a name in words, and every radio sits in a group with a legend", async () => {
    render(<Admin api={adminApi()} search="" />);
    await screen.findByRole("heading", { name: /free access/i });
    expect(symbolOnlyButtons()).toEqual([]);
    expect(ungrouped()).toEqual([]);
    expect(document.querySelectorAll('input[type="radio"]').length).toBeGreaterThan(0);
  });

  it("admin: the page bar's buttons say which page they go to, and the box marks the current page", async () => {
    render(<Admin api={adminApi()} search="" />);
    const bar = await screen.findByRole("navigation", { name: /pages of recent changes/i });
    const names = within(bar)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent);
    expect(names).toEqual(
      expect.arrayContaining(["Newest, page 1", "Newer, page 1", "Older, page 2", "Oldest, page 3"]),
    );
    expect(within(bar).getByLabelText("Page").getAttribute("aria-current")).toBe("page");
  });

  it("the import page: buttons have names in words, and the ticks sit in a group with a legend", async () => {
    const api = mockApi("table");
    render(<Import api={api} path="/account/import/100.cmp1" />);
    const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
    const file = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    fireEvent.change(input);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("button", { name: w.checkButton });
    expect(symbolOnlyButtons()).toEqual([]);
    expect(ungrouped()).toEqual([]);
    await waitFor(() => expect(document.querySelectorAll('input[type="checkbox"]').length).toBeGreaterThan(0));
  });

  it("proves the checks can fail: a symbol-only button and a loose radio are both caught", () => {
    render(
      <div>
        <button type="button">✕</button>
        <label>
          <input type="radio" name="x" /> Loose
        </label>
      </div>,
    );
    expect(symbolOnlyButtons()).toHaveLength(1);
    expect(ungrouped()).toEqual(["Loose"]);
  });
});
