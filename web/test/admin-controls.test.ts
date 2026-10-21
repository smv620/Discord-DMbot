// The admin page's styles must not stretch a radio or checkbox like a text box (#2198): the
// generic input rule once gave every radio a full-width, tap-high box, so each label fell onto a
// line of its own. happy-dom can't lay out the page, so this reads the rules in the source; the
// layout itself was measured in a real browser (see the PR).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/pages/admin.astro", import.meta.url), "utf8");

describe("the admin page's form controls (#2198)", () => {
  it("leaves radios and checkboxes out of the full-width input rule", () => {
    expect(css).toContain(
      '.admin :global(input:not([type="radio"]):not([type="checkbox"])),\n  .admin :global(select) {',
    );
    expect(css).not.toMatch(/\.admin :global\(input\),/);
  });

  it("lays a .choice out as one row with a small control, and keeps the row a full tap target", () => {
    const row = /\.admin :global\(\.choice\) \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(row).toContain("display: flex");
    expect(row).toContain("align-items: center");
    expect(row).toContain("min-height: var(--tap)");
    const control = /\.admin :global\(\.choice input\) \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(control).toContain("width: 1.25rem");
    expect(control).toContain("height: 1.25rem");
  });
});
