/** @jsxImportSource preact */
// Picks the API for the start-up wizard (#3956), as QuestionsIsland does. The server comes from the address (`/new?server=<id>`),
// which Discord's ＋ New campaign button opens; without it the page asks the person to open it from Discord.
import { useEffect, useState } from "preact/hooks";

import { WIZARD_LIVE, wizardOn } from "../content/wizard-switch";
import { wizardWords as w } from "../content/wizard-words";
import Wizard from "./Wizard";
import type { WizardApi } from "./wizard-api";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function WizardIsland() {
  const [api, setApi] = useState<WizardApi | null>(null);
  const [guild, setGuild] = useState<string | null>(null);
  // The page is built once for every address, so whether it is on is known only in the browser (the test host, wizard-switch.ts).
  const [on, setOn] = useState(WIZARD_LIVE);

  useEffect(() => {
    const live = wizardOn(window.location.hostname);
    setOn(live);
    if (!live) return; // switched off: no API is built and nothing is asked
    const id = new URLSearchParams(window.location.search).get("server") ?? "";
    if (base === "mock") {
      setGuild(/^[0-9]{1,20}(\.[a-z]+)?$/.test(id) ? id : "");
      void import("./wizard-mock").then(({ mockWizard }) => setApi(mockWizard()));
      return;
    }
    setGuild(/^[0-9]{1,20}$/.test(id) ? id : ""); // (a server's Discord id: digits only)
    void import("./wizard-http").then(({ httpWizard }) => setApi(httpWizard(base)));
  }, []);

  if (!on) return <p class="muted">{w.notYet}</p>;
  if (guild === "") return <p class="muted">{w.noServer}</p>;
  if (!api || guild === null) return <p class="muted" role="status">{w.loading}</p>;
  return (
    <Wizard api={api} guild={guild} />
  );
}
