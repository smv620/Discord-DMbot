// The plain line for each word a wizard route can refuse with (core web/app.py and web/wizard.py): what the person is told to do.
// In a file of its own so the pages and their steps share it.
import { wizardWords as w } from "../content/wizard-words";
import { WizardError } from "./wizard-api";

/** Each refusal word a route can give (core web/app.py and web/wizard.py), as a plain line telling the person what to do. */
export const problemWords = (error: unknown): string => {
  if (!(error instanceof WizardError)) return w.failed;
  switch (error.kind) {
    case "no_room":
      return w.noRoom;
    case "too_fast":
    case "too_soon":
      return w.tooFast;
    case "expired":
    case "no_draft":
      return w.expired;
    case "not_allowed":
    case "not_found":
      return w.notAllowed;
    case "needs_name":
    case "bad_name":
      return w.needsName;
    case "needs_rights":
    case "rights_needed":
      return w.docs.rightsNeeded;
    case "too_big":
    case "doc_too_big":
      return w.docs.unreadable["too_big"]!;
    case "too_many":
    case "too_many_files":
      return w.docs.full;
    case "signed_out":
      return w.signedOut;
    case "network":
      return w.network;
    case "bad_document":
      return w.docs.unreadable["not_text"]!;
    case "bad_fallback":
      return w.rules.same;
    case "bad_value":
      return error.key === "fallback" ? w.rules.same : w.badAnswer;
    default:
      return error.kind.startsWith("bad_") ? w.badAnswer : w.failed;
  }
};
