// The words of the PDF way of Upload lore (#4762, behind LORE_PDF_LIVE): sent, counted without any AI, then read after the DM's yes.
// In a file of its own, so the pages that only need the lore words do not carry them (the page-weight budget, #1947).
export const lorePdfWords = {
  label: "Choose a PDF",
  help: "A PDF of up to 50 MB. DMbot counts its pages and pictures first and tells you the cost. It reads none of the words until you say yes. A PDF you don't read is deleted within a day.",
  sending: "Sending the PDF…",
  counting: "Counting its pages and pictures…",
  counted: (pages: number, pictures: number, cost: string | null): string =>
    `That's ${pages} ${pages === 1 ? "page" : "pages"}${pictures > 0 ? ` and ${pictures} ${pictures === 1 ? "picture" : "pictures"}` : ""}.` +
    (cost === null ? " DMbot can't tell the cost yet." : ` ${cost}`) +
    ' Answer the questions below, tick the box, then choose "Read this PDF".',
  rights: "I have the right to use this PDF, and its pictures.",
  rightsNeeded: "Check the box to confirm you have the right to use this PDF.",
  dropped: "Okay. DMbot deleted the PDF and read nothing.",
  slow: "This is taking longer than usual. You can choose the PDF again.",
  rightsNote: "DMbot doesn't check this for you. It uses the PDF and its pictures for this campaign only, and keeps the names and connections it finds, not the PDF.",
  read: "Read this PDF",
  never: "Never mind",
  gone: "That file expired before DMbot could count it. Choose it again.",
  problems: {
    "bad-file": "That doesn't look like a PDF, or it's empty or over 50 MB. Choose another.",
    "uploads-off": "PDF uploads aren't open yet.",
    "not-counted": "DMbot hasn't counted that PDF yet. Choose it again.",
    "not-found": "That PDF isn't waiting any more. Choose it again.",
    "slow-down": "DMbot is already reading something for this campaign. Here is where it is.",
    "too-soon": "That's five uploads in the last hour. You can try again soon.",
  } as Record<string, string>,
};
