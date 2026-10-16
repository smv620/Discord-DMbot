// The legal drafts (#433): each has the short version, the draft notice, and facts that
// match the plan file, so the pages and the pricing never disagree.
import { readFileSync } from "node:fs";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import facts from "../src/content/plans.json";
import { deletionWarnings, notAffiliated, operator, retentionRows } from "../src/content/legal";
import { byId, formatPeriod } from "../src/content/pricing";
import Privacy from "../src/pages/legal/privacy.astro";
import Refunds from "../src/pages/legal/refunds.astro";
import Terms from "../src/pages/legal/terms.astro";

const pages: [name: string, page: AstroComponentFactory][] = [
  ["terms", Terms],
  ["privacy", Privacy],
  ["refunds", Refunds],
];

const rendered = new Map<string, Document>();
const text = (node: Element | Document | null): string =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

beforeAll(async () => {
  const container = await AstroContainer.create();
  for (const [name, page] of pages) {
    const html = await container.renderToString(page, {
      request: new Request(`https://dmbot.example/legal/${name}`),
    });
    rendered.set(name, parsePage(html));
  }
});

const doc = (name: string): Document => {
  const found = rendered.get(name);
  if (!found) throw new Error(`not rendered: ${name}`);
  return found;
};

describe.each(pages.map(([name]) => [name]))("legal/%s", (name) => {
  it("starts with the short version and says it is a draft", () => {
    const main = doc(name).querySelector("main");
    const short = main?.querySelector(".short");
    expect(text(short?.querySelector("h2") ?? null)).toBe("The short version");
    const lines = [...(short?.querySelectorAll("p") ?? [])].map((p) => text(p));
    expect(lines.join(" ").length).toBeGreaterThan(80);
    for (const line of lines) expect(line.length).toBeGreaterThan(0);
    expect(text(main?.querySelector(".draft") ?? null)).toMatch(/^Draft for review/);
    expect(text(main?.querySelector(".effective") ?? null)).toContain(
      "Effective date: 9 October 2026",
    );
  });

  it("leaves no half-written placeholder", () => {
    const body = text(doc(name).querySelector("main"));
    // Remove well-formed placeholders; no stray braces may be left. Nothing leaked from data.
    expect(body.replace(/\{\{[^{}]+\}\}/g, "")).not.toMatch(/[{}]/);
    expect(body).not.toMatch(/\b(undefined|null|NaN)\b|\[object/);
  });
});

describe("retention and warnings", () => {
  it("lists every plan's retention as on the pricing page", () => {
    const rows = [...doc("privacy").querySelectorAll("tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => text(td)),
    );
    expect(rows).toEqual(retentionRows.map((r) => [r.plan, r.keep]));
    expect(rows).toHaveLength(facts.order.length);
    expect(rows).toEqual([
      ["Try It", "60 days"],
      ["Table", "6 months"],
      ["Two Tables", "1 year"],
      ["Guild", "1 year"],
      ["Pro", "1 year"],
    ]);
    expect(rows[0]).toEqual([byId["try-it"].name, formatPeriod(byId["try-it"].keepAfterLastSession)]);
  });

  it("lists every warning day from the plan file", () => {
    expect(deletionWarnings).toBe("14 days and 3 days");
    for (const day of facts.deletionWarningDaysBefore) expect(deletionWarnings).toContain(`${day} day`);
  });

  it("states the 120 days after a plan stops and the warning days", () => {
    expect(text(doc("privacy").querySelector("main"))).toContain(
      "kept for 120 days. We message the game master on Discord 14 days and 3 days before",
    );
    expect(text(doc("terms").querySelector("main"))).toContain("campaigns are kept for 120 days");
    // No $1.99 first month any more (2026-10-14): not on the rendered terms either.
    expect(text(doc("terms").querySelector("main"))).not.toMatch(/\$1\.99|first month/i);
    expect(text(doc("refunds").querySelector("main"))).toContain("kept for 120 days");
  });
});

describe("the facts #433 asks for", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));
  const terms = (): string => text(doc("terms").querySelector("main"));

  it("names what we keep and who helps us", () => {
    for (const fact of ["Discord account number", "email", "names and numbers of the Discord servers you are in", "Deepgram", "Anthropic"]) {
      expect(privacy()).toContain(fact);
    }
  });

  it("says voices are not kept and transcripts are visible to the whole server", () => {
    expect(privacy()).toContain("Your voice is not kept.");
    expect(privacy()).toContain("everyone in the Discord server");
  });

  it("says typed chat is read only from people who said yes and kept as typed (#1155)", () => {
    expect(privacy()).toContain("What was typed.");
    expect(privacy()).toContain("people who said yes type in the table's chat");
    expect(privacy()).toContain("dmb-chat");
    expect(privacy()).toContain("marked as typed");
  });

  it("says DMbot learns the names people are called, from people who said yes, and what stop removes (#1210)", () => {
    expect(privacy()).toContain("Names at the table.");
    expect(privacy()).toContain("only from people who said yes to being recorded");
    expect(privacy()).toContain("Names your game master typed in themselves stay.");
  });

  it("sets the minimum age at 16, with no 'older where local law requires' marker", () => {
    expect(terms()).toContain("at least 16");
    expect(privacy()).toContain("at least 16");
    expect(terms()).not.toContain("older age");
    expect(privacy()).not.toContain("older age");
  });

  // Owner's answers of 2026-10-10 (#922): who runs DMbot, how to reach them, and the rules.
  it("names DMbot LLC and email-only contact, with no postal address field", () => {
    for (const page of ["terms", "privacy", "refunds"]) {
      const body = text(doc(page).querySelector("main"));
      expect(body).not.toContain("{{OWNER");
      expect(body).not.toContain("{{CONTACT EMAIL}}");
    }
    expect(terms()).toContain("DMbot is run by DMbot LLC");
    expect(privacy()).toContain("DMbot is run by DMbot LLC");
    expect(terms()).toContain("dmbot.admin@gmail.com");
    // The owner chose email-only contact (2026-10-10): the address field must not come back.
    expect(Object.keys(operator)).toEqual(["name", "email"]);
    for (const page of ["terms", "privacy", "refunds"]) {
      expect(text(doc(page).querySelector("main"))).not.toMatch(/postal address|street|OWNER ADDRESS/i);
    }
  });

  it("names OVH US as the host and the US companies, with Stripe as seller of record", () => {
    expect(privacy()).toContain("OVH US LLC (OVHcloud, U.S.A.)");
    expect(privacy()).toContain("Its servers are in the United States");
    expect(privacy()).toMatch(/Deepgram, Anthropic, OVH US, Cloudflare, GitHub and Stripe/);
    expect(privacy()).toContain("Stripe is the seller of record and handles withdrawal rights, invoices and tax");
    expect(privacy()).not.toContain("HOSTING PROVIDER");
  });

  it("states the owner's choices on prices, closing, Try It, takedowns and service level", () => {
    expect(terms()).toContain("Prices may change at any time. The price shown when you buy is the price you pay for that period.");
    expect(terms()).toContain("at least 30 days' notice");
    expect(terms()).toContain("Try It never turns into a paid plan by itself, and it needs no payment details");
    expect(terms()).toContain("Write to dmbot.admin@gmail.com with a link to the material");
    expect(terms()).toContain("DMbot is provided as is, with no promise it will always be available.");
  });

  it("states the refund rules the owner chose", () => {
    const refunds = text(doc("refunds").querySelector("main"));
    expect(refunds).toContain("within 14 days of paying and we'll refund it. A monthly renewal is not a new plan.");
    expect(refunds).toContain("can't be refunded, used or not.");
    expect(refunds).toContain("within 5 working days");
    expect(refunds).toContain("Stripe is the seller");
    expect(refunds).not.toContain("{{CHECK");
  });

  it("says what the code does about sign-in, cookies and backups", () => {
    expect(privacy()).toContain("We use it once to read your account, then cancel it. We never keep it.");
    expect(privacy()).toContain("one cookie to keep you signed in, and two short ones");
    expect(privacy()).toContain("but not what was said");
    expect(privacy()).toContain("(they last 10 minutes)");
    expect(privacy()).toContain("We keep this only while you are signed in.");
    expect(privacy()).toContain("your Discord name");
    expect(privacy()).toContain("only if Discord has confirmed it is yours");
    expect(privacy()).toContain("This removes your sign-in details and your plan, straight away.");
  });

  it("says we are not connected to the publisher, once, on the terms page", () => {
    expect(terms()).toContain(notAffiliated);
    expect(privacy()).not.toContain("Wizards");
    expect(text(doc("refunds").querySelector("main"))).not.toContain("Wizards");
  });

  it("says shared material is the game master's responsibility and gets no legal review", () => {
    expect(terms()).toMatch(/confirm they have the right to use it\. We don't check this/);
  });

  // Owner's decisions of 2026-10-11 (#922): campaigns outlive the account, the rights wording,
  // and the "Our responsibility" text. No placeholder is left on any legal page.
  it("says campaigns stay when an account is deleted", () => {
    for (const body of [terms(), privacy()]) {
      expect(body).toContain("Your campaigns are not deleted with your account");
      expect(body).toContain("the other players may still want to read or download them");
      // PLAN: kept like a campaign whose plan stopped paying (120 days), unless a DM takes it over.
      expect(body).toContain("They are deleted 120 days later, unless another DM of the campaign takes them over.");
    }
  });

  it("points to the Download my data button for a copy (#1150)", () => {
    expect(privacy()).toContain("Press Download my data in My Account. You get one file with your account, plan and payments");
    const link = doc("privacy").querySelector('a[href="/account"]');
    expect(link?.textContent).toBe("My Account");
  });

  it("tells people how to ask about their information, with a 30-day answer", () => {
    expect(privacy()).toContain(
      "You can ask us to show you, correct, send you a copy of, or delete the information we hold about you, by emailing dmbot.admin@gmail.com. We answer within 30 days.",
    );
  });

  it("uses the owner's approved 'Our responsibility' text", () => {
    // The owner's words, with only the spelling fixed (Supervisor, 2026-10-11).
    expect(terms()).toContain(
      "DMbot LLC, its affiliates and any contributors to the DMbot project make no claims or promises towards the accuracy or stability of the product's performance. DMbot LLC does not warrant or guarantee the goods or services purchased. By using the DMbot agent and the associated tools, users agree to hold DMbot LLC and its affiliates harmless towards any losses, material, non-material or deemed.",
    );
  });

  it("says a campaign whose owner's Discord account is gone can be taken on, in terms and privacy (#1220)", () => {
    for (const name of ["terms", "privacy"]) {
      const body = text(doc(name).querySelector("main"));
      expect(body, name).toContain("If the owner's Discord account is gone, or the owner has");
      expect(body, name).toContain("out of the server for 7 days");
      expect(body, name).toContain("button to take the campaign on. Only a game master who presses it takes over.");
    }
  });

  it("keeps the 120 days in the deletion sentence in step with the plan file", () => {
    expect(facts.keepAfterPlanStopsPaying).toEqual({ count: 120, unit: "day" });
  });

  it("leaves no placeholder on any legal page", () => {
    for (const page of ["terms", "privacy", "refunds"]) {
      expect(text(doc(page).querySelector("main"))).not.toMatch(/\{\{/);
    }
  });

  it("describes the three DM-screen settings and that peeks are seen", () => {
    expect(privacy()).toMatch(/players who choose to peek \(the usual setting/);
    expect(privacy()).toContain("the game master can see who peeked");
  });

  it("tells people a failed payment has a short grace period, with no number of days", () => {
    expect(terms()).toContain("If a renewal payment fails, there is a short grace period to fix it before the plan stops");
    expect(terms()).toContain("If your first payment doesn't go through, the plan doesn't start until it does");
    expect(terms()).toContain("Tap Fix my payment in My Account");
    expect(terms()).not.toMatch(/\d+ days to fix it/);
  });

  it("names Stripe as the payment company on every legal page", () => {
    for (const page of ["terms", "privacy", "refunds"]) {
      expect(text(doc(page).querySelector("main"))).toContain("Stripe");
    }
    expect(terms()).not.toMatch(/PAYMENT PROVIDER|Paddle/);
  });

  it("uses one sign-in cookie and no tracking", () => {
    expect(privacy()).toMatch(/one cookie to keep you signed in/);
    expect(privacy()).toContain("no tracking or ad cookies");
  });

  // Stopping now goes through the ⚙️ Menu (#807, #830): no page may send people to a
  // "Stop recording me" button that isn't there.
  it.each(["terms", "privacy"])("%s: stopping is explained through the ⚙️ Menu", (name) => {
    const body = text(rendered.get(name)?.querySelector("main") ?? rendered.get(name) ?? null);
    const stops = [...body.matchAll(/Stop recording me/g)];
    expect(stops.length).toBeGreaterThan(0);
    for (const stop of stops) {
      expect(body.slice(Math.max(0, stop.index - 80), stop.index)).toContain("⚙️ Menu");
    }
  });

  it("says where the background artwork came from, on the terms page", () => {
    expect(terms()).toContain("The background artwork was made with Gemini and Claude, from our own prompts.");
  });

  it("names the State of Ohio, USA, for the law and the courts", () => {
    expect(terms()).toContain("The laws of the State of Ohio, USA, apply to these terms");
    expect(terms()).toContain("heard in the courts of the State of Ohio, USA");
    expect(terms()).not.toContain("governing law and courts");
  });
});

describe("email from DMbot (#1283)", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));

  it("says which email each kind goes to, what is sent, and how long the notes stay", () => {
    const t = privacy();
    expect(t).toContain("A move code goes only to the email you paid with");
    expect(t).toContain("two Try It emails");
    expect(t).toContain("deleted after 90 days");
    expect(t).toContain("The notes for the Try It emails are deleted a year after they were sent");
    expect(t).toContain("All your notes are deleted when you delete your account");
    expect(t).toContain("This list stays even after you delete your account, so we never email you again after you said stop");
    expect(t).toContain("We do not keep the words of the email or the address it went to");
    expect(t).toContain("Sign-in links and move codes have no such link, because you asked for them");
  });

  it("no longer promises emails about the account and plan that nothing sends", () => {
    expect(privacy()).not.toContain("tell you about your account and plan");
  });

  it("puts no postal address on the page", () => {
    expect(privacy()).not.toMatch(/\b\d{2,5} [A-Z][a-z]+ (Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Drive)\b/);
  });
});

describe("what the AI company does with the text (#1328)", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));

  it("says the text isn't used to train the AI company's AI, as the yes message does", () => {
    const consent = readFileSync(new URL("../../core/src/dmbot/consent_dm.py", import.meta.url), "utf8");
    // The promise made to a player when they say yes (AI_NOTE) and the page must say the same.
    expect(consent).toContain("It isn't used to train their AI.");
    const anthropic = privacy().slice(privacy().indexOf("Anthropic:"));
    // Named, so "their" can't be read as covering the speech company listed just above.
    expect(anthropic.slice(0, 260)).toContain("It gets the text it needs, not sound. That text isn't used to train Anthropic's AI.");
  });

  it("says it once, under the AI company, and nowhere else on the page", () => {
    // Not copied to another company or to the short summary (which has no such promise to make).
    expect(privacy().match(/train/gi)).toHaveLength(1);
  });

  it("makes no such promise for the speech company: it depends on that company's own setting", () => {
    // Anthropic's commercial terms (Section B) say it may not train on customer content. Deepgram's
    // use of audio depends on its Model Improvement Program: core opts out of it (mip_opt_out=true,
    // transcription/deepgram.py), but what that covers needs Deepgram's own docs, so nothing is
    // promised for it here.
    const deepgram = privacy().slice(privacy().indexOf("Deepgram:"), privacy().indexOf("Anthropic:"));
    expect(deepgram).toContain("turns the sound of people who said yes into text");
    expect(deepgram).not.toMatch(/train/i);
  });
});

describe("shared house rules (#1254)", () => {
  it("the privacy page says only rules are shared, never story or transcripts", () => {
    const t = text(doc("privacy").querySelector("main"));
    expect(t).toContain("only the rules themselves");
    expect(t).toContain("The story, names, notes and what was said are never shared");
  });
  it("says a suggestion back to the publisher carries only the rule, never the sender (#1313)", () => {
    const t = text(doc("privacy").querySelector("main"));
    expect(t).toContain("can suggest a rule back to the game master who shares them");
    expect(t).toContain("A suggestion carries only the rule's words (and the rule it would replace), never who sent it");
    expect(t).toContain("keeps no record of the sender");
  });
  it("is true: the publisher's table of suggestions has no column for who sent one (#1313)", () => {
    const schema = readFileSync(new URL("../../core/src/dmbot/schema.py", import.meta.url), "utf8");
    const table = /CREATE TABLE rule_set_suggestions \(([\s\S]*?)\n    \);/.exec(schema)?.[1] ?? "";
    expect(table).toContain("set_id");
    expect(table).not.toMatch(/sent_by|follower|sender|from_guild|from_campaign/);
  });
});

describe("story state (#1284)", () => {
  it("the privacy page says which items are one of a kind and who carries what are kept per campaign", () => {
    const t = text(doc("privacy").querySelector("main"));
    expect(t).toContain("which items are the only one of their kind and who carries what, and whether the game master or the bot said so");
    expect(t).toContain("A backup holds it, and it is deleted when the campaign is deleted");
  });
});

describe("writing to the team (#1340)", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));

  it("says the message is private, kept 12 months, and goes with the account", () => {
    const t = privacy();
    expect(t).toContain("When you write to the team");
    expect(t).toContain("goes only to the team, privately, and is not posted anywhere public");
    expect(t).toContain("We keep it for 12 months, then delete it");
    expect(t).toContain("The team replies by email to the address on your account");
    expect(t).toContain("the website does not show it to you again");
    expect(t).toContain("If you delete your account, we delete your messages too");
  });

  it("is not mixed up with the public Say hello page, whose posts anyone can read", () => {
    const section = [...doc("privacy").querySelectorAll("h3")].find((h) =>
      text(h).startsWith("When you write to the team"),
    );
    expect(section).toBeTruthy();
    expect(text(section?.nextElementSibling ?? null)).not.toContain("GitHub");
    expect(text(section?.nextElementSibling ?? null)).not.toContain("anyone can read");
  });
});
