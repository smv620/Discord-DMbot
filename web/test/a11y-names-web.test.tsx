/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Accessibility of the Names page (#2295) and the relationship web (#2324): axe-core on each in
// the states a person can reach, as a11y-islands.test.tsx does for the account page (#2249).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import Web from "../src/account/Web";
import { namesPage as nw } from "../src/content/names-words";

afterEach(cleanup);

async function serious(): Promise<string[]> {
  const result = await axe.run(document.body, {
    // No layout in happy-dom, so contrast is measured from the stylesheet (a11y-contrast.test.ts).
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

describe("Names page", () => {
  it.each([
    ["a DM", "/account/names/100.cmp1"],
    ["a server manager who is not a DM", "/account/names/100.cmp1.names-manager"],
    ["someone who may not see it", "/account/names/100.cmp1.names-none"],
  ])("%s: nothing serious or critical", async (_who, path) => {
    render(<Names api={mockApi("table")} path={path} />);
    await waitFor(() => expect(screen.queryByText(nw.loading)).toBeNull());
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(40);
    expect(await serious()).toEqual([]);
  });

  it("a name open to change, and the question before removing it", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    const list = await screen.findByRole("list", { name: nw.listLabel });
    const row = within(list).getAllByRole("listitem").find((li) => li.textContent?.includes("Gorrak")) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: nw.changeFor("Gorrak") }));
    expect(await serious()).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: /Remove this name/ }));
    await within(row).findByRole("button", { name: /Yes, remove it/ });
    expect(await serious()).toEqual([]);
  });
});

describe("relationship web", () => {
  it.each([
    ["a DM", "/account/web/100.cmp1"],
    ["a server manager who is not a DM", "/account/web/100.cmp1.web-manager"],
    ["someone who may not see it", "/account/web/100.cmp1.web-none"],
  ])("%s: nothing serious or critical", async (_who, path) => {
    render(<Web api={mockApi("table")} path={path} />);
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(40);
    expect(await serious()).toEqual([]);
  });

  it("the big-web question", async () => {
    render(<Web api={mockApi("table")} path="/account/web/100.cmp1.web-big" />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.input(screen.getByLabelText("Steps out from the middle"), { target: { value: "6" } });
    await screen.findByText(/This will draw \d+ names and may be slow/);
    expect(await serious()).toEqual([]);
  });
});
