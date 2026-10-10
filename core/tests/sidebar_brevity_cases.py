"""Real table questions for the DM sidebar's brevity rules (#934; owner, 2026-10-09).

Each case: the question a DM might ask at the table, what a fake AI replies (the replies are
in order; a case with two replies is one where the first breaks a rule and the engine asks
again), and what the final answer must satisfy. CI runs them with a fake AI client
(`tests/test_sidebar_answer.py`); `scripts/sidebar_brevity_check.py` runs the same questions
against the real model on the server and prints the answers for Supervisor to read.
"""

from __future__ import annotations

from dataclasses import dataclass


def reply(
    answer: str,
    source: str = "none",
    sure: str = "sure",
    in_game: str = "yes",
    on_topic: str = "yes",
) -> str:
    return (
        f"ANSWER: {answer}\nSOURCE: {source}\nSURE: {sure}\n"
        f"IN_GAME: {in_game}\nON_TOPIC: {on_topic}"
    )


@dataclass(frozen=True)
class Case:
    question: str
    replies: tuple[str, ...]  # what the fake AI says, one per call
    must_any: tuple[str, ...]  # at least one of these (any case) is in the answer
    yes_no: bool = False  # the answer starts with Yes or No
    calls: int = 1  # how many AI calls the engine should make
    must_not: tuple[str, ...] = ()  # none of these (any case) is in the answer (#992)
    in_game: bool = True


CASES: tuple[Case, ...] = (
    Case(  # the real model once said "The free rules don't say" with the entry in front of it:
        # asked once more, then the entry's own words and source (#992)
        "do you need line of sight for fireball",
        (
            reply("The free rules don't say. Your call.", "SRD 5.2.1 p. 131", "not sure"),
            reply("The free rules don't say. Your call.", "SRD 5.2.1 p. 131", "not sure"),
        ),
        ("point you choose", "choose a point"),
        calls=2,
        must_not=("don't say", "your call"),
    ),
    Case(
        "hold on, I need to find if you need line of sight for fireball",
        (reply("No. It starts at a point you choose within range.", "SRD 5.2.1 p. 131"),),
        ("point you choose", "choose a point"),
        yes_no=True,
        must_not=("don't say",),
    ),
    Case(
        "how much damage does fireball do",
        (reply("8d6 fire damage, half on a successful save.", "SRD 5.2.1 p. 131"),),
        ("8d6",),
    ),
    Case(  # greeting and offer are stripped in code: no second call needed
        "does fireball set things on fire",
        (
            reply(
                "Great question! Yes, flammable objects in the area that aren't worn or "
                "carried start burning. Let me know if you want the full spell text.",
                "SRD 5.2.1 p. 131",
            ),
        ),
        ("burning",),
        yes_no=True,
    ),
    Case(  # not in the free rules' index: no source, so never "sure" (#992)
        "can fireball hit someone behind a wall",
        (reply("No. A solid wall blocks the blast.", "SRD 5.2.1 p. 131", "not sure"),),
        ("not in dmbot's rules",),
        yes_no=True,
        must_not=("sure)", ", sure"),
    ),
    Case(  # no Yes/No first: asked once more
        "can fireball spread around a corner",
        (
            reply("Fireball spreads around corners, so it can.", "SRD 5.2.1 p. 131"),
            reply("Yes. It spreads around corners.", "SRD 5.2.1 p. 131"),
        ),
        ("around corners",),
        yes_no=True,
        calls=2,
    ),
    Case(
        "can a goblin be grappled while it's prone",
        (
            reply(
                "The Grappled condition and the Prone condition can both apply to a creature "
                "at the same time, because neither one ends the other. A grappled creature's "
                "speed is 0, and a prone creature can only crawl or stand up. So yes.",
                "SRD 5.2.1 p. 182",
            ),
            reply(
                "Yes. A creature can be Grappled and Prone at once; its speed is 0 while grappled.",
                "SRD 5.2.1 p. 182",
            ),
        ),
        ("yes",),
        yes_no=True,
        calls=2,
    ),
    Case(
        "what does the grappled condition do",
        (
            reply(
                "A grappled creature's speed is 0 and it has disadvantage on attacks against "
                "anyone but the grappler. The condition ends if the grappler is incapacitated "
                "or the creature is moved out of reach. It also affects carrying capacity in "
                "some situations and interacts with many other rules in many ways.",
                "SRD 5.2.1 p. 182",
            ),
            reply(
                "A grappled creature's speed is 0 and it has disadvantage on attacks against "
                "anyone but the grappler. The condition ends if the grappler is incapacitated "
                "or the creature is moved out of reach. It also affects carrying capacity too.",
                "SRD 5.2.1 p. 182",
            ),
        ),
        ("speed is 0", "speed becomes 0"),
        calls=2,
    ),
    Case(
        "is there a rule for a critical hit on a spell attack",
        (reply("Yes. A spell attack can crit on a natural 20.", "none", "sure"),),
        ("not in dmbot's rules",),
        yes_no=True,
        must_not=("sure)", ", sure"),
    ),
    Case(
        "what's my house rule for criticals",
        (reply("House rule 3: criticals deal max damage plus the roll.", "House rule 3"),),
        ("house rule 3",),
    ),
    Case(
        "what do I know about Belleros",
        (reply("I don't have that. Your call.", "none", "not sure"),),
        ("your call",),
    ),
    Case(  # the older fireball is named as such and cites its own page (#1005)
        "does fireball go around corners in 2014",
        (reply("Yes. In 2014 it spreads around corners.", "SRD 5.1 p. 144"),),
        ("[Legacy 2014]", "spreads around corners"),
        yes_no=True,
    ),
    Case(
        "who does Belleros work for",
        (reply("I don't have that. Your call.", "none", "not sure"),),
        ("your call",),
    ),
    Case(
        "how do I stop DMbot recording me",
        (
            reply(
                "Press Menu in DMbot's private message, then Stop recording me.",
                "DMbot help",
                in_game="no",
            ),
        ),
        ("stop recording me",),
        in_game=False,
    ),
    Case(
        "can players read the transcript",
        (reply("Yes. Anyone in the server can read and download it.", "DMbot help", in_game="no"),),
        ("anyone in the server",),
        yes_no=True,
        in_game=False,
    ),
    Case(
        "is the 2014 goblin different",
        (reply("Yes. It is now the Goblin Warrior in the newer rules.", "SRD 5.2.1 p. 290"),),
        ("goblin warrior",),
        yes_no=True,
        must_not=("don't say",),
    ),
    Case(
        "what's the best pizza topping",
        (reply("Pepperoni.", on_topic="no"),),
        ("only help with the game",),
        in_game=False,
    ),
    Case(
        "write me a poem about my dog",
        (reply("Woof.", on_topic="no", in_game="no"),),
        ("only help with the game",),
        in_game=False,
    ),
    Case(  # class features are not in the index yet: said plainly, never "sure" (#992)
        "should I let the rogue sneak attack with a spell",
        (reply("No. Sneak Attack needs a weapon attack.", "none", "sure"),),
        ("not in dmbot's rules",),
        yes_no=True,
        must_not=("sure)", ", sure"),
    ),
)
