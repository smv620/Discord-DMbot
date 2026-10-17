// Stripe's billing login link on the Refunds page (#1711): shown only for a https://billing.stripe.com/
// address, under "Stopping your plan". (Its own file: the page renders through Astro's container, like
// legal.test.ts, not through the Preact test environment.)
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parsePage } from "./dom";

import { portalWords } from "../src/content/billing-login";
import Refunds from "../src/pages/legal/refunds.astro";

const GOOD = "https://billing.stripe.com/p/login/test_abc123";
const VAR = "PUBLIC_STRIPE_PORTAL_LOGIN_URL";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Refunds", () => {
  const render_ = async (): Promise<Document> => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(Refunds, {
      request: new Request("https://dmbot.example/legal/refunds"),
    });
    return parsePage(html);
  };

  it("shows nothing while the setting is empty or not Stripe's", async () => {
    expect((await render_()).body.textContent).not.toContain("Can't sign in with Discord?");
    vi.stubEnv(VAR, "https://example.com/p/login/x");
    const doc = await render_();
    expect(doc.body.textContent).not.toContain("Can't sign in with Discord?");
    expect(doc.querySelector('a[href*="example.com/p/login"]')).toBeNull();
  });

  it("shows the sentence and the link under Stopping your plan", async () => {
    vi.stubEnv(VAR, GOOD);
    const doc = await render_();
    const link = doc.querySelector(`a[href="${GOOD}"]`);
    expect(link?.textContent).toBe(portalWords.linkText);
    expect(link?.parentElement?.textContent).toContain(portalWords.refunds);
    const heading = [...doc.querySelectorAll("h2")].find((h) => h.id === "stopping");
    let node: Element | null = heading?.nextElementSibling ?? null;
    let found = false;
    while (node && node.tagName !== "H2") {
      if (node.contains(link)) found = true;
      node = node.nextElementSibling;
    }
    expect(found).toBe(true);
  });
});

describe("Help, Your account", () => {
  // help.ts reads the setting when it loads, so each case loads a fresh copy.
  const helpPage = async (): Promise<Document> => {
    vi.resetModules();
    const { default: Help } = await import("../src/pages/help.astro");
    const container = await AstroContainer.create();
    return parsePage(
      await container.renderToString(Help, { request: new Request("https://dmbot.example/help") }),
    );
  };

  it("has no billing login link while the setting is empty", async () => {
    const doc = await helpPage();
    expect(doc.querySelector('#account-heading ~ ol a[href*="stripe.com"]')).toBeNull();
  });

  it("adds the step with the link when the setting is Stripe's", async () => {
    vi.stubEnv(VAR, GOOD);
    const doc = await helpPage();
    const link = doc.querySelector(`#account-heading ~ ol a[href="${GOOD}"]`);
    expect(link?.textContent).toBe(portalWords.linkText);
    expect(link?.closest("li")?.textContent).toContain("Can't sign in with Discord?");
  });
});
