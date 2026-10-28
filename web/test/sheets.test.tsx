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
    } finally {
      vi.useRealTimers();
    }
  });
});
