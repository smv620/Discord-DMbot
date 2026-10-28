/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Character sheets" on a campaign's page (#2816): a player sees and links or forgets their own characters' sheets; a DM
// sees every sheet read only and asks for them to be read again. Against the pretend API, and only while SHEETS_LIVE is
// on (false today), so nothing is asked of the API before then.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import CharacterSheetsSection from "../src/account/Sheets";
import { sheetWords as w } from "../src/content/sheets-words";

afterEach(cleanup);

const calls = (api: ReturnType<typeof mockApi>, start: string) =>
  api.calls.filter((c) => c.startsWith(start));
async function open(ending = "") {
  const api = mockApi("table");
  render(<CharacterSheetsSection api={api} campaignRef={`100.sh${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("the player's view", () => {
  it("shows their own character with its link, and an unlinked one with a form to link it", async () => {
    await open();
    expect(screen.getByText(w.mine("Sorrel"))).toBeTruthy();
    expect(screen.getByText(w.onlyYou, { exact: false })).toBeTruthy();
    expect(
      screen.getByRole("link", {
        name: "https://www.dndbeyond.com/characters/12345",
      }),
    ).toBeTruthy();
    expect(screen.getByText(w.summary(3, "Wizard 3"))).toBeTruthy();
    expect(screen.getAllByText("Ivy", { exact: false }).length).toBeGreaterThan(
      0,
    );
  });

  it("links a sheet: the link goes to the bot queue, and the saved words show once it is done", async () => {
    const api = await open();
    const box = screen.getAllByRole("textbox")[0] as HTMLInputElement;
    fireEvent.input(box, {
      target: { value: "https://www.dndbeyond.com/characters/777" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
    expect(
      await screen.findByText(w.linkSaved, {}, { timeout: 8000 }),
    ).toBeTruthy();
    expect(calls(api, "chooseSheet")[0]).toContain('"action":"link"');
  });

  it("says plainly that a link which isn't a character link was refused, and sends nothing", async () => {
    const api = await open();
    fireEvent.input(screen.getAllByRole("textbox")[0] as HTMLInputElement, {
      target: { value: "https://example.com/x" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
    expect(
      await screen.findByText(
        w.problems["bad-link"] as string,
        {},
        { timeout: 8000 },
      ),
    ).toBeTruthy();
    expect(screen.queryByText(w.linkSaved)).toBeNull(); // nothing was linked
    expect(calls(api, "chooseSheet")).toHaveLength(1); // the API refused it, once
  });

  it("forgets their sheet", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.forget }));
    expect(
      await screen.findByText(w.forgetDone, {}, { timeout: 8000 }),
    ).toBeTruthy();
  });
});

describe("the DM's view", () => {
  it("shows every sheet read only: no link form, and a read again button", async () => {
    await open(".sheets-dm");
    expect(screen.getByText(w.dmOnly)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.forget })).toBeNull();
    expect(screen.getByRole("button", { name: w.readAgain })).toBeTruthy();
  });

  it("asks for the sheets to be read again, once", async () => {
    const api = await open(".sheets-dm");
    fireEvent.click(screen.getByRole("button", { name: w.readAgain }));
    expect(
      await screen.findByText(w.readAgainDone, {}, { timeout: 8000 }),
    ).toBeTruthy();
    expect(calls(api, "chooseSheet")[0]).toContain('"action":"read_again"');
  });

  it("says when the minute has not passed yet", async () => {
    await open(".sheets-dm.sheets-slow");
    fireEvent.click(screen.getByRole("button", { name: w.readAgain }));
    expect(
      await screen.findByText(
        w.problems["too-soon"] as string,
        {},
        { timeout: 8000 },
      ),
    ).toBeTruthy();
  });
});

describe("nothing for someone who may not see the campaign", () => {
  it("shows the failure words, not the sheets, for a campaign that isn't theirs", async () => {
    const api = mockApi("table");
    render(
      <CharacterSheetsSection api={api} campaignRef="100.sh.sheets-none" />,
    );
    expect(await screen.findByText(w.failedToLoad)).toBeTruthy();
    expect(screen.queryByText("Sorrel")).toBeNull();
  });
});

describe("the wait", () => {
  it("shows what DMbot is doing while the answer is still waiting, and says so when it stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await open(".sheets-stuck");
      fireEvent.input(screen.getAllByRole("textbox")[0] as HTMLInputElement, {
        target: { value: "https://www.dndbeyond.com/characters/9" },
      });
      fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
      expect(
        await screen.findByText(
          w.stage.waiting as string,
          {},
          { timeout: 8000 },
        ),
      ).toBeTruthy();
      await waitFor(() =>
        expect(screen.queryByText(w.stage.waiting as string)).toBeTruthy(),
      );
      // Nothing moves on the bot's side for a minute: the panel must say so, not keep saying "running".
      await vi.advanceTimersByTimeAsync(61_000);
      expect(
        await screen.findByText(w.quiet, {}, { timeout: 8000 }),
      ).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Type in my character", () => {
  // Sorrel (first) has a sheet linked; Ivy (second) has none. The player's list is in that order.
  const fill = (index: number, values: { className?: string; level?: string; species?: string; names?: string }) => {
    if (values.className !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedClass)[index]!, { target: { value: values.className } });
    if (values.level !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedLevel)[index]!, { target: { value: values.level } });
    if (values.species !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedSpecies)[index]!, { target: { value: values.species } });
    if (values.names !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedNames)[index]!, { target: { value: values.names } });
  };
  // Submitted as a form: a click on the button inside the closed "Type in my character" panel does not submit here.
  const save = (index: number) => fireEvent.submit(screen.getAllByLabelText(w.typedClass)[index]!.closest("form")!);
  const typedCalls = (api: ReturnType<typeof mockApi>) => calls(api, "chooseSheet").filter((c) => c.includes('"action":"typed"'));

  it("offers the form to the player and not to the DM", async () => {
    await open();
    expect(screen.getAllByText(w.typedSummary).length).toBeGreaterThan(0);
    cleanup();
    await open(".sheets-dm");
    expect(screen.queryByText(w.typedSummary)).toBeNull();
  });

  it("sends what was typed, trimmed, with the level as a number", async () => {
    const api = await open();
    fill(1, { className: "  Wizard ", level: " 5 ", species: " Elf ", names: "Misty Step, Shield" });
    save(1);
    expect(await screen.findByText(w.typedSaved, {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls).toContain(
      `chooseSheet:100.sh:sh-ivy:${JSON.stringify({ action: "typed", className: "Wizard", level: 5, species: "Elf", names: "Misty Step, Shield" })}`,
    );
  });

  it("refuses a level that is not 1 to 20 and sends nothing", async () => {
    const api = await open();
    for (const level of ["0", "21", "x"]) {
      fill(1, { className: "Wizard", level });
      save(1);
      expect(await screen.findByText(w.typedBadLevel)).toBeTruthy();
    }
    expect(typedCalls(api)).toHaveLength(0);
  });

  it("refuses a blank class and sends nothing", async () => {
    const api = await open();
    fill(1, { className: "   ", level: "5" });
    save(1);
    expect(await screen.findByText(w.typedNeedsClass)).toBeTruthy();
    expect(typedCalls(api)).toHaveLength(0);
  });

  it("tells a player with a linked sheet that saving replaces the link", async () => {
    await open();
    expect(screen.getAllByText(w.typedReplacesLink)).toHaveLength(1);
    fill(0, { className: "Wizard", level: "5" });
    save(0);
    expect(await screen.findByText(w.typedSaved + w.typedReplacedLink, {}, { timeout: 8000 })).toBeTruthy();
  });
});
