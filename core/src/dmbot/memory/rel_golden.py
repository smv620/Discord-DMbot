"""The golden set for relationship typing (#3244): invented one-sentence descriptions, each with
the type it must land on, so dev1's measured run (real embeddings) can report right, wrong and weak
matches as counts. Every `not_this` line of every type has at least one case that must land on the
type the line names (the owner's rule 9), and the two boundary rules (an epithet is another name;
a shared surname alone is not kin) have their own. Names are invented. Pure data.

`expected` is a type key, `NONE` (the match must not return it: a place, a companion or a tie
that is left unlinked), or `ALIAS` (another name for the person, not a tie).
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
        "Sister Nora is a nun of the Order of the Dawn, and the villagers call her Mother Nora.",
        "member_of",
        ("kin_of", 3),
    ),
    # --- knows's not_this
    c(
        "Mira works for Lord Aldric as his steward and answers to him.",
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
        "Pip is one of the Ash Wolf crew and has no orders to follow.",
        "member_of",
        ("serves", 2),
    ),
    c(
        "Brog is a clansman of the Deepforge tribe, a common member with no rank.",
        "member_of",
        ("serves", 3),
    ),
    c(
        "The merchant bought a slave from the market, and the slave belongs to him now.",
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
        "Which god does the cleric serve? She pledged herself to the goddess of the harvest.",
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
        "Hale said in passing that he would look after the dog, and Rowan thanked him.",
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
        "Rolo is Tavin's father.",
        "kin_of",
        ("mentors", 1),
    ),
    c(
        "Rolo also taught Tavin to hunt.",
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
        "Kaelen is one of the ordinary members of the dragon cult.",
        "member_of",
        ("leads", 1),
    ),
    c(
        "The professor teaches the arcane arts at the academy.",
        "mentors",
        ("leads", 2),
    ),
    c(
        "The archmage tutors one promising novice alone, in her tower.",
        "mentors",
        ("leads", 3),
    ),
    # --- member_of's not_this
    c(
        "Captain Vale leads the Silver Lanterns and speaks for them.",
        "leads",
        ("member_of", 0),
    ),
    c(
        "Joram works for Lord Aldric alone and is no part of any group.",
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
        "Mira has the longbow in her hands right now and is aiming it.",
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
        "Cerra owns the Sunbrand sword, though it hangs in her father's hall far away.",
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
        "The text says the Smith brothers, Tom and Joss, grew up in the same house.",
        "kin_of",
        "note7",
    ),
    # --- plain cases, one for each type
    c("Brin and her husband Hale run the mill together.", "kin_of"),
    c("Kell and Tavin have been friends since the road.", "knows"),
    c("The Iron Court is at war with the river clans.", "knows"),
    c("The two inns fight over the same travellers.", "rival_of"),
    c("The guards answer to the captain.", "serves"),
    c("Captain Vane took a solemn vow to defend the wall.", "sworn_to"),
    c("Brother Aldric taught Mira healing.", "mentors"),
    c("Queen Ysolde rules the northern realm.", "leads"),
    c("Tavin joined the temple's choir last spring.", "member_of"),
    c("The bandits took the miller's daughter for ransom.", "held_captive_by"),
    c("Old Brin owns the ferry and the dock.", "owns"),
    c("The messenger bears a sealed letter.", "carries"),
    c("Good day, sir, what brings you to our village?", "term_of_address"),
    c("Old Toris is the guardian of his young ward, Pip.", "kin_of"),
)
