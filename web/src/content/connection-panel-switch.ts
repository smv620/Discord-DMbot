// The switch of the Story Graph's redesigned connection panel (#4155, owner request 2026-11-11): a compact list of the connections
// between two names with the direction as an arrow, one line opened at a time, edits staged until Save, a dot where something is
// unsaved, and a warning before leaving with changes pending. In a file of its own so the pages only ask "is it on?". Off until
// the whole redesign (Ctrl+Click, the jump from a name, the API's pair words and direction toggle) is built and checked
// (docs/GO_LIVE.md); off, the old panel is shown as it was.
export const CONNECTION_PANEL_LIVE = false;
