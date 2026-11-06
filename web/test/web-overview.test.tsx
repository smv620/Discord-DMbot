/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph's overview (#2632): the owner's campaign had 51 names and the page drew 2. The page now says how
// many names there are, lists the ones with no connection yet beside the map, and offers every other group
// of linked names; a player sees none of what is hidden from them, and an older API leaves the page as it was.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const at = (suffix: string): string => `/account/web/100.cmp1${suffix}`;
async function open(suffix = ".web-lonely", api = mockApi("table")) {
  render(<Web api={api} path={at(suffix)} />);
  await screen.findByText(/names?, \d+ lines?/);
  return api;
}

describe("the count line", () => {
  it("says every name and every connection, not only the ones drawn", async () => {
    const api = await open();
    const overview = await api.webOverview!("100.cmp1.web-lonely", {});
    expect(await screen.findByText(w.overviewLine(overview.counts.names, overview.counts.connections))).toBeTruthy();
    expect(overview.counts.names).toBeGreaterThan(overview.counts.connectedNames); // (some have no connection)
  });
});

describe("the Not connected yet tray", () => {
  it("lists the names with no connection, by kind, and says so when one is picked", async () => {
    await open();
    const tray = await screen.findByRole("region", { name: new RegExp(w.trayHeading) });
    expect(within(tray).getByText(w.trayLead)).toBeTruthy();
    expect(within(tray).getByRole("button", { name: /^Old Mill/ })).toBeTruthy();
    expect(within(tray).getByRole("button", { name: /^Brother Venn/ })).toBeTruthy();
    expect(within(tray).getByText(w.trayKind(w.kinds["place"]!, 2))).toBeTruthy();
    fireEvent.click(within(tray).getByRole("button", { name: /^Old Mill/ }));
    expect(await screen.findByText(w.noConnectionsYet("Old Mill"))).toBeTruthy();
    expect(within(tray).getByRole("button", { name: /^Old Mill/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(tray).getByRole("button", { name: /^Old Mill/ })); // again: put it away
    await waitFor(() => expect(screen.queryByText(w.noConnectionsYet("Old Mill"))).toBeNull());
  });

  it("never lists a name that has a connection", async () => {
    const api = await open();
    const overview = await api.webOverview!("100.cmp1.web-lonely", {});
    const connected = new Set(overview.groups.map((g) => g.center));
    const tray = screen.getByRole("region", { name: new RegExp(w.trayHeading) });
    for (const id of connected) {
      const name = overview.groups.find((g) => g.center === id)!.centerName;
      expect(within(tray).queryByRole("button", { name })).toBeNull();
    }
  });
});

describe("other groups of linked names", () => {
  it("offers each group and puts the picked one in the middle", async () => {
    const api = await open();
    const heading = await screen.findByText(w.groupsHeading);
    expect(heading).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: w.groupButton("Brask", 2) }));
    await waitFor(() => expect(api.calls.some((c) => /webGraph:.*center=pair-brask/.test(c) || /webGraph:.*center=pair-ford/.test(c))).toBe(true));
    await screen.findByText(w.groupHere, { exact: false });
  });

  it("offers no switch when there is only one group", async () => {
    await open(".web-1"); // an ordinary pretend campaign: one ring of names
    expect(screen.queryByText(w.groupsHeading)).toBeNull();
    expect(screen.queryByText(w.trayHeading, { exact: false })).toBeNull();
  });
});

describe("who sees what", () => {
  it("asks for the Players View again when a DM switches to it", async () => {
    const api = await open();
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("webOverview")).pop()).toContain("view=players"));
  });

  it("is the map alone when the API has no overview, or it fails", async () => {
    const old = mockApi("table");
    delete (old as { webOverview?: unknown }).webOverview;
    await open(".web-lonely", old);
    expect(screen.queryByText(w.trayHeading, { exact: false })).toBeNull();
    cleanup();
    const failing = mockApi("table");
    failing.webOverview = async () => {
      throw new ApiError("server");
    };
    await open(".web-lonely", failing);
    expect(screen.queryByText(w.trayHeading, { exact: false })).toBeNull();
  });
});

describe("the Open its page link on the overview", () => {
  it("a tray name and a group's centre get the link when marked", async () => {
    await open();
    const tray = await screen.findByRole("region", { name: new RegExp(w.trayHeading) });
    expect(within(tray).getByRole("link", { name: `${w.openItsPage}${w.openItsPageFor("Brother Venn")}` })).toBeTruthy();
    const groups = document.querySelector(".web-groups");
    expect(groups).toBeTruthy();
    expect(within(groups as HTMLElement).getAllByRole("link", { name: (label: string) => label.startsWith(w.openItsPage) }).length).toBeGreaterThan(0);
  });
});
