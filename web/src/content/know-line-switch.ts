// The switch of the "who knows it" line on the website (#5332): the one line Discord puts under a pick (🌍 Everyone knows, ✅ The whole
// party knows, 👥 Known to: …, 🤫 No one in the party knows), shown as the DM ticks characters in "Some know", and a connection's
// knowers grouped by the party, those who left and others. Off until core #5338 (`POST …/know-line`, `status` on `knownBy`) is
// deployed; flipped with docs/GO_LIVE.md, "KNOW_LINE_LIVE".
export const KNOW_LINE_LIVE = false;
