// The switch for QAbot's privacy section (#1356). QAbot answers posts in the public "Q&A for DMbot"
// on GitHub (docs/PLAN.md, "QAbot and support tickets"). The privacy page must never describe a
// helper that isn't running, so the section stays out of the page until it is.
//
// HOW IT TURNS ON: when the owner has installed the workflow and added the key (the steps on
// #1341) and QAbot has answered a real post, change QABOT_LIVE to true. In the same change:
//   - move "Ask a question in public" out of `held` in whats-new.ts and change the Help sentence
//     "may answer first" (#1353);
//   - update the two privacy lines the new section makes incomplete: the GitHub line (it names
//     only the Say hello page) and the Anthropic line (it names only names, rules and story notes);
//   - update qabot-privacy.test.ts: the "switched off today" assertion, and the What's new
//     coupling, fail on purpose until the above is done.
export const QABOT_LIVE = false;
