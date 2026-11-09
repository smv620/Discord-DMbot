"""The story roles an entry can hold (#3172, owner-approved 2026-11-01). Pure.

A role says what an entry IS in the story (a quest giver, a mentor, a plot artifact); an entry may
hold several at once, and roles are for the DM only, like the importance rank: no player, card or
recap line shows one. They inform PlotBot's reading of importance and never add to a rank or set a
floor of their own (the one floor is the companion's, from `travels_with`, in `importance_rank`).

`SEEDED_ROLES` is the owner's list, with each role's description, examples and the role it is
easily confused with, copied word for word from Supervisor's comment on #3172 (2026-11-01). The
wording changes only after the owner or Supervisor rules. What a vector is made from is
`embed_text` (description plus examples, one text); `not_this` is never embedded, because a
negation pulls a vector toward what it denies: it goes only into the reading's prompt and the
tie-break. A campaign's own roles (added by the reading) carry the same three fields, checked by
`check_added`.

`commoner` is the default and is never stored: an entry with no other role reads as a commoner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    code: str
    group: str  # the heading it is listed under (Plot, Party, Story-telling, World, Things)
    description: str
    examples: tuple[str, ...]
    not_this: str  # the nearest role it could be confused with: never embedded

    @property
    def label(self) -> str:
        """The role in plain words ("quest giver")."""
        return self.code.replace("_", " ")

    @property
    def embed_text(self) -> str:
        """What is embedded: the description and its examples as one text, never `not_this`."""
        return embed_text(self.description, self.examples)


def embed_text(description: str, examples: tuple[str, ...]) -> str:
    """The one text a role is matched by (#3172): its description, then its examples. Never
    `not_this`: its words would pull the role toward the very one it must be told apart from."""
    return " ".join((description.strip(), *(e.strip() for e in examples)))


COMMONER = "commoner"  # the default, never stored

SEEDED_ROLES: tuple[Role, ...] = (
    Role(
        "quest_giver",
        "Plot",
        (
            "A person, creature, group or power that asks the party to do a job, and so starts a "
            "quest or a mission. They make the request, explain what is wanted, and usually "
            "offer a reward, a favour, payment, information or a way out of trouble. A patron, "
            "an employer, a client or a desperate villager begging for help; a noble posting a "
            "bounty, a guild master handing out contracts, a dying stranger pressing a map into "
            "a hero's hand, a spirit or god demanding a task. The quest comes from them, and the "
            "party often returns to them to report or to collect."
        ),
        (
            (
                "The innkeeper Bessa begs the party to find her missing son, and promises free "
                "rooms for a year."
            ),
            "Lord Varrow posts a bounty of 500 gold for the head of the bandit chief.",
            (
                "A dying knight gives Kael a sealed letter and asks him to carry it to the "
                "temple at Highmere."
            ),
            (
                "The river spirit will let them cross only if they cleanse the poisoned spring "
                "upstream."
            ),
        ),
        (
            "`herald` (announces news but asks nothing); `informant` (passes secrets but sets no "
            "task)."
        ),
    ),
    Role(
        "quest_target",
        "Plot",
        (
            "The person, creature, object or place that a quest is after: what the party has "
            "been sent to find, rescue, capture, kill, steal, escort, deliver or destroy. The "
            "goal of the mission, the bounty, the missing person, the stolen relic, the beast to "
            "be slain, the prisoner to be freed, the fugitive on the run. The story points "
            "toward them; reaching them completes or advances the quest."
        ),
        (
            "The bounty is on Grell One-Eye, the bandit chief hiding in the old mill.",
            "They have been hired to bring back the stolen Moonstone Chalice.",
            "Somewhere in the marsh is the innkeeper's missing son, Toby.",
            "The wyvern that has been taking sheep must be killed before the harvest fair.",
        ),
        (
            "`plot_artifact` (an item central to the whole plot, not just one quest's goal); "
            "`captive` (held by someone, whether or not a quest is after them)."
        ),
    ),
    Role(
        "antagonist",
        "Plot",
        (
            "The main opponent of the party or of a player character: the villain, the big bad, "
            "the enemy who works against the heroes and whose plans drive the conflict. They "
            "scheme, attack, threaten, betray or oppose the party, and the party works to stop "
            "or defeat them. An evil wizard, a tyrant, a cult leader, a dragon, a demon, a rival "
            "kingdom's general, a corrupt official, a curse given will. Their motives shape the "
            "campaign's story."
        ),
        (
            "The necromancer Valdrek is raising the dead to take the throne of Esk.",
            "Behind the attacks on the caravans is the Black Hand cult and its masked leader.",
            (
                "The red dragon Ashmaw wants the dwarven forge for her hoard and will burn the "
                "valley to get it."
            ),
            "Councillor Thorne has been poisoning the king and blaming the party.",
        ),
        (
            "`mini_boss` (a lesser villain under or beside the main one); `rival` (competes, but "
            "is not out to destroy them)."
        ),
    ),
    Role(
        "mini_boss",
        "Plot",
        (
            "A lesser villain or a strong enemy who stands between the party and the main "
            "antagonist: a lieutenant, a captain, a champion, a guardian of a dungeon level, a "
            "cult's high priest, a warlord serving a greater evil. Dangerous enough to be a big "
            "fight or an obstacle on their own, but not the final opponent. Often met at the end "
            "of one part of the story, with clues that lead further up the chain."
        ),
        (
            "Captain Rusk commands the necromancer's skeleton army at the bridge.",
            "The ogre chieftain Grumbash guards the mine entrance for the giants.",
            "Sister Nyla, the cult's high priestess, holds the temple until her master returns.",
            "Beating the troll warden of the second level opens the stairs down to the vault.",
        ),
        "`antagonist` (the main villain); `minion` (a rank-and-file follower).",
    ),
    Role(
        "minion",
        "Plot",
        (
            "A follower, servant, soldier or creature that serves a villain or another power and "
            "carries out its orders: a lackey, a thug, a goon, a henchman, a guard in a "
            "villain's pay, a summoned or created servant, part of a pack or horde. Any kind of "
            "being: goblins, cultists, skeletons, imps, hired swords, animated constructs, a "
            "wolf pack under a druid's control. Loyal out of fear, payment, magic or belief, and "
            "usually not acting for themselves."
        ),
        (
            "Three of the cult's acolytes are waiting in the crypt, sent by the high priestess.",
            "The necromancer's zombies shamble through the village at night.",
            "Snik the goblin runs errands for the hag and reports everything back to her.",
            "The bandit chief's thugs shake down the farmers every market day.",
        ),
        "`mini_boss` (leads minions); `captive` (forced, not serving willingly).",
    ),
    Role(
        "rival",
        "Plot",
        (
            "Someone who competes with a player character or the party for the same goal, prize, "
            "reputation, love, title or treasure, without being their deadly enemy. A competing "
            "adventuring party, a fellow student, an ambitious officer, a rival treasure hunter, "
            "a sibling seeking the same inheritance, a bard who mocks the party's fame. The "
            "competition can be friendly or bitter, and a rival may become an ally or an enemy "
            "as the story moves on."
        ),
        (
            (
                "The Silver Spurs, another adventuring company, want the same dragon bounty and "
                "keep getting there first."
            ),
            (
                "Liane and Kael were apprentices of the same master, and she never forgave him "
                "for being chosen."
            ),
            (
                "Every time the party wins a contest at the fair, the bard Corwin claims he "
                "could have done it better."
            ),
            "Two heirs race to find their grandfather's will before the other does.",
        ),
        "`antagonist` (wants to defeat or harm them); `ally` (on their side).",
    ),
    Role(
        "spy",
        "Plot",
        (
            "Someone who secretly gathers information for another person or power, often while "
            "pretending to be something else: an agent, a mole, a double agent, an infiltrator, "
            "a plant, a traitor in the party's midst. They watch, listen, report back, steal "
            "letters or plans, and may be loyal to the villain, a rival kingdom, a guild or a "
            "god. Their true allegiance is often hidden from the players and known only from the "
            "DM's notes."
        ),
        (
            (
                "The friendly stable boy at the inn has been reporting the party's movements to "
                "the baron."
            ),
            "In secret, Lady Ressa works for the Shadow Court and is reading the king's letters.",
            "The new party hireling is a cult agent sent to learn where the relic is kept.",
            "A raven follows them from town to town; it belongs to the witch.",
        ),
        "`informant` (passes information *to* the party); `minion` (open servant, not hidden).",
    ),
    Role(
        "scapegoat",
        "Plot",
        (
            "Someone who is blamed, accused or punished for something they did not do, or for "
            "more than their share: framed, falsely accused, made to carry the guilt to protect "
            "the real culprit. Often an outsider, a poor or unpopular person, a stranger, an "
            "enemy's convenient victim. The truth that they are innocent is often a secret the "
            "DM holds, or a mystery the party uncovers."
        ),
        (
            (
                "The town blames the old herbalist Marta for the plague, though it was the "
                "poisoned well."
            ),
            (
                "The guard captain arrested the half-orc stable hand for the murder to close the "
                "case quickly."
            ),
            "The cult framed the party's ally, Bren, for stealing the temple relic.",
            "Everyone believes the missing apprentice set the fire; in truth he was taken.",
        ),
        (
            "`martyr` (chooses to suffer for a cause); `captive` (held, whether or not they are "
            "blamed)."
        ),
    ),
    Role(
        "rebel",
        "Plot",
        (
            "Someone who resists, defies or fights against a ruler, government, empire, order, "
            "master or the way things are: a freedom fighter, a resistance leader, a "
            "revolutionary, an outlaw with a cause, a deserter, a heretic standing against the "
            "church. They may lead an uprising, hide refugees, sabotage the powerful or stir up "
            "the common folk. Sympathetic or dangerous depending on whose side the story is on."
        ),
        (
            "Mara Fenwick leads the farmers who refuse to pay the duke's new tax.",
            "Deep in the forest, a band of outlaws strikes at the Empire's supply wagons.",
            "A young priest preaches against the High Temple's corruption and is hunted for it.",
            "The goblin Skrit has had enough of the warlord and wants to lead his tribe away.",
        ),
        "`antagonist` (opposes the party); `rival` (competes with them).",
    ),
    Role(
        "tempter",
        "Plot",
        (
            "Someone or something that lures a player character or the party toward a bad, "
            "selfish, forbidden or dangerous choice by offering what they want: power, money, "
            "love, revenge, knowledge, an end to pain. A devil offering a pact, a seductive "
            "stranger, a cursed weapon that whispers, a corrupt patron, a hag making a deal, a "
            "voice in a dream. They use charm, promises, flattery, guilt or desire, and the "
            "price is hidden or terrible."
        ),
        (
            "The devil Malphas offers to bring Kael's sister back to life if he signs in blood.",
            "The black sword whispers to Liane that with it she could finally avenge her village.",
            (
                "A charming noble keeps inviting the party's rogue to private dinners and asking "
                "about the vault."
            ),
            "The hag will lift the curse, for the price of their firstborn.",
        ),
        "`quest_giver` (asks for a job, not a fall); `antagonist` (opposes them openly).",
    ),
    Role(
        "companion",
        "Party",
        (
            "A creature or being bound to a player character who travels and lives with them: a "
            "familiar, an animal companion, a steed, a summoned or bonded spirit, a beast of the "
            "ranger, a druid's wolf, a wizard's owl, a paladin's warhorse, a pet that follows "
            "everywhere. Usually not a person with their own story agenda, but always present, "
            "often helping in fights, scouting or carrying messages."
        ),
        (
            "Pip, Liane's owl familiar, scouts ahead and reports what she sees.",
            "Kael's wolf Ash growls whenever someone lies to him.",
            "The paladin's warhorse Brightmane can be called to her side.",
            "The goblin's pet rat rides in his hood and steals keys.",
        ),
        "`sidekick` (a person who tags along, with their own life).",
    ),
    Role(
        "sidekick",
        "Party",
        (
            "A person who tags along with someone and helps them, a player character or an NPC: "
            "a squire, an apprentice, a loyal friend, a hired guide who stayed, a kid who "
            "follows the heroes, a junior partner, an NPC's right-hand helper. They have their "
            "own personality and story, carry gear, fight a little, offer advice or support, and "
            "are usually loyal to the one they follow."
        ),
        (
            "Young Tam, the squire, insists on following Sir Kael into every fight.",
            "The guide Orrin stayed on after the mountains and now handles the party's horses.",
            "The baron never goes anywhere without his clerk Willem, who remembers every name.",
            "Little Bea follows the party around town and fetches whatever they need.",
        ),
        "`companion` (a bonded creature); `minion` (serves a villain).",
    ),
    Role(
        "mentor",
        "Party",
        (
            "Someone who teaches, trains, guides or advises a player character: a master, a "
            "tutor, a sensei, an old adventurer, a spiritual guide, a guildmaster who took them "
            "in, a wizard who taught them their first spells. They pass on skills, knowledge, "
            "wisdom or a legacy, and may test, protect or push the character to grow. Often "
            "older or more experienced, sometimes with a past of their own."
        ),
        (
            "Old Master Hara taught Liane the sword, and she still writes to him for advice.",
            "The druid Elowen shows Kael how to speak with the trees.",
            (
                "Brother Aldo, who raised the orphaned rogue in the monastery, gives her a last "
                "lesson before he dies."
            ),
            "The retired adventurer at the tavern teaches the party how to fight trolls.",
        ),
        "`ally` (helps, but does not teach); `quest_giver` (assigns a job).",
    ),
    Role(
        "ally",
        "Party",
        (
            "Someone who is friendly to the party or to a player character and on their side: a "
            "friend, a supporter, a helper, an old comrade, a trusted contact, a guild, a town "
            "or a creature that fights with them or helps them in need. They lend aid, shelter, "
            "gold, information, swords or a place to rest, and the party can turn to them."
        ),
        (
            "The dwarf smith Brannoc always has a bed and a whetstone for the party.",
            "The elves of Silverglen sent riders to help defend the village.",
            "Captain Mirrel of the city watch looks the other way when the party needs it.",
            "The talking crow they freed now warns them of danger.",
        ),
        "`sidekick` (travels with them); `informant` (passes secrets).",
    ),
    Role(
        "love_interest",
        "Party",
        (
            "Someone a player character is drawn to romantically, or who is drawn to them: a "
            "crush, a sweetheart, a lover, a betrothed, a flirtation, an old flame, a forbidden "
            "love. They are the subject of romance, longing, jealousy or heartbreak, and their "
            "fate matters to the character's heart."
        ),
        (
            "Kael blushes every time the baker's daughter Elin smiles at him.",
            "Liane's betrothed, Lord Aren, waits for her return in the capital.",
            "The bard has been writing songs about the mysterious elf at the masquerade.",
            "Her old flame Corin shows up again, now working for the enemy.",
        ),
        "`tempter` (lures toward a bad choice); `ally` (friendship, not romance).",
    ),
    Role(
        "informant",
        "Party",
        (
            "Someone who passes information to the party quietly, secretly or under limits: an "
            "informer, a contact, a snitch, a source, a whisperer, a go-between, a servant who "
            "overhears things, a fence who knows the underworld. The information comes at a "
            "price, in a code, in private, or at a risk to the informant, and they may not want "
            "to be seen with the party."
        ),
        (
            "The barmaid Sal slips Kael a note about who has been asking about him.",
            "For two silver, the beggar at the gate tells them which guards take bribes.",
            "The palace maid will only talk in the chapel at midnight.",
            (
                "Old Fenn the fence hears everything that moves through the docks and sells some "
                "of it."
            ),
        ),
        "`spy` (gathers information *for someone else*); `herald` (announces openly).",
    ),
    Role(
        "captive",
        "Party",
        (
            "Someone held against their will: a prisoner, a hostage, a slave, an enslaved "
            "worker, a kidnapped person, a creature in a cage, a soul bound by magic, a person "
            "locked away. They may need rescue, may be used as leverage, or may be a source of "
            "information or of help once freed."
        ),
        (
            "The bandits are holding the miller's daughter in the old tower for ransom.",
            "Chained deep in the mines, the dwarves have been forced to dig for the drow.",
            "A unicorn is kept in a cage in the duke's menagerie.",
            "The spirit of the old king is bound inside the crown.",
        ),
        "`quest_target` (sought by a quest); `scapegoat` (blamed).",
    ),
    Role(
        "herald",
        "Story-telling",
        (
            "Someone who announces, proclaims or delivers news, orders, warnings or messages, "
            "but not prophecies: a town crier, a messenger, a courier, an envoy, an ambassador, "
            "a royal herald, a delivery of a summons, a voice that brings the news. They bring "
            "information into the story and set events moving, but usually do not themselves "
            "take part."
        ),
        (
            (
                "The royal herald reads the king's decree in the market square: all blades must "
                "be registered."
            ),
            "A breathless rider brings news that the northern fort has fallen.",
            "The envoy of the elven queen delivers a demand for the return of the stone.",
            "A sending spell carries the guildmaster's message: come at once.",
        ),
        "`prophet` (foretells the future); `informant` (passes it quietly).",
    ),
    Role(
        "prophet",
        "Story-telling",
        (
            "Someone who predicts, foretells or sees the future: a seer, an oracle, a fortune "
            "teller, a soothsayer, a visionary, a dreamer of true dreams, a sibyl, a voice of a "
            "god speaking of what is to come. They give prophecies, omens, warnings and riddles "
            "about fate, often unclear until they come true."
        ),
        (
            "The oracle of the cave says the crown will be lost when the twin moons rise.",
            "Old Mother Gwen reads Kael's palm and warns him of a betrayal by someone close.",
            "A child in the village dreams of fire every night, and the dreams keep coming true.",
            "The prophecy speaks of a hero born under a falling star.",
        ),
        "`herald` (announces what has happened or is ordered).",
    ),
    Role(
        "witness",
        "Story-telling",
        (
            "Someone who saw or heard something that matters to the story: a bystander, an "
            "eyewitness, a survivor, the last person to see someone, someone who overheard a "
            "plot, who saw a murder or a theft, who was there when it happened. What they know "
            "can solve a mystery, clear a name or put them in danger."
        ),
        (
            "The stable boy saw a hooded figure leave the inn the night of the murder.",
            "The only survivor of the caravan attack says the raiders wore the duke's colours.",
            "The old fisherman watched the ship sink and swears it was dragged under.",
            "A maid overheard the councillors plotting behind the library door.",
        ),
        "`informant` (passes information as their job); `scapegoat` (blamed).",
    ),
    Role(
        "martyr",
        "Story-telling",
        (
            "Someone who suffers or gives their life for a cause, a belief, a person or the "
            "greater good: a self-sacrifice, a hero who died holding the line, a saint, a fallen "
            "leader, someone whose death inspires others. Their sacrifice or memory matters to "
            "the story and may drive others to act."
        ),
        (
            (
                "Sister Anwen gave her life to seal the demon in the shrine, and the town still "
                "lights candles for her."
            ),
            "Captain Doran held the bridge alone so the villagers could escape.",
            "The rebels rally under the banner of their executed leader.",
            "Kael's mentor stepped in front of the arrow meant for him.",
        ),
        "`scapegoat` (blamed, not choosing to suffer).",
    ),
    Role(
        "comic_relief",
        "Story-telling",
        (
            "Someone who is there to lighten the mood, make the table laugh, or bring a playful "
            "touch to the story: a clown, a jester, a bumbling guard, a talkative merchant, a "
            "mischievous creature, a cowardly sidekick, a fool who stumbles into trouble, a "
            "funny recurring character the players love."
        ),
        (
            "Bumbles the gnome keeps trying to sell the party 'magic' rocks.",
            "The guard Pell always falls asleep at the worst moment.",
            "A goat follows the party everywhere and eats their maps.",
            "The innkeeper tells the same terrible joke every time they arrive.",
        ),
        "`sidekick` (helps, whether funny or not).",
    ),
    Role(
        "ruler",
        "World",
        (
            "Someone who rules, governs or leads a land, people, city, tribe, group or "
            "organisation: a king, a queen, an emperor, a duke, a baron, a lord, a mayor, a "
            "chieftain, a warlord, a guildmaster, a high priest, the head of a council or a "
            "clan. Their decisions shape the lives of many, and they hold power, laws, soldiers "
            "and wealth."
        ),
        (
            "Queen Isolde rules the kingdom of Valemar from the White Keep.",
            "The mayor of Thornbury runs the town council and owns half its businesses.",
            "Chieftain Ugrak leads the Bloodfang orcs.",
            "The Grandmaster of the Mages' Guild decides who may study in the tower.",
        ),
        "`god` (a divine power); `lore_figure` (important in history, not ruling now).",
    ),
    Role(
        "god",
        "World",
        (
            "A deity, a divine or near-divine power, or the avatar or chosen form of one: a god, "
            "a goddess, a demigod, an archfey, a demon lord, a great elemental, an ancient "
            "spirit worshipped as divine. They grant power, answer prayers, send omens, curse or "
            "bless, and may walk the world in disguise."
        ),
        (
            "The young elf with golden eyes is the avatar of Seluna, goddess of the moon.",
            "The paladin's power comes from Torvald, god of the forge.",
            "The Lady of the Lake is worshipped by the villagers as a goddess.",
            "The demon lord Vexis whispers to his cultists in their dreams.",
        ),
        "`ruler` (a mortal ruler); `lore_figure` (important in legend).",
    ),
    Role(
        "lore_figure",
        "World",
        (
            "Someone important in the world's history, legend or myth, often dead, absent, "
            "legendary or long gone: an ancient hero, a founder, a legendary king, a lost "
            "wizard, a figure of song and story, the maker of a famous artifact, a name carved "
            "on old monuments. They shape the present through what they did, left behind or are "
            "remembered for."
        ),
        (
            "The city was founded by Aldric the Bold, whose statue still stands in the square.",
            "The sword was forged by Mirelle, the legendary elven smith, a thousand years ago.",
            "Bards still sing of the Seven who sealed the dragon beneath the mountain.",
            "The lost wizard Ebon vanished with his tower three centuries ago.",
        ),
        "`god` (divine); `ruler` (governs now).",
    ),
    Role(
        "worker",
        "World",
        (
            "Someone who works a trade or job that the party deals with in everyday life: a "
            "shopkeeper, a merchant, a smith, a barkeep, a tavern worker, an innkeeper, a stable "
            "hand, a cook, a guard, a ferryman, a healer, a clerk, a farmer, a servant. Mostly "
            "part of the setting, they sell, serve, guard, fix and help, and may become more "
            "important if the story turns to them."
        ),
        (
            "The smith Brannoc can fix their armour by morning.",
            "Bessa runs the Crooked Lantern inn and knows every regular.",
            "The gate guards are checking every wagon for smuggled goods.",
            "The ferryman charges a silver for the crossing and twice that after dark.",
        ),
        "`commoner` (no work role that matters to the story).",
    ),
    Role(
        "commoner",
        "World",
        (
            "An ordinary person or creature in the world with no special part in the story so "
            "far: a villager, a passer-by, a townsperson, a traveller, a farmer's child, a face "
            "in the crowd, a member of the public. The default for anyone or anything that has "
            "no other role. Never stored; an entry with no other role is a commoner."
        ),
        (
            "A woman in the crowd shouts at the herald.",
            "Some children are playing by the well.",
            "A traveller on the road nods as they pass.",
            "A farmer is leading his cow to market.",
        ),
        "any other role (the default).",
    ),
    Role(
        "plot_artifact",
        "Things",
        (
            "An item central to the story's plot: a relic, a magic object, a weapon of legend, a "
            "key, a book, a map, a crown, a gem, a cursed thing that drives the action. Villains "
            "want it, the heroes must find, protect or destroy it, and its power or history "
            "shapes the campaign. The object at the heart of the big bad's plans or of the "
            "party's quest."
        ),
        (
            "The Crown of Ash would let the necromancer command every dead soul in the kingdom.",
            "The Starfall Map shows the way to the lost city, and three factions want it.",
            "The cursed dagger that killed the king has been stolen from the vault.",
            "Only the Sunstone can relight the temple's sacred fire.",
        ),
        "`quest_target` (one quest's goal, not central to the whole plot).",
    ),
    Role(
        "key_location",
        "Things",
        (
            "A place central to the story: a dungeon, a castle, a city, a ruin, a temple, a "
            "forest, a cave, a tower, a plane of existence, a hideout, a battlefield. Important "
            "events happen there, the party must go there, and secrets, treasure, enemies or "
            "answers are found there."
        ),
        (
            "The Sunken Temple holds the last seal on the demon.",
            "Everything in the campaign leads back to the ruined city of Karsa.",
            "The thieves' guild hides under the old bathhouse.",
            "The final battle will be fought at the Pass of Winds.",
        ),
        "`central_event` (something that happens, not a place).",
    ),
    Role(
        "central_event",
        "Things",
        (
            "An event central to the story: a battle, a war, a coronation, a disaster, a murder, "
            "a betrayal, a festival, a ritual, a plague, an eclipse, a wedding, a fall of a "
            "city, a past tragedy or a coming danger that shapes what everyone does. The event "
            "the plot turns on, often foretold, remembered or feared."
        ),
        (
            "The Night of Red Stars, when the old king was murdered, started the civil war.",
            "When the ritual is completed at the eclipse, the demon will be freed.",
            "The Harvest Festival is when the cult plans to strike.",
            "The Great Flood a century ago buried the old capital.",
        ),
        "`key_location` (a place).",
    ),
)
SEEDED = {r.code: r for r in SEEDED_ROLES}
STORED_SEEDED = tuple(r.code for r in SEEDED_ROLES if r.code != COMMONER)  # (commoner: never a row)

# A role a campaign's reading adds (#3172): its code and the same three fields, within these limits,
# and no more than `ADDED_MAX` per campaign. A bad one is dropped and counted, never coerced.
ADDED_MAX = 40
CODE_SHAPE = re.compile(r"^[a-z]+(?:_[a-z]+){0,3}$")
CODE_CHARS = 32
LABEL_CHARS = 60
DESCRIPTION_CHARS = 1200
EXAMPLE_CHARS = 200
EXAMPLES_MAX = 6
EXAMPLES_MIN = 3


def check_added(
    code: str, label: str, description: str, examples: tuple[str, ...], not_this: str
) -> str | None:
    """Why this added role is refused, as a count key, or None when it may be stored. The words
    themselves are never logged."""
    if not CODE_SHAPE.fullmatch(code) or len(code) > CODE_CHARS:
        return "bad_code"
    if code in SEEDED:
        return "seeded_code"
    if not label.strip() or len(label) > LABEL_CHARS or label.strip().isdigit():
        return "bad_label"
    if not description.strip() or len(description) > DESCRIPTION_CHARS:
        return "bad_description"
    if not EXAMPLES_MIN <= len(examples) <= EXAMPLES_MAX or any(
        not e.strip() or len(e) > EXAMPLE_CHARS for e in examples
    ):
        return "bad_examples"
    if len(not_this) > LABEL_CHARS * 4:
        return "bad_not_this"
    return None


__all__ = [
    "ADDED_MAX", "COMMONER", "SEEDED", "SEEDED_ROLES", "STORED_SEEDED", "Role", "check_added",
]  # fmt: skip
