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

  it("accepts on the server it runs on, and links to the campaign", async () => {
    const api = await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.accepted)).toBeTruthy();
    expect(calls(api, "handoverAccept")).toEqual(["handoverAccept:open:s1"]);
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

  it("says why the plan has no free slot (no-free-slot) and stays on the page", async () => {
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

  it("says why DMbot can't run it on a server (bad-server) and stays on the page", async () => {
    const api = mockApi("table");
    api.handoverAccept = async () => {
      throw new ApiError("bad-server");
    };
    await open("#open", api);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.problem["bad-server"] as string)).toBeTruthy();
  });

  it("shows the sign-in view, not 'try again', when the session ends during accept", async () => {
    const api = mockApi("table");
    api.handoverAccept = async () => {
      throw new ApiError("signed-out");
    };
    await open("#open", api);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByRole("link", { name: w.signInDiscord })).toBeTruthy();
    expect(sessionStorage.getItem("dmbot:handover")).toBe("open"); // the code is kept for after
  });

  it("forgets the code for your own campaign, so My Account stops waiting for it", async () => {
    await open("#own");
    await screen.findByText(w.why.owner as string);
    expect(sessionStorage.getItem("dmbot:handover")).toBeNull();
  });

  it("says 'Nothing needs installing' in the server step when the person can accept", async () => {
    await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    const fieldset = screen.getByRole("group", { name: w.whereHeading }) as HTMLElement;
    expect(within(fieldset).getByText(/Nothing needs installing\./)).toBeTruthy();
    expect(within(fieldset).getByText(/your Discord account simply becomes its DM there/)).toBeTruthy();
  });

  it("links to My Account when the plan has no free slot", async () => {
    const api = mockApi("table");
    const real = api.handoverLook!;
    api.handoverLook = async (t) => {
      const look = await real(t);
      return look.status === "open" ? { ...look, canAccept: false, why: "no_slot" as const } : look;
    };
    await open("#open", api);
    expect((await screen.findByRole("link", { name: w.myAccount })).getAttribute("href")).toBe("/account");
    expect(screen.getByText(w.linkKept)).toBeTruthy();
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

  it("takes the code out of the address bar once it has been read", async () => {
    window.history.replaceState(null, "", "/handover#open");
    await open(window.location.hash);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    expect(window.location.hash).toBe("");
    expect(sessionStorage.getItem("dmbot:handover")).toBe("open"); // still kept for the way back
  });

  it("says the server step's DM line, and the sign-in note names the email link", () => {
    expect(w.whereLead).toContain("your Discord account simply becomes its DM there");
    expect(w.signInNote).toBe("After you sign in, open the link in the offer email again.");
  });

  it("has one Help step for it, and one GO_LIVE row", async () => {
    const { featureSteps } = await import("../src/content/help-features");
    expect(featureSteps.filter((f) => f.whatsNew === "Receive a campaign by email")).toHaveLength(1);
    const { readFileSync } = await import("node:fs");
    const golive = readFileSync(`${process.cwd()}/../docs/GO_LIVE.md`, "utf8");
    expect(golive.split("\n").filter((l) => l.startsWith("| `HANDOVER_LIVE`"))).toHaveLength(1);
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

  it("replaces the old Discord Hand over button (the open offer and its Withdraw stay)", async () => {
    live.on = true;
    render(<Account api={mockApi("offers")} go={vi.fn()} search="" />);
    const ashen = (await screen.findByRole("link", { name: "Ashen Crown" })).closest("li") as HTMLElement;
    expect(within(ashen).queryByRole("button", { name: "Hand over" })).toBeNull();
    expect(within(ashen).getByRole("button", { name: "Take back offer" })).toBeTruthy();
    expect(within(ashen).getByRole("button", { name: /Hand over by email/ })).toBeTruthy();
  });

  it("is not offered on a Try It account", async () => {
    live.on = true;
    render(<Account api={mockApi("try-it")} go={vi.fn()} search="" />);
    await screen.findByRole("heading", { name: /My Account/ });
    expect(screen.queryByRole("button", { name: /Hand over by email/ })).toBeNull();
  });

  it("checks the address lightly first, so a slip does not use one of the day's offers", async () => {
    const { api, row } = await card();
    fireEvent.click(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") }));
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "nobody" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    expect(await within(row).findByText(o.problem["bad-email"] as string)).toBeTruthy();
    expect(calls(api, "offerHandover")).toHaveLength(0);
  });

  it("sends to someone else, which takes back the open offer", async () => {
    const { api, row } = await card();
    fireEvent.click(within(row).getByRole("button", { name: o.buttonFor("The Brynwater Crossing") }));
    fireEvent.input(within(row).getByLabelText(o.label), { target: { value: "first@example.com" } });
    fireEvent.click(within(row).getByRole("button", { name: o.send }));
    fireEvent.click(await screen.findByRole("button", { name: o.sendElsewhere }));
    expect(screen.getByText(o.replaces)).toBeTruthy();
    fireEvent.input(screen.getByLabelText(o.label), { target: { value: "second@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: o.send }));
    await waitFor(() => expect(calls(api, "offerHandover")).toHaveLength(2));
    expect(calls(api, "offerHandover")[1]).toContain("second@example.com");
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

describe("moving to another server (#2550)", () => {
  it("greys a server the campaign can't move to, with the page's own words, and the current one still works", async () => {
    const api = await open("#shares");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    expect((screen.getByRole("radio", { name: /Sunday Table/ }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(w.serverWhy.shares_rules!)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.accepted)).toBeTruthy();
    expect(calls(api, "handoverAccept")).toEqual(["handoverAccept:shares:s1"]);
  });

  it("shows the sentence about a move that did not happen, and the accept button is still there", async () => {
    await open("#failed");
    expect(await screen.findByText("A session is running. Try again after it ends.")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.accept })).toBeTruthy();
  });

  it("shows nothing about a failed move when there is none", async () => {
    await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    expect(screen.queryByText(/Try again after it ends/)).toBeNull();
  });

  it("says Moving… for another server, then links to the campaign when it appears", async () => {
    const api = await open("#open");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.moving)).toBeTruthy();
    expect(await screen.findByText(w.accepted, undefined, { timeout: 12_000 })).toBeTruthy();
    expect(screen.getByRole("link", { name: w.openCampaign }).getAttribute("href")).toContain("s2.cmp9");
    expect(calls(api, "handoverAccept")).toEqual(["handoverAccept:open:s2"]);
  }, 20_000);

  it("the real client reads 202 moving and 409 shares_rules", async () => {
    const moving = httpApi("https://api.example", (async () => new Response(JSON.stringify({ status: "moving" }), { status: 202 })) as typeof fetch);
    expect(await moving.handoverAccept!("tok", "s2")).toEqual({ moving: true });
    const shares = httpApi("https://api.example", (async () => new Response(JSON.stringify({ detail: "shares_rules" }), { status: 409 })) as typeof fetch);
    await expect(shares.handoverAccept!("tok", "s2")).rejects.toMatchObject({ kind: "shares-rules" });
    expect(w.problem["shares-rules"]).toBeTruthy();
  });

  it("a refusal for sharing a rule set says so, and the link still works for the current server", async () => {
    live.on = true;
    const api = mockApi("table");
    api.handoverAccept = async () => {
      throw new ApiError("shares-rules");
    };
    render(<Handover api={api} hash="#open" />);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.problem["shares-rules"]!)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.accept })).toBeTruthy();
  });

  it("goes back to the offer, with the reason, when the move did not happen", async () => {
    const api = await open("#movefail");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.moving)).toBeTruthy();
    expect(await screen.findByText("A session is running. Try again after it ends.", undefined, { timeout: 15_000 })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.accept })).toBeTruthy(); // usable again
    expect(screen.queryByText(w.moving)).toBeNull();
    expect(screen.queryByText(w.movingSlow)).toBeNull();
    expect(calls(api, "handoverLook").length).toBeGreaterThan(2); // it looked at the offer while it waited
  }, 25_000);

  it("never starts on a server the campaign can't move to, and offers no Accept when only those are left", async () => {
    await open("#greyonly");
    expect(await screen.findByText(w.onlyHere)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.accept })).toBeNull();
    expect(screen.getByRole("button", { name: w.decline })).toBeTruthy();
    expect(screen.queryByText(w.noServers)).toBeNull();
  });

  it("does not hide the current server's choice behind a greyed one", async () => {
    await open("#shares");
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    expect((screen.getByRole("radio", { name: /Friday Night Dice/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByRole("link", { name: /Add DMbot to Sunday Table/ })).toBeNull(); // (it has a reason, and DMbot is there)
  });

  it("a failed read of /me before accepting does not block the accept", async () => {
    live.on = true;
    const api = mockApi("table");
    const real = api.me.bind(api);
    let n = 0;
    api.me = async () => {
      n += 1;
      if (n === 1) return real(); // the page's own look
      throw new ApiError("network");
    };
    render(<Handover api={api} hash="#open" />);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.accept }));
    expect(await screen.findByText(w.accepted)).toBeTruthy();
  });

  it("stops looking when the page goes away", async () => {
    live.on = true;
    const api = mockApi("table");
    api.handoverAccept = async () => ({ moving: true });
    const view = render(<Handover api={api} hash="#open" />);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
      fireEvent.click(screen.getByRole("button", { name: w.accept }));
      await vi.advanceTimersByTimeAsync(4000);
      view.unmount();
      const before = calls(api, "handoverLook").length;
      await vi.advanceTimersByTimeAsync(60_000);
      expect(calls(api, "handoverLook").length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up looking after about three minutes and says the email link still works", async () => {
    live.on = true;
    const api = mockApi("table");
    const real = api.handoverLook!.bind(api);
    let looks = 0;
    api.handoverAccept = async () => ({ moving: true });
    api.handoverLook = async (token) => (looks++ === 0 ? real(token) : { status: "ended" });
    render(<Handover api={api} hash="#open" />);
    await screen.findByText(w.offer("The Silver Hall", "Mira"));
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("radio", { name: /Sunday Table/ }));
      fireEvent.click(screen.getByRole("button", { name: w.accept }));
      await vi.advanceTimersByTimeAsync(200_000);
      expect(screen.getByText(w.movingSlow)).toBeTruthy();
      expect(w.movingSlow).toContain("open the link in the offer email again");
    } finally {
      vi.useRealTimers();
    }
  });
});
