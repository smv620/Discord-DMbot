/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Recent changes as a fixed window with page navigation (#2199): 20 entries a page, newest first,
// a bar underneath that is hidden for one page, disabled (not hidden) at the ends, a jump box that
// takes only real page numbers, and a return to page 1 after any change.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Admin from "../src/admin/Admin";
import {
  type AdminApi,
  AdminApiError,
  type AdminLogEntry,
  type AdminMe,
  type GrantsView,
  type LogPage,
} from "../src/admin/api";
import { text } from "../src/content/admin";

afterEach(cleanup);

const ME: AdminMe = { email: "owner@example.com", csrf: "csrf-1" };
const SIZE = 20;
const START = 1_800_000_000;

/** `n` log lines, newest first; line i (from 0 = newest) is by `admin<i>@example.com`. */
const makeLog = (n: number): AdminLogEntry[] =>
  Array.from({ length: n }, (_, i) => ({
    at: START + (n - i) * 86400,
    by: `admin${i}@example.com`,
    action: "grant" as const,
    discordId: `1000000000000000${String(i).padStart(2, "0")}`,
    name: null,
  }));

function setup(total: number, over: Partial<AdminApi> = {}) {
  const log = makeLog(total);
  const pages = Math.max(1, Math.ceil(total / SIZE));
  const grants = vi.fn(
    async (): Promise<GrantsView> => ({
      free: [],
      grants: [],
      log: log.slice(0, SIZE),
      logPages: pages,
    }),
  );
  const grantsLog = vi.fn(async (page: number): Promise<LogPage> => {
    if (!Number.isInteger(page) || page < 1 || page > pages) throw new AdminApiError("stale");
    return { log: log.slice((page - 1) * SIZE, page * SIZE), page, pages };
  });
  const api: AdminApi = {
    me: vi.fn(async () => ME),
    ways: vi.fn(async () => ({ google: true, password: true })),
    googleUrl: () => "https://api.example/admin/auth/google/start",
    signIn: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    grants,
    grantsLog,
    give: vi.fn(async () => ({ action: "grant" as const, name: null })),
    revoke: vi.fn(async () => undefined),
    tickets: vi.fn(async () => ({
      tickets: [],
      counts: { new: 0, answered: 0, closed: 0, escalated: 0 },
    })),
    setTicketStatus: vi.fn(async () => undefined),
    replyToTicket: vi.fn(async () => undefined),
    ...over,
  };
  render(<Admin api={api} search="" />);
  return { api, grants, grantsLog, log, pages };
}

const nav = () => screen.findByRole("navigation", { name: text.pagesNav });
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
const shown = () =>
  within(screen.getByRole("region", { name: text.historyList }))
    .getAllByRole("listitem")
    .map((li) => li.textContent ?? "");
const status = () => screen.getByText(/^Page \d+ of \d+$|^Pick a page|^We couldn't load|^That page isn't/);

describe("the window", () => {
  it("shows the newest twenty first, with the page number under it", async () => {
    setup(45);
    await nav();
    const lines = shown();
    expect(lines).toHaveLength(20);
    expect(lines[0]).toContain("admin0@example.com");
    expect(lines[19]).toContain("admin19@example.com");
    expect(status().textContent).toBe(text.pageNow(1, 3));
  });

  it("disables what can't be used at either end, and never hides it", async () => {
    setup(45);
    await nav();
    expect(button(text.pageNewest).disabled).toBe(true);
    expect(button(text.pageNewer).disabled).toBe(true);
    expect(button(text.pageOlder).disabled).toBe(false);
    expect(button(text.pageOldest).disabled).toBe(false);
    fireEvent.click(button(text.pageOldest));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(3, 3)));
    expect(button(text.pageOlder).disabled).toBe(true);
    expect(button(text.pageOldest).disabled).toBe(true);
    expect(button(text.pageNewer).disabled).toBe(false);
    expect(button(text.pageNewest).disabled).toBe(false);
  });

  it("moves with Older, Newer, Oldest and Newest, newest first on every page", async () => {
    const { grantsLog } = setup(45);
    await nav();
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
    expect(shown()[0]).toContain("admin20@example.com");
    fireEvent.click(button(text.pageOldest));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(3, 3)));
    expect(shown()).toHaveLength(5); // the remainder
    expect(shown()[4]).toContain("admin44@example.com");
    fireEvent.click(button(text.pageNewer));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
    fireEvent.click(button(text.pageNewest));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(1, 3)));
    expect(grantsLog.mock.calls.map((c) => c[0])).toEqual([2, 3, 2, 1]);
  });

  it("jumps to a typed page on Enter or Go, and says the page is 'of' the total", async () => {
    setup(45);
    await nav();
    expect(screen.getByText(text.pageOf(3))).toBeTruthy();
    const box = screen.getByLabelText(text.pageBox) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "3" } });
    fireEvent.submit(box.closest("form")!);
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(3, 3)));
    fireEvent.input(box, { target: { value: "2" } });
    fireEvent.click(button(text.pageGo));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
    expect(box.value).toBe("2");
  });

  it.each(["0", "4", "-1", "abc", "2.5", "1e1", "", "  ", "99999999999"])(
    "refuses %j in the jump box with 'Pick a page from 1 to 3.' and asks nothing",
    async (typed) => {
      const { grantsLog } = setup(45);
      await nav();
      const box = screen.getByLabelText(text.pageBox);
      fireEvent.input(box, { target: { value: typed } });
      fireEvent.click(button(text.pageGo));
      expect(status().textContent).toBe(text.pagePick(3));
      expect(grantsLog).not.toHaveBeenCalled();
      // Typing again clears it; the page that was showing is still showing.
      fireEvent.input(box, { target: { value: "1" } });
      expect(status().textContent).toBe(text.pageNow(1, 3));
      expect(shown()[0]).toContain("admin0@example.com");
    },
  );
});

describe("when there is nothing to page", () => {
  it("hides the bar with one page, and still lists the entries", async () => {
    setup(7);
    await screen.findByRole("region", { name: text.historyList });
    expect(shown()).toHaveLength(7);
    expect(screen.queryByRole("navigation", { name: text.pagesNav })).toBeNull();
    expect(screen.queryByLabelText(text.pageBox)).toBeNull();
  });

  it("shows 'No changes yet.' with an empty log, and no bar", async () => {
    setup(0);
    expect(await screen.findByText(text.noHistory)).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: text.pagesNav })).toBeNull();
  });

  it("shows exactly twenty with no bar on an API without pages", async () => {
    const log = makeLog(20);
    // (An API older than #2199 has no grantsLog at all.)
    setup(20, {
      grants: vi.fn(async () => ({ free: [], grants: [], log })),
      grantsLog: undefined,
    } as unknown as Partial<AdminApi>);
    await screen.findByRole("region", { name: text.historyList });
    expect(shown()).toHaveLength(20);
    expect(screen.queryByRole("navigation", { name: text.pagesNav })).toBeNull();
  });
});

describe("after a change, and when a page can't be read", () => {
  it("goes back to page 1 after a change, so the new entry is in view", async () => {
    const { grants } = setup(45);
    await nav();
    fireEvent.click(button(text.pageOldest));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(3, 3)));
    // Revoke is not on this page (no grants), so make a change through the form.
    fireEvent.input(screen.getByLabelText(text.idLabel), { target: { value: "123456789012345678" } });
    fireEvent.click(screen.getByRole("button", { name: text.add }));
    await waitFor(() => expect(grants).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(1, 3)));
    expect(shown()[0]).toContain("admin0@example.com");
    expect((screen.getByLabelText(text.pageBox) as HTMLInputElement).value).toBe("1");
  });

  it("says so, and stays on the page it was on, when a page can't be read", async () => {
    let fail = false;
    const grantsLog = vi.fn(async (page: number): Promise<LogPage> => {
      if (fail) throw new AdminApiError("down");
      return { log: makeLog(45).slice((page - 1) * SIZE, page * SIZE), page, pages: 3 };
    });
    setup(45, { grantsLog });
    await nav();
    fail = true;
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(status().textContent).toBe(text.pageDown));
    expect(shown()[0]).toContain("admin0@example.com");
    expect((screen.getByLabelText(text.pageBox) as HTMLInputElement).value).toBe("1");
    expect(grantsLog).toHaveBeenCalledTimes(1);
    // And it works again afterwards.
    fail = false;
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
  });

  it("goes back to page 1 when the API says that page is gone", async () => {
    setup(45, {
      grantsLog: vi.fn(async (page: number): Promise<LogPage> => {
        if (page !== 1) throw new AdminApiError("stale");
        return { log: makeLog(45).slice(0, SIZE), page: 1, pages: 3 };
      }),
    });
    await nav();
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(1, 3)));
    expect(shown()[0]).toContain("admin0@example.com");
  });

  it("says the page is gone, and shows the newest, when the API no longer has the page asked for", async () => {
    const grantsLog = vi.fn(async (page: number): Promise<LogPage> => {
      if (page !== 1) throw new AdminApiError("stale");
      return { log: makeLog(45).slice(0, SIZE), page: 1, pages: 3 };
    });
    setup(45, { grantsLog });
    await nav();
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(grantsLog).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(text.pageGone));
  });

  it("opens each page at its top, not where the last was scrolled to", async () => {
    setup(45);
    await nav();
    const box = screen.getByRole("region", { name: text.historyList });
    box.scrollTop = 120;
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
    expect(box.scrollTop).toBe(0);
  });

  it("lets only the newest request set what is shown", async () => {
    let slow!: (page: LogPage) => void;
    const pagesOf = (page: number): LogPage => ({
      log: makeLog(45).slice((page - 1) * SIZE, page * SIZE),
      page,
      pages: 3,
    });
    const grantsLog = vi
      .fn<(page: number) => Promise<LogPage>>()
      .mockImplementationOnce(() => new Promise<LogPage>((done) => (slow = done)))
      .mockImplementation(async (page) => pagesOf(page));
    setup(45, { grantsLog });
    await nav();
    // Older starts (slowly) and is answered by a later Oldest; the late answer is ignored.
    fireEvent.click(button(text.pageOlder));
    await waitFor(() => expect(grantsLog).toHaveBeenCalledTimes(1));
    slow(pagesOf(2));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(2, 3)));
    fireEvent.click(button(text.pageOldest));
    await waitFor(() => expect(status().textContent).toBe(text.pageNow(3, 3)));
    expect(shown()).toHaveLength(5);
  });
});
