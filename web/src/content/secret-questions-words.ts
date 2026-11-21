// The words of the 🤫 Known or secret? question on the Questions page (#4598). The question and what each answer does come from the
// API (the same words as Discord's card, `secret_question_words.py`); the page only words what it shows around them. Plain words;
// a name or a quote is only ever text on the page.
export const secretWords = {
  heading: "🤫 Known or secret?",
  loading: "Loading…",
  failed: "We couldn't load these. Reload the page to try again.",
  why: "DMbot never changes what your players know without asking you.",
  waiting: (n: number): string => `${n} waiting`,
  empty: "Nothing left to answer. If DMbot finds more, they'll show up here.",
  saving: "Saving your answer",
  stillWorking: "DMbot is still working on your answer. Reload in a minute to see where it stands.",
  notAllowed: "Only a DM of the campaign can answer this.",
  couldNot: "DMbot couldn't change that, so it was left as it is. Showing what is left.",
  // (#4908: answered, or removed when its speaker stopped being recorded or its transcript was
  // deleted; true of both)
  gone: "This question isn't waiting any more. Showing what is left.",
  tooLate: "It's too late to undo that one here. Showing what is left.",
  undo: "Undo",
  undone: "Undone. The question is back, so you can answer it again.",
} as const;
