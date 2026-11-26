// The switch of the half-second counts read on the lore upload and import pages (#3534 part 3; core #5286, migration 0630): while a
// job runs and its tab is visible, the page reads the job's counts twice a second instead of waiting for the next three-second look.
// Off until core's door is deployed and dev1 has measured what one read costs on the shared server (Supervisor's ruling on #3534).
// Flip it with the What's new entry "Live counts while DMbot reads" (docs/GO_LIVE.md).
export const WAIT_COUNTS_LIVE = false;
