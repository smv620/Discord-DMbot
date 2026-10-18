// Credits (#1923): the attribution the SRD's licence (CC-BY-4.0) asks for, where DMbot's rules text is
// used. The two statements are copied word for word from the ATTRIBUTION.md next to the data, and a
// test (credits.test.ts) reads those files and fails if this page drifts from them. Credits, not
// branding: no logos, and no names beyond the ones the licence needs.
import { notAffiliated } from "./legal";

export const creditsHeading = "Credits";

export const creditsIntro =
  "DMbot uses rules text from two free documents. Here is where each one comes from and the licence it is shared under.";

const repo = "https://github.com/smv620/Discord-DMbot/blob/main/core/src/dmbot/rules/data";

export const sources = [
  {
    name: "System Reference Document 5.2.1",
    statement:
      "This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.",
    file: `${repo}/srd52/ATTRIBUTION.md`,
    path: "core/src/dmbot/rules/data/srd52/ATTRIBUTION.md",
    folder: "srd52",
  },
  {
    name: "System Reference Document 5.1",
    statement:
      "This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.",
    file: `${repo}/srd51/ATTRIBUTION.md`,
    path: "core/src/dmbot/rules/data/srd51/ATTRIBUTION.md",
    folder: "srd51",
  },
];

export const whatDmbotDid =
  "DMbot took the text out of the PDFs, fixed words the PDF had split, and cut it into entries.";

export const creditsNotAffiliated = notAffiliated;

export const lookupHeading = "What DMbot can look up";
export const lookupIntro = "Only what is in this free document, not the full books. These numbers are counted from the real data, not typed by hand.";
