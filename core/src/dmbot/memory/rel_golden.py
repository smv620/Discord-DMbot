"""The golden set for relationship typing (#3244): invented one-sentence descriptions, each with
the type it must land on, so dev1's measured run (real embeddings) can report right, wrong and weak
matches as counts. Every `not_this` line of every type has at least one case that must land on the
type the line names (the owner's rule 9), and the two boundary rules (an epithet is another name;
a shared surname alone is not kin) have their own. Names are invented. Pure data.

`expected` is a type key, `NONE` (the match must not return it: a place, a companion or a tie
that is left unlinked), or `ALIAS` (another name for the person, not a tie).

Scoring (dev1's measured run): a case with a type key is right when the match lands on it. `NONE`
is right when the match returns no type or a weak one (never `kin_of` for a shared surname, never
`knows` for a place); `ALIAS` is right when no tie is made and the epithet goes to the "another
name" path. The plain cases are fresh sentences, not the embedded examples, so the right-rate is
not inflated.
"""

from __future__ import annotations

from dataclasses import dataclass

NONE = "none"
ALIAS = "alias"


@dataclass(frozen=True, slots=True)
class Case:
    description: str
    expected: str
    covers: (
        tuple[str, int] | str
    )  # (type key, index of the `not_this` line), "note6"/"note7", or ""


def c(description: str, expected: str, covers: tuple[str, int] | str = "") -> Case:
    return Case(description, expected, covers)


CASES: tuple[Case, ...] = (
    # --- kin_of's not_this
    c(
        "An elder calls the young adventurer 'my child': a form of address, not a family tie.",
        "term_of_address",
        ("kin_of", 0),
    ),
    c(
        "The sergeant calls every soldier 'son' as a way of speaking, with no family behind it.",
        "term_of_address",
        ("kin_of", 0),
    ),
    c(
        "Pell and Orrin both belong to the noble house of the grey owl; they are not related.",
        "member_of",
        ("kin_of", 1),
    ),
    c(
        "Dain is Kaelen's closest friend, whom Kaelen has always called his sister.",
        "knows",
        ("kin_of", 2),
    ),
    c(
        "Nora is a nun of the Order of the Dawn and wears its habit.",
        "member_of",
        ("kin_of", 3),
    ),
    # --- knows's not_this
    c(
        "Ilsa works for Lord Perrin as his steward and answers to him.",
        "serves",
        ("knows", 0),
    ),
    c(
        "Dorn and Ilse both want to be named the master's heir and compete for it.",
        "rival_of",
        ("knows", 1),
    ),
    c("Rolo is Tavin's father and raised him.", "kin_of", ("knows", 2)),
    # --- rival_of's not_this
    c(
        "Vorn wants the heroes dead and hunts them across the valley.",
        "knows",
        ("rival_of", 0),
    ),
    c("Brin simply cannot stand Kell and avoids him at the inn.", "knows", ("rival_of", 1)),
    # --- serves's not_this
    c(
        "Cerra swore an oath of fealty to the king before the whole court.",
        "sworn_to",
        ("serves", 0),
    ),
    c(
        "Master Kael trains young Tavin in swordplay.",
        "mentors",
        ("serves", 1),
    ),
    c(
        "Wick sails with the Gull crew as a plain deckhand, with no say in anything.",
        "member_of",
        ("serves", 2),
    ),
    c(
        "Brog is a clansman of the Deepforge tribe, a common member with no rank.",
        "member_of",
        ("serves", 3),
    ),
    c(
        "The merchant bought a slave at the market and keeps him in chains.",
        "held_captive_by",
        ("serves", 4),
    ),
    c(
        "The prisoner is guarded day and night by the warden and cannot leave.",
        "held_captive_by",
        ("serves", 5),
    ),
    c(
        "The priestess is in lifelong servitude to the sun god and has given up everything to it.",
        "sworn_to",
        ("serves", 6),
    ),
    c(
        "The cleric is bound in servitude to the goddess of the harvest and answers to her alone.",
        "sworn_to",
        ("serves", 7),
    ),
    # --- sworn_to's not_this
    c(
        "Joram clerks for the merchant guild for pay, and swore nothing to anyone.",
        "serves",
        ("sworn_to", 0),
    ),
    c(
        "Hale said in passing that he would look after the dog.",
        "knows",
        ("sworn_to", 1),
    ),
    c(
        "The bandit chief accepted the quest from the old queen and now takes her orders.",
        "serves",
        ("sworn_to", 2),
    ),
    # --- mentors's not_this
    c(
        "The foreman gives the workers their orders and pays them every week.",
        "serves",
        ("mentors", 0),
    ),
    c(
        "Odo is Berrin's father.",
        "kin_of",
        ("mentors", 1),
    ),
    c(
        "Odo also taught Berrin to track deer.",
        "mentors",
        ("mentors", 1),
    ),
    c(
        "The master chained the slave in the cellar as a lesson and kept him locked up.",
        "held_captive_by",
        ("mentors", 2),
    ),
    # --- leads's not_this
    c(
        "The scout takes orders directly from the captain and reports to him.",
        "serves",
        ("leads", 0),
    ),
    c(
        "Haldor is one rank-and-file member of the lantern brotherhood.",
        "member_of",
        ("leads", 1),
    ),
    c(
        "The professor teaches the arcane arts at the academy.",
        "mentors",
        ("leads", 2),
    ),
    c(
        "The old astronomer teaches a single gifted pupil in his observatory.",
        "mentors",
        ("leads", 3),
    ),
    # --- member_of's not_this
    c(
        "Warden Ilka commands the harbour company and speaks for it at the council.",
        "leads",
        ("member_of", 0),
    ),
    c(
        "Hob works for Lady Corra alone and is no part of any group.",
        "serves",
        ("member_of", 1),
    ),
    c("Brin and Hale are husband and wife.", "kin_of", ("member_of", 2)),
    c(
        "The two cousins are both of the house of the grey owl, blood of one family.",
        "kin_of",
        ("member_of", 3),
    ),
    c(
        "Lyra and Valen are blood relations who live in the same noble house.",
        "kin_of",
        ("member_of", 4),
    ),
    # --- held_captive_by's not_this
    c(
        "Tor spent one night in the town cells after a brawl, as the law says.",
        NONE,
        ("held_captive_by", 0),
    ),
    c(
        "The hired guard watches the prisoners for pay, and is paid by the baron.",
        "serves",
        ("held_captive_by", 1),
    ),
    # --- owns's not_this
    c(
        "Mira has Heartseeker in her hands right now and is aiming it.",
        "carries",
        ("owns", 0),
    ),
    c(
        "The sword is lying under the bed in the inn at Eastgate.",
        NONE,
        ("owns", 1),
    ),
    c(
        "The scribe is bound to his master and does what he is told.",
        "serves",
        ("owns", 2),
    ),
    c(
        "The scribe is bound to his master and locked in a room against his will.",
        "held_captive_by",
        ("owns", 2),
    ),
    c(
        "The wolf travels with the ranger everywhere and sleeps by her fire.",
        NONE,
        ("owns", 3),
    ),
    # --- carries's not_this
    c(
        "Dessa owns a silver circlet, though it sits in a vault in her home city.",
        "owns",
        ("carries", 0),
    ),
    c(
        "The map is lying on the table in the library.",
        NONE,
        ("carries", 1),
    ),
    # --- term_of_address's not_this
    c(
        "Tavin's real mother, Shen, called him 'my child' on the day he left.",
        "kin_of",
        ("term_of_address", 0),
    ),
    c(
        "Kell and Tavin are old friends who greet each other as 'friend'.",
        "knows",
        ("term_of_address", 1),
    ),
    # --- rule 6: epithets are another name
    c("The people call Cerra 'Breaker of Dragons' after the battle on the pass.", ALIAS, "note6"),
    c("He is known across the land as 'Wielder of Justice'.", ALIAS, "note6"),
    # --- rule 7: a surname alone is not kin
    c(
        "Tom Smith the baker and Anna Smith the blacksmith share a trade name and never met.",
        NONE,
        "note7",
    ),
    c(
        "Harl Cooper and Wen Cooper are two unrelated barrel makers with the same trade surname.",
        NONE,
        "note7",
    ),
    c(
        "Tom and Joss are brothers, sons of the same mother.",
        "kin_of",
        "note7",
    ),
    # --- friendly rivals are still rivals (a feeling is a separate knows tie), and groups
    c(
        "Two friendly rival bards compete for the festival crown and toast each other after.",
        "rival_of",
    ),
    c("The weavers' guild and the dyers' guild are on cordial terms and trade often.", "knows"),
    # --- found on the owner's re-upload (#3238): a hire is no service to the target's master, and a
    # heated debate is not a competition
    c(
        "A retired bounty hunter offers the travellers a contract to eliminate Garn, the bodyguard "
        "of the caravan master Orsa.",
        NONE,
        "found3238",
    ),
    c(
        "Hob and Rask had a heated debate over smelting methods in the tavern.",
        "knows",
        "found3238",
    ),
    # --- plain cases, one for each type
    c("Wen married Joss in the spring, and the two now share one roof.", "kin_of"),
    c("Ossa and Pell met years ago and are still fond of one another.", "knows"),
    c(
        "The salt merchants and the ferrymen have been fighting over the harbour for years.",
        "knows",
    ),
    c("Two bakeries on one street both try to win the baron's wedding order.", "rival_of"),
    c("The night watchmen take their orders from the sergeant of the gate.", "serves"),
    c("Ensign Rusk gave a sacred promise before the altar to guard the pass.", "sworn_to"),
    c("Old Hessa showed the girl Fenn how to set bones and brew salves.", "mentors"),
    c("Lord Thane governs the eastern march and speaks for its people.", "leads"),
    c("Lio signed up with the river rangers at harvest time.", "member_of"),
    c("The smugglers seized the ferryman's son and shut him in a hold.", "held_captive_by"),
    c("Marta holds the deed to the brewery and the barn beside it.", "owns"),
    c("The courier has a wax-sealed packet strapped to her chest.", "carries"),
    c("Evening to you, madam, will you be wanting a room?", "term_of_address"),
    c("Maren has raised the orphan Tess as her legal ward since the fever.", "kin_of"),
)
