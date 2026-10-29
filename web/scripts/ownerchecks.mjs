// Ties the held What's new entries to the owner's checks in docs/OWNER_CHECKS.md (#1894), so no
// entry waits on a check nobody asks for, and no check stays after its entry went live.
// Pure functions: the vitest reads the two files and hands the text in.

// A held reason that waits on the owner seeing the feature work. Reasons that wait on something
// else (a deploy alone, the story extractor #1282, an ElevenLabs account, Stripe, QAbot, the
// billing link) are skipped: no check from the owner can unlock them.
const waitsOnOwner = /owner'?s live check|seen working|live-checked|answered a real question|has not yet checked/i;
const waitsOnSomethingElse = /story extractor|ElevenLabs|Stripe|QAbot|billing login/i;

// Reasons that wait on something no owner check can unlock. Every held entry must match one of the
// two patterns, so an entry worded some new way fails the test instead of being skipped silently.
const waitsOnOther = /deploy|story extractor|ElevenLabs|Stripe|QAbot|billing login|recap|payments stay off|measurement|owner's steps|installed/i;

const numbersIn = (text) => [...text.matchAll(/#(\d+)/g)].map((m) => m[1]);

/** The held entries (title and reason) that the owner's checks must cover. */
export function ownerWaiting(held) {
  return held.filter((h) => waitsOnOwner.test(h.reason) && !waitsOnSomethingElse.test(h.reason));
}

/** Held entries whose reason matches neither pattern: they need a person to decide which kind they are. */
export function unclassified(held) {
  return held.filter((h) => !waitsOnOwner.test(h.reason) && !waitsOnOther.test(h.reason));
}

/** Numbers named on `Unlocks:` lines, and numbers named in the "Not on this list" section. */
export function readChecks(doc) {
  const unlocks = new Set();
  for (const m of doc.matchAll(/^Unlocks:\s*(.+)$/gm)) numbersIn(m[1]).forEach((n) => unlocks.add(n));
  const parts = doc.split(/^## Not on this list.*$/m);
  if (parts.length !== 2) throw new Error('docs/OWNER_CHECKS.md must have exactly one "## Not on this list" heading.');
  return { unlocks, explained: new Set(numbersIn(parts[1])) };
}

/** Plain sentences for each mismatch; an empty list means the two files agree. */
export function ownerCheckProblems(held, doc) {
  const { unlocks, explained } = readChecks(doc);
  const problems = [];
  for (const h of ownerWaiting(held)) {
    const nums = numbersIn(h.reason);
    if (!nums.some((n) => unlocks.has(n) || explained.has(n))) {
      problems.push(
        `"${h.title}" waits for the owner's live check, but no check in docs/OWNER_CHECKS.md names ${nums.map((n) => "#" + n).join(" or ") || "its issue number"}. ` +
          `Add a check for it with an "Unlocks:" line, or say why it is not on the list under "Not on this list".`,
      );
    }
  }
  for (const h of unclassified(held)) {
    problems.push(
      `"${h.title}" is held, but its reason says neither that it waits for the owner's check nor what else it waits for. ` +
        `Word it as "held until it has been seen working on the test server (#N)", or add its reason to waitsOnOther in web/scripts/ownerchecks.mjs.`,
    );
  }
  const cited = new Set(held.flatMap((h) => numbersIn(h.reason)));
  for (const n of explained) {
    if (!cited.has(n)) {
      problems.push(
        `docs/OWNER_CHECKS.md says under "Not on this list" that #${n} is not checked, but no held What's new entry cites #${n} any more. ` +
          `Take #${n} off that list.`,
      );
    }
  }
  for (const n of unlocks) {
    if (!cited.has(n)) {
      problems.push(
        `docs/OWNER_CHECKS.md has a check that unlocks #${n}, but no held What's new entry cites #${n}. ` +
          `The entry went live (or the number is wrong): take the number off the "Unlocks:" line, and the check if it has none left.`,
      );
    }
  }
  return problems;
}
