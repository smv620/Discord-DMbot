/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The hand-over accept page (#2534; core #2532): the emailed link's token is read from the hash, the
// offer is looked at, and the person accepts onto a server or says no thanks. The API decides who may
// accept; the page says what it is told. Shown only while HANDOVER_LIVE is true (false today).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import Account from "../src/account/Account";
import Handover, { handoverToken } from "../src/account/Handover";
import { mockApi } from "../src/account/mock";
import { handoverOwnerWords as o, handoverWords as w } from "../src/content/handover-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/handover-words", async (original) => {
  const real = await original<typeof import("../src/content/handover-words")>();
  return {
    ...real,
    get HANDOVER_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
  sessionStorage.clear();
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

async function open(hash: string, api: AccountApi = mockApi("table")) {
  live.on = true;
  render(<Handover api={api} hash={hash} />);
  return api;
}

describe("the hand-over accept page", () => {
  it("is closed while it is switched off, and asks the API nothing", async () => {
    const api = mockApi("table");
    render(<Handover api={api} hash="#open" />);
    expect(screen.getByText(w.notOpen)).toBeTruthy();
    expect(calls(api, "handoverLook")).toHaveLength(0);
  });

  it("says to open the link from the email when there is no code", async () => {
    await open("");
    expect(await screen.findByText(w.noLink)).toBeTruthy();
  });

  it("shows the campaign, who offered it and where it runs, and sends the code in a body", async () => {
    const api = await open("#open");
    expect(await screen.findByText(w.offer("The Silver Hall", "Mira"))).toBeTruthy();
    expect(screen.getByText(w.runsOn("Friday Night Dice"))).toBeTruthy();
    expect(calls(api, "handoverLook")).toEqual(["handoverLook:open"]);
  });

  it("accepts onto the picked server, and links to the campaign", async () => {
    const api = await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.accepted)).toBeTruthy();
    expect(calls(api, "handoverAccept")).toEqual(["handoverAccept:open:s2"]);
    expect(screen.getByRole("link", { name: w.openCampaign }).getAttribute("href")).toContain("s1.cmp1");
  });

  it("starts on the server it runs on now, and a server without DMbot can't be picked", async () => {
    await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    expect((screen.getByRole("radio", { name: /Friday Night Dice/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: /New Server/ }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("link", { name: w.addBotFor("New Server") })).toBeTruthy();
  });

  it("says no thanks and changes nothing else", async () => {
    const api = await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.decline }));
    expect(await screen.findByText(w.declined)).toBeTruthy();
    expect(calls(api, "handoverDecline")).toEqual(["handoverDecline:open"]);
    expect(calls(api, "handoverAccept")).toHaveLength(0);
  });

  it.each([
    ["no-plan", "no_plan"],
    ["try-it", "try_it"],
    ["own", "owner"],
  ])("says why it can't be taken (%s) and offers no Accept", async (token, why) => {
    await open(`#${token}`);
    expect(await screen.findByText(w.why[why] as string)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.accept })).toBeNull();
  });

  it("lets someone who can't take it say no thanks, and says something for a reason it doesn't know", async () => {
    const api = mockApi("table");
    const real = api.handoverLook!;
    api.handoverLook = async (t) => {
      const look = await real(t);
      return look.status === "open" ? { ...look, canAccept: false, why: "something_new" as never } : look;
    };
    await open("#open", api);
    expect(await screen.findByText(w.cantNow)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.decline }));
    expect(await screen.findByText(w.declined)).toBeTruthy();
    expect(calls(api, "handoverDecline")).toEqual(["handoverDecline:open"]);
  });

  it("tries again after a failed look", async () => {
    const api = mockApi("table");
    const real = api.handoverLook!;
    let asked = 0;
    api.handoverLook = async (t) => {
      asked += 1;
      if (asked === 1) throw new ApiError("network");
      return real(t);
    };
    await open("#open", api);
    fireEvent.click(await screen.findByRole("button", { name: w.tryAgain }));
    expect(await screen.findByText(w.offer("The Silver Hall", "Mira"))).toBeTruthy();
  });

  it("says what taking the campaign means, in normal-size text, before the buttons", async () => {
    await open("#open");
    const note = await screen.findByText(w.acceptNote);
    expect(note.className).not.toContain("small");
  });

  it("links to the plans when a plan is what is missing, but not for the owner", async () => {
    await open("#no-plan");
    expect((await screen.findByRole("link", { name: w.seePlans })).getAttribute("href")).toBe("/pricing");
    cleanup();
    await open("#own");
    await screen.findByText(w.why.owner as string);
    expect(screen.queryByRole("link", { name: w.seePlans })).toBeNull();
  });

  it("says the offer has ended, in the words PLAN fixes, and keeps nothing", async () => {
    await open("#late");
    expect(await screen.findByText(w.ended)).toBeTruthy();
    expect(w.ended).toBe("This offer has ended. Ask the DM to hand the campaign over again.");
    expect(sessionStorage.length).toBe(0);
  });

  it("says ended when the offer ends between looking and accepting", async () => {
    await open("#gone-after");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.ended)).toBeTruthy();
  });

  it("says why a server was refused and stays on the page", async () => {
    await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    const api = mockApi("table");
    cleanup();
    api.handoverAccept = async () => {
      throw new ApiError("no-free-slot");
    };
    await open("#open", api);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.problem["no-free-slot"] as string)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.accept })).toBeTruthy();
  });

  it("asks to sign in when signed out, and keeps the code for after", async () => {
    await open("#open", mockApi("signed-out"));
    expect(await screen.findByRole("link", { name: w.signInDiscord })).toBeTruthy();
    expect(sessionStorage.getItem("dmbot:handover")).toBe("open");
    expect(handoverToken("")).toBe("open"); // the way back, with no hash
  });

  it("says it couldn't open the offer when the API is down", async () => {
    const api = mockApi("table");
    api.handoverLook = async () => {
      throw new ApiError("network");
    };
    await open("#open", api);
    expect(await screen.findByText(w.loadFailed)).toBeTruthy();
  });

  it("ignores a code that is not a code", () => {
    expect(handoverToken("#<script>")).toBeNull();
    expect(handoverToken("#ok-1_2")).toBe("ok-1_2");
  });

  it("has nothing serious or critical for axe", async () => {
    await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });

  it("is held with its What's new entry", async () => {
    const real = await vi.importActual<typeof import("../src/content/handover-words")>("../src/content/handover-words");
    const { held } = await import("../src/content/whats-new");
    expect(held.map((h) => h.title).includes("Receive a campaign by email")).toBe(!real.HANDOVER_LIVE);
  });
});

describe("the real client", () => {
  it("posts the code in the body, never in an address", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname}${new URL(url).search} ${init?.body}`);
      return new Response(JSON.stringify({ status: "ended", campaignRef: "x" }), { status: 200 });
    }) as typeof fetch);
    await api.handoverLook!("tok");
    await api.handoverAccept!("tok", "s1");
    await api.handoverDecline!("tok");
    expect(seen).toEqual([
      'POST /me/handover/look {"token":"tok"}',
      'POST /me/handover/accept {"token":"tok","serverId":"s1"}',
      'POST /me/handover/decline {"token":"tok"}',
    ]);
  });

  it.each([
    [409, "ended", "ended"],
    [402, "not_on_a_paid_plan", "no-paid-plan"],
    [402, "no_free_slot", "no-free-slot"],
    [403, "owner", "owner-offer"],
    [422, "bad_server", "bad-server"],
  ])("reads %s %s as %s, and each has words", async (status, code, kind) => {
    const api = httpApi("https://api.example", (async () => new Response(JSON.stringify({ detail: code }), { status })) as typeof fetch);
    await expect(api.handoverAccept!("tok", "s1")).rejects.toMatchObject({ kind });
    expect(w.problem[kind]).toBeTruthy();
  });
});

describe("the owner's side: hand over by email", () => {
  async function card(api: AccountApi = mockApi("table")) {
    live.on = true;
    render(<Account api={api} go={vi.fn()} search="" />);
    const link = await screen.findByRole("link", { name: "The Brynwater Crossing" });
    return { api, row: link.closest("li") as HTMLElement };
  }

  it("shows nothing while it is switched off", async () => {
    render(<Account api={mockApi("table")} go={vi.fn()} search="" />);
    await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(screen.queryByRole("button", { name: /Hand over by email/ })).toBeNull();
  });

  it("is offered on a campaign you own, and not on one you only help run", async () => {
    const { row } = await card();
    expect(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") })).toBeTruthy();
    const coDm = (await screen.findByRole("link", { name: "Shrine of Varrow" })).closest("li") as HTMLElement;
    expect(within(coDm).queryByRole("button", { name: /Hand over by email/ })).toBeNull();
  });

  it("sends the offer to the typed address, and says only that it was emailed", async () => {
    const { api, row } = await card();
    fireEvent.click(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") }));
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "new@example.com" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    await waitFor(() => expect(calls(api, "offerHandover")).toEqual(["offerHandover:100.cmp1:new@example.com"]));
    // the list is read again: an open offer now shows its end and Take back (never the address)
    expect(await screen.findByText(/We emailed the offer on/)).toBeTruthy();
    expect(screen.queryByText(/new@example\.com/)).toBeNull(); // the address is never shown back
    expect(screen.getByText(o.elsewhere)).toBeTruthy();
    expect(screen.getByRole("button", { name: o.takeBackFor("The Brynwater Crossing") })).toBeTruthy();
  });

  it("takes an open offer back", async () => {
    const { api, row } = await card();
    fireEvent.click(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") }));
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "new@example.com" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    fireEvent.click(await screen.findByRole("button", { name: o.takeBackFor("The Brynwater Crossing") }));
    await waitFor(() => expect(calls(api, "takeBackHandover")).toEqual(["takeBackHandover:100.cmp1"]));
    expect(await screen.findByText(o.takenBack)).toBeTruthy();
    expect(screen.getByRole("button", { name: o.buttonFor("The Brynwater Crossing") })).toBeTruthy();
  });

  it("says why an offer wasn't sent, and keeps the form", async () => {
    const { row } = await card();
    fireEvent.click(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") }));
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "bad" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    expect(await within(row).findByText(o.problem["bad-email"] as string)).toBeTruthy();
    expect(within(row).getByLabelText(o.label).getAttribute("aria-invalid")).toBe("true");
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "many@example.com" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    expect(await within(row).findByText(o.problem["too-soon"] as string)).toBeTruthy();
    expect(within(row).getByLabelText(o.label)).toBeTruthy();
  });

  it("the real client posts the address and deletes to take back", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname} ${init?.body ?? ""}`);
      return new Response(JSON.stringify({ status: "emailed" }), { status: 200 });
    }) as typeof fetch);
    await api.offerHandover!("100.cmp1", "a@example.com");
    await api.takeBackHandover!("100.cmp1");
    expect(seen).toEqual(['POST /me/campaigns/100.cmp1/handover {"email":"a@example.com"}', "DELETE /me/campaigns/100.cmp1/handover "]);
  });
});
