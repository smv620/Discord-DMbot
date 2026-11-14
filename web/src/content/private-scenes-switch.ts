// The switch of "Private scenes" on the Names page (#4102, the web half of #2996 rule 5): the times two player characters were alone
// together while the party was split, for the DM, with Not private. Off until the website server has the doors (core migration 0439,
// `dmbot_web_private_scenes` and `dmbot_web_private_scene_not_private`) and the bot finds scenes (`DMBOT_PRIVATE_SCENES`), and dev1
// has checked a sample session. Flip it with the What's new entry "See the private scenes" (docs/GO_LIVE.md).
export const PRIVATE_SCENES_LIVE = false;
