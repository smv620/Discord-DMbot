/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Until WIZARD_LIVE is flipped, the wizard pages are on only at the development site's own address, so the owner can try the real
// wizard on the test server (docs/DEPLOY.md); every other address (the public site, a pull request preview, localhost) says "not yet".
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import WizardIsland from "../src/account/WizardIsland";
import { WIZARD_LIVE, WIZARD_TEST_HOST, wizardOn } from "../src/content/wizard-switch";
import { wizardWords as w } from "../src/content/wizard-words";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("wizardOn", () => {
  it("is on for the development site's exact host only", () => {
    expect(WIZARD_LIVE).toBe(false); // (the real switch stays off; this test is about the host)
    expect(WIZARD_TEST_HOST).toBe("dev.getdmbot.com");
    expect(wizardOn("dev.getdmbot.com")).toBe(true);
    for (const host of ["getdmbot.com", "www.getdmbot.com", "dev.getdmbot.com.evil.example", "evil-dev.getdmbot.com", "getdmbot.pages.dev", "abc123.getdmbot.pages.dev", "localhost", ""]) {
      expect(wizardOn(host), host).toBe(false);
    }
  });
});

function visit(host: string, search = "") {
  vi.stubGlobal("location", { hostname: host, search, href: `https://${host}/new${search}` });
}

describe("the wizard island", () => {
  it("says not yet on the public address", async () => {
    visit("getdmbot.com", "?server=123");
    render(<WizardIsland />);
    expect(await screen.findByText(w.notYet)).toBeTruthy();
  });

  it("opens on the development site (and asks for a server when the address has none)", async () => {
    visit("dev.getdmbot.com");
    render(<WizardIsland />);
    await waitFor(() => expect(screen.queryByText(w.notYet)).toBeNull());
    expect(await screen.findByText(w.noServer)).toBeTruthy();
  });
});
