// The checks behind "Type in my character" (#2832), tested as plain logic: no page, no network.
import { describe, expect, it } from "vitest";

import { checkTyped, type TypedValues } from "../src/account/sheets-typed";

const typed = (over: Partial<TypedValues> = {}): TypedValues => ({ className: "Wizard", level: "5", species: "", names: "", ...over });

describe("checkTyped", () => {
  it("refuses a blank class, and one that is only spaces", () => {
    expect(checkTyped(typed({ className: "" }))).toEqual({ ok: false, problem: "class" });
    expect(checkTyped(typed({ className: "   " }))).toEqual({ ok: false, problem: "class" });
  });

  it.each(["0", "21", "x", "1.5", "-1", "", "100"])("refuses the level %j", (level) => {
    expect(checkTyped(typed({ level }))).toEqual({ ok: false, problem: "level" });
  });

  it.each([["1", 1], ["01", 1], ["20", 20], [" 7 ", 7]])("accepts the level %j as the number %i", (level, n) => {
    expect(checkTyped(typed({ level }))).toMatchObject({ ok: true, level: n });
  });

  it("trims the class, species and names", () => {
    expect(checkTyped(typed({ className: "  Wizard ", species: " Elf ", names: "  Misty Step, Shield  " }))).toEqual({
      ok: true,
      className: "Wizard",
      level: 5,
      species: "Elf",
      names: "Misty Step, Shield",
    });
  });

  it("leaves species and names empty when nothing is typed", () => {
    expect(checkTyped(typed())).toEqual({ ok: true, className: "Wizard", level: 5, species: "", names: "" });
  });
});
