// The "Use a document link" check (#2843), tested as plain logic: no page, no network.
import { describe, expect, it } from "vitest";

import { checkDocumentLink } from "../src/account/sheets-document";

describe("checkDocumentLink", () => {
  it("accepts a plain https link and keeps it", () => {
    expect(checkDocumentLink("https://example.com/sheet.pdf")).toEqual({ ok: true, url: "https://example.com/sheet.pdf" });
  });

  it("trims the link before it is checked and sent", () => {
    expect(checkDocumentLink("  https://example.com/sheet  ")).toEqual({ ok: true, url: "https://example.com/sheet" });
  });

  it("refuses an empty link, or one that is only spaces", () => {
    expect(checkDocumentLink("")).toEqual({ ok: false, problem: "empty" });
    expect(checkDocumentLink("   ")).toEqual({ ok: false, problem: "empty" });
  });

  it("refuses a link with a control character in it, even at its start", () => {
    expect(checkDocumentLink("\u0001https://example.com/sheet")).toEqual({ ok: false, problem: "not-web" });
    expect(checkDocumentLink("https://example.com/\tsheet")).toEqual({ ok: false, problem: "not-web" });
  });

  it("refuses an http link: the bot fetches only https", () => {
    expect(checkDocumentLink("http://example.com/sheet.pdf")).toEqual({ ok: false, problem: "not-web" });
    expect(checkDocumentLink("HTTP://example.com/sheet.pdf")).toEqual({ ok: false, problem: "not-web" });
  });

  it("accepts an https link whatever the case of the scheme", () => {
    expect(checkDocumentLink("HTTPS://example.com/sheet.pdf")).toEqual({ ok: true, url: "HTTPS://example.com/sheet.pdf" });
  });

  it("refuses a link without a literal https:// prefix, even where the URL parser would read it", () => {
    expect(checkDocumentLink("http:/example.com")).toEqual({ ok: false, problem: "not-web" });
  });

  it("refuses a word with spaces, a link with another scheme, and a script link", () => {
    expect(checkDocumentLink("a word with spaces")).toEqual({ ok: false, problem: "not-web" });
    expect(checkDocumentLink("ftp://x")).toEqual({ ok: false, problem: "not-web" });
    expect(checkDocumentLink("javascript:alert(1)")).toEqual({ ok: false, problem: "not-web" });
  });

  it("refuses a link of 401 characters, and accepts one of exactly 400", () => {
    const prefix = "https://example.com/";
    expect(checkDocumentLink(prefix + "a".repeat(401 - prefix.length))).toEqual({ ok: false, problem: "too-long" });
    const exact = prefix + "a".repeat(400 - prefix.length);
    expect(exact.length).toBe(400);
    expect(checkDocumentLink(exact)).toEqual({ ok: true, url: exact });
  });
});
