"""The relationship types a tie is matched to, as the owner wrote them (2026-11-02, #3244).

Each type has what it *is* (embedded, with its examples), eight invented examples (embedded) and
what it is *not* (`not_this`: never embedded; it tells a reviewer and the golden set where a near
miss belongs). The text is the owner's, word for word: change it only when the owner does. The
names in it are invented; none is from a published setting or from any table.

`ally_of` and `enemy_of` are not here on purpose: since #3175 an alliance or enmity is a `knows`
tie with a stance word, and old rows are still read as they are today. `located_in` and
`travels_with` appear in some `not_this` lines only to steer the AI's own pick; the match never
returns them (places and companions are outside stage 1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RelType:
    key: str
    gloss: str
    is_text: str  # what the type is: embedded
    examples: tuple[str, ...]  # eight, invented: embedded with `is_text`
    not_this: tuple[str, ...]  # "what it is not -> where it goes": NEVER embedded

    @property
    def stored(self) -> bool:
        """Whether a tie of this type is kept. A form of address is only counted."""
        return self.key != TERM_OF_ADDRESS


TERM_OF_ADDRESS = "term_of_address"


TYPES: tuple[RelType, ...] = (
    RelType(
        "kin_of",
        "family",
        is_text=(
            "Two individuals tied by family: blood, marriage, adoption or by a guardianship of an "
            "underaged ward. It covers a parent and a child (mother, father, son, daughter, "
            "prince, princess, heir, heiress), brothers and sisters (siblings, twins, "
            "half-siblings, step-siblings), husband and wife (spouse, partner in marriage, "
            "widow), grandparents and grandchildren, aunts, uncles, nieces, nephews, cousins and "
            "in-laws, and generational lineage. Groups can be tied by bloodlines as a group of "
            "cousins especially in clans or tribes. At the table it shows up as a backstory line, "
            '"my father was a smith", a family quarrel, an inheritance, a lost sibling; in lore '
            "as a family tree or a house's line. The tie is a fact about who they are to each "
            "other and that tie remains true when they are apart or estranged. The detail says "
            "which: parent, child, sibling, spouse, grandparent, aunt or uncle, cousin. It also "
            "covers lineage both common and of royal lines, a family name the text treats as "
            "family, royalty of the same house, and shared known ancestry."
        ),
        examples=(
            "Tavin's father Rolo taught him to hunt.",
            "Vela is Tavin's younger sister.",
            "Quill is Rolo's brother, so Tavin's uncle.",
            "Brin and her husband Hale run the mill together.",
            "Old Toris is the guardian to his young ward, Pip.",
            "Prince Valen is the son of Kaelen, son of Morvan, of the high royal line.",
            (
                "Brog and Dain of the Deepforge clan call each other cousins by right "
                "of their shared "
                "ancestry."
            ),
            "Princess Lyra is the recognized heir to the sacred throne of Queen Ysolde.",
        ),
        not_this=(
            (
                'a title or nickname used to address someone ("my child", "brother", '
                '"Father") with '
                "no real tie → term_of_address"
            ),
            "a member of the same noble house with no family tie → member_of",
            "a close friend called a sister → knows",
            "a holy order or clergy title → member_of",
        ),
    ),
    RelType(
        "knows",
        "acquaintance, feeling, alliance, and enmity",
        is_text=(
            "individuals or groups who know or are aware of each other with only informal ties: "
            "they know of each other, have met, or share a history. The relationship has a tone "
            "or alignment such as: sworn enemy, hostile, at war, allies, distrusts, wary, "
            "neutral, trusts, friendly, close, devoted, partners in a plan, or companions in a "
            "cause. It covers the feeling individuals or groups have towards another regardless "
            "if positive or negative such as friends, old companions, lover and sweethearts, "
            "sworn enemies, faction allies; who dislike, fear, resent or distrust each other; who "
            "owe each other a debt or a favour, an old flame; who looks up to another, or loosely "
            'joined in a common cause or fighting each other. At the table it comes out as "they '
            'have been friends since the road", "she cannot stand him", "the river guild and the '
            'stormguards hold the bridge together", "Vorn the lich wants the heroes dead", "they '
            'were once lovers". It is the default for two individuals or groups when no other '
            "structural kind fits, and the feeling or stance label carries the sentiment. It is "
            "also used when the individuals or groups have only awareness of each other with "
            "neutral sentiment."
        ),
        examples=(
            "Kell and Tavin have been friends since the road.",
            "The Iron Court is at war with the river clans.",
            "The river guild and the lamplighters hold the bridge together.",
            "Ilse distrusts the innkeeper.",
            "Master Vorn wants the wanderers off his land.",
            "Rowan owes a debt of gratitude to Old Toris.",
            "Corin looks up to Lady Ysolde as an inspiration.",
            "Kaelen and Maron were once sweethearts.",
        ),
        not_this=(
            "working for someone → serves",
            "competing for the same prize → rival_of",
            "a real family tie → kin_of",
        ),
    ),
    RelType(
        "rival_of",
        "competing",
        is_text=(
            "Two individuals or groups competing for the same specific prize, place, target, or "
            "standing. It is purely about the structure of competing for a single shared goal: "
            "rival apprentices, rival merchants, rival guilds, two inns after the same "
            "travellers, two heirs for one seat, two bards at the same festival. Rivals can be "
            "friendly or unfriendly; it is the fact that they are competing for a common award, "
            "status, or items that makes them rivals. Words used: rivals, competing, contest, "
            "one-upping, outdo, vie for, race. It shows up as a contest at the table, a trade "
            "war, competing for the affection of the same love interest; or a rivalry between "
            "students for class rank, and describes the competitive alignment regardless of "
            "personal feelings friendly or unfriendly. There must be a common reward they both "
            "desire and compete for, viewed with a scarcity mindset."
        ),
        examples=(
            "Dorn and Ilse compete to be the master's heir.",
            "The two inns fight over the same travellers.",
            "Her rival apprentice sabotaged the experiment.",
            "The Silver Quill and the Red Press race to print the news first.",
            "Two veteran smiths vie for the royal commission.",
            "The two captains race to claim the uncharted island.",
            "The guild duelists outdo each other at every festival.",
            "Brother Tomas and Sister Nora compete for the bishop's favor.",
        ),
        not_this=(
            "they want the other harmed → knows",
            "mere dislike → knows",
        ),
    ),
    RelType(
        "serves",
        "works for or answers to",
        is_text=(
            "An individual or group works for, answers to or takes orders from another: a servant "
            "to a lord, a soldier to a captain, an agent to a patron, a hireling to the one who "
            "pays, a cultist to a cult leader, a clerk to a guild, a bodyguard to the one they "
            "guard. Words used: serves, serving, works for, answers to, in the pay of, employed "
            "by, follows orders from, retainer, hireling, steward, agent of. It shows up in who "
            "gives the orders, who pays, who an individual or group reports to, and it may be "
            "paid, commanded or devout, but it is a duty rather than a promise sworn."
        ),
        examples=(
            "Mira works for Lord Aldric as his steward.",
            "The guards answer to the captain.",
            "The hired swords follow whoever pays the most.",
            "The cultist serves the proclaimed leader without question.",
            "Joram serves as the head clerk for the merchant guild.",
            "The royal scout takes orders directly from the chancellor.",
            "A faithful acolyte serves the high priestess.",
            "The manor house groom tends the horses for Sir Valen.",
        ),
        not_this=(
            "a formal vow → sworn_to",
            "they teach or train each other → mentors",
            "belonging to a group without orders → member_of",
            "belonging to a group or tribe without rank → member_of",
            "a slave to a master → held_captive_by",
            "a prisoner to a guard → held_captive_by",
            "in servitude to a deity → sworn_to",
            "what god do you serve → sworn_to",
        ),
    ),
    RelType(
        "sworn_to",
        "bound by oath",
        is_text=(
            "A formal promise, magical pact, or oath that binds an individual or group to "
            "another, a cause, a god, a law or a duty: a covenant of fealty to a lord, a "
            "paladin's oath, a pledge to a cause, a blood oath, a promise made on holy ground, a "
            "warlock's pact, a vow of vengeance or loyalty. Words used: swore, sworn, vowed, "
            "oath, pledged, bound by oath, took the vow, gave their word before witnesses. It "
            "shows up as a scene of swearing, an ancient binding that still holds, a vow "
            "remembered at a hard moment, and the oath is the tie, whether or not the person or "
            "group also works for them. A warlock in service to a Patron or other worldly being "
            "of power."
        ),
        examples=(
            "Cerra swore the oath of vengeance over her brother's grave.",
            "He is sworn to the king and will not break it.",
            "The knights pledged themselves to the old cause.",
            "She bound herself to the order with a blood oath.",
            "Captain Vane took a solemn vow to defend the wall.",
            "The paladin pledged his sword to the sun god.",
            "They swore an oath of secrecy before the elder council.",
            (
                "My Patron finally spoke back and gave me the power to burn down "
                "everything that ever "
                "hurt me."
            ),
        ),
        not_this=(
            "only working for someone without a vow → serves",
            "a promise made in passing → knows",
            "a quest accepted → serves",
        ),
    ),
    RelType(
        "mentors",
        "teaches and trains",
        is_text=(
            "One person or group teaches, trains or guides the other: a master and an apprentice, "
            "a tutor and pupil, a mentor and protégé, a coach, a sergeant who trains recruits, a "
            "priest who teaches a novice. Words used: taught, trained, mentor, master, "
            "apprentice, pupil, student, Sensei, tutor, instructor, tutelage, teach, schooled, "
            'took under their wing. It shows up in backstory ("she learned healing from Brother '
            'Aldric"), a training scene or a lineage of teachers, and the teaching is the tie '
            "even when other ties exist as well."
        ),
        examples=(
            "Doran trains Ilsa as his apprentice.",
            "Brother Aldric taught Mira healing.",
            "The old sergeant drilled every new recruit in the yard.",
            "Her master sent her to the northern school at twelve.",
            "Master Kael mentored young Tavin in the art of swordplay.",
            "The archmage tutors a promising novice in arcane studies.",
            "A veteran tracker schooled the ranger in woodcraft.",
            "The guildmaster guides her protégé through trade negotiations.",
        ),
        not_this=(
            "a boss and worker → serves",
            "a father who also taught → both kin_of and mentors (two ties)",
            "a slave being punished as a lesson from their master → held_captive_by",
        ),
    ),
    RelType(
        "leads",
        "in charge of",
        is_text=(
            "One person or group commands, heads or rules another person or group: a captain of a "
            "company, a chief and a tribe, a guildmaster and a guild, a queen and a realm, the "
            "head of a household, a party's leader, a priest in charge of a temple. Words used: "
            "leads, leader, king, queen, emperor, chieftain, commands, heads, runs, rules, "
            "general, captain of, chief of, master of, in charge of. It shows up as who gives the "
            "orders for a group, who speaks for it, who is chosen or deposed, and it describes "
            "the position over a group rather than a single servant."
        ),
        examples=(
            "Captain Vale leads the Silver Lanterns.",
            "The elder heads the village council.",
            "Old Brin runs the dockside guild.",
            "Queen Ysolde rules the northern realm.",
            "Chief Harlan leads the mountain tribe into battle.",
            "High Priestess Veda commands the sun temple.",
            "Matron Vanya heads the noble house of the owl.",
            "Master Corin leads the expedition into the ruins.",
        ),
        not_this=(
            "an individual who answers to an individual → serves",
            "ordinary membership → member_of",
            "a teacher or professor → mentors",
            "an individual who instructs a single individual → mentors",
        ),
    ),
    RelType(
        "member_of",
        "belongs to a group",
        is_text=(
            "An individual or group belongs to a larger group: a guild, an order, a faction, a "
            "household, a party, a crew, a congregation, a school. Words used: member of, belongs "
            "to, joined, part of, in the company of, one of the, enrolled in. It shows up in who "
            "is in which faction, who joined or left, who wears which badge and which house an "
            "individual or group belongs to by birth or choice, and it says where someone belongs "
            "rather than what they do for the group."
        ),
        examples=(
            "Cerra is a member of the Lantern Guild.",
            "Pip is in the Ash Wolf crew.",
            "Tavin joined the temple's choir last spring.",
            "Mira belongs to the house of the grey owl.",
            "Brog joined the ironworkers' fellowship three years ago.",
            "Sister Nora is enrolled in the scholar's circle.",
            "Dorn belongs to the high mountain garrison.",
            "Kaelen is one of the inner circle of the dragon cult.",
        ),
        not_this=(
            "leader of a group → leads",
            "working for one person → serves",
            "a family tie → kin_of",
            "a member of the same family → kin_of",
            "blood relations in the same house → kin_of",
        ),
    ),
    RelType(
        "held_captive_by",
        "kept against their will",
        is_text=(
            "A person or group is held against their will by another person or group: a prisoner, "
            "a hostage, someone locked in a cell, chained in a cellar, a kidnapped child, a "
            "captive of bandits or a cult, a person kept under watch who cannot leave. Words "
            "used: captive, prisoner, slave, hostage, kidnapped, locked up, imprisoned, held by, "
            "in chains, kept in the cellar. It shows up as a rescue quest, a ransom demand, an "
            "escape or a dungeon scene, and it is an ongoing state of being held."
        ),
        examples=(
            "Tor is held by the circus master.",
            "The cultists keep the hostage in the cellar.",
            "The bandits took the miller's daughter for ransom.",
            "He spent three years in the baron's dungeon.",
            "Lord Vane holds the rival diplomat captive in the tower.",
            "The sea rovers locked the merchant in the brig.",
            "A young squire is held hostage until the treaty is signed.",
            "The goblins chained the lost miner in the cavern.",
        ),
        not_this=(
            (
                "a lawful sentence the story treats as ordinary, such as a short night "
                "in the cells → "
                "leave unlinked"
            ),
            "working for pay → serves",
        ),
    ),
    RelType(
        "owns",
        "belongs to them",
        is_text=(
            "A thing is theirs: an item, a weapon, a house, a ship, land, an attuned magic item, "
            "a warlock's pact weapon, a shop, livestock or a beast kept as property, owned by an "
            "individual or a group, whoever has control over it. Words used: owns, belongs to, "
            "hers, his, the owner of, heirloom, inherited, bought, property of, attuned to, "
            "soulbound. It shows up as who a named blade belongs to, who holds the deed to a "
            "tower, whose ship it is, who inherited what, and it says the thing is theirs to keep "
            "or sell."
        ),
        examples=(
            "Cerra owns the Sunbrand sword, left by her father.",
            "The Lantern Guild owns the Silver Tower.",
            "Old Brin owns the ferry Gull and the dock.",
            "The ring is an heirloom of the house of the grey owl.",
            "Lord Aldric owns the vast timber lands south of the river.",
            "The wizard owns the Brass Grimoire of Aldmoor.",
            "Master Joram bought the apothecary shop in town.",
            "Vela owns Stormhoof, a prize warhorse.",
        ),
        not_this=(
            "who has it in hand right now → carries",
            "where it is lying → located_in",
            "a person bound to a master → serves, or held_captive_by if held against their will",
            "a familiar or animal companion that travels with them → travels_with (#3176)",
        ),
    ),
    RelType(
        "carries",
        "has it in hand",
        is_text=(
            "A person has an item with them right now, in hand or on their body or in their pack: "
            "carrying a weapon, wearing a cloak, holding a key, bearing a banner, wielding a "
            "staff, having a letter in their pocket. Words used: carries, wields, wears, holds, "
            'has on them, bears, in their pack. It shows up in a fight ("she draws Heartseeker"), '
            "in a search, in who is holding the stolen thing at this moment, and it may differ "
            "from who owns it."
        ),
        examples=(
            "Mira carries Heartseeker.",
            "Tavin wears the charged cloak.",
            "The messenger bears the Sealed Letter of Orrin Dace.",
            "Dorn wields his father's axe.",
            "Captain Vale carries the emblem of the silver watch.",
            "The scholar has the ancient map hidden in his pack.",
            "Brother Tomas carries the Holy Symbol of Lathander.",
            "The thief carries the Keyring of the Grey Vault at her belt.",
        ),
        not_this=(
            "the item is theirs but elsewhere → owns",
            "the item is lying in a place → located_in",
        ),
    ),
    RelType(
        "term_of_address",
        "a form of address, not a tie (counted, not stored)",
        is_text=(
            "A petname, nickname or figure of speech used to speak to someone, with no "
            'relationship behind it: an elder saying "my child" to a young adventurer, a soldier '
            'calling a comrade "brother", a villager calling a priest "Father", an abbess '
            'addressed as "Mother", a noble greeting a stranger as "good sir", a barkeep or '
            'shopkeeper calling every patron "friend". Words used: my child, brother, sister, '
            "father, mother, my friend, sir, lady, boy, lass, as a way of speaking. It shows up "
            "in dialogue and tells us about manners, rank and tone, never about who the two are "
            "to each other."
        ),
        examples=(
            "You are bold, my child, and some would say foolish.",
            "Brother, pass me the rope.",
            "Thank you, Father, for the blessing.",
            "Welcome, friend, take a seat by the fire.",
            "Good day, sir, what brings you to our village?",
            "Stand back, lass, this bridge is unsafe.",
            "My lady, the carriage is ready for your departure.",
            "Rest easy, young boy, the danger has passed.",
        ),
        not_this=(
            "a real family tie that shares the word → kin_of",
            "a real friendship → knows",
        ),
    ),
)


BY_KEY = {t.key: t for t in TYPES}
