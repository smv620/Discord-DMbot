// Buttons must look plain until hovered (#1778): the base rule may not fill them with the accent colour at rest.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/styles/global.css", import.meta.url), "utf8");

describe("button look at rest (#1778)", () => {
  it("fills with the accent colour only on hover", () => {
    const rest = css.match(/\.button \{([^}]*)\}/)?.[1] ?? "";
    expect(rest).not.toMatch(/background:\s*var\(--accent\);/);
    const hover = css.match(/\.button:hover:not\(:disabled\) \{([^}]*)\}/)?.[1] ?? "";
    expect(hover).toMatch(/background:\s*var\(--accent\);/);
  });
});
