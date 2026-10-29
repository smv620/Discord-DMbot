// The recap page reads back the reference the Import page links (#2241): a bot-made reference
// (guild.campaign.time, as `job_ref` writes it) and an awkward one both survive the link's
// encoding. Kept apart from the Import page's tests, which mock the importer's words.
import { describe, expect, it } from "vitest";

import { refFromPath } from "../src/account/Recaps";

const linkFor = (recap: string): string => `/account/recaps/${encodeURIComponent(recap)}`;

describe("the recap address round trip", () => {
  it("reads back a reference the way the bot writes it", () => {
    expect(refFromPath(linkFor("123.cmp1.1700000000"))).toBe("123.cmp1.1700000000");
  });

  it("reads back a reference with characters that need encoding", () => {
    expect(refFromPath(linkFor("a/b?c#d"))).toBe("a/b?c#d");
  });
});
