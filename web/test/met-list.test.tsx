/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "👣 Met but not named" on the Names page (#4084): the list in the words of Discord's, and the three answers (a name, "Same as…",
// Remove). A name the campaign already has asks whether it is the same one; a name still waiting in the name check says so.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import MetList, { dateOf } from "../src/account/MetList";
import { metWords as w } from "../src/content/met-list-words";

const zone = process.env.TZ;
afterEach(() => {
  cleanup();
  if (zone === undefined) delete process.env.TZ;
  else process.env.TZ = zone;
});
const REF = "100.cmp1";

async function open() {
  const api = mockApi("table");
  render(<MetList api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: new RegExp(w.heading) });
  return api;
}
const item = (what: string) => screen.getByText(what).closest("li") as HTMLElement;
const sent = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("answerMet:")).map((c) => c.split(":").slice(2).join(":"));

describe("the date", () => {
  it("is the session's day where the reader is, not in UTC", () => {
    // 14 Nov 2023, 22:13 UTC
    process.env.TZ = "UTC";
    expect(dateOf(1_700_000_000)).toBe("14 Nov 2023");
    process.env.TZ = "Pacific/Auckland"; // (already the 15th there)
    expect(dateOf(1_700_000_000)).toBe("15 Nov 2023");
    expect(dateOf(null)).toBe("");
  });
});

describe("the list", () => {
  it("shows each one as Discord does, with the session's date", async () => {
    process.env.TZ = "UTC";
    await open();
    expect(screen.getByRole("heading").textContent).toContain("(3)");
    expect(screen.getByText(w.label("barbarians", 2))).toBeTruthy();
    expect(screen.getByText(w.label("rats", 0))).toBeTruthy();
    expect(screen.getByText("a wolf")).toBeTruthy();
    expect(within(item(w.label("barbarians", 2))).getByText(/met in the session of 14 Nov 2023/)).toBeTruthy();
    expect(screen.getByText(w.lead)).toBeTruthy();
  });

  it("says so when nobody is waiting", async () => {
    const api = await open();
    for (const what of ["barbarians", "rats", "a wolf"]) {
      const li = item(what === "barbarians" ? w.label(what, 2) : what === "rats" ? w.label(what, 0) : what);
      fireEvent.click(within(li).getByRole("button", { name: w.remove }));
      await waitFor(() => expect(screen.queryByText(li.textContent!.split("·")[0]!.trim())).toBeNull());
    }
    expect(await screen.findByText(w.empty)).toBeTruthy();
    expect(sent(api)).toHaveLength(3);
  });
});

describe("the answers", () => {
  it("Remove takes it off the list and says so", async () => {
    const api = await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.remove }));
    expect(await screen.findByText(w.removed("a wolf"))).toBeTruthy();
    expect(screen.queryByText("a wolf")).toBeNull();
    expect(sent(api)).toEqual([`${"c".repeat(32)}:{"action":"remove"}`]);
  });

  it("Give them a name sends the name", async () => {
    const api = await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.give }));
    fireEvent.input(screen.getByLabelText(w.nameLabel("a wolf")), { target: { value: "Greyfang" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.named("a wolf", "Greyfang"))).toBeTruthy();
    expect(sent(api)).toEqual([`${"c".repeat(32)}:{"action":"name","name":"Greyfang"}`]);
  });

  it("a name the campaign already has asks whether it is the same one, and Yes joins them", async () => {
    const api = await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.give }));
    fireEvent.input(screen.getByLabelText(w.nameLabel("a wolf")), { target: { value: "Mira Voss" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.exists("Mira Voss", "a wolf"))).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: w.yesSame }) as HTMLButtonElement).disabled).toBe(false)); // (the list is read again first)
    fireEvent.click(screen.getByRole("button", { name: w.yesSame }));
    expect(await screen.findByText(w.joined("a wolf", "Mira Voss"))).toBeTruthy();
    const calls = sent(api);
    expect(calls).toHaveLength(2);
    expect(calls[1]).toMatch(/"action":"join","target":"[^"]+"/);
  });

  it("No, different asks for another name", async () => {
    await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.give }));
    fireEvent.input(screen.getByLabelText(w.nameLabel("a wolf")), { target: { value: "Mira Voss" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByRole("button", { name: w.noDifferent });
    await waitFor(() => expect((screen.getByRole("button", { name: w.noDifferent }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.noDifferent }));
    await waitFor(() => expect(screen.queryByText(w.exists("Mira Voss", "a wolf"))).toBeNull());
    expect(screen.getByText(w.different)).toBeTruthy(); // (as guidance, not as a success)
    expect(screen.getByText(w.different).getAttribute("role")).toBeNull();
    expect(screen.getByLabelText(w.nameLabel("a wolf"))).toBeTruthy(); // (the name question is back, for them to try another)
  });

  it("a name waiting in the name check says to check it there first", async () => {
    await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.give }));
    fireEvent.input(screen.getByLabelText(w.nameLabel("a wolf")), { target: { value: "Waiting Wolf" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.waitingName)).toBeTruthy();
    expect(screen.getByText("a wolf")).toBeTruthy(); // (still on the list)
  });

  it("Same as… searches the names and joins the one pressed", async () => {
    const api = await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.same }));
    fireEvent.input(screen.getByLabelText(w.sameLabel), { target: { value: "Mira" } });
    fireEvent.click(await screen.findByRole("button", { name: /Mira/ }));
    expect(await screen.findByText(w.joined("a wolf", "Mira Voss"))).toBeTruthy();
    expect(sent(api)[0]).toMatch(/"action":"join"/);
  });

  it("an answer for one that is already gone says the list is up to date", async () => {
    const api = await open();
    api.metList = async () => [];
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.remove }));
    await screen.findByText(w.removed("a wolf"));
  });

  it("a name the campaign has that cannot be found by its exact name sends the DM to Same as…", async () => {
    await open();
    fireEvent.click(within(item("a wolf")).getByRole("button", { name: w.give }));
    fireEvent.input(screen.getByLabelText(w.nameLabel("a wolf")), { target: { value: "Mira" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.useSame)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.yesSame })).toBeNull();
    await waitFor(() => expect((screen.getAllByRole("button", { name: w.same }).at(-1) as HTMLButtonElement).disabled).toBe(false));
  });
});
