/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Stripe's billing login link (#1711): it shows on Refunds, in Help and on My Account only when the
// build gives a https://billing.stripe.com/ address, and never for any other address.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { mockApi, type Scenario } from "../src/account/mock";
import { portalLoginSteps, portalLoginUrl, portalWords } from "../src/content/billing-login";
import { accountSteps } from "../src/content/help";

const GOOD = "https://billing.stripe.com/p/login/test_abc123";
const VAR = "PUBLIC_STRIPE_PORTAL_LOGIN_URL";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("which address counts", () => {
  it("accepts only an https address on billing.stripe.com", () => {
    expect(portalLoginUrl(GOOD)).toBe(GOOD);
    expect(portalLoginUrl(`  ${GOOD}  `)).toBe(GOOD);
  });

  it("treats empty and missing as nothing", () => {
    expect(portalLoginUrl("")).toBeNull();
    expect(portalLoginUrl("   ")).toBeNull();
    expect(portalLoginUrl(undefined)).toBeNull();
  });

  it("refuses any other host, scheme or look-alike", () => {
    for (const bad of [
      "http://billing.stripe.com/p/login/x",
      "https://example.com/p/login/x",
      "https://billing.stripe.com.example.com/p/login/x",
      "https://evil.example/billing.stripe.com/",
      "https://user:pw@billing.stripe.com/p/login/x",
      "https://billing.stripe.com:8443/p/login/x",
      "javascript:alert(1)",
      "billing.stripe.com/p/login/x",
      "not a url",
    ]) {
      expect(portalLoginUrl(bad), bad).toBeNull();
    }
  });

  it("reads the build setting when none is passed", () => {
    expect(portalLoginUrl()).toBeNull();
    vi.stubEnv(VAR, GOOD);
    expect(portalLoginUrl()).toBe(GOOD);
    vi.stubEnv(VAR, "https://example.com/");
    expect(portalLoginUrl()).toBeNull();
  });
});

describe("Help", () => {
  it("adds one step with the link, only for a good address", () => {
    expect(portalLoginSteps(null)).toEqual([]);
    const [step] = portalLoginSteps(GOOD);
    expect(step?.text).toContain("Can't sign in with Discord?");
    expect(step?.link).toEqual({ text: portalWords.linkText, href: GOOD });
  });

  it("leaves the account steps alone while the setting is empty", () => {
    expect(accountSteps.some((s) => s.link?.href.includes("stripe.com"))).toBe(false);
  });
});

describe("My Account", () => {
  const show = async (scenario: Scenario) => {
    render(<Account api={mockApi(scenario)} go={vi.fn()} />);
    await screen.findByRole("heading", { level: 2, name: /plan/i });
  };

  it("shows nothing while the setting is empty", async () => {
    await show("table");
    expect(screen.queryByText(portalWords.account)).toBeNull();
  });

  it("shows nothing for any other host", async () => {
    vi.stubEnv(VAR, "https://example.com/p/login/x");
    await show("table");
    expect(screen.queryByText(portalWords.account)).toBeNull();
  });

  it("shows a small link on a paid plan", async () => {
    vi.stubEnv(VAR, GOOD);
    await show("table");
    expect(screen.getByRole("link", { name: portalWords.account }).getAttribute("href")).toBe(GOOD);
  });

  it("shows it for someone with free access who still pays", async () => {
    vi.stubEnv(VAR, GOOD);
    await show("free-paying");
    expect(screen.getByRole("link", { name: portalWords.account }).getAttribute("href")).toBe(GOOD);
  });
});
