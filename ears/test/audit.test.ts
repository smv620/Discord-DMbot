import assert from "node:assert/strict";
import { test } from "node:test";
import { SessionAudit } from "../src/audit.js";

test("sessions core doesn't mention after reconnecting are reported", () => {
  const audit = new SessionAudit();
  audit.begin(["1", "2", "3"]);
  audit.confirm("2");
  audit.confirm("9"); // a server ears had no session for: harmless
  assert.deepEqual(audit.takeUnconfirmed().sort(), ["1", "3"]);
  assert.deepEqual(audit.takeUnconfirmed(), []); // cleared
});

test("a new reconnect starts a fresh list", () => {
  const audit = new SessionAudit();
  audit.begin(["1"]);
  audit.begin(["2"]);
  assert.deepEqual(audit.takeUnconfirmed(), ["2"]);
});
