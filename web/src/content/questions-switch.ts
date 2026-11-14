// The switch of the Questions page (#3438): every question DMbot waits on, in one place, with the same words as
// Discord's "❓ Questions for you" (#3435). Switched off until the bot writes the counts the page reads
// (`question_counts`, CloudDev's #3435) and the website server is deployed with the matching door; the bot must go
// first. Not flipped until names to check, connections, "Which one?" and readings can be answered on the page
// (Supervisor's ruling on #3438). Flip it with the What's new entry "Questions for you on the website" (docs/GO_LIVE.md).
export const QUESTIONS_LIVE = false;
