"""The golden set for matching a proposed story role to the campaign's roles (#3172): invented roles
as a reading might propose them, each with the seeded role it should land on, or `NONE` when it is
a role of its own and must be added. dev1's run (`devtools/role_match_check.py`, real embeddings)
counts agreement, the margin and wrong matches on `NONE`; Supervisor rules the threshold, margin and
switch from those counts. Every word is invented for this file. Pure data.

The matched cases are fresh wordings (other codes, other examples), never the seeded text, so the
agreement is not inflated; several sit on a seeded role's `not_this` line (`antagonist` and
`mini_boss`, `spy` and `informant`, `herald` and `prophet`), where a wrong match costs most.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory import roles

NONE = "none"


@dataclass(frozen=True, slots=True)
class Case:
    code: str  # as a reading would propose it
    description: str
    examples: tuple[str, str, str]
    expected: str  # a seeded code, or NONE

    @property
    def text(self) -> str:
        """What is embedded (`roles.embed_text`), the same as for a real proposed role."""
        return roles.embed_text(self.description, self.examples)


def c(code: str, description: str, examples: tuple[str, str, str], expected: str) -> Case:
    return Case(code, description, examples, expected)


CASES: tuple[Case, ...] = (
    # --- Plot
    c(
        "job_offerer",
        "Someone who hires the party for a task and promises a reward when it is done.",
        (
            "The harbourmaster pays them to clear the sunken wreck.",
            "A widow begs them to find her son.",
            "The guild posts a contract and the party takes it.",
        ),
        "quest_giver",
    ),
    c(
        "employer",
        "A patron who sends the adventurers out on missions and expects a report.",
        (
            "Lady Orsa sends them north every season.",
            "The temple asks them to escort a pilgrim.",
            "A merchant prince funds their errands.",
        ),
        "quest_giver",
    ),
    c(
        "bounty",
        "The person or thing the party was sent to bring back, dead or alive.",
        (
            "The escaped forger they must return to the city.",
            "The stolen idol they were hired to recover.",
            "The lost caravan they are searching for.",
        ),
        "quest_target",
    ),
    c(
        "main_villain",
        "The chief enemy of the heroes, whose scheme drives the whole campaign.",
        (
            "The lich who wants to drown the valley.",
            "The usurper behind every attack.",
            "The cult leader the party must stop.",
        ),
        "antagonist",
    ),
    c(
        "big_bad",
        "The final foe the story builds toward, the one pulling every string.",
        (
            "The dragon queen in the mountain.",
            "The archmage who betrayed the order.",
            "The demon waiting beyond the gate.",
        ),
        "antagonist",
    ),
    c(
        "lieutenant",
        "A strong officer under the main villain who must be beaten on the way to them.",
        (
            "The ogre captain who guards the bridge.",
            "The witch's right hand at the tower.",
            "The general who leads the usurper's army.",
        ),
        "mini_boss",
    ),
    c(
        "dungeon_guardian",
        "A powerful creature that defends one level or vault, a boss short of the last.",
        (
            "The golem at the sealed door.",
            "The hag ruling the swamp's edge.",
            "The knight who holds the second gate.",
        ),
        "mini_boss",
    ),
    c(
        "henchman",
        "A low-ranking thug who does a villain's dirty work.",
        (
            "The bandits who ambush the road for the baron.",
            "Cultists carrying out the high priest's orders.",
            "Goblin raiders sent by the warlord.",
        ),
        "minion",
    ),
    c(
        "enforcer",
        "A hired muscle who follows a crime boss's orders without question.",
        (
            "The bruiser collecting debts for the syndicate.",
            "Guards paid to keep the docks quiet.",
            "Skeletons raised to serve the necromancer.",
        ),
        "minion",
    ),
    c(
        "competitor",
        "Someone racing the party for the same prize without wanting them dead.",
        (
            "Another adventuring band after the same treasure.",
            "A bard competing for the same patron.",
            "A noble seeking the same title.",
        ),
        "rival",
    ),
    c(
        "mole",
        "A hidden agent who reports secrets back to a hostile power while pretending to belong.",
        (
            "The servant who sends letters to the enemy.",
            "The guard captain secretly in the cult.",
            "A merchant gathering news for a foreign crown.",
        ),
        "spy",
    ),
    c(
        "double_agent",
        "Someone who appears to work for one side but secretly gathers information for another.",
        (
            "The courtier passing plans to the rebels.",
            "A thief reporting the guild's moves to the watch.",
            "The envoy listening for her queen.",
        ),
        "spy",
    ),
    c(
        "framed",
        "Someone wrongly blamed for a crime they did not commit.",
        (
            "The stable boy accused of the murder.",
            "A priest blamed for the plague.",
            "The miller hanged for another man's theft.",
        ),
        "scapegoat",
    ),
    c(
        "freedom_fighter",
        "Someone leading an uprising against an unjust ruler or empire.",
        (
            "The outlaw who raids the tax wagons.",
            "A slave who leads the revolt in the mines.",
            "The partisan captain in the hills.",
        ),
        "rebel",
    ),
    c(
        "insurgent",
        "A member of a resistance working to overthrow those in power.",
        (
            "The printer spreading pamphlets against the duke.",
            "Villagers who refuse the tithe.",
            "A deserter who joined the uprising.",
        ),
        "rebel",
    ),
    c(
        "corrupter",
        "Someone who offers the heroes their heart's desire to lead them astray.",
        (
            "The devil offering a dying hero more years.",
            "A whisper promising power to the warlock.",
            "The stranger tempting the paladin with gold.",
        ),
        "tempter",
    ),
    # --- Party
    c(
        "familiar",
        "A small magical creature bonded to a spellcaster that goes everywhere with them.",
        (
            "The wizard's owl that scouts ahead.",
            "A ranger's wolf at her side.",
            "The druid's bonded spirit hound.",
        ),
        "companion",
    ),
    c(
        "squire",
        "A loyal person who follows a hero, carries their things and helps where they can.",
        (
            "The farm girl who joined the knight.",
            "An apprentice following the old wizard.",
            "A guide who stayed with the party after the pass.",
        ),
        "sidekick",
    ),
    c(
        "teacher",
        "Someone who trains a hero in their craft and gives them advice.",
        (
            "The old monk who taught her to fight.",
            "A retired thief who teaches lockpicking.",
            "The archmage tutoring the young sorcerer.",
        ),
        "mentor",
    ),
    c(
        "trainer",
        "A master who guides a character's growth in skill or faith.",
        (
            "The swordmaster at the academy.",
            "A hermit who teaches the druid the old ways.",
            "The priestess guiding the cleric's prayers.",
        ),
        "mentor",
    ),
    c(
        "friend",
        "Someone friendly to the party who helps when asked.",
        (
            "The innkeeper who hides them from the guards.",
            "An old comrade who lends them horses.",
            "A noble who speaks for them at court.",
        ),
        "ally",
    ),
    c(
        "supporter",
        "A trusted contact on the heroes' side who backs them up.",
        (
            "The captain who vouches for them.",
            "A druid circle that offers shelter.",
            "The guildmistress who sends help.",
        ),
        "ally",
    ),
    c(
        "romance",
        "Someone a character falls in love with or who loves them.",
        (
            "The bard the rogue keeps writing to.",
            "A betrothed waiting at home.",
            "The ranger's old flame in the city.",
        ),
        "love_interest",
    ),
    c(
        "snitch",
        "Someone who quietly tells the party what they hear, for coin or favours.",
        (
            "The beggar who knows the gang's hideout.",
            "A clerk leaking the duke's ledgers to them.",
            "The barmaid who passes them rumours.",
        ),
        "informant",
    ),
    c(
        "prisoner",
        "Someone held against their will.",
        (
            "The prince chained in the dungeon.",
            "Villagers taken by the slavers.",
            "A spirit bound in a lamp.",
        ),
        "captive",
    ),
    c(
        "hostage",
        "Someone kept by force to make others obey.",
        (
            "The mayor's daughter held by the bandits.",
            "A merchant kidnapped for ransom.",
            "The elf held in a cage by the giants.",
        ),
        "captive",
    ),
    # --- Story-telling
    c(
        "messenger",
        "Someone who carries news or orders from one place to another.",
        (
            "The courier bringing the king's summons.",
            "A town crier reading the decree.",
            "The rider with word of the siege.",
        ),
        "herald",
    ),
    c(
        "seer",
        "Someone who sees what is to come.",
        (
            "The blind woman who foretells the flood.",
            "An oracle in the temple of the moon.",
            "The child who dreams of the future.",
        ),
        "prophet",
    ),
    c(
        "fortune_teller",
        "Someone who reads cards or stars to tell what will happen.",
        (
            "The fortune teller at the fair.",
            "A soothsayer warning the king.",
            "The hermit who reads omens in smoke.",
        ),
        "prophet",
    ),
    c(
        "eyewitness",
        "Someone who saw something important happen.",
        (
            "The fisherman who saw the ship burn.",
            "A maid who heard the argument.",
            "The last person to see the missing wizard.",
        ),
        "witness",
    ),
    c(
        "survivor",
        "The only one who lived through what happened and can tell of it.",
        (
            "The scout who escaped the massacre.",
            "A child who hid during the raid.",
            "The sailor who survived the wreck.",
        ),
        "witness",
    ),
    c(
        "sacrifice",
        "Someone who gives their life for others or for a cause.",
        (
            "The knight who held the gate so others could flee.",
            "A priestess who died sealing the rift.",
            "The farmer who gave himself up to save his village.",
        ),
        "martyr",
    ),
    c(
        "jester",
        "Someone who makes the table laugh and keeps the mood light.",
        (
            "The clumsy guard who always gets lost.",
            "A talking goat with opinions.",
            "The court fool who mocks the king.",
        ),
        "comic_relief",
    ),
    # --- World
    c(
        "monarch",
        "Someone who governs a realm or city.",
        (
            "The queen of the northern kingdom.",
            "A mayor running the river town.",
            "The chieftain of the hill clans.",
        ),
        "ruler",
    ),
    c(
        "lord",
        "A noble who rules land and the people on it.",
        ("The baron of the marsh.", "A duke over three cities.", "The elder council of the elves."),
        "ruler",
    ),
    c(
        "deity",
        "A god or divine power that people worship.",
        (
            "The goddess of the harvest.",
            "A demon lord with mortal followers.",
            "The storm god the sailors pray to.",
        ),
        "god",
    ),
    c(
        "legend",
        "A famous figure from the world's past remembered in stories.",
        (
            "The founder of the city, dead a thousand years.",
            "A hero of the old war in the songs.",
            "The first king, now a statue.",
        ),
        "lore_figure",
    ),
    c(
        "shopkeeper",
        "Someone who works a trade the party deals with often.",
        (
            "The blacksmith who mends their armour.",
            "A merchant selling potions.",
            "The innkeeper who rents them rooms.",
        ),
        "worker",
    ),
    c(
        "tradesman",
        "A craftsperson or seller the heroes buy from or hire.",
        (
            "The fletcher in the market.",
            "A ferryman taking them across.",
            "The stablemaster who keeps their horses.",
        ),
        "worker",
    ),
    # --- Things
    c(
        "relic",
        "An object at the heart of the whole story.",
        (
            "The crown that can command the dead.",
            "A sword forged to slay the dragon.",
            "The book every faction is hunting.",
        ),
        "plot_artifact",
    ),
    c(
        "stronghold",
        "A place the story keeps coming back to.",
        (
            "The ruined castle on the cliff.",
            "A temple buried under the desert.",
            "The villain's tower in the swamp.",
        ),
        "key_location",
    ),
    c(
        "landmark_site",
        "A location central to the campaign's events.",
        (
            "The haunted forest where it began.",
            "A city under siege.",
            "The cave where the gate will open.",
        ),
        "key_location",
    ),
    c(
        "turning_point",
        "Something that happened which changed the course of the story.",
        (
            "The night the king was murdered.",
            "A battle that broke the alliance.",
            "The eclipse that woke the old god.",
        ),
        "central_event",
    ),
    c(
        "catastrophe",
        "A disaster that shapes everything after it.",
        (
            "The flood that drowned the lower city.",
            "A plague that emptied the valley.",
            "The fire that burned the library.",
        ),
        "central_event",
    ),
    # --- near a `not_this` line: must land on the role named, not its neighbour
    c(
        "crier",
        "Someone who announces public news and decrees, never predicting anything.",
        (
            "The herald reading the new tax law.",
            "A rider announcing the prince's wedding.",
            "The bellman calling out the curfew.",
        ),
        "herald",
    ),
    c(
        "whisperer",
        "Someone who passes secrets to the heroes, not to anyone else.",
        (
            "The maid who tells the party what the duke said.",
            "A fence who points them to the vault.",
            "The monk who slips them the abbot's letters.",
        ),
        "informant",
    ),
    c(
        "captain_of_the_guard",
        "The villain's second, strongest of the enemy's officers but not the one in charge.",
        (
            "The black knight who serves the necromancer.",
            "The pirate first mate of the dread captain.",
            "The chief of the cult's warriors.",
        ),
        "mini_boss",
    ),
    # --- roles of their own: nothing should be used
    c(
        "heir",
        "Someone with a claim to rule who does not rule yet.",
        (
            "The exiled prince waiting to come of age.",
            "A bastard daughter with the king's ring.",
            "The young duke's cousin next in line.",
        ),
        NONE,
    ),
    c(
        "benefactor",
        "Someone who pays the party's way without asking for any task in return.",
        (
            "An anonymous donor who covers their debts.",
            "The old lady who leaves them a house.",
            "A merchant who gives them a ship out of gratitude.",
        ),
        NONE,
    ),
    c(
        "judge",
        "Someone who settles disputes and passes sentence.",
        (
            "The magistrate at the trial.",
            "An elder who decides between clans.",
            "The arbiter of the duel.",
        ),
        NONE,
    ),
    c(
        "ghost",
        "The restless dead lingering where they died, wanting something finished.",
        (
            "The drowned bride in the well.",
            "A soldier who still walks the wall.",
            "The child who knocks at night.",
        ),
        NONE,
    ),
    c(
        "creditor",
        "Someone the party owes money or a favour to.",
        (
            "The moneylender who funded their gear.",
            "A fey who is owed a song.",
            "The noble who paid their bail.",
        ),
        NONE,
    ),
    c(
        "debtor",
        "Someone who owes the party money or a favour.",
        (
            "The gambler who lost to the rogue.",
            "A lord whose life they saved.",
            "The smith who promised them a free blade.",
        ),
        NONE,
    ),
    c(
        "exile",
        "Someone banished from their home who cannot go back.",
        (
            "The elf cast out of her forest.",
            "A disgraced knight in a far land.",
            "The heretic thrown out of the city.",
        ),
        NONE,
    ),
    c(
        "recruiter",
        "Someone trying to get the party to join a faction.",
        (
            "The guild agent offering membership.",
            "A sergeant signing up soldiers.",
            "The cult greeter at the gate.",
        ),
        NONE,
    ),
    c(
        "hermit",
        "Someone who lives alone, far from others, and wants to be left alone.",
        (
            "The old man in the mountain hut.",
            "A druid in the deep woods.",
            "The monk on the island.",
        ),
        NONE,
    ),
    c(
        "matchmaker",
        "Someone who arranges marriages and introductions between others.",
        (
            "The aunt planning every wedding in town.",
            "A noblewoman pairing off courtiers.",
            "The innkeeper who sets up travellers.",
        ),
        NONE,
    ),
    c(
        "rescuer",
        "Someone who once saved the party's life.",
        (
            "The fisher who pulled them from the river.",
            "A ranger who found them in the snow.",
            "The healer who carried them out of the fire.",
        ),
        NONE,
    ),
    c(
        "chronicler",
        "Someone who writes down the party's deeds for history.",
        (
            "The scribe following them from town to town.",
            "A bard composing their saga.",
            "The historian recording the war.",
        ),
        NONE,
    ),
)

__all__ = ["CASES", "NONE", "Case"]
