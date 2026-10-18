// Credits (#1923): the attribution the SRD's licence (CC-BY-4.0) asks for, where DMbot's rules text is
// used. The two statements are copied word for word from the ATTRIBUTION.md next to the data, and a
// test (credits.test.ts) reads those files and fails if this page drifts from them. Credits, not
// branding: no logos, and no names beyond the ones the licence needs.
import { notAffiliated } from "./legal";

export const creditsHeading = "Credits";

export const creditsIntro =
  "DMbot shows rules text from two free rules documents. This is where it comes from, and under which licence.";

const repo = "https://github.com/smv620/Discord-DMbot/blob/main/core/src/dmbot/rules/data";

export const sources = [
  {
    name: "System Reference Document 5.2.1",
    statement:
      "This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.",
    file: `${repo}/srd52/ATTRIBUTION.md`,
    path: "core/src/dmbot/rules/data/srd52/ATTRIBUTION.md",
  },
  {
    name: "System Reference Document 5.1",
    statement:
      "This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.",
    file: `${repo}/srd51/ATTRIBUTION.md`,
    path: "core/src/dmbot/rules/data/srd51/ATTRIBUTION.md",
  },
];

export const whatDmbotDid =
  "DMbot took the text out of the PDFs, mended the words the PDF had cut, and split it into entries. The changes are listed in the files linked below.";

export const creditsNotAffiliated = notAffiliated;
