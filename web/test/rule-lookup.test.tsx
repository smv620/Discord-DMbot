/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Look up a rule" on a campaign's page (#5156): the card in its pages, the close names for a "none", the three errors,
// the wait while DMbot looks, one look at a time, and nothing kept when the campaign changes. Discord's markdown is shown
// as elements, never as HTML.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type RuleCard } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RuleLookup from "../src/account/RuleLookup";
import { discordPieces } from "../src/account/discord-text";
import { ruleLookupWords as w } from "../src/content/rule-lookup-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function panel(api: AccountApi, campaignRef = "100.cmp1") {
  return render(<RuleLookup api={api} campaignRef={campaignRef} />);
}

async function ask(text: string) {
  fireEvent.input(screen.getByLabelText(w.label, { exact: false }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: w.button }));
}

describe("Look up a rule", () => {
  it("holds its What's new entry back until the box is switched on", () => {
    const title = "Look up rules on the website";
    expect(held.some((h) => h.title === title)).toBe(true);
    expect(entries.some((e) => e.title === title)).toBe(false);
  });

  it("shows a card in its pages, bold and italic as formatted, and nothing run as HTML", async () => {
    const api = mockApi("table");
    const view = panel(api);
    await ask("Fireball");
    expect(await screen.findByText("Fireball", { selector: "strong" })).toBeTruthy();
    expect(view.container.querySelector("em")?.textContent).toBe("3rd-level evocation");
    api.ruleLookup = async () => ({ kind: "card", parts: ["<b>x</b> stays as written"], close: [] }) as RuleCard;
    await ask("anything");
    expect(await screen.findByText("<b>x</b> stays as written")).toBeTruthy();
    expect(view.container.querySelector("b")).toBeNull();
  });

  it("a none answer shows its close names, and pressing one looks that name up", async () => {
    const api = mockApi("table");
    panel(api);
    await ask("nobody");
    expect(await screen.findByText(w.didYouMean)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Firebolt" }));
    await waitFor(() => expect(api.calls).toContain("ruleLookup:100.cmp1:Firebolt"));
    expect(((screen.getByLabelText(w.label, { exact: false })) as HTMLInputElement).value).toBe("Firebolt");
  });

  it("bold-italic shows as bold", () => {
    expect(discordPieces("***Fireball***")).toEqual([{ text: "Fireball", kind: "bold" }]);
  });

  it("shows the three errors as plain words", async () => {
    const cases: [string, string][] = [
      ["not-found", w.notFound],
      ["bad-text", w.badText],
      ["too-soon", w.tooSoon],
    ];
    for (const [kind, words] of cases) {
      const api = mockApi("table");
      api.ruleLookup = async () => {
        throw new ApiError(kind as "not-found");
      };
      const view = panel(api);
      await ask("Fireball");
      expect(await screen.findByText(words)).toBeTruthy();
      view.unmount();
    }
  });

  it("says DMbot is looking while the request is held, and a second press is ignored", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    let asked = 0;
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    api.ruleLookup = async () => {
      asked += 1;
      await gate;
      return { kind: "card", parts: ["done"], close: [] } as RuleCard;
    };
    const view = panel(api);
    fireEvent.input(screen.getByLabelText(w.label, { exact: false }), { target: { value: "Fireball" } });
    const form = view.container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(screen.getByText(w.stage, { exact: false })).toBeTruthy();
    expect(asked).toBe(1);
    release();
  });

  it("clears the answer and the box when the campaign changes", async () => {
    const api = mockApi("table");
    const view = panel(api, "100.cmp1");
    await ask("Fireball");
    expect(await screen.findByText("Fireball", { selector: "strong" })).toBeTruthy();
    view.rerender(<RuleLookup api={api} campaignRef="100.cmp2" />);
    await waitFor(() => expect(screen.queryByText("Fireball", { selector: "strong" })).toBeNull());
    expect(((screen.getByLabelText(w.label, { exact: false })) as HTMLInputElement).value).toBe("");
  });
});

describe("the card's formatting", () => {
  it("cuts bold, italic, code and plain text in order", () => {
    expect(discordPieces("**a** and *b* and `c`")).toEqual([
      { text: "a", kind: "bold" },
      { text: " and ", kind: "plain" },
      { text: "b", kind: "italic" },
      { text: " and ", kind: "plain" },
      { text: "c", kind: "code" },
    ]);
  });

  it("leaves an unclosed ** as plain text", () => {
    expect(discordPieces("a ** b")).toEqual([{ text: "a ** b", kind: "plain" }]);
  });

  it("keeps an underscore inside a word literal, and italicises a whole-word _x_", () => {
    expect(discordPieces("snake_case_name")).toEqual([{ text: "snake_case_name", kind: "plain" }]);
    expect(discordPieces("_x_")).toEqual([{ text: "x", kind: "italic" }]);
  });
});
