// The legal drafts (#433): each has the short version, the draft notice, and facts that
// match the plan file, so the pages and the pricing never disagree.
import { readFileSync } from "node:fs";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import facts from "../src/content/plans.json";
import { deletionWarnings, notAffiliated, operator, retentionRows } from "../src/content/legal";
import { QABOT_LIVE } from "../src/content/qabot";
import { RECAPS_LIVE } from "../src/content/recap-words";
import { initiativeLine, peopleListLine, storyStatesLine } from "../src/content/privacy-channels";
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
    // The people list and initiative lines show only while their features are live (#1810).
    expect(privacy().includes(peopleListLine.lead)).toBe(peopleListLine.live);
    expect(privacy().includes(initiativeLine.lead)).toBe(initiativeLine.live);
    expect(privacy().includes(storyStatesLine.lead)).toBe(storyStatesLine.live);
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
    expect(privacy()).toMatch(/Deepgram, Anthropic, ElevenLabs, OVH US, Cloudflare, GitHub and Stripe/);
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
    expect(privacy()).toContain("the game masters can see who peeked");
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

  // Stopping now goes through the 🗂️ Menu (#807, #830): no page may send people to a
  // "Stop recording me" button that isn't there.
  it.each(["terms", "privacy"])("%s: stopping is explained through the 🗂️ Menu", (name) => {
    const body = text(rendered.get(name)?.querySelector("main") ?? rendered.get(name) ?? null);
    const stops = [...body.matchAll(/Stop recording me/g)];
    expect(stops.length).toBeGreaterThan(0);
    for (const stop of stops) {
      expect(body.slice(Math.max(0, stop.index - 80), stop.index)).toContain("🗂️ Menu");
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
    // The public Q&A section (#1356) repeats the same sentence once it is switched on, so it counts
    // there too; qabot-privacy.test.ts checks that it is the same sentence.
    // The Recaps section (#1384) repeats it too once recaps are live: it says the same of the recap writer.
    expect(privacy().match(/train/gi)).toHaveLength(1 + (QABOT_LIVE ? 1 : 0) + (RECAPS_LIVE ? 1 : 0));
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

  it("says an adventure file's text is kept for that campaign only and is not in a backup (#1720)", () => {
    const t = text(doc("privacy").querySelector("main"));
    expect(t).toContain("An adventure file is the one exception. DMbot keeps its text, not the file, for that campaign only. It stays until the game master removes it or the campaign is deleted. A backup holds only its name");
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
    // #1373: the account page lists the person's own questions; #2193: each opens to its whole
    // conversation, which only the person and the team can read.
    expect(t).toContain(
      "your account page shows you its subject, when you sent it and whether it was answered. You can open it there to read the whole conversation: your message and the replies. On our website, only you and the team can read it",
    );
    expect(t).not.toContain("never the message itself");
    expect(t).not.toContain("does not show it to you again");
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

describe("questions to DMbot are in the written record (#1379)", () => {
  // CLAUDE.md "Transcripts are shared": the DM's quick questions and DMbot's in-game answers are
  // saved as sidebar lines in the raw transcript (sidebar/service.py _save_lines); the Help page
  // says so, so the privacy page must too.
  it("says so in the What was typed item", () => {
    const page = text(doc("privacy").querySelector("main"));
    expect(page).toContain(
      "Questions put to DMbot, and its answers about the game, go in the written record too, marked as such.",
    );
  });
});

describe("generated content belongs to the campaign (#1388)", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));
  const terms = (): string => text(doc("terms").querySelector("main"));

  it("says on both pages that what DMbot makes belongs to the campaign, and the campaign to its game master", () => {
    for (const page of [privacy(), terms()]) {
      expect(page).toContain("What DMbot makes from a session belongs to the campaign");
      expect(page).toContain("a recap, story facts, notes for the game master, and anything else DMbot writes from what was said");
      expect(page).toMatch(/deleted only with the campaign/);
    }
  });

  it("says stopping or deleting your own data does not remove it, on both pages", () => {
    const privacyPage = privacy();
    expect(privacyPage).toContain(
      "What DMbot already made from sessions you took part in, such as a recap or the story facts your game master kept, stays with the campaign. Your voice was not kept.",
    );
    expect(privacyPage).toContain("What DMbot made from those sessions stays with the campaign too: deleting your account does not remove it.");
    const termsPage = terms();
    expect(termsPage).toContain("If you stop being recorded, take back your yes or delete your account");
    expect(termsPage).toContain("What DMbot already made from sessions you took part in stays with the campaign, and so do copies other people downloaded.");
    // The terms point to the privacy page for what is removed, rather than repeating it.
    const link = doc("terms").querySelector('main a[href="/legal/privacy"]');
    expect(text(link)).toBe("Privacy page");
  });

  it("says only deleting the campaign removes it, and only the game master whose plan it uses can (#1610)", () => {
    expect(privacy()).toContain(
      "Only the game master whose plan the campaign uses can (for a free campaign, the one who started it). Deleting it is the only thing that removes what DMbot made for it, such as its recaps, story facts and notes. A player who wants something removed can ask their game master.",
    );
    expect(terms()).toContain(
      "Only deleting the campaign removes what DMbot made, and only the game master whose plan the campaign uses can delete it.",
    );
  });

  it("says who owns the campaign, the licence DMbot gets, and who runs and deletes it (#1610)", () => {
    const page = terms();
    expect(page).toContain(
      "You keep what you and your table make. Your campaign, notes and what your table says belong to you and your table, not to us. You give DMbot a worldwide, royalty-free licence to store, process and show them, to run DMbot and to improve it (you can turn the improving off in My Account).",
    );
    expect(page).toContain(
      "Within the table, the game masters run the campaign. Only the game master whose plan it uses can delete it (for a free campaign, the one who started it).",
    );
    expect(page).not.toContain("only to run DMbot for you");
  });

  it("says the improvement choice on the privacy page: where it is, and that it is not recording (#1610)", () => {
    const page = privacy();
    expect(page).toContain("DMbot uses transcripts and what people type in the table's chat to get better.");
    expect(page).toContain("You can turn this off in My Account. It covers every server.");
    expect(page).toContain("Account details (Discord account numbers, emails and payment records) are never published.");
    expect(page).toContain('unless you give a separate yes to "Save my voice for tests"');
    const link = doc("privacy").querySelector('main a[href="/account"]');
    expect(link).toBeTruthy();
  });

  it("says deletion is separable on the terms page (#1610)", () => {
    const page = terms();
    expect(page).toContain("as far as they can be separated from the rest");
    expect(page).toContain("If we can't delete something, it stays, and everything else you asked for still happens.");
  });

  it("keeps the retention and 120-day wording as it was", () => {
    expect(privacy()).toContain("They are deleted 120 days later, unless another DM of the campaign takes them over.");
    expect(terms()).toContain("They are deleted 120 days later, unless another DM of the campaign takes them over.");
    expect(privacy()).toContain("Lines you said in someone else's campaign stay there, readable by that server, until that campaign is deleted.");
  });

  it("uses plain words: no technical terms for the content DMbot makes", () => {
    for (const page of [privacy(), terms()]) {
      expect(page).not.toMatch(/knowledge graph|ontology|derived data|generated content|extractor|state facts/i);
    }
  });
});

describe("privacy lines for new features (#1810)", () => {
  it("say what they hear and keep, in plain words, with no AI claim for initiative", () => {
    expect(initiativeLine.text).toContain("uses no AI");
    expect(initiativeLine.text).toContain("only people who said yes");
    expect(initiativeLine.text).toContain("not tied to any person");
    expect(peopleListLine.text).toContain("never shows secrets, notes");
    for (const line of [initiativeLine, peopleListLine, storyStatesLine]) expect(line.text).not.toMatch(/\bNPC\b|\bAPI\b|database/i);
  });
});
