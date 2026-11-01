// Help steps for the four campaign settings (#2985): each step shows only while its own switch is on, so Help never
// describes a button that is not there yet. Each test sets the four switches, then loads Help fresh.
import { afterEach, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ story: false, invite: false, rulesets: false, houseFile: false }));
vi.mock("../src/content/story-mode-switch", () => ({ get STORY_MODE_LIVE() { return live.story; } }));
vi.mock("../src/content/invite-switch", () => ({ get INVITE_LIVE() { return live.invite; } }));
vi.mock("../src/content/rulesets-switch", () => ({ get RULESETS_LIVE() { return live.rulesets; } }));
vi.mock("../src/content/house-file-switch", () => ({ get HOUSE_FILE_LIVE() { return live.houseFile; } }));

const STORY_START = "To choose how DMbot follows your adventure";
const INVITE_START = "To pass on your campaign's invite from dndbeyond.com";
const RULESETS_START = "To choose which rules DMbot checks first";
const HOUSE_START = "To keep your house rules in a file";

async function help(on: Partial<typeof live>) {
  vi.resetModules();
  Object.assign(live, { story: false, invite: false, rulesets: false, houseFile: false }, on);
  return import("../src/content/help");
}
const texts = (steps: { text: string }[]) => steps.map((s) => s.text);
const has = (steps: { text: string }[], start: string) => texts(steps).some((t) => t.startsWith(start));

afterEach(() => {
  Object.assign(live, { story: false, invite: false, rulesets: false, houseFile: false });
});

// Each test loads Help fresh (a slow import when the suite runs in parallel), so allow more than the 5 s default.
describe("Help steps for the campaign settings", { timeout: 30000 }, () => {
  it("with every switch off, Help has none of the four steps", async () => {
    const h = await help({});
    expect(has(h.dmSteps, STORY_START)).toBe(false);
    expect(has(h.dmSteps, INVITE_START)).toBe(false);
    expect(has(h.dmSteps, RULESETS_START)).toBe(false);
    expect(has(h.dmSteps, HOUSE_START)).toBe(false);
  });

  it("with only the Shared story switch on, Help has that step and none of the other three", async () => {
    const h = await help({ story: true });
    expect(has(h.dmSteps, STORY_START)).toBe(true);
    expect(has(h.dmSteps, INVITE_START)).toBe(false);
    expect(has(h.dmSteps, RULESETS_START)).toBe(false);
    expect(has(h.dmSteps, HOUSE_START)).toBe(false);
  });

  it("with only the Main rules switch on, Help has that step and none of the other three", async () => {
    const h = await help({ rulesets: true });
    expect(has(h.dmSteps, RULESETS_START)).toBe(true);
    expect(has(h.dmSteps, STORY_START)).toBe(false);
    expect(has(h.dmSteps, INVITE_START)).toBe(false);
    expect(has(h.dmSteps, HOUSE_START)).toBe(false);
  });

  it("each step's text is exactly the words in the issue", async () => {
    const h = await help({});
    expect(h.storyModeHelpSteps[0]!.text).toBe(
      "To choose how DMbot follows your adventure, open your campaign's page here and find Shared story under Settings. Off means DMbot doesn't follow the adventure's story. Watch the story warns you on your DM screen, once, if someone the adventure needs is gone or something it needs is lost. Guide me does that too, and you can ask DMbot what comes next in your adventure. Only you see the answer. The first time you turn it on, you tick that you have the right to use the material you give DMbot. DMbot never makes up story and never decides anything: you do.",
    );
    expect(h.inviteHelpSteps[0]!.text).toBe(
      "To pass on your campaign's invite from dndbeyond.com, open your campaign's page here and find Campaign invite from dndbeyond.com under Settings. Paste the invite link from your campaign there and press Save the link. Players then see a Join button on new messages from DMbot. It opens your campaign's invite on dndbeyond.com. DMbot can't add anyone to your campaign for you. To take the button away, press Remove the link.",
    );
    expect(h.rulesetsHelpSteps[0]!.text).toBe(
      "To choose which rules DMbot checks first, open your campaign's page here and find Main rules under Settings. Pick the 2024 rules (newest) or the 2014 rules (older). Then pick what DMbot does if the main rules don't cover something: use the other set, or skip it. Press Save. DMbot only points out what the rules say. You decide.",
    );
    expect(h.houseFileHelpSteps[0]!.text).toBe(
      "To keep your house rules in a file, open the House rules page here and find House-rules file. Connect a link to your file (a Doc, PDF or web page that anyone with the link can open), or upload a .txt file once. Press Compare now to see what is different. DMbot never changes a rule on its own: it asks on your DM screen in Discord, and nothing changes until you answer there.",
    );
  });

  it("with only the Campaign invite switch on, Help has that step and none of the other three", async () => {
    const h = await help({ invite: true });
    expect(has(h.dmSteps, INVITE_START)).toBe(true);
    expect(has(h.dmSteps, STORY_START)).toBe(false);
    expect(has(h.dmSteps, RULESETS_START)).toBe(false);
    expect(has(h.dmSteps, HOUSE_START)).toBe(false);
  });

  it("with only the House-rules file switch on, Help has that step and none of the other three", async () => {
    const h = await help({ houseFile: true });
    expect(has(h.dmSteps, HOUSE_START)).toBe(true);
    expect(has(h.dmSteps, STORY_START)).toBe(false);
    expect(has(h.dmSteps, INVITE_START)).toBe(false);
    expect(has(h.dmSteps, RULESETS_START)).toBe(false);
  });

  it("with all four switches on, the four steps appear in the order the issue gives", async () => {
    const h = await help({ story: true, invite: true, rulesets: true, houseFile: true });
    const order = [STORY_START, INVITE_START, RULESETS_START, HOUSE_START].map((start) =>
      texts(h.dmSteps).findIndex((t) => t.startsWith(start)),
    );
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });
});
