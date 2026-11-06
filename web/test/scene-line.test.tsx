/** @jsxImportSource preact */
// @vitest-environment happy-dom
// DMbot's summary of the scene on the website (#3644, behind SCENES_LIVE): shown above the answers on a person the story
// never named and on a name a recap asks to check; plain text; no line at all when there is none or the switch is off.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import SceneLine from "../src/account/SceneLine";
import { sceneWords as w } from "../src/content/scene-words";

const live = vi.hoisted(() => ({ on: true, notNamed: true }));
vi.mock("../src/content/scene-switch", () => ({
  get SCENES_LIVE() {
    return live.on;
  },
}));
vi.mock("../src/content/not-named-switch", () => ({
  get NOT_NAMED_LIVE() {
    return live.notNamed;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

describe("the line", () => {
  it("shows the summary as plain text under its label", () => {
    render(<SceneLine scene="The party reached the forge <i>late</i>." />);
    expect(screen.getByText(w.label)).toBeTruthy();
    expect(document.querySelector(".scene-line")?.textContent).toContain("<i>late</i>"); // (never read as HTML)
    expect(document.querySelector(".scene-line i")).toBeNull();
  });
  it("draws nothing without a scene, or while the switch is off", () => {
    const { container, rerender } = render(<SceneLine scene={null} />);
    expect(container.innerHTML).toBe("");
    rerender(<SceneLine scene="  " />);
    expect(container.innerHTML).toBe("");
    live.on = false;
    rerender(<SceneLine scene="A scene." />);
    expect(container.innerHTML).toBe("");
  });
});

describe("on the Names page", () => {
  it("shows the scene of an unnamed person to the DM, and none on one that has none", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.sc1.not-named" />);
    await screen.findByRole("list", { name: /./ });
    const open = async (who: string) => fireEvent.click(await screen.findByRole("button", { name: who }));
    await open("Ken's sister");
    expect(await screen.findByText(/at the harbour/)).toBeTruthy();
    expect(screen.getAllByText(w.label)).toHaveLength(1);
    await open("the ferryman's cousin");
    expect(screen.queryAllByText(w.label)).toHaveLength(0); // (the other person has no scene: no line, no empty box)
  });
});
