/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The table's view of a place (#5045): a culture and who lives here, for what the DM kept and showed, in the panel's words. A named
// culture reads by its name; with nothing kept or shown, the view says nothing is known.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, NameRow, PlaceWorldTable } from "../src/account/api";
import PlaceWorldLines from "../src/account/PlaceWorldLines";
import { placeWorldWords as w } from "../src/content/place-world-words";

afterEach(cleanup);

const row = { id: "n24", name: "Saltmarsh" } as unknown as NameRow;
const withTable = (data: PlaceWorldTable): AccountApi => ({ placeWorldTable: async () => data }) as unknown as AccountApi;
const kept = (over: Partial<PlaceWorldTable["rows"][number]>): PlaceWorldTable["rows"][number] => ({
  layer: "culture",
  value: "dwarf",
  words: "",
  groupName: null,
  how: "strong",
  part: "",
  culture: null,
  ...over,
});

async function open(api: AccountApi) {
  render(<PlaceWorldLines api={api} refId="100.cmp1" row={row} />);
  fireEvent.click(screen.getByRole("button", { name: w.table.show }));
}

describe("a place's culture in the table view", () => {
  it("shows the culture and who lives here in the panel's words", async () => {
    await open(
      withTable({
        rows: [
          kept({ value: "dwarf", how: "strong" }),
          kept({ layer: "culture", value: "", culture: "Illuskan", how: "some" }),
          kept({ layer: "population", value: "halfling", how: "most" }),
          kept({ layer: "population", value: "human", how: "" }),
        ],
      }),
    );
    expect(await screen.findByText("🏛️ Culture: dwarven (strongly), Illuskan (somewhat)")).toBeTruthy();
    expect(screen.getByText("🏘️ Who lives here: mostly halflings, humans")).toBeTruthy();
  });

  it("with no culture or people kept, it says nothing is known about the place", async () => {
    await open(withTable({ rows: [] }));
    expect(await screen.findByText(w.table.none)).toBeTruthy();
  });

  it("a culture alone is shown, with nothing for the layers not kept", async () => {
    await open(withTable({ rows: [kept({ value: "elf", how: "once" })] }));
    await waitFor(() => expect(screen.getByText("🏛️ Culture: elven (once)")).toBeTruthy());
    expect(screen.queryByText(/Who lives here/)).toBeNull();
  });
});
