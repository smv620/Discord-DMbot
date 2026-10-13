/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Accessibility of the pages' interactive parts (#1146): axe-core on the account page in every
// state it can show, and on the "Say hello" forms, including a form with an error.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { text as accountText } from "../src/content/account";
import { text as helloText } from "../src/content/hello";
import Account from "../src/account/Account";
import { mockApi, scenarios } from "../src/account/mock";
import HelloForm from "../src/hello/HelloForm";

afterEach(cleanup);

async function serious(): Promise<string[]> {
  const result = await axe.run(document.body, {
    // No layout in happy-dom, so contrast is measured from the stylesheet's colours instead
    // (a11y-contrast.test.ts).
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

describe("account page", () => {
  it("catches an unlabelled picture (proof the check can fail)", async () => {
    render(<img src="x.png" />);
    expect((await serious()).join(" ")).toMatch(/image-alt/);
  });

  it.each(scenarios)("%s has nothing serious or critical", async (s) => {
    render(<Account api={mockApi(s)} go={vi.fn()} />);
    // Wait for the state itself, not a fixed time: axe must see the loaded page.
    await waitFor(() => expect(screen.queryByText(accountText.loading)).toBeNull());
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(40);
    expect(await serious()).toEqual([]);
  });
});

describe("say hello forms", () => {
  it.each(["feedback", "question"] as const)("%s form, with an error showing", async (kind) => {
    render(<HelloForm kind={kind} send={vi.fn()} siteKey="" />);
    expect(await serious()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: helloText[kind].send }));
    const box = await screen.findByLabelText(helloText[kind].label);
    // The error is announced and tied to the field it is about.
    const alert = await screen.findByRole("alert");
    expect(box.getAttribute("aria-describedby")).toContain(alert.id);
    expect(await serious()).toEqual([]);
  });
});
