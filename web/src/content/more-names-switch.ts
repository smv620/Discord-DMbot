// The switch of "📝 More names" on the Questions page (#5081, the web half of #5055 B): turns the names an imported
// session's scan left waiting (past its first 10) into names to check, as Discord's button does. Off until core's route
// (`POST /me/campaigns/{ref}/more-names`, CloudDev's PR for #5081) is deployed, bot first, and seen to work. It moves
// with the What's new entry "More names from imports" and docs/GO_LIVE.md. No server setting is needed.
export const MORE_NAMES_LIVE = false;
