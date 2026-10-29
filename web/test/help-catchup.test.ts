// The Help page's catch-up (#1460): the Try It sentence is always there; the recap steps, the
// narration add-on sentence and the AI helper's three sentences show only while their switch is on,
// and the narration price comes from its one constant. The switches are plain values the test sets
// before the page's words are loaded fresh.
import { afterEach, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ recaps: false, audio: false, support: false, price: null as string | null }));

vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.recaps;
    },
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});
vi.mock("../src/content/support-words", async (original) => {
  const real = await original<typeof import("../src/content/support-words")>();
  return {
    ...real,
    get SUPPORT_AI_LIVE() {
      return live.support;
    },
  };
});
vi.mock("../src/content/recap-audio", async (original) => {
  const real = await original<typeof import("../src/content/recap-audio")>();
  return { ...real, price: () => live.price };
});

afterEach(() => {
  live.recaps = live.audio = live.support = false;
  live.price = null;
  vi.resetModules();
});

async function help() {
  vi.resetModules();
  return await import("../src/content/help");
}

const gettingText = (h: Awaited<ReturnType<typeof help>>) =>
  JSON.stringify(h.gettingHelp.items);
const recaps = (h: Awaited<ReturnType<typeof help>>) => h.recapSteps.map((s) => s.text).join(" ");

describe("getting help", () => {
  it("says any account can write to the team, whatever the switches", async () => {
    const h = await help();
    const item = h.gettingHelp.items.find((i) => i.lead === "Have an account?");
    expect(item).toBeTruthy();
    expect(JSON.stringify(item)).toContain("Write to the team in private from");
    expect(JSON.stringify(item)).not.toMatch(/paid plan/i);
  });

  it("says nothing about the AI helper while it is off", async () => {
    const h = await help();
    expect(gettingText(h)).not.toMatch(/AI helper/);
  });

  it("says it in three short sentences when it is on", async () => {
    live.support = true;
    const h = await help();
    const item = h.gettingHelp.items.find((i) => i.lead === "Who answers?");
    const words = JSON.stringify(item);
    expect(words).toContain("DMbot's AI helper (a program, not a person) answers first, by email");
    expect(words).toContain("A person on the team takes over if the helper can't help, or if you ask for one");
    expect(words).toContain("the first line of the last reply");
    expect(h.gettingHelp.items.map((i) => i.lead).slice(-2)).toEqual(["Anyone, on any plan.", "Who answers?"]);
    const sentences = (item?.parts[0] as string).split(/(?<=\.) /);
    expect(sentences.length).toBeLessThanOrEqual(4);
  });
});

describe("the recap steps (shown only while recaps are on, by the page)", () => {
  it("say the DM screen tells the DM, then email unless turned off, and the DM screen's buttons", async () => {
    const all = recaps(await help());
    expect(all).toContain("DMbot tells you on the DM screen when a recap is ready");
    expect(all).toContain("It also emails you, unless you turn that off, and the email says why if a recap couldn't be written");
    expect(all).toContain("Read it");
    expect(all).toContain("Press Post to the table to send it to your players; DMbot asks once first.");
    expect(all).toContain("Not this one");
    expect(all).toContain("Press Recaps on ⚙️ Settings");
    expect(all).toContain("Each campaign gets up to 10 recaps a month.");
  });

  it("say what narration is, since it is on (#2617), and nothing of an add-on while it has no price", async () => {
    live.recaps = true;
    const all = recaps(await help());
    expect(all).toMatch(/narrator's voice and music/i);
    expect(all).not.toMatch(/add-on/i);
  });

  it("add the narration add-on sentence, with the price from its constant, when narration is on", async () => {
    live.recaps = live.audio = true;
    live.price = "$4.99 a month";
    const all = recaps(await help());
    expect(all).toContain("Narration comes with Guild and Pro. On the other paid plans it is an add-on, $4.99 a month.");
    expect(all).toContain("ask for it again on the recap page. You can do that once every 10 minutes");
    live.price = null; // no price set: the sentence leaves it out rather than guessing one
    const bare = recaps(await help());
    expect(bare).toContain("it is an add-on. If the audio");
    expect(bare).not.toMatch(/\$\d/);
  });
});
