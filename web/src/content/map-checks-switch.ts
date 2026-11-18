// The website's Map checks switch on a campaign's Settings (#4687). Off until core's half (migration 0538, #4730) is
// deployed and this is flipped in the same deploy as `DMBOT_MAP_MEASURE`, never before (Supervisor's ruling on #4687).
// Flipped with docs/GO_LIVE.md.
export const MAP_CHECKS_LIVE = false;
