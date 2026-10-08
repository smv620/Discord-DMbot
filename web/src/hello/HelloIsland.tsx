/** @jsxImportSource preact */
// The two "Say hello" forms (#665). PUBLIC_API_BASE is the web API's address ("mock" in
// preview builds: every message pretends to send). PUBLIC_TURNSTILE_SITE_KEY turns on
// Cloudflare's "are you a person?" check.
import { httpSend, pretendSend } from "./api";
import HelloForm from "./HelloForm";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";
const siteKey: string = import.meta.env.PUBLIC_TURNSTILE_SITE_KEY ?? "";
const send = base === "mock" ? pretendSend : httpSend(base);

export default function HelloIsland() {
  return (
    <div class="forms">
      <HelloForm kind="feedback" send={send} siteKey={siteKey} />
      <HelloForm kind="question" send={send} siteKey={siteKey} />
    </div>
  );
}
